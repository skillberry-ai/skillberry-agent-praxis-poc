"""
Agentic orchestration for the Skillberry Worker.

This module owns execute_agentic_graph(), trajectory(), and disconnect().
It is a clean consumer of skillberry_agent_lib — no config_ui, no
config_structure, no llm/common.py, no llm-switchboard.

Agent configuration (skill_uuid, skill_name, etc.) is passed in via the
agent_config dict extracted from x-skillberry-* headers injected by Praxis.
No os.environ reads for agent configuration.
"""
import asyncio
import concurrent.futures
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from skillberry_agent_lib.data_model.virtual_mcp_server import VirtualMcpServer
from skillberry_agent_lib.langgraph_nodes import create_react_tools_workflow
from skillberry_agent_lib.mcp_interceptor import get_mcp_tools
from skillberry_agent_lib.prompt import build_chat_messages
from skillberry_agent_lib.skill_manager import resolve_skill_uuid
from skillberry_agent_lib.trajectory_manager import trajectory_manager
from skillberry_agent_lib.vmcp_server_manager import (
    get_or_create_vmcp_server,
    remove_vmcp_server,
)

from worker.llm_client import build_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_openai_tool_to_langchain(tool_dict: Dict[str, Any]) -> Any:
    """
    Convert a client-supplied tool in OpenAI format to a LangChain StructuredTool.

    The actual execution of these tools is handled by the caller (the agent),
    not by the LangGraph ToolNode, so the inner function is a dummy placeholder.
    """
    function_def = tool_dict.get("function", {})
    tool_name = function_def.get("name", "unknown_tool")
    tool_description = function_def.get("description", "")
    parameters = function_def.get("parameters", {})

    properties = parameters.get("properties", {})
    required = parameters.get("required", [])

    type_mapping = {
        "string": str,
        "number": float,
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    field_definitions = {}
    for prop_name, prop_schema in properties.items():
        python_type = type_mapping.get(prop_schema.get("type", "string"), str)
        prop_description = prop_schema.get("description", "")
        if prop_name in required:
            field_definitions[prop_name] = (python_type, Field(..., description=prop_description))
        else:
            field_definitions[prop_name] = (python_type, Field(None, description=prop_description))

    ArgsModel = create_model(f"{tool_name}Args", **field_definitions) if field_definitions else create_model(f"{tool_name}Args")

    def dummy_func(**kwargs):
        raise NotImplementedError(f"Tool {tool_name} is executed by the agent, not the worker")

    return StructuredTool(
        name=tool_name,
        description=tool_description,
        func=dummy_func,
        args_schema=ArgsModel,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_agentic_graph(
    chat_messages: list,
    skillberry_context: dict,
    model: str,
    temperature: float,
    agent_tools: Optional[List[Dict[str, Any]]] = None,
    agent_config: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Execute the agentic ReAct workflow.

    Parameters
    ----------
    chat_messages:
        LangChain BaseMessage objects (HumanMessage, AIMessage, SystemMessage …).
    skillberry_context:
        Dict containing at minimum ``env_id``. Extracted from request headers
        by the HTTP layer using ``unflatten_keys``.
    model:
        Model string forwarded verbatim from the client's request body.
        Praxis llm-egress handles routing and rewriting.
    temperature:
        Temperature forwarded verbatim from the client's request body.
    agent_tools:
        Optional list of client-supplied tools in OpenAI JSON schema format.
    agent_config:
        Agent configuration dict extracted from x-skillberry-* headers by
        the HTTP layer. Injected by Praxis from its own env vars at deploy time.
        Keys: skill_uuid, skill_name, enable_think_logs, use_agent_tools,
              use_agent_prompts, mcp_prompts_position, react_recursion_limit,
              tools_url.

    Returns
    -------
    str or AIMessage
        String content when the agent reaches a final answer.
        AIMessage (with ``tool_calls``) when the agent wants the client to
        execute a tool (agent-executable tools pass-through).
    """
    if skillberry_context is None:
        raise ValueError("skillberry_context cannot be None")

    logger.info("=======>>> execute_agentic_graph started <<<=======")

    # --- Read agent config from headers (injected by Praxis) -----------------
    cfg                    = agent_config or {}
    env_skill_uuid         = cfg.get("skill_uuid")
    env_skill_name         = cfg.get("skill_name")
    enable_think_logs      = cfg.get("enable_think_logs", False)
    use_agent_tools        = cfg.get("use_agent_tools", True)
    use_agent_prompts      = cfg.get("use_agent_prompts", True)
    mcp_prompts_position   = cfg.get("mcp_prompts_position", "postfix")
    recursion_limit        = cfg.get("react_recursion_limit", 20)

    # --- Filter tools / prompts per env var ----------------------------------
    if not use_agent_tools:
        agent_tools = None

    if not use_agent_prompts:
        original = len(chat_messages)
        chat_messages = [m for m in chat_messages if not isinstance(m, SystemMessage)]
        logger.info(f"Filtered out {original - len(chat_messages)} system messages (USE_AGENT_PROMPTS=false)")

    # 1. Resolve skill UUID
    resolved_skill_uuid = resolve_skill_uuid(
        skill_uuid=env_skill_uuid,
        skill_name=env_skill_name,
        chat_history=chat_messages,
    )
    logger.info(f"Resolved skill UUID: {resolved_skill_uuid}")

    # 2. Create / retrieve VMCP server
    try:
        vmcp_data = get_or_create_vmcp_server(skillberry_context, skill_uuid=resolved_skill_uuid)
    except ValueError as e:
        return f"Failed to create VMCP server: {e}"

    server = VirtualMcpServer(**vmcp_data)

    # 3. Get MCP tools (with trajectory interceptor)
    tools = get_mcp_tools(port=server.port, server_name=server.name, skillberry_context=skillberry_context)
    logger.info(f"MCP tools retrieved: {len(tools)}")

    # 3.5 Merge client-supplied agent tools
    all_tools = list(tools)
    agent_executable_tool_names: List[str] = []

    if agent_tools:
        for tool_dict in agent_tools:
            try:
                lc_tool = _convert_openai_tool_to_langchain(tool_dict)
                all_tools.append(lc_tool)
                agent_executable_tool_names.append(lc_tool.name)
            except Exception as e:
                name = tool_dict.get("function", {}).get("name", "unknown")
                logger.error(f"Failed to convert tool {name}: {e}")

    # 4. Build LLM and bind tools
    llm = build_llm(model=model, temperature=temperature)
    llm_with_tools = llm.bind_tools(tools=all_tools, tool_choice="auto") if all_tools else llm

    # 5. Compile ReAct workflow
    workflow = create_react_tools_workflow(
        tools=all_tools,
        enable_tool_logging=False,
        normalize_anthropic_to_openai=True,
        agent_executable_tool_names=agent_executable_tool_names,
    )
    graph = workflow.compile()

    # 6. Inject MCP prompts into message list
    llm_messages = build_chat_messages(
        chat_history=chat_messages,
        mcp_port=server.port,
        mcp_server_name=server.name,
        skillberry_context=skillberry_context,
        mcp_prompts_position=mcp_prompts_position,
    )

    # 7. Stream the graph — handle both sync and async call contexts
    async def _stream() -> Any:
        final = None
        async for s in graph.astream(
            {"messages": llm_messages, "llm": llm_with_tools},
            {"recursion_limit": recursion_limit, "max_execution_time": 120},
            stream_mode="values",
        ):
            final = s["messages"][-1]
        return final

    try:
        try:
            asyncio.get_running_loop()
            # Already inside an event loop (FastAPI async context) — run in a thread
            with concurrent.futures.ThreadPoolExecutor() as pool:
                final_message = pool.submit(asyncio.run, _stream()).result()
        except RuntimeError:
            final_message = asyncio.run(_stream())
    except Exception as e:
        logger.error(f"Error streaming react agent: {e}")
        return "I apologize, but I'm experiencing a technical difficulty. Could you please repeat your request?"

    logger.info("=======>>> execute_agentic_graph ended <<<=======")

    # 8. Return result
    if hasattr(final_message, "tool_calls") and final_message.tool_calls:
        # Agent-executable tool — return AIMessage so tool_calls pass through to client
        return final_message

    ai_response = final_message.content if final_message else ""
    if enable_think_logs:
        return f"<think>Skillberry ReAct loop complete.</think>\n{ai_response}"
    return ai_response


def trajectory(skillberry_context: dict) -> list:
    """Return the recorded tool-call / tool-result trajectory for this env_id."""
    try:
        msgs = trajectory_manager.get_trajectory(skillberry_context)
        return [m.model_dump() for m in msgs]
    except ValueError as e:
        logger.error(f"Failed to get trajectory: {e}")
        return []


def disconnect(skillberry_context: dict) -> None:
    """Tear down the VMCP server and purge the trajectory for this env_id."""
    logger.info(f"Disconnecting context: {skillberry_context}")
    try:
        remove_vmcp_server(skillberry_context)
    except Exception as e:
        logger.warning(f"remove_vmcp_server error: {e}")
    try:
        trajectory_manager.remove_trajectory(skillberry_context)
    except Exception as e:
        logger.warning(f"remove_trajectory error: {e}")
