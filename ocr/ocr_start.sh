#!/usr/bin/env bash
# Start, stop, and inspect the model-independent local OCR service.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

OCR_PROJECT_DIR="$REPO_DIR/environments/mcp-local-ocr"
DEFAULT_PYTHON="$OCR_PROJECT_DIR/.venv/bin/python"
PYTHON="${OCR_PYTHON:-$DEFAULT_PYTHON}"
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
    echo "[ERROR] Python not found for the repository-local OCR uv environment: $PYTHON" >&2
    echo "Restore it with: uv sync --project \"$OCR_PROJECT_DIR\" --locked" >&2
    echo "Or set OCR_PYTHON=/path/to/.venv/bin/python for diagnostics." >&2
    exit 1
fi

export PATH="$(dirname "$PYTHON"):$PATH"
export PYTHONNOUSERSITE=1

# Keep the WSL driver mapping visible only to this OCR launcher and its child
# processes. The uv-locked CUDA 12.6 wheels locate their bundled runtime
# libraries through package-relative paths.
for runtime_library_dir in /usr/lib/wsl/lib; do
    if [[ -d "$runtime_library_dir" && ":${LD_LIBRARY_PATH:-}:" != *":$runtime_library_dir:"* ]]; then
        LD_LIBRARY_PATH="$runtime_library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
done
export LD_LIBRARY_PATH

HOST="${OCR_HOST:-127.0.0.1}"
PORT="${OCR_PORT:-8002}"
MODEL_NAME="${OCR_MODEL_NAME:-PaddlePaddle/PaddleOCR-VL-1.6}"
IDLE_TIMEOUT="${OCR_IDLE_TIMEOUT:-30}"
PID_FILE="${OCR_PID_FILE:-/tmp/ocr-server.pid}"
LOG_FILE="${OCR_LOG_FILE:-/tmp/ocr-server.log}"
LAYOUT_PYTHON="${OCR_LAYOUT_PYTHON:-}"
LAYOUT_MODEL="${OCR_LAYOUT_MODEL:-}"
LAYOUT_PYTHON="${LAYOUT_PYTHON:-$PYTHON}"

export OCR_HOST="$HOST"
export OCR_PORT="$PORT"
export OCR_MODEL_NAME="$MODEL_NAME"
export OCR_IDLE_TIMEOUT="$IDLE_TIMEOUT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(<"$PID_FILE")
        if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi
    return 1
}

do_check() {
    local all_ok=true
    echo "=== OCR Dependency Check ==="
    "$PYTHON" --version
    local imports=(
        "torch"
        "transformers"
        "transformers:AutoModelForImageTextToText"
        "fastapi"
        "uvicorn"
        "PIL"
        "pydantic"
        "mcp"
        "fitz"
    )
    local imp mod symbol
    for imp in "${imports[@]}"; do
        if [[ "$imp" == *:* ]]; then
            mod="${imp%%:*}"
            symbol="${imp##*:}"
            if "$PYTHON" -c "from ${mod} import ${symbol}" 2>/dev/null; then
                echo "✓ from ${mod} import ${symbol}"
            else
                echo "✗ from ${mod} import ${symbol}"
                all_ok=false
            fi
        elif "$PYTHON" -c "import ${imp}" 2>/dev/null; then
            echo "✓ import ${imp}"
        else
            echo "✗ import ${imp}"
            all_ok=false
        fi
    done
    if "$PYTHON" -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
        echo "✓ CUDA: $($PYTHON -c 'import torch; print(torch.cuda.get_device_name(0))')"
    else
        echo "✗ CUDA unavailable"
        all_ok=false
    fi
    local recognition_resolution recognition_mode recognition_path
    if recognition_resolution="$(
        cd "$REPO_DIR"
        OCR_MODEL_NAME="$MODEL_NAME" "$PYTHON" -c '
import os
from ocr.model_paths import resolve_model_path
path, local_only = resolve_model_path(os.environ["OCR_MODEL_NAME"])
print("{}\t{}".format("local" if local_only else "hub", path))
'
    )"; then
        recognition_mode="${recognition_resolution%%$'\t'*}"
        recognition_path="${recognition_resolution#*$'\t'}"
        if [[ "$recognition_mode" == "local" ]]; then
            echo "✓ local recognizer: $recognition_path"
        else
            echo "! recognizer uses the Hugging Face cache/Hub: $recognition_path"
        fi
    else
        echo "✗ recognizer configuration is invalid"
        all_ok=false
    fi
    if [[ "${OCR_USE_LAYOUT:-1}" == "0" ]]; then
        echo "! layout detection disabled; dense pages use bounded tiles"
    elif [[ ! -x "$LAYOUT_PYTHON" ]]; then
        echo "✗ layout Python not found: $LAYOUT_PYTHON"
        all_ok=false
    else
        if PYTHONNOUSERSITE=1 "$LAYOUT_PYTHON" -c "import paddle, paddlex" 2>/dev/null; then
            echo "✓ Paddle layout runtime: $LAYOUT_PYTHON"
        else
            echo "✗ Paddle layout imports failed: $LAYOUT_PYTHON"
            all_ok=false
        fi
        local resolved_layout
        if resolved_layout="$(
            cd "$REPO_DIR"
            "$PYTHON" -c '
