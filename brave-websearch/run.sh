#!/usr/bin/env bash
# =============================================================================
# brave-websearch MCP Server Launcher
# =============================================================================
# Launches the official @brave/brave-search-mcp-server as an MCP stdio server
# for use with OpenCode, Claude Desktop, or any MCP-compatible client.
#
# Provides 7 search tools:
#   brave_web_search      - General web search
#   brave_local_search    - Local businesses & places
#   brave_news_search     - News articles
#   brave_video_search    - Video content
#   brave_image_search    - Image search
#   brave_llm_context     - LLM-optimized web content retrieval
#   brave_summarizer      - AI-generated search result summaries
#
# Usage:
#   Just point your MCP client config to this script:
#
#     "brave_websearch": {
#       "type": "local",
#       "command": ["/path/to/mcp-tools/brave-websearch/run.sh"],
#       "env": { "BRAVE_API_KEY": "BSA..." },
#       "timeout": 30000
#     }
#
#   Or run directly for debugging:
#     BRAVE_API_KEY=BSA... ./run.sh
#     echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | BRAVE_API_KEY=BSA... ./run.sh
#
# Prerequisites:
#   - Node.js >= 22 (for native fetch() proxy support)
#   - npx (ships with Node.js)
#   - Brave Search API key: https://brave.com/search/api/
#
# Proxy support (for users behind firewalls):
#   Set standard proxy env vars (https_proxy, http_proxy, etc.) in your MCP
#   client's env block. This script automatically enables Node.js native fetch()
#   proxy support via NODE_USE_ENV_PROXY.
# =============================================================================

set -euo pipefail

# ---- helpers ---------------------------------------------------------------

RED='' GREEN='' YELLOW='' NC=''
if [[ -t 2 ]]; then
    RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'
fi

die() { printf "%bERROR:%b %s\n" "${RED}" "${NC}" "$*" >&2; exit 1; }
warn() { printf "%bWARN:%b %s\n" "${YELLOW}" "${NC}" "$*" >&2; }
info() { printf "%bINFO:%b %s\n" "${GREEN}" "${NC}" "$*" >&2; }

# ---- version ---------------------------------------------------------------

SCRIPT_VERSION="1.0.0"
NODE_MIN_VERSION=22

usage() {
    cat >&2 <<EOF
brave-websearch MCP Server Launcher v${SCRIPT_VERSION}

Usage: $0 [--help] [--version] [mcp-server-options...]

Launches @brave/brave-search-mcp-server in MCP stdio mode.

Environment variables:
  BRAVE_API_KEY              (required) Brave Search API key
  BRAVE_MCP_LOG_LEVEL        Logging level: debug|info|warn|error (default: info)
  BRAVE_MCP_ENABLED_TOOLS    Space-separated tool names to enable (default: all)
  BRAVE_MCP_DISABLED_TOOLS   Space-separated tool names to disable (default: none)

Get an API key: https://brave.com/search/api/
EOF
    exit 0
}

# ---- flags -----------------------------------------------------------------

for arg in "$@"; do
    case "$arg" in
        --help|-h) usage ;;
        --version) echo "v${SCRIPT_VERSION}"; exit 0 ;;
    esac
done

# ---- pre-flight checks -----------------------------------------------------

# 1. Node.js availability & version
if ! command -v node &>/dev/null; then
    die "Node.js is not installed or not in PATH. Requires Node.js >= ${NODE_MIN_VERSION}."
fi

node_version=$(node --version 2>/dev/null | sed 's/^v//' || echo "0")
node_major=$(echo "$node_version" | cut -d. -f1)
if [[ "$node_major" =~ ^[0-9]+$ ]] && [[ "$node_major" -lt "$NODE_MIN_VERSION" ]]; then
    warn "Node.js v${node_version} detected. Recommended: v${NODE_MIN_VERSION}+ for native fetch() proxy support."
    warn "Without v${NODE_MIN_VERSION}+, HTTP_PROXY/HTTPS_PROXY env vars may be ignored."
    warn "If you don't use a proxy, you can ignore this warning."
fi

# 2. npx availability
if ! command -v npx &>/dev/null; then
    die "npx is not in PATH. It ships with Node.js — check your Node.js installation."
fi

# 3. API key
if [[ -z "${BRAVE_API_KEY:-}" ]]; then
    die "BRAVE_API_KEY is not set. Get one at https://brave.com/search/api/"
fi

# ---- runtime ----------------------------------------------------------------

# Enable Node.js native fetch() to respect HTTP_PROXY / HTTPS_PROXY / http_proxy / https_proxy.
# Without this, Node.js fetch() ignores proxy env vars on Linux/macOS.
export NODE_USE_ENV_PROXY=1

# Tell npx to use the env proxy vars when downloading the package (first run only).
# npx downloads @brave/brave-search-mcp-server once and caches it locally;
# subsequent runs use the cache and don't hit the network.
export NODE_USE_ENV_PROXY="${NODE_USE_ENV_PROXY:-1}"

# ---- launch ----------------------------------------------------------------

info "Brave Search MCP server v${SCRIPT_VERSION} — starting..."
info "Node.js $(node --version) with proxy support: ${NODE_USE_ENV_PROXY}"

# Pass user-configurable options to the MCP server
MCP_ARGS=()
[[ -n "${BRAVE_MCP_LOG_LEVEL:-}" ]]   && MCP_ARGS+=(--logging-level "${BRAVE_MCP_LOG_LEVEL}")
[[ -n "${BRAVE_MCP_ENABLED_TOOLS:-}" ]] && MCP_ARGS+=(--enabled-tools ${BRAVE_MCP_ENABLED_TOOLS})
[[ -n "${BRAVE_MCP_DISABLED_TOOLS:-}" ]] && MCP_ARGS+=(--disabled-tools ${BRAVE_MCP_DISABLED_TOOLS})

exec npx --yes @brave/brave-search-mcp-server \
    --transport stdio \
    "${MCP_ARGS[@]}" \
    "$@"
