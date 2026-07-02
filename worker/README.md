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
from its own environment. Praxis owns all of this.

The LLM provider API key is handled separately by the `credential_injection` filter
on llm-egress (`SPAPRAXIS_API_KEY`).

To start Praxis with the full pipeline, see [`scripts/start.sh`](../scripts/start.sh).
