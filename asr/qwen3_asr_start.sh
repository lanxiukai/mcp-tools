#!/usr/bin/env bash
#
# Qwen3-ASR API 服务启动脚本
# ========================
#
# 用法:
#   ./asr/qwen3_asr_start.sh              # 后台启动（默认端口 8000）
#   ./asr/qwen3_asr_start.sh --fg         # 前台启动
#   ./asr/qwen3_asr_start.sh stop         # 停止
#   ./asr/qwen3_asr_start.sh status       # 查看状态
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="<YOUR-PATH>"

# Ensure conda environment binaries (ffmpeg etc.) are in PATH
CONDA_BIN="$(dirname "$PYTHON")"
export PATH="$CONDA_BIN:$PATH"
SERVER_SCRIPT="$REPO_DIR/asr/qwen3_asr_server.py"
PID_FILE="/tmp/qwen3-asr-server.pid"
LOG_FILE="/tmp/qwen3-asr-server.log"
PORT="${ASR_PORT:-8000}"
HOST="${ASR_HOST:-0.0.0.0}"

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
        curl -s "http://localhost:$PORT/health" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (health check failed)"
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

    # 删除 __pycache__ 缓存（避免脚本修改后运行过期字节码）
    find "$REPO_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

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
            (( elapsed++ ))
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
    start)  shift; do_start "${1:-false}" ;;
    --fg)   do_start "true" ;;
    stop)   do_stop ;;
    status) do_status ;;
    *)      echo "Usage: $0 [start|--fg|stop|status]" ; exit 1 ;;
esac
