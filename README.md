# skillberry-agent-praxis-poc

> ⚠️ **Work in Progress** — This repository is actively evolving. Features, APIs, and configuration may change at any time.

Deployment layer that turns [Praxis](https://github.com/praxis-proxy/praxis) into the Skillberry agent proxy.

It provides the Praxis pipeline configuration (listeners, filter chains, credential injection) and the Skillberry Worker — a lightweight Python service that runs the LangGraph ReAct loop, including skill resolution, VMCP server lifecycle, and MCP tool fetching.

All LLM routing and provider credentials are owned by Praxis. Agent configuration is injected by Praxis into the worker (as `x-skillberry-*` request headers).

## How It Works

```
Client
  │  POST /v1/chat/completions
  │  (client model/temperature/api-key are all ignored by Praxis)
  ▼
Praxis port 7000 — client-ingress
  │  headers filter injects agent config from Praxis env vars
  │  as x-skillberry-* headers into every worker request
  │  router + load_balancer → Skillberry Worker
  ▼
Skillberry Worker (port 7010)   ← Python / FastAPI — this repo: worker/
  │  Reads agent config from x-skillberry-* headers (set by Praxis)
  │  Resolves skill UUID, creates / retrieves VMCP server, fetches MCP tools
  │  Runs LangGraph ReAct loop
  │  LLM calls loopback via Praxis port 8081
  ▼
Praxis port 8081 — llm-egress (loopback only)
  │  Injects model (SPAPRAXIS_MODEL) and temperature
  │  (SPAPRAXIS_TEMPERATURE), performs routing,
  │  credential_injection (SPAPRAXIS_API_KEY),
  │  token_usage_headers
  │  (client Authorization header overwritten — client key never forwarded)
  ▼
LLM provider (LiteLLM proxy / OpenAI / Ollama / WatsonX)
```

---

## Praxis Filters Used

Configured in [`pipeline/skillberry-agent-proxy.yaml.tmpl`](pipeline/skillberry-agent-proxy.yaml.tmpl).

### client-ingress (port 7000)

| Filter | Description |
|--------|-------------|
| `headers` | Injects agent config and LLM policy (`SPAPRAXIS_MODEL`, `SPAPRAXIS_TEMPERATURE`) from Praxis env vars as `x-skillberry-*` headers into every worker request. Client-supplied model/temperature are ignored. |
| `router` | Routes all traffic to the Skillberry Worker (`127.0.0.1:7010`). |
| `load_balancer` | Forwards to the worker endpoint with configured timeouts (connect 5 s, read 120 s). |

### llm-egress (port 8081, loopback only)

| Filter | Description |
|--------|-------------|
| `model_to_header` | Promotes the `model` field from the JSON request body to `X-Model` header. |
| `router` | Routes all LLM traffic to the configured upstream cluster. |
| `credential_injection` | Injects `SPAPRAXIS_API_KEY` as `Authorization: Bearer` before the request leaves the host. Overwrites any client-supplied key — the client's credentials are never forwarded. |
| `load_balancer` | Forwards to the LLM upstream endpoint (set via `SPAPRAXIS_LITELLMPROXY`). Enables TLS when port is 443. |

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
1. Reads agent config from `x-skillberry-*` headers (injected by Praxis)
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

### 1. Import the demo skill into Skillberry Store

This repo ships a minimal demo skill under [`skills/praxis-demo-hello-world/`](skills/praxis-demo-hello-world/).
It has two tools — `praxis_demo_greet` and `praxis_demo_echo`.

Import the tools first (one `curl` per Python file), then import the skill folder:

```console
# Import the skill folder (SKILL.md + scripts/) as a named skill.
curl -s -X POST "http://localhost:8000/skills/import-anthropic" \
  -F "source_type=folder" \
  -F "folder_path=$(pwd)/skills/praxis-demo-hello-world" \
  -F "snippet_mode=file"
```

Verify the skill was imported:

```console
curl -s http://localhost:8000/skills/praxis-demo-hello-world | python3 -m json.tool
```

### 2. Build Praxis

```console
cd ~
git clone https://github.com/praxis-proxy/praxis.git praxis
cd praxis && git checkout 0bc9534e922a8be313331dd9f317356e5097d109
```

```console
cd ~/praxis && cargo update && cargo build --package praxis-proxy
```

### 3. Start the Skillberry Worker

```console
cd ~/skillberry-praxis-filters
pip install -e worker/
uvicorn worker.main:app --host 127.0.0.1 --port 7010 --reload
```

### 4. Start Praxis

Set required env vars and run:

```console
export SKILL_NAME="praxis-demo-hello-world" # matches the name in skills/praxis-demo-hello-world/SKILL.md
export SPAPRAXIS_MODEL="aws/gpt-oss-120b"   # model name for all LLM calls
export SPAPRAXIS_TEMPERATURE="0.0"          # temperature for all LLM calls
export SPAPRAXIS_API_KEY="<your-key>"       # provider API key
export SPAPRAXIS_LITELLMPROXY="<your-litellm-proxy>"  # host:port
./scripts/start.sh
```

`scripts/start.sh` expands [`pipeline/skillberry-agent-proxy.yaml.tmpl`](pipeline/skillberry-agent-proxy.yaml.tmpl) via `envsubst` and starts Praxis with the generated config.

**Note:** If needed, (re-)build it first (`cd ~/praxis && cargo build --package praxis-proxy`)

### 5. Verify

```console
curl http://localhost:7000/health    # Praxis ingress
curl http://localhost:7010/health    # Worker
```

### 6. Run the client emulator

```console
pip install litellm
export OPENAI_API_BASE=http://localhost:7000/v1
python pipeline/emulate_client.py
```

The emulator sends `"Show me your tools"` to the agent using the fixed session
identifier `praxis-demo-env`. Because the `praxis-demo-hello-world` skill is loaded,
the agent responds with a human-readable list of both tools.

> The client does not need an API key — Praxis injects `SPAPRAXIS_API_KEY`
> into every outbound LLM request and the client-supplied model/temperature
> are overridden by `SPAPRAXIS_MODEL` / `SPAPRAXIS_TEMPERATURE`.
>
> ⚠️ **Workaround:** model and temperature are currently propagated via
> `x-skillberry-llm-*` headers because no generic JSON body-field override
> filter exists in Praxis yet. Track
> [skillberry-ai/skillberry-agent-praxis-poc#13](https://github.com/skillberry-ai/skillberry-agent-praxis-poc/issues/13)
> — once the upstream Praxis filter is ready, this header-based workaround can
> be replaced with a native `llm-egress` body transform.

---

## Repository Layout

```
skills/                                 Skillberry skills (imported into skillberry-store)
  praxis-demo-hello-world/              Praxis demo skill — praxis_demo_greet + praxis_demo_echo
    SKILL.md                            Skill metadata (name: praxis-hello-world)
    scripts/
      praxis_demo_greet.py              praxis_demo_greet(name) tool
      praxis_demo_echo.py               praxis_demo_echo(message) tool
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
| `SPAPRAXIS_MODEL` | — | **Required.** Model name injected into every LLM request. Overrides the client-supplied model. |
| `SPAPRAXIS_TEMPERATURE` | — | **Required.** Temperature injected into every LLM request. Overrides the client-supplied temperature. |
| `SPAPRAXIS_API_KEY` | — | **Required.** Provider API key injected into every outbound LLM request (`Authorization: Bearer`). The client key is never forwarded. |
| `SPAPRAXIS_LITELLMPROXY` | — | LiteLLM proxy endpoint (`host:port`) |

**Worker-owned** (set on the worker process):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BASE_URL` | `http://127.0.0.1:8081/v1` | Praxis llm-egress URL |
| `WORKER_LOG_LEVEL` | `INFO` | Log level |
| `WORKER_LOG_FILE` | `/tmp/worker.log` | Log file path |
| `WORKER_PORT` | `7010` | HTTP listen port |

---

## Guides

| Guide | Description |
|-------|-------------|
| [Running Tau2 Benchmarks with Praxis](docs/tau2-praxis-run.md) | End-to-end walkthrough for running the Tau2 airline benchmark suite with Praxis as the Skillberry agent gateway |
