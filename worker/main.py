"""
Skillberry Worker Service — FastAPI entry point.

Endpoints
---------
POST /v1/chat/completions   Run the agentic ReAct loop; return OpenAI-compatible response.
GET  /trajectory            Return the recorded tool-call trajectory for this session.
POST /disconnect            Tear down the VMCP server and purge trajectory.
GET  /health                Liveness probe.

Worker environment variables (read at startup)
----------------------------------------------
LLM_BASE_URL        Praxis llm-egress URL   default: http://127.0.0.1:8081/v1
WORKER_LOG_LEVEL    Python log level        default: INFO
WORKER_LOG_FILE     Log file path           default: /tmp/worker.log
WORKER_PORT         HTTP listen port        default: 8001

Agent configuration — injected by Praxis as x-skillberry-* request headers
---------------------------------------------------------------------------
Praxis expands its own env vars into the pipeline config at deploy time
(envsubst) and injects them via the built-in `headers` filter on the
client-ingress → worker leg. The worker reads them from headers only.

x-skillberry-skill-uuid             SKILL_UUID
x-skillberry-skill-name             SKILL_NAME
x-skillberry-enable-think-logs      ENABLE_THINK_LOGS       default: false
x-skillberry-use-agent-tools        USE_AGENT_TOOLS         default: true
x-skillberry-use-agent-prompts      USE_AGENT_PROMPTS       default: true
x-skillberry-mcp-prompts-position   MCP_PROMPTS_POSITION    default: postfix
x-skillberry-react-recursion-limit  REACT_RECURSION_LIMIT   default: 20
x-skillberry-tools-url              SKILLBERRY_TOOLS_URL    default: http://127.0.0.1:8000
"""
import json
import logging
import logging.handlers
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from worker.agentic_graph import disconnect, execute_agentic_graph, trajectory

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
log_level = os.environ.get("WORKER_LOG_LEVEL", "INFO").upper()
log_file  = os.environ.get("WORKER_LOG_FILE", "/tmp/worker.log")

logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s [%(filename)s:%(lineno)d] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=3),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Skillberry Worker",
    description="Thin agentic shim behind Praxis.",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
SKILLBERRY_CONTEXT_KEY = "skillberry-context"


class ChatMessage(BaseModel):
    role: str = Field(..., description="'system', 'user', or 'assistant'")
    content: str = Field(..., description="Message content")

    def to_langchain(self):
        if self.role == "user":
            return HumanMessage(content=self.content)
        if self.role == "assistant":
            return AIMessage(content=self.content)
        if self.role == "system":
            return SystemMessage(content=self.content)
        return HumanMessage(content=self.content)  # safe fallback


class ToolFunction(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class Tool(BaseModel):
    type: str = "function"
    function: ToolFunction


class ChatRequest(BaseModel):
    model: str = Field(..., description="Model string; forwarded to Praxis llm-egress as-is")
    messages: List[ChatMessage]
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(8192, gt=0)
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[str] = "auto"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_skillberry_context(request: Request) -> dict:
    """Reconstruct skillberry_context from x-skillberry-context-* headers."""
    from skillberry_agent_lib.utils import unflatten_keys
    ctx = unflatten_keys(dict(request.headers)).get(SKILLBERRY_CONTEXT_KEY)
    if ctx is None:
        logger.warning("No Skillberry context headers found; using default env_id")
        ctx = {"env_id": "default"}
    elif "env_id" not in ctx:
        logger.warning("Skillberry context missing env_id; using default")
        ctx["env_id"] = "default"
    return ctx


def _safe_int(value: Optional[str], default: int) -> int:
    """Parse an integer header value, falling back to default on any error."""
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        logger.warning(f"Invalid integer header value '{value}', using default {default}")
        return default


def _extract_agent_config(request: Request) -> dict:
    """Read agent configuration from x-skillberry-* headers injected by Praxis.

    Praxis expands its own env vars into the pipeline config at deploy time
    (envsubst) and sets these headers via the built-in `headers` filter on every
    request forwarded to the worker. The worker never reads os.environ for these.
    """
    h = request.headers
    return {
        "skill_uuid":            h.get("x-skillberry-skill-uuid"),
        "skill_name":            h.get("x-skillberry-skill-name"),
        "enable_think_logs":     h.get("x-skillberry-enable-think-logs", "false").lower() in ("true", "1", "yes"),
        "use_agent_tools":       h.get("x-skillberry-use-agent-tools", "true").lower() in ("true", "1", "yes"),
        "use_agent_prompts":     h.get("x-skillberry-use-agent-prompts", "true").lower() in ("true", "1", "yes"),
        "mcp_prompts_position":  h.get("x-skillberry-mcp-prompts-position", "postfix"),
        "react_recursion_limit": _safe_int(h.get("x-skillberry-react-recursion-limit"), 20),
        "tools_url":             h.get("x-skillberry-tools-url", "http://127.0.0.1:8000"),
    }


def _convert_tools(tools: List[Tool]) -> List[Dict[str, Any]]:
    """Convert Pydantic Tool models to OpenAI JSON schema dicts for the shared lib."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.function.name,
                "description": t.function.description or "",
                "parameters": t.function.parameters or {},
            },
        }
        for t in tools
    ]


def _build_response(llm_response: Any, model: str) -> Dict[str, Any]:
    """Serialise the graph output into an OpenAI-compatible chat.completion object."""
    if hasattr(llm_response, "content"):
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": llm_response.content or "",
        }
        if hasattr(llm_response, "tool_calls") and llm_response.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.get("id", f"call_{int(time.time())}"),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("args", {})),
                    },
                }
                for tc in llm_response.tool_calls
            ]
            finish_reason = "tool_calls"
        else:
            finish_reason = "stop"
    else:
        message = {"role": "assistant", "content": str(llm_response)}
        finish_reason = "stop"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        # Token counts are injected by the Praxis token_usage_headers filter as
        # Praxis-Token-* response headers. The worker does not track them itself.
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions", tags=["chat"])
def chat_completions(chat_request: ChatRequest, request: Request):
    logger.info(
        f"POST /v1/chat/completions model={chat_request.model} "
        f"msgs={len(chat_request.messages)} tools={len(chat_request.tools or [])}"
    )
    skillberry_context = _extract_skillberry_context(request)
    agent_config = _extract_agent_config(request)
    chat_messages = [m.to_langchain() for m in chat_request.messages]
    agent_tools = _convert_tools(chat_request.tools) if chat_request.tools else None

    result = execute_agentic_graph(
        chat_messages=chat_messages,
        skillberry_context=skillberry_context,
        model=chat_request.model,
        temperature=chat_request.temperature,
        agent_tools=agent_tools,
        agent_config=agent_config,
    )

    return _build_response(result, model=chat_request.model)


@app.get("/trajectory", tags=["session"])
def get_trajectory(request: Request):
    skillberry_context = _extract_skillberry_context(request)
    return {"trajectory": trajectory(skillberry_context)}


@app.post("/disconnect", tags=["session"])
def api_disconnect(request: Request):
    skillberry_context = _extract_skillberry_context(request)
    try:
        disconnect(skillberry_context)
    except Exception as e:
        logger.warning(f"disconnect error (non-fatal): {e}")
    return {"status": "disconnected"}


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def start():
    uvicorn.run(
        "worker.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("WORKER_PORT", "8001")),
        log_level=log_level.lower(),
        reload=True,
    )


if __name__ == "__main__":
    start()
