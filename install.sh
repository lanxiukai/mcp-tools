#!/usr/bin/env bash
# ============================================================================
# MCP Tools — One-Click Install Script
# ============================================================================
# Usage:
#   bash install.sh                 # Install all three MCP runtimes
#   bash install.sh --asr-only      # Install mcp-local-asr only
#   bash install.sh --ocr-only      # Install the unified OCR runtime
#   bash install.sh --cpu-only      # Install shared mcp-local only
#   bash install.sh --browser-only  # Compatibility alias for --cpu-only
#
# Prerequisites:
#   - Linux (Ubuntu 22.04+ recommended) or WSL2
#   - NVIDIA GPU + CUDA 12.4+ (for ASR / OCR; mcp-local is CPU only)
#   - conda / mamba installed

# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "\n${CYAN}${BOLD}===[ $* ]===${NC}"; }

# --------------- argument parsing ---------------
INSTALL_ASR=true; INSTALL_OCR=true; INSTALL_CPU=true
for arg in "$@"; do
    case "$arg" in
        --asr-only)     INSTALL_ASR=true;     INSTALL_OCR=false;    INSTALL_CPU=false ;;
        --ocr-only)     INSTALL_ASR=false;    INSTALL_OCR=true;     INSTALL_CPU=false ;;
        --cpu-only|--browser-only)
                        INSTALL_ASR=false;    INSTALL_OCR=false;    INSTALL_CPU=true ;;
        -h|--help)
                        printf '%s\n' \
                            'Usage: bash install.sh [--asr-only|--ocr-only|--cpu-only|--browser-only]' \
                            '' \
                            'Options:' \
                            '  --asr-only      Provision mcp-local-asr (Qwen3-ASR and ASR Pipeline).' \
                            '  --ocr-only      Provision the unified mcp-local-ocr runtime.' \
                            '  --cpu-only      Provision mcp-local (Browser Fetch, Format Conversion, Qwen Vision).' \
                            '  --browser-only  Compatibility alias for --cpu-only.'
                        exit 0 ;;
        *)              error "Unknown option: $arg"; exit 1 ;;
    esac
done

# --------------- prerequisite checks ---------------
step "Checking prerequisites"

# conda/mamba
if command -v mamba &>/dev/null; then
    CONDA_CMD="mamba"
elif command -v conda &>/dev/null; then
    CONDA_CMD="conda"
else
    error "conda or mamba not found. Please install Miniconda first: https://docs.conda.io/en/latest/miniconda.html"
    exit 1
fi
info "Package manager: $CONDA_CMD"

environment_exists() {
    local environment_name="$1"
    "$CONDA_CMD" env list | awk -v environment_name="$environment_name" '
        $1 == environment_name { found = 1; exit }
        END { exit !found }
    '
}

ensure_environment() {
    local environment_name="$1"
    if environment_exists "$environment_name"; then
        info "conda environment '$environment_name' already exists; keeping it and repairing dependencies"
    else
        info "Creating conda environment: $environment_name"
        "$CONDA_CMD" create -n "$environment_name" python=3.12 -y
    fi
}

# CUDA
if ! $INSTALL_ASR && ! $INSTALL_OCR; then
    info "GPU runtimes not selected; provisioning shared CPU runtime only"
elif command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null; then
    info "CUDA GPU detected ($(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1))"
elif [[ -e /dev/dxg ]]; then
    info "WSL GPU interface detected"
else
    warn "CUDA GPU was not detected. ASR and OCR require GPU; runtime verification may fail"
fi

# --------------- ASR installation ---------------
if $INSTALL_ASR; then
    step "Installing Qwen3-ASR (Speech-to-Text)"

    ENV_NAME="mcp-local-asr"
    ensure_environment "$ENV_NAME"

    CONDA_PYTHON="$($CONDA_CMD run -n "$ENV_NAME" which python)"
    info "Python: $CONDA_PYTHON"

    info "Installing PyTorch + CUDA..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" pip install \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu130

    info "Installing ASR dependencies..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" pip install \
        "transformers==4.57.6" \
        "qwen-asr" \
        fastapi "uvicorn[standard]" click annotated-doc python-multipart pydantic \
        "mcp>=1.0.0" soundfile ffmpeg-python pyannote.audio

    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" python -c \
        "import annotated_doc, click, fastapi, uvicorn" \
        || die "ASR runtime dependency verification failed"

    info "Installing ffmpeg..."
    $CONDA_CMD install -n "$ENV_NAME" ffmpeg -c conda-forge -y 2>/dev/null || \
        warn "ffmpeg install failed, please install manually: sudo apt install ffmpeg"

    # Pre-download snapshots to the canonical resolver paths. If either
    # snapshot is missing or incomplete, runtime resolution falls back to Hub.
    info "Pre-downloading Qwen3-ASR and ForcedAligner model snapshots..."
    PYTHONNOUSERSITE=1 PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        $CONDA_CMD run -n "$ENV_NAME" python -c "
