# Skillberry Worker

Thin agentic shim behind Praxis. Owns the LangGraph ReAct loop and three
session endpoints. No provider credentials, no config UI, no llm-switchboard.

## Quick start

### 1. Install dependencies

From the **repo root** (not from inside `worker/`):

```bash
cd ~/skillberry-praxis-filters
.venv/bin/pip install -e worker/
```

### 2. Set worker env vars

```bash
export LLM_BASE_URL="http://127.0.0.1:8081/v1"   # Praxis llm-egress
export WORKER_LOG_LEVEL="INFO"
```

### 3. Start the worker

From the **repo root**:

```bash
.venv/bin/uvicorn worker.main:app --host 127.0.0.1 --port 7010 --reload
```

> `worker.main` must be importable as a package from the repo root.
> Do not run from inside the `worker/` directory.

### 4. Verify

```bash
curl http://localhost:7010/health
# {"status": "ok"}
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Run agentic ReAct loop; returns OpenAI-compatible response |
| `GET` | `/trajectory` | Tool-call trajectory for this session |
| `POST` | `/disconnect` | Tear down VMCP server and purge trajectory |
| `GET` | `/health` | Liveness probe |

## Configuration injected by Praxis

The worker never reads provider credentials, model names, or LLM parameters
from its own environment. Praxis owns all of this and injects it as
`x-skillberry-*` request headers via the `headers` filter in
[`pipeline/skillberry-agent-proxy.yaml.tmpl`](../pipeline/skillberry-agent-proxy.yaml.tmpl).

### Agent configuration headers

| Header | Praxis env var | Default |
|--------|---------------|---------|
| `x-skillberry-skill-uuid` | `SKILL_UUID` | — |
| `x-skillberry-skill-name` | `SKILL_NAME` | — |
| `x-skillberry-enable-think-logs` | `ENABLE_THINK_LOGS` | `false` |
| `x-skillberry-use-agent-tools` | `USE_AGENT_TOOLS` | `true` |
| `x-skillberry-use-agent-prompts` | `USE_AGENT_PROMPTS` | `true` |
| `x-skillberry-mcp-prompts-position` | `MCP_PROMPTS_POSITION` | `postfix` |
| `x-skillberry-react-recursion-limit` | `REACT_RECURSION_LIMIT` | `20` |
| `x-skillberry-tools-url` | `SKILLBERRY_STORE_URL` | `http://127.0.0.1:8000` |

### LLM policy headers

The client-supplied `model` and `temperature` from the request body are
**ignored**. Praxis injects the authoritative values:

| Header | Praxis env var | Notes |
|--------|---------------|-------|
| `x-skillberry-llm-model` | `SPAPRAXIS_MODEL` | Required |
| `x-skillberry-llm-temperature` | `SPAPRAXIS_TEMPERATURE` | Required |

The provider API key is handled separately by the `credential_injection` filter
on llm-egress (`SPAPRAXIS_API_KEY`) — the worker never sees or forwards it.

To start Praxis with the full pipeline, see [`scripts/start.sh`](../scripts/start.sh).
