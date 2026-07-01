# skillberry-praxis-filters

> ⚠️ **Work in Progress** — This repository is actively evolving. Features, APIs, and configuration may change at any time.

Deployment layer that turns [Praxis](https://github.com/praxis-proxy/praxis) into the Skillberry agent gateway.

It provides the Praxis pipeline configuration (listeners, filter chains, credential injection) and the Skillberry Worker — a lightweight Python service that runs the LangGraph ReAct loop, including skill resolution, VMCP server lifecycle, and MCP tool fetching.

All LLM routing and provider credentials are owned by Praxis. Agent configuration is injected by Praxis into the worker (as `x-skillberry-*` request headers).

## How It Works

```
Client
  │  POST /v1/chat/completions
  ▼
Praxis port 7000 — client-ingress
  │  headers filter injects agent config from Praxis env vars as x-skillberry-* headers
  │  router + load_balancer → Skillberry Worker
  ▼
Skillberry Worker (port 8001)   ← Python / FastAPI — this repo: worker/
  │  Reads agent config from x-skillberry-* headers (set by Praxis)
  │  Resolves skill UUID, creates / retrieves VMCP server, fetches MCP tools
  │  Runs LangGraph ReAct loop
  │  LLM calls loop back via Praxis port 8081
  ▼
Praxis port 8081 — llm-egress (loopback only)
  │  model_to_header, router, credential_injection, token_usage_headers
  ▼
LLM provider (LiteLLM proxy / OpenAI / Ollama / WatsonX)
```

---

## Praxis Filters Used

Configured in [`pipeline/skillberry-agent-proxy.yaml.tmpl`](pipeline/skillberry-agent-proxy.yaml.tmpl).

### client-ingress (port 7000)

| Filter | Description |
|--------|-------------|
| `headers` | Injects agent config from Praxis env vars as `x-skillberry-*` headers into every worker request. |
| `router` | Routes all traffic to the Skillberry Worker (`127.0.0.1:8001`). |
| `load_balancer` | Forwards to the worker endpoint with configured timeouts (connect 5 s, read 120 s). |

### llm-egress (port 8081, loopback only)

| Filter | Description |
|--------|-------------|
| `model_to_header` | Promotes the `model` field from the JSON request body to `X-Model` header. |
| `router` | Routes all LLM traffic to the configured upstream cluster. |
| `credential_injection` | Injects `OPENAI_API_KEY` as `Authorization: Bearer` before the request leaves the host. The worker does not deal with authentication. |
| `load_balancer` | Forwards to the LLM upstream endpoint (set via `SPAPRAXIS_LITELLMPROXY`) or to native provider endpoints. |
| `token_usage_headers` | Injects `Praxis-Token-Input`, `Praxis-Token-Output`, `Praxis-Token-Total` response headers when token usage metadata is present. |

---

## Skillberry Worker (Python)

See [`worker/README.md`](worker/README.md) for quick-start instructions.

The worker is a FastAPI service with four endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/chat/completions` | Run the agentic ReAct loop; return OpenAI-compatible response |
| `GET`  | `/trajectory` | Tool-call trajectory for this session |
| `POST` | `/disconnect` | Tear down the VMCP server and purge the trajectory |
| `GET`  | `/health` | Liveness probe |

Per request it:
1. Reads agent config from `x-skillberry-*` headers (injected by Praxi)
2. Resolves the skill UUID (direct from header, or via `skillberry-store` API lookup by name)
3. Creates or retrieves a VMCP server for the session's `env_id`
4. Fetches MCP tools from the VMCP server over SSE
5. Merges client-supplied tools with MCP tools
6. Injects MCP prompts into the message list
7. Builds the LLM (pointing at Praxis llm-egress port 8081) and runs the LangGraph ReAct loop
8. Returns an OpenAI-compatible `chat.completion` response

---

## Quickstart

❗Ensure that the [skillberry-store](https://github.com/skillberry-ai/skillberry-store) is running.

### 1. Build Praxis

```console
cd ~
git clone https://github.com/praxis-proxy/praxis.git praxis
cd praxis && git checkout 0bc9534e922a8be313331dd9f317356e5097d109
cargo build --package praxis-proxy
```

### 2. Start the Skillberry Worker

```console
cd ~/skillberry-praxis-filters
pip install -e worker/
uvicorn worker.main:app --host 0.0.0.0 --port 8001 --reload
```

### 3. Start Praxis

Set required env vars and run:

```console
export SKILL_NAME="my-skill"          # or SKILL_UUID=<uuid>
export OPENAI_API_KEY="<your-key>"
export SPAPRAXIS_LITELLMPROXY="<your-litellm-proxy>"  # host:port
./scripts/start.sh
```

`scripts/start.sh` expands [`pipeline/skillberry-agent-proxy.yaml.tmpl`](pipeline/skillberry-agent-proxy.yaml.tmpl) via `envsubst` and starts Praxis with the generated config.

**Note:** If needed, (re-)build it first (`./scripts/build-praxis.sh`)

### 4. Verify

```console
curl http://localhost:7000/health    # Praxis ingress
curl http://localhost:8001/health    # Worker
```

### 5. Run the client emulator

```console
pip install litellm
export OPENAI_API_KEY=<your-key>
export OPENAI_API_BASE=http://localhost:7000/v1
python pipeline/emulate_client.py
```

---

## Repository Layout

```
worker/                                 Skillberry Worker (Python / FastAPI)
  main.py                               HTTP endpoints, header parsing
  agentic_graph.py                      Skill resolution, VMCP, MCP tools, ReAct loop
  llm_client.py                         LangChain LLM pointed at Praxis llm-egress
  pyproject.toml
  README.md
pipeline/
  skillberry-agent-proxy.yaml.tmpl      Two-listener Praxis pipeline template
  skillberry-agent-proxy.yaml           Generated at deploy time (gitignored)
  emulate_client.py                     Client emulation script
scripts/
  start.sh                              envsubst + Praxis launcher
  build-praxis.sh                       Builds the Praxis binary
docs/
  tau2-praxis-run.md                    Tau2 benchmark walkthrough
```

## Environment Variables

**Praxis-owned** (set on the Praxis process; defaults applied by `scripts/start.sh`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SKILL_UUID` | — | Direct skill UUID (optional) |
| `SKILL_NAME` | — | Skill name — resolved to UUID via skillberry-store API (optional) |
| `ENABLE_THINK_LOGS` | `false` | Include `<think>` block in response |
| `USE_AGENT_TOOLS` | `true` | Pass client-supplied tools to the ReAct loop |
| `USE_AGENT_PROMPTS` | `true` | Pass system messages to the ReAct loop |
| `MCP_PROMPTS_POSITION` | `postfix` | MCP prompt injection position (`prefix`/`postfix`) |
| `REACT_RECURSION_LIMIT` | `20` | LangGraph ReAct max iterations |
| `SKILLBERRY_STORE_URL` | `http://127.0.0.1:8000` | Skillberry Store Service URL |
| `OPENAI_API_KEY` | — | API key injected by Praxis into every outbound LLM request |
| `SPAPRAXIS_LITELLMPROXY` | — | LiteLLM proxy endpoint (`host:port`) |

**Worker-owned** (set on the worker process):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BASE_URL` | `http://127.0.0.1:8081/v1` | Praxis llm-egress URL |
| `WORKER_LOG_LEVEL` | `INFO` | Log level |
| `WORKER_LOG_FILE` | `/tmp/worker.log` | Log file path |
| `WORKER_PORT` | `8001` | HTTP listen port |

---

## Guides

| Guide | Description |
|-------|-------------|
| [Running Tau2 Benchmarks with Praxis](docs/tau2-praxis-run.md) | End-to-end walkthrough for running the Tau2 airline benchmark suite with Praxis as the Skillberry agent gateway |