from huggingface_hub import snapshot_download
from pathlib import Path
from asr.model_source import (
    FORCED_ALIGNER_HUB_MODEL_ID,
    FORCED_ALIGNER_LOCAL_MODEL_RELATIVE_PATH,
    HUB_MODEL_ID,
    LOCAL_MODEL_RELATIVE_PATH,
)

repository_root = Path('${REPO_DIR}')
for model_id, relative_path in (
    (HUB_MODEL_ID, LOCAL_MODEL_RELATIVE_PATH),
    (FORCED_ALIGNER_HUB_MODEL_ID, FORCED_ALIGNER_LOCAL_MODEL_RELATIVE_PATH),
):
    target = repository_root / relative_path
    target.mkdir(parents=True, exist_ok=True)
    print(f'Downloading {model_id} snapshot...')
    snapshot_download(model_id, local_dir=str(target))
print('Done!')
" 2>&1 | tail -3 || warn "Model pre-download failed. Missing or incomplete repository-local snapshots fall back to Hugging Face Hub at runtime."

    info "ASR installation complete!"
    echo "  Python: $CONDA_PYTHON"
    echo "  MCP server: $REPO_DIR/asr/asr_mcp_server.py"
fi

# --------------- OCR installation ---------------
if $INSTALL_OCR; then
    step "Installing OCR (PaddleOCR-VL-1.6 Document Parsing)"

    ENV_NAME="mcp-local-ocr"
    ensure_environment "$ENV_NAME"

    CONDA_PYTHON="$($CONDA_CMD run -n "$ENV_NAME" which python)"
    info "Python: $CONDA_PYTHON"

    info "Installing PaddlePaddle layout dependencies in the OCR runtime..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" pip install \
        "paddlepaddle-gpu==3.2.1" \
        --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" pip install \
        "paddleocr==3.7.0" "paddlex==3.7.2"

    # Install PyTorch after PaddlePaddle. Both distributions use the
    # site-packages/nvidia namespace; installing CUDA 13 last keeps the
    # shared NCCL/cuDNN files compatible with the resident recognizer.
    info "Installing PyTorch + CUDA..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" pip install \
        "torch==2.11.0" "torchvision==0.26.0" "torchaudio==2.11.0" \
        --index-url https://download.pytorch.org/whl/cu130

    info "Installing OCR dependencies..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" pip install \
        "transformers==5.8.0" \
        fastapi "uvicorn[standard]" click annotated-doc python-multipart pydantic \
        "mcp>=1.0.0" pillow \
        accelerate pymupdf

    info "Verifying the consolidated OCR runtime..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" python -c \
        "import torch; print(f'PyTorch {torch.__version__}, CUDA={torch.cuda.is_available()}')"
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" python -c \
        "import paddle, paddlex; print(f'PaddlePaddle {paddle.__version__}, CUDA={paddle.device.is_compiled_with_cuda()}, PaddleX {paddlex.__version__}')"

    info "Caching PP-DocLayoutV3 for page segmentation..."
    PYTHONNOUSERSITE=1 $CONDA_CMD run -n "$ENV_NAME" python -c \
        "from paddlex import create_predictor; create_predictor('PP-DocLayoutV3', device='cpu')" \
        >/dev/null || warn "PP-DocLayoutV3 cache warm-up failed; retry on first OCR request."

    info "OCR runtime ready. The launcher prefers the local PaddleOCR-VL-1.6 snapshot under ~/project/hf-models."
    if [[ ! -d "$HOME/project/hf-models/models/safetensors/PaddlePaddle/PaddleOCR-VL-1.6" ]]; then
        warn "Local PaddleOCR-VL-1.6 snapshot not found; the backend will fall back to Hugging Face on first start."
    fi

    info "OCR installation complete!"
    echo "  Python: $CONDA_PYTHON"
    echo "  MCP server: $REPO_DIR/ocr/ocr_mcp_server.py"
fi

