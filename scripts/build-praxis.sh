#!/usr/bin/env bash
# Build the Praxis proxy binary.
#
# Usage:
#   ./scripts/build-praxis.sh           # debug build (default)
#   ./scripts/build-praxis.sh --release # release build
#
# Environment variables:
#   PRAXIS_ROOT   Path to the Praxis repo   default: $HOME/praxis

set -euo pipefail

PRAXIS_ROOT="${PRAXIS_ROOT:-${HOME}/praxis}"
RELEASE=""

if [[ "${1:-}" == "--release" ]]; then
    RELEASE="--release"
fi

if [[ ! -d "${PRAXIS_ROOT}" ]]; then
    echo "ERROR: Praxis repo not found at ${PRAXIS_ROOT}"
    echo "  Clone it with:"
    echo "    git clone https://github.com/praxis-proxy/praxis.git ${PRAXIS_ROOT}"
    exit 1
fi

echo "Building Praxis in ${PRAXIS_ROOT} ${RELEASE:+(release)}..."
cd "${PRAXIS_ROOT}"
cargo update
cargo build --package praxis-proxy ${RELEASE}

if [[ -n "${RELEASE}" ]]; then
    BIN="${PRAXIS_ROOT}/target/release/praxis"
else
    BIN="${PRAXIS_ROOT}/target/debug/praxis"
fi

echo ""
echo "Build complete: ${BIN}"
echo "Run with:  PRAXIS_ROOT=${PRAXIS_ROOT} ./scripts/start.sh"
