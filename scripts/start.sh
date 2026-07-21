#!/usr/bin/env bash
# Start the Skillberry Agent Proxy (Praxis).
#
# Usage:
#   ./scripts/start.sh [--config-only]
#
# --config-only  Expand the template and exit without starting Praxis.
#                Useful for inspecting the generated config before launch.
#
# Required environment variables (Praxis owns these):
#   SKILL_UUID / SKILL_NAME           — which skill to activate
#   ENABLE_THINK_LOGS                 — default: false
#   USE_AGENT_TOOLS                   — default: true
#   USE_AGENT_PROMPTS                 — default: true
#   MCP_PROMPTS_POSITION              — default: postfix
#   REACT_RECURSION_LIMIT             — default: 20
#   SKILLBERRY_STORE_URL              — default: http://127.0.0.1:8000
#   SPAPRAXIS_MODEL                   — model name injected into every LLM request
#   SPAPRAXIS_TEMPERATURE             — temperature injected into every LLM request
#   SPAPRAXIS_API_KEY                 — provider API key injected into every outbound
#                                       LLM request; client key is never forwarded
#   SPAPRAXIS_LITELLMPROXY            — LiteLLM proxy endpoint (host:port)
#
# Worker environment variables (the worker process reads these itself):
#   LLM_BASE_URL        — default: http://127.0.0.1:8081/v1
#   WORKER_LOG_LEVEL    — default: INFO
#   WORKER_LOG_FILE     — default: /tmp/worker.log
#   WORKER_PORT         — default: 7010

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TMPL="${REPO_ROOT}/pipeline/skillberry-agent-proxy.yaml.tmpl"
CONF="${REPO_ROOT}/pipeline/skillberry-agent-proxy.yaml"

# Set defaults for optional agent config env vars before expanding the template.
# These are only applied if the variable is not already set in the environment.
export ENABLE_THINK_LOGS="${ENABLE_THINK_LOGS:-false}"
export USE_AGENT_TOOLS="${USE_AGENT_TOOLS:-false}"
export USE_AGENT_PROMPTS="${USE_AGENT_PROMPTS:-true}"
export MCP_PROMPTS_POSITION="${MCP_PROMPTS_POSITION:-postfix}"
export REACT_RECURSION_LIMIT="${REACT_RECURSION_LIMIT:-20}"
export SKILLBERRY_STORE_URL="${SKILLBERRY_STORE_URL:-http://127.0.0.1:8000}"
export SKILL_UUID="${SKILL_UUID:-}"
export SKILL_NAME="${SKILL_NAME:-}"

# Validate required LLM policy vars — fail early with a clear message.
if [[ -z "${SPAPRAXIS_MODEL:-}" ]]; then
    echo "ERROR: SPAPRAXIS_MODEL is not set."
    echo "  Set it to the model name Praxis should use for all LLM calls."
    exit 1
fi
if [[ -z "${SPAPRAXIS_TEMPERATURE:-}" ]]; then
    echo "ERROR: SPAPRAXIS_TEMPERATURE is not set."
    echo "  Set it to the temperature Praxis should use for all LLM calls (e.g. 0.0)."
    exit 1
fi
if [[ -z "${SPAPRAXIS_API_KEY:-}" ]]; then
    echo "ERROR: SPAPRAXIS_API_KEY is not set."
    echo "  Set it to the provider API key for outbound LLM requests."
    exit 1
fi

# Derive the upstream hostname and detect TLS from SPAPRAXIS_LITELLMPROXY (host:port).
export SPAPRAXIS_LITELLMPROXY_HOST="${SPAPRAXIS_LITELLMPROXY%%:*}"
LITELLM_PORT="${SPAPRAXIS_LITELLMPROXY##*:}"

echo "Expanding pipeline template..."
envsubst < "${TMPL}" > "${CONF}"

# Strip TLS and Host-rewrite from llm-egress when upstream is plain HTTP.
if [[ "${LITELLM_PORT}" != "443" ]]; then
    sed -i '/# __TLS_BEGIN__/,/# __TLS_END__/d' "${CONF}"
    echo "Plain HTTP upstream (port ${LITELLM_PORT}) — TLS disabled on llm-egress."
else
    echo "HTTPS upstream — TLS enabled on llm-egress."
fi

echo "Generated: ${CONF}"

if [[ "${1:-}" == "--config-only" ]]; then
    echo "Config-only mode — exiting without starting Praxis."
    exit 0
fi

# Resolve praxis binary
# Override by setting PRAXIS_ROOT or PRAXIS_BIN in your environment.
PRAXIS_ROOT="${PRAXIS_ROOT:-${HOME}/praxis}"
PRAXIS_BIN="${PRAXIS_BIN:-${PRAXIS_ROOT}/target/debug/praxis}"

if [[ ! -x "${PRAXIS_BIN}" ]]; then
    echo "ERROR: praxis binary not found at ${PRAXIS_BIN}"
    echo "  Build it with:  cargo build --package praxis-proxy  (inside ${PRAXIS_ROOT})"
    echo "  Or set PRAXIS_ROOT or PRAXIS_BIN to override."
    exit 1
fi

echo "Using praxis: ${PRAXIS_BIN}"
exec "${PRAXIS_BIN}" --config "${CONF}"