# --------------- shared CPU runtime installation ---------------
if $INSTALL_CPU; then
    step "Installing shared CPU runtime (Browser Fetch, Format Conversion, Qwen Vision)"

    ENV_NAME="mcp-local"
    ensure_environment "$ENV_NAME"

    CONDA_PYTHON="$($CONDA_CMD run -n "$ENV_NAME" which python)"
    info "Python: $CONDA_PYTHON"

    info "Installing shared CPU runtime dependencies..."
    $CONDA_CMD install -n "$ENV_NAME" -c conda-forge \
        weasyprint markdown-it-py pymupdf -y

    $CONDA_CMD run -n "$ENV_NAME" pip install \
        "mcp>=1.0.0" \
        nodriver \
        playwright \
        trafilatura \
        markdownify

    info "Installing Playwright Chromium binary (~280 MB)..."
    $CONDA_CMD run -n "$ENV_NAME" playwright install chromium

    if ! command -v npm &>/dev/null; then
        error "npm is required for Format Conversion's pinned MathJax runtime"
        exit 1
    fi
    info "Installing pinned Format Conversion MathJax runtime (lifecycle scripts disabled)..."
    npm ci --prefix "$REPO_DIR/format-conversion" --ignore-scripts --no-audit --no-fund

    info "Installing Playwright Chromium system libs (may prompt for sudo)..."
    $CONDA_CMD run -n "$ENV_NAME" playwright install-deps chromium 2>/dev/null || \
        warn "playwright install-deps failed (likely no sudo). If browser launches fail later, install libs manually: see browser-fetch/README.md"

    if command -v apt-get &>/dev/null; then
        info "Installing Noto CJK and emoji fonts for Format Conversion..."
        sudo apt-get install -y fonts-noto-cjk fonts-noto-color-emoji || \
            warn "Noto font install failed. CJK or emoji output may contain missing glyphs."
    else
        warn "apt-get not found. Install a fontconfig-visible Noto CJK font manually."
    fi

    if command -v google-chrome &>/dev/null; then
        info "System Chrome: $(google-chrome --version 2>/dev/null)"
    elif command -v chromium-browser &>/dev/null; then
        info "System Chromium: $(chromium-browser --version 2>/dev/null)"
    else
        warn "No system Chrome/Chromium detected. nodriver requires one. Install via: sudo apt install google-chrome-stable"
    fi

    info "Shared CPU runtime installation complete!"
    echo "  Python: $CONDA_PYTHON"
    echo "  Browser Fetch:      $REPO_DIR/browser-fetch/browser_fetch_mcp_server.py"
    echo "  Format Conversion:  $REPO_DIR/format-conversion/format_mcp_server.py"
fi

# --------------- configuration output ---------------
step "Next step: Register with OpenCode"

echo ""
echo -e "${BOLD}Add the following to the \"mcp\" block of your opencode.jsonc:${NC}"
echo ""

if $INSTALL_ASR; then
    echo -e "${CYAN}  # === ASR (Speech-to-Text) ===${NC}"
    echo '  "asr": {'
    echo '    "type": "local",'
    echo '    "command": ["<YOUR-PYTHON>", "'$REPO_DIR'/asr/asr_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 1800000'
    echo '  },'
    echo ""
fi

if $INSTALL_OCR; then
    echo -e "${CYAN}  # === OCR (Document Parsing) ===${NC}"
    echo '  "ocr": {'
    echo '    "type": "local",'
    echo '    "command": ["<YOUR-PYTHON>", "'$REPO_DIR'/ocr/ocr_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 1800000'
    echo '  },'
    echo ""
fi

if $INSTALL_CPU; then
    echo -e "${CYAN}  # === Browser Fetch (Anti-bot Web Page Fetching) ===${NC}"
    echo '  "browser_fetch": {'
    echo '    "type": "local",'
    echo '    "command": ["<YOUR-PYTHON>", "'$REPO_DIR'/browser-fetch/browser_fetch_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 120000'
    echo '  },'
    echo ""
    echo -e "${CYAN}  # === Format Conversion ===${NC}"
    echo '  "format_conversion": {'
    echo '    "type": "local",'
    echo '    "command": ["<YOUR-PYTHON>", "'$REPO_DIR'/format-conversion/format_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 60000'
    echo '  },'
    echo ""
fi

echo -e "${YELLOW}Note:${NC} Replace <YOUR-PYTHON> with the Python path from your conda environment"
echo "  ASR:     $($CONDA_CMD run -n mcp-local-asr which python 2>/dev/null || echo '<mcp-local-asr>/bin/python')"
echo "  OCR:     $($CONDA_CMD run -n mcp-local-ocr which python 2>/dev/null || echo '<mcp-local-ocr>/bin/python')"
echo "  CPU:     $($CONDA_CMD run -n mcp-local which python 2>/dev/null || echo '<mcp-local>/bin/python')"

if $INSTALL_CPU; then
    echo ""
    echo "Google Scholar and academic-research also use mcp-local only after their separately installed and registered implementations are available; this repository does not install them."
fi

echo ""
echo -e "${BOLD}Installation complete!${NC} Restart OpenCode to start using MCP tools."
echo "See README.md for details."
