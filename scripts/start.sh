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
#   SKILLBERRY_TOOLS_URL              — default: http://127.0.0.1:8000
#   RITS_API_KEY / OPENAI_API_KEY / LITELLM_MASTER_KEY  — provider credentials
#
# Worker environment variables (the worker process reads these itself):
#   LLM_BASE_URL        — default: http://127.0.0.1:8081/v1
#   WORKER_LOG_LEVEL    — default: INFO
#   WORKER_LOG_FILE     — default: /tmp/worker.log
#   WORKER_PORT         — default: 8001

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TMPL="${REPO_ROOT}/pipeline/skillberry-agent-proxy.yaml.tmpl"
CONF="${REPO_ROOT}/pipeline/skillberry-agent-proxy.yaml"

# Set defaults for optional agent config env vars before expanding the template.
# These are only applied if the variable is not already set in the environment.
export ENABLE_THINK_LOGS="${ENABLE_THINK_LOGS:-false}"
export USE_AGENT_TOOLS="${USE_AGENT_TOOLS:-true}"
export USE_AGENT_PROMPTS="${USE_AGENT_PROMPTS:-true}"
export MCP_PROMPTS_POSITION="${MCP_PROMPTS_POSITION:-postfix}"
export REACT_RECURSION_LIMIT="${REACT_RECURSION_LIMIT:-20}"
export SKILLBERRY_TOOLS_URL="${SKILLBERRY_TOOLS_URL:-http://127.0.0.1:8000}"
export SKILL_UUID="${SKILL_UUID:-}"
export SKILL_NAME="${SKILL_NAME:-}"

echo "Expanding pipeline template..."
envsubst < "${TMPL}" > "${CONF}"
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
