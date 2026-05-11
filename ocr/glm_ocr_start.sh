#!/usr/bin/env bash
#
# GLM-OCR API 服务启动脚本
# ==========================
#
# 用法:
#   ./ocr/glm_ocr_start.sh              # 后台启动（默认端口 8002）
#   ./ocr/glm_ocr_start.sh --fg         # 前台启动
#   ./ocr/glm_ocr_start.sh stop         # 停止
#   ./ocr/glm_ocr_start.sh status       # 查看状态
#   ./ocr/glm_ocr_start.sh check        # 检查依赖
#
# 环境变量:
#   GLM_OCR_PORT             — 服务端口 (默认 8002)
#   GLM_OCR_HOST             — 绑定地址 (默认 0.0.0.0)
#   GLM_OCR_MODEL_NAME       — 模型名 (默认 zai-org/GLM-OCR)
#   GLM_OCR_IDLE_TIMEOUT     — 空闲超时秒数 (默认 30)
#   HF_HUB_CACHE             — HuggingFace 缓存目录 (默认 $REPO_DIR/models/safetensors)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# --------------- 环境配置 ---------------
PYTHON="/home/lanxiukai/mambaforge/envs/glm-ocr/bin/python"

# 检查 Python 是否存在
if [[ ! -x "$PYTHON" ]]; then
    echo -e "\033[0;31m[ERROR]\033[0m Python not found: $PYTHON"
    echo ""
    echo "请先创建 conda 环境并安装依赖:"
    echo "  mamba create -n glm-ocr python=3.12 -y"
    echo "  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130"
    echo '  pip install "transformers>=5.3.0" fastapi "uvicorn[standard]" python-multipart pydantic "mcp>=1.0.0" pillow'
    echo "  pip install accelerate pymupdf"
    exit 1
fi

# 确保 conda 环境中的库在 PATH 中
CONDA_BIN="$(dirname "$PYTHON")"
export PATH="$CONDA_BIN:$PATH"

# HF 缓存指向本地 models/safetensors/ 目录（模型文件缓存在 repo 内）
export HF_HUB_CACHE="${HF_HUB_CACHE:-$REPO_DIR/models/safetensors}"

# --------------- 路径 ---------------
SERVER_SCRIPT="$REPO_DIR/ocr/glm_ocr_server.py"
PID_FILE="/tmp/glm-ocr-server.pid"
LOG_FILE="/tmp/glm-ocr-server.log"
PORT="${GLM_OCR_PORT:-8002}"
HOST="${GLM_OCR_HOST:-0.0.0.0}"
MODEL_NAME="${GLM_OCR_MODEL_NAME:-zai-org/GLM-OCR}"

export OCR_IDLE_TIMEOUT="${GLM_OCR_IDLE_TIMEOUT:-30}"

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

do_check() {
    echo -e "${BOLD}=== GLM-OCR Dependency Check ===${NC}"
    echo ""

    # Check Python
    if "$PYTHON" --version > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Python: $("$PYTHON" --version 2>&1)"
    else
        echo -e "${RED}✗${NC} Python not found: $PYTHON"
        return 1
    fi

    # Check key imports
    # Format: "module_name" or "module_name FROM_IMPORT"
    local imports=(
        "torch"
        "transformers"
        "transformers:GlmOcrForConditionalGeneration"
        "fastapi"
        "uvicorn"
        "PIL"
        "pydantic"
        "mcp"
    )

    local all_ok=true
    for imp in "${imports[@]}"; do
        if [[ "$imp" == *:* ]]; then
            local mod="${imp%%:*}" cls="${imp##*:}"
            if "$PYTHON" -c "from ${mod} import ${cls}" 2>/dev/null; then
                echo -e "${GREEN}✓${NC} from ${mod} import ${cls}"
            else
                echo -e "${RED}✗${NC} from ${mod} import ${cls} FAILED"
                all_ok=false
            fi
        else
            if "$PYTHON" -c "import ${imp}" 2>/dev/null; then
                echo -e "${GREEN}✓${NC} import ${imp}"
            else
                echo -e "${RED}✗${NC} import ${imp} FAILED"
                all_ok=false
            fi
        fi
    done

    # Check pymupdf (optional)
    if "$PYTHON" -c "import fitz" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} import fitz (pymupdf - PDF support)"
    else
        echo -e "${YELLOW}⚠${NC} pymupdf not installed (PDF multi-page limited)"
    fi

    # Check CUDA
    if "$PYTHON" -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
        local gpu_name; gpu_name=$("$PYTHON" -c "
import torch
print(torch.cuda.get_device_name(0))
" 2>/dev/null)
        echo -e "${GREEN}✓${NC} CUDA available: ${gpu_name}"
    else
        echo -e "${YELLOW}⚠${NC} CUDA not available (CPU-only mode)"
    fi

    if $all_ok; then
        echo ""
        info "All core dependencies OK"
        return 0
    else
        echo ""
        error "Some dependencies missing. Install with:"
        echo "  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130"
        echo '  pip install "transformers>=5.3.0" fastapi "uvicorn[standard]" python-multipart pydantic "mcp>=1.0.0" pillow'
        echo "  pip install accelerate"
        echo "  pip install pymupdf  # optional, for PDF multi-page"
        return 1
    fi
}

do_stop() {
    local pid
    if pid=$(get_pid); then
        info "Stopping GLM-OCR server (PID: $pid)..."
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

    info "Starting GLM-OCR API Server..."
    info "  Model:  $MODEL_NAME"
    info "  Script: $SERVER_SCRIPT"
    info "  Host:   $HOST:$PORT"
    info "  Log:    $LOG_FILE"
    info "  HF cache: $HF_HUB_CACHE"

    if [[ "$foreground" == "true" ]]; then
        PYTHONUNBUFFERED=1 exec "$PYTHON" "$SERVER_SCRIPT" \
            --model "$MODEL_NAME" \
            --host "$HOST" \
            --port "$PORT"
    else
        PYTHONUNBUFFERED=1 nohup "$PYTHON" "$SERVER_SCRIPT" \
            --model "$MODEL_NAME" \
            --host "$HOST" \
            --port "$PORT" \
            > "$LOG_FILE" 2>&1 &
        echo "$!" > "$PID_FILE"

        # 等待服务就绪 (首次启动需加载模型，给更多时间)
        local max_wait=90 elapsed=0
        while (( elapsed < max_wait )); do
            sleep 2
            (( elapsed += 2 ))
            if curl -s "http://localhost:$PORT/health" > /dev/null 2>&1; then
                info "Server started successfully (${elapsed}s)"
                echo ""
                echo -e "${CYAN}Endpoints:${NC}"
                echo -e "  Health:  ${BOLD}http://$HOST:$PORT/health${NC}"
                echo -e "  Parse:   ${BOLD}POST http://$HOST:$PORT/v1/ocr/parse${NC}"
                echo -e "  API Docs: ${BOLD}http://$HOST:$PORT/docs${NC}"
                echo ""
                echo -e "${CYAN}Test:${NC}"
                echo "  curl -F \"file=@image.png\" http://localhost:$PORT/v1/ocr/parse"
                return
            fi
            # 每 20s 输出一次进度提示
            if (( elapsed % 20 == 0 )); then
                info "Still waiting for server... (${elapsed}s)"
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
    check)  do_check ;;
    *)      echo "Usage: $0 [start|--fg|stop|status|check]" ; exit 1 ;;
esac
