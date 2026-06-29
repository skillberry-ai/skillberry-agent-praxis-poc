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
export WORKER_PORT="8001"
```

### 3. Start the worker

From the **repo root**:

```bash
.venv/bin/uvicorn worker.main:app --host 0.0.0.0 --port 8001 --reload
```

> `worker.main` must be importable as a package from the repo root.
> Do not run from inside the `worker/` directory.

### 4. Verify

```bash
curl http://localhost:8001/health
# {"status": "ok"}
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Run agentic ReAct loop; returns OpenAI-compatible response |
| `GET` | `/trajectory` | Tool-call trajectory for this session |
| `POST` | `/disconnect` | Tear down VMCP server and purge trajectory |
| `GET` | `/health` | Liveness probe |

## Agent configuration

Agent config (`SKILL_UUID`, `SKILL_NAME`, etc.) is **not** read from env vars
by the worker. Praxis injects it as `x-skillberry-*` request headers via the
`headers` filter in [`pipeline/skillberry-agent-proxy.yaml.tmpl`](../pipeline/skillberry-agent-proxy.yaml.tmpl).

To start Praxis with the full pipeline, see [`scripts/start.sh`](../scripts/start.sh).

## Running without Praxis (dev/test)

Pass the headers directly with curl:

```bash
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-skillberry-context-env-id: dev-env" \
  -H "x-skillberry-skill-name: my-skill" \
  -H "x-skillberry-react-recursion-limit: 5" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "temperature": 0.0}'
```
