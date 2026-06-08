#!/usr/bin/env bash
# ============================================================================
# MCP Tools — 一键安装脚本
# ============================================================================
# 用法:
#   bash install.sh              # 安装全部 MCP 工具
#   bash install.sh --asr-only   # 仅安装 ASR
#   bash install.sh --ocr-only   # 仅安装 OCR
#
# 前置条件:
#   - Linux (推荐 Ubuntu 22.04+) 或 WSL2
#   - NVIDIA GPU + CUDA 12.4+
#   - conda / mamba 已安装

# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "\n${CYAN}${BOLD}===[ $* ]===${NC}"; }

# --------------- 参数解析 ---------------
INSTALL_ASR=true; INSTALL_OCR=true
for arg in "$@"; do
    case "$arg" in
        --asr-only) INSTALL_OCR=false ;;
        --ocr-only) INSTALL_ASR=false ;;
        -h|--help)  echo "Usage: bash install.sh [--asr-only|--ocr-only]"; exit 0 ;;
        *)          error "Unknown option: $arg"; exit 1 ;;
    esac
done

# --------------- 前置检查 ---------------
step "检查前置条件"

# conda/mamba
if command -v mamba &>/dev/null; then
    CONDA_CMD="mamba"
elif command -v conda &>/dev/null; then
    CONDA_CMD="conda"
else
    error "未找到 conda 或 mamba，请先安装 Miniconda: https://docs.conda.io/en/latest/miniconda.html"
    exit 1
fi
info "包管理器: $CONDA_CMD"

# CUDA
if python3 -c "import torch; assert torch.cuda.is_available()" &>/dev/null; then
    info "CUDA: 可用 ($(python3 -c "import torch; print(torch.cuda.get_device_name(0))" 2>/dev/null))"
else
    warn "CUDA 不可用。ASR 和 OCR 需要 GPU，将以 CPU 模式安装（性能较差）"
fi

# --------------- ASR 安装 ---------------
if $INSTALL_ASR; then
    step "安装 Qwen3-ASR (语音转文字)"

    ENV_NAME="qwen-asr"
    if $CONDA_CMD env list | grep -q "$ENV_NAME"; then
        warn "conda 环境 '$ENV_NAME' 已存在，跳过创建"
    else
        info "创建 conda 环境: $ENV_NAME"
        $CONDA_CMD create -n "$ENV_NAME" python=3.12 -y
    fi

    CONDA_PYTHON="$($CONDA_CMD run -n "$ENV_NAME" which python)"
    info "Python: $CONDA_PYTHON"

    info "安装 PyTorch + CUDA..."
    $CONDA_CMD run -n "$ENV_NAME" pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

    info "安装 ASR 依赖..."
    $CONDA_CMD run -n "$ENV_NAME" pip install \
        "transformers>=4.45.0" \
        "qwen-asr" \
        fastapi "uvicorn[standard]" python-multipart pydantic \
        "mcp>=1.0.0" soundfile ffmpeg-python

    info "安装 ffmpeg..."
    $CONDA_CMD install -n "$ENV_NAME" ffmpeg -c conda-forge -y 2>/dev/null || \
        warn "ffmpeg 安装失败，请手动安装: sudo apt install ffmpeg"

    # 下载模型（首次运行时会自动下载，这里做预热）
    info "预下载 Qwen3-ASR-1.7B 模型（首次使用约 3.4GB）..."
    $CONDA_CMD run -n "$ENV_NAME" python -c "
from transformers import AutoModelForCausalLM
print('Downloading Qwen3-ASR-1.7B...')
AutoModelForCausalLM.from_pretrained('Qwen/Qwen3-ASR-1.7B', trust_remote_code=True)
print('Done!')
" 2>&1 | tail -3 || warn "模型预下载失败，首次转写时 HuggingFace 会自动下载"

    info "ASR 安装完成！"
    echo "  Python: $CONDA_PYTHON"
    echo "  MCP server: $REPO_DIR/asr/asr_mcp_server.py"
fi

# --------------- OCR 安装 ---------------
if $INSTALL_OCR; then
    step "安装 GLM-OCR (文档解析)"

    ENV_NAME="glm-ocr"
    if $CONDA_CMD env list | grep -q "$ENV_NAME"; then
        warn "conda 环境 '$ENV_NAME' 已存在，跳过创建"
    else
        info "创建 conda 环境: $ENV_NAME"
        $CONDA_CMD create -n "$ENV_NAME" python=3.12 -y
    fi

    CONDA_PYTHON="$($CONDA_CMD run -n "$ENV_NAME" which python)"
    info "Python: $CONDA_PYTHON"

    info "安装 PyTorch + CUDA..."
    $CONDA_CMD run -n "$ENV_NAME" pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

    info "安装 OCR 依赖..."
    $CONDA_CMD run -n "$ENV_NAME" pip install \
        "transformers>=5.3.0" \
        fastapi "uvicorn[standard]" python-multipart pydantic \
        "mcp>=1.0.0" pillow httpx \
        accelerate pymupdf

    # 预下载模型
    info "预下载 GLM-OCR 0.9B 模型（首次使用约 2.5GB）..."
    $CONDA_CMD run -n "$ENV_NAME" python -c "
from transformers import GlmOcrForConditionalGeneration
print('Downloading GLM-OCR 0.9B...')
GlmOcrForConditionalGeneration.from_pretrained('zai-org/GLM-OCR', trust_remote_code=True)
print('Done!')
" 2>&1 | tail -3 || warn "模型预下载失败，首次解析时 HuggingFace 会自动下载"

    info "OCR 安装完成！"
    echo "  Python: $CONDA_PYTHON"
    echo "  MCP server: $REPO_DIR/ocr/glm_ocr_mcp_server.py"
fi

# --------------- 配置提示 ---------------
step "下一步：注册到 OpenCode"

echo ""
echo -e "${BOLD}将以下内容添加到你的 opencode.jsonc 的 \"mcp\" 块中:${NC}"
echo ""

if $INSTALL_ASR; then
    echo -e "${CYAN}  # === ASR (语音转文字) ===${NC}"
    echo '  "qwen3_asr": {'
    echo '    "type": "local",'
    echo '    "command": "<YOUR-PYTHON>",'
    echo '    "args": ["'$REPO_DIR'/asr/asr_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 15000'
    echo '  },'
    echo ""
fi

if $INSTALL_OCR; then
    echo -e "${CYAN}  # === OCR (文档解析) ===${NC}"
    echo '  "glm_ocr": {'
    echo '    "type": "local",'
    echo '    "command": "<YOUR-PYTHON>",'
    echo '    "args": ["'$REPO_DIR'/ocr/glm_ocr_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 15000'
    echo '  },'
    echo ""
fi

echo -e "${YELLOW}注意:${NC} 将 <YOUR-PYTHON> 替换为 conda 环境中的 Python 路径"
echo "  ASR:  $($CONDA_CMD run -n qwen-asr which python 2>/dev/null || echo '<qwen-asr-env>/bin/python')"
echo "  OCR: $($CONDA_CMD run -n glm-ocr which python 2>/dev/null || echo '<glm-ocr-env>/bin/python')"

echo ""
echo -e "${BOLD}安装完成！${NC} 重启 OpenCode 即可使用 MCP 工具。"
echo "详请见 README.md"