from ocr.model_paths import resolve_layout_model_path
path = resolve_layout_model_path()
print(path or "")
'
        )"; then
            if [[ -n "$resolved_layout" ]]; then
                echo "✓ local layout model: $resolved_layout"
            else
                echo "! layout model uses the PaddleX managed cache/download"
            fi
        else
            echo "✗ layout model configuration is invalid"
            all_ok=false
        fi
    fi
    $all_ok
}

do_stop() {
    local pid
    if ! pid=$(get_pid); then
        warn "No running OCR server"
        return 0
    fi
    info "Stopping OCR server (PID: $pid)..."
    kill "$pid" 2>/dev/null || true
    local attempt
    for attempt in 1 2 3 4 5; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        warn "Server did not exit gracefully; sending SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    info "Server stopped"
}

do_status() {
    local pid
    if pid=$(get_pid); then
        echo "[RUNNING] PID: $pid"
        curl -fsS "http://$HOST:$PORT/health" | "$PYTHON" -m json.tool || true
    else
        echo "[STOPPED]"
        return 1
    fi
}

run_server() {
    cd "$REPO_DIR"
    PYTHONUNBUFFERED=1 exec "$PYTHON" -m ocr.ocr_server \
        --model "$MODEL_NAME" \
        --host "$HOST" \
        --port "$PORT"
}

do_start() {
    local foreground="${1:-false}"
    if [[ "$foreground" == "true" ]]; then
        if get_pid >/dev/null; then
            error "Server is already running (PID: $(get_pid))"
            exit 1
        fi
        info "Starting OCR API Server"
        info "  Model: $MODEL_NAME"
        info "  Host: $HOST:$PORT"
        info "  Idle timeout: $IDLE_TIMEOUT seconds"
        info "  Layout: ${OCR_USE_LAYOUT:-1} (${LAYOUT_MODEL:-automatic cache resolution})"
        info "  Job root: ${OCR_JOB_ROOT:-server XDG fallback}"
        run_server
    fi

    mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")"
    local start_lock_fd
    exec {start_lock_fd}>"${PID_FILE}.start.lock"
    if ! flock -w 95 "$start_lock_fd"; then
        error "Timed out waiting for another OCR startup attempt"
        return 1
    fi
    if get_pid >/dev/null; then
        info "Server is already running (PID: $(get_pid))"
        return 0
    fi
    info "Starting OCR API Server"
    info "  Model: $MODEL_NAME"
    info "  Host: $HOST:$PORT"
    info "  Idle timeout: $IDLE_TIMEOUT seconds"
    info "  Layout: ${OCR_USE_LAYOUT:-1} (${LAYOUT_MODEL:-automatic cache resolution})"
    info "  Job root: ${OCR_JOB_ROOT:-server XDG fallback}"
    cd "$REPO_DIR"
    PYTHONUNBUFFERED=1 nohup "$PYTHON" -m ocr.ocr_server \
        --model "$MODEL_NAME" \
        --host "$HOST" \
        --port "$PORT" \
        >"$LOG_FILE" 2>&1 &
    echo "$!" >"$PID_FILE"

    local elapsed=0
    while ((elapsed < 90)); do
        sleep 2
        ((elapsed += 2))
        if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
            info "Server started successfully (${elapsed}s); log: $LOG_FILE"
            return 0
        fi
        if ! get_pid >/dev/null; then
            error "Server exited during startup; see $LOG_FILE"
            tail -20 "$LOG_FILE" >&2 || true
            return 1
        fi
    done
    error "Server startup timed out after 90s; see $LOG_FILE"
    return 1
}

case "${1:-start}" in
    start)
        shift
        if [[ "${1:-}" == "--fg" ]]; then
            do_start true
        else
            do_start false
        fi
        ;;
    --fg) do_start true ;;
    stop) do_stop ;;
    status) do_status ;;
    check) do_check ;;
    *) echo "Usage: $0 [start [--fg]|--fg|stop|status|check]" >&2; exit 1 ;;
esac
