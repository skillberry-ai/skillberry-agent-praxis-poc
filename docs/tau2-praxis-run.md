# Running Tau2 Benchmarks with Praxis as the Skillberry Agent

This guide walks through running [Tau2](https://github.com/sierra-research/tau2-bench) airline benchmarks
using Praxis as the Skillberry agent gateway.

---

## Services Involved

### Skillberry Store
[skillberry-store](https://github.com/skillberry-ai/skillberry-store) is the backend service that manages
skills, their associated MCP tool definitions, and the Virtual MCP server lifecycle. The worker and Praxis
both depend on it being reachable at `SKILLBERRY_STORE_URL` (default: `http://127.0.0.1:8000`).

### Skillberry Benchmarks (forked)
The benchmark is run from a fork of the Tau2 benchmark suite at
[aviweit/skillberry-benchmarks](https://github.com/aviweit/skillberry-benchmarks), branch `skillberry-praxis-poc`.
The fork includes tailored configuration for the Praxis Skillberry proxy agent: the `SPA` (Skillberry
Proxy Agent) flag routes all agent calls through Praxis on port 7000 enabling end-to-end evaluation of the full Praxis pipeline.

### Agentic Worker Service
The worker (`worker/` in this repo) is a thin Python HTTP service that owns the agentic orchestration.
It runs the LangGraph ReAct loop using the
[skillberry-agent-lib](https://github.com/skillberry-ai/skillberry-agent/tree/main/shared/python/skillberry_agent_lib)
shared library — the same library used by the original [skillberry-agent](https://github.com/skillberry-ai/skillberry-agent). The worker contains no provider
credentials and no routing logic; all of that is delegated to Praxis.

---

## Setup

### Clone and build Praxis

Clone Praxis at the pinned commit, then build it using the helper script:

```console
cd ~
git clone https://github.com/praxis-proxy/praxis.git praxis
cd praxis && git checkout 0bc9534e922a8be313331dd9f317356e5097d109
```

```console
cd ~/skillberry-praxis-filters
./scripts/build-praxis.sh
```

---

### Clone the benchmarks

```console
cd ~
git clone https://github.com/aviweit/skillberry-benchmarks.git
cd skillberry-benchmarks
git checkout skillberry-praxis-poc
```

---

### Terminal 1 — Start the Skillberry Worker

```console
cd ~/skillberry-praxis-filters
pip install -e worker/
uvicorn worker.main:app --host 127.0.0.1 --port 7010 --reload
```

---

### Terminal 2 — Start Praxis

Set the required environment variables and start Praxis:

```console
cd ~/skillberry-praxis-filters

export SKILL_NAME="flight_reservation_management"   # or SKILL_UUID=<uuid>
export OPENAI_API_KEY="<your-key>"
export SPAPRAXIS_LITELLMPROXY="<your-litellm-proxy-host:port>"
export USE_AGENT_TOOLS=false

./scripts/start.sh
```

`scripts/start.sh` expands the pipeline template via `envsubst` and starts Praxis with
the two-listener config (client-ingress on port 7000, llm-egress on port 8081).

---

### Terminal 3 — Run the benchmarks

```console
cd ~/skillberry-benchmarks/tau2
SPA=true make all
```

`SPA=true` routes all agent calls through the Praxis Skillberry proxy agent on `http://localhost:7000/v1`.

### Obtain trajectory results

```console
cd ~/skillberry-benchmarks/tau2
make view view-results_tau2
```
