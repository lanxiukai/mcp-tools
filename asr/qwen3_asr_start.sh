#!/usr/bin/env bash
#
# Qwen3-ASR API Server Start Script
# ==================================
#
# Usage:
#   ./asr/qwen3_asr_start.sh              # Start in background (default port 8000)
#   ./asr/qwen3_asr_start.sh --fg         # Start in foreground
#   ./asr/qwen3_asr_start.sh stop         # Stop
#   ./asr/qwen3_asr_start.sh status       # Check status
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Python interpreter for the mcp-local-asr conda environment
# Override with: export ASR_PYTHON=/path/to/mcp-local-asr/bin/python
if [[ -z "${ASR_PYTHON:-}" ]]; then
    if command -v conda &>/dev/null; then
        ASR_PYTHON="$(conda run -n mcp-local-asr which python 2>/dev/null)" || true
    fi
fi
PYTHON="${ASR_PYTHON:-}"

# Validate Python interpreter
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
    echo -e "\033[0;31m[ERROR]\033[0m Python not found for mcp-local-asr conda environment."
    echo ""
    echo "Options:"
    echo "  1. Create the conda environment: mamba create -n mcp-local-asr python=3.12 -y"
    echo "  2. Or set the Python path manually: export ASR_PYTHON=/path/to/mcp-local-asr/bin/python"
    exit 1
fi

# Ensure conda environment binaries (ffmpeg etc.) are in PATH
CONDA_BIN="$(dirname "$PYTHON")"
export PATH="$CONDA_BIN:$PATH"
# Keep packages from ~/.local out of this isolated runtime. A user-site
# uvicorn/fastapi can otherwise shadow the conda environment and leave the
# server with an internally inconsistent dependency set.
export PYTHONNOUSERSITE=1
SERVER_SCRIPT="$REPO_DIR/asr/qwen3_asr_server.py"
PID_FILE="/tmp/qwen3-asr-server.pid"
LOG_FILE="/tmp/qwen3-asr-server.log"
PORT="${ASR_PORT:-8000}"
HOST="${ASR_HOST:-localhost}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid; pid=$(<"$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi
    return 1
}

do_stop() {
    local pid
    if pid=$(get_pid); then
        info "Stopping Qwen3-ASR server (PID: $pid)..."
        kill "$pid" 2>/dev/null
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            warn "Force killing..."
            kill -9 "$pid" 2>/dev/null
        fi
        rm -f "$PID_FILE"
        info "Server stopped"
    else
        warn "No running server"
    fi
}

do_status() {
    local pid
    if pid=$(get_pid); then
        echo -e "${GREEN}[RUNNING]${NC} PID: $pid"
        if command -v curl &>/dev/null; then
            curl -s "http://localhost:$PORT/health" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (health check failed)"
        else
            echo "  (curl not found — cannot check health)"
        fi
    else
        echo -e "${RED}[STOPPED]${NC}"
    fi
}

do_start() {
    local foreground="${1:-false}"

    if get_pid > /dev/null; then
        error "Server is already running (PID: $(get_pid))"
        error "Use: $0 stop"
        exit 1
    fi

    # Remove __pycache__ (avoid stale bytecode after script changes)
    find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

    info "Starting Qwen3-ASR API Server..."
    info "  Script: $SERVER_SCRIPT"
    info "  Host:   $HOST:$PORT"
    info "  Log:    $LOG_FILE"

    if [[ "$foreground" == "true" ]]; then
        PYTHONUNBUFFERED=1 exec "$PYTHON" "$SERVER_SCRIPT" --host "$HOST" --port "$PORT"
    else
        PYTHONUNBUFFERED=1 nohup "$PYTHON" "$SERVER_SCRIPT" --host "$HOST" --port "$PORT" \
            > "$LOG_FILE" 2>&1 &
        echo "$!" > "$PID_FILE"

        # Wait for server to be ready
        local max_wait=60 elapsed=0
        while (( elapsed < max_wait )); do
            sleep 1
            (( elapsed += 1 ))
            if curl -s "http://localhost:$PORT/health" > /dev/null 2>&1; then
                info "Server started successfully (${elapsed}s)"
                echo ""
                echo -e "${CYAN}Endpoints:${NC}"
                echo -e "  Health:        ${BOLD}http://$HOST:$PORT/health${NC}"
                echo -e "  Transcription: ${BOLD}POST http://$HOST:$PORT/v1/audio/transcriptions${NC}"
                echo -e "  API Docs:      ${BOLD}http://$HOST:$PORT/docs${NC}"
                echo ""
                echo -e "${CYAN}Test:${NC}"
                echo "  curl -F \"file=@audio.wav\" http://localhost:$PORT/v1/audio/transcriptions"
                return
            fi
        done
        error "Server startup timed out (${max_wait}s)"
        if [[ -f "$LOG_FILE" ]]; then
            echo ""
            error "Last 10 log lines:"
            tail -10 "$LOG_FILE" | while IFS= read -r line; do echo "  $line"; done
        fi
        exit 1
    fi
}

case "${1:-start}" in
    start)
        if (( $# > 1 )); then
            echo "Usage: $0 [start|--fg|stop|status]"
            exit 1
        fi
        do_start
        ;;
    --fg)
        if (( $# != 1 )); then
            echo "Usage: $0 [start|--fg|stop|status]"
            exit 1
        fi
        do_start "true"
        ;;
    stop)
        if (( $# != 1 )); then
            echo "Usage: $0 [start|--fg|stop|status]"
            exit 1
        fi
        do_stop
        ;;
    status)
        if (( $# != 1 )); then
            echo "Usage: $0 [start|--fg|stop|status]"
            exit 1
        fi
        do_status
        ;;
    *)      echo "Usage: $0 [start|--fg|stop|status]" ; exit 1 ;;
esac
