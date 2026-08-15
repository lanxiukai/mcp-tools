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
#   - uv for all three repository-local runtimes; system FFmpeg for ASR

# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
ASR_PROJECT_DIR="$REPO_DIR/environments/mcp-local-asr"
ASR_PYTHON="$ASR_PROJECT_DIR/.venv/bin/python"
CPU_PROJECT_DIR="$REPO_DIR/environments/mcp-local"
CPU_PYTHON="$CPU_PROJECT_DIR/.venv/bin/python"
OCR_PROJECT_DIR="$REPO_DIR/environments/mcp-local-ocr"
OCR_PYTHON="$OCR_PROJECT_DIR/.venv/bin/python"

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

# uv provisions all repository-local Python projects.
UV_BIN=""
if $INSTALL_ASR || $INSTALL_OCR || $INSTALL_CPU; then
    if ! UV_BIN="$(command -v uv)"; then
        error "uv not found. Install uv before provisioning the selected runtime: https://docs.astral.sh/uv/"
        exit 1
    fi
    info "uv: $UV_BIN"
fi
if $INSTALL_ASR; then
    if ! command -v ffmpeg &>/dev/null; then
        error "System FFmpeg not found. Install it before provisioning the ASR runtime."
        exit 1
    fi
    info "System FFmpeg: $(command -v ffmpeg)"
fi

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

    info "Restoring the locked repository-local uv project..."
    "$UV_BIN" sync --project "$ASR_PROJECT_DIR" --locked
    if [[ ! -x "$ASR_PYTHON" ]]; then
        error "uv sync completed without creating the expected interpreter: $ASR_PYTHON"
        exit 1
    fi
    info "Python: $ASR_PYTHON"

    info "Verifying ASR runtime dependencies..."
    if ! PYTHONNOUSERSITE=1 "$ASR_PYTHON" -c \
        "import annotated_doc, click, fastapi, ffmpeg, mcp, pyannote.audio, qwen_asr, soundfile, torch, torchaudio, torchcodec, transformers, uvicorn"; then
        error "ASR runtime dependency verification failed"
        exit 1
    fi

    # Pre-download snapshots to the canonical resolver paths. If either
    # snapshot is missing or incomplete, runtime resolution falls back to Hub.
    info "Pre-downloading Qwen3-ASR and ForcedAligner model snapshots..."
    PYTHONNOUSERSITE=1 PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        "$ASR_PYTHON" -c "
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
    echo "  Python: $ASR_PYTHON"
    echo "  MCP server: $REPO_DIR/asr/asr_mcp_server.py"
fi

# --------------- OCR installation ---------------
if $INSTALL_OCR; then
    step "Installing OCR (PaddleOCR-VL-1.6 Document Parsing)"

    info "Restoring the locked repository-local uv project..."
    "$UV_BIN" sync --project "$OCR_PROJECT_DIR" --locked
    if [[ ! -x "$OCR_PYTHON" ]]; then
        error "uv sync completed without creating the expected interpreter: $OCR_PYTHON"
        exit 1
    fi
    info "Python: $OCR_PYTHON"

    info "Verifying the unified CUDA 12.6 OCR runtime..."
    PYTHONNOUSERSITE=1 "$OCR_PYTHON" -c \
        "import torch; assert torch.cuda.is_available(); print(f'PyTorch {torch.__version__}, CUDA={torch.version.cuda}')"
    PYTHONNOUSERSITE=1 "$OCR_PYTHON" -c \
        "import paddle, paddlex; assert paddle.device.is_compiled_with_cuda(); print(f'PaddlePaddle {paddle.__version__}, CUDA={paddle.version.cuda()}, PaddleX {paddlex.__version__}')"

    OCR_LAYOUT_MODEL_DIR=""
    if [[ -d "$HOME/project/hf-models/models/safetensors/PaddlePaddle/PP-DocLayoutV3" ]]; then
        OCR_LAYOUT_MODEL_DIR="$HOME/project/hf-models/models/safetensors/PaddlePaddle/PP-DocLayoutV3"
    fi
    info "Caching PP-DocLayoutV3 for page segmentation..."
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
        MCP_TOOLS_OCR_LAYOUT_MODEL_DIR="$OCR_LAYOUT_MODEL_DIR" \
        PYTHONNOUSERSITE=1 "$OCR_PYTHON" -c \
        "import os; from paddlex import create_predictor; model_dir = os.environ.get('MCP_TOOLS_OCR_LAYOUT_MODEL_DIR'); create_predictor('PP-DocLayoutV3', model_dir=model_dir or None, device='cpu')" \
        >/dev/null || warn "PP-DocLayoutV3 cache warm-up failed; retry on first OCR request."

    info "OCR runtime ready. The launcher prefers the local PaddleOCR-VL-1.6 snapshot under ~/project/hf-models."
    if [[ ! -d "$HOME/project/hf-models/models/safetensors/PaddlePaddle/PaddleOCR-VL-1.6" ]]; then
        warn "Local PaddleOCR-VL-1.6 snapshot not found; the backend will fall back to Hugging Face on first start."
    fi

    info "OCR installation complete!"
    echo "  Python: $OCR_PYTHON"
    echo "  MCP server: $REPO_DIR/ocr/ocr_mcp_server.py"
fi

# --------------- shared CPU runtime installation ---------------
if $INSTALL_CPU; then
    step "Installing shared CPU runtime (Browser Fetch, Format Conversion, Qwen Vision)"

    info "Restoring the locked repository-local uv project..."
    "$UV_BIN" sync --project "$CPU_PROJECT_DIR" --locked
    if [[ ! -x "$CPU_PYTHON" ]]; then
        error "uv sync completed without creating the expected interpreter: $CPU_PYTHON"
        exit 1
    fi
    info "Python: $CPU_PYTHON"

    info "Verifying shared CPU runtime dependencies..."
    if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$CPU_PYTHON" -c \
        "import fitz, markdown_it, markdownify, mcp, nodriver, PIL, playwright, trafilatura, weasyprint; import google_scholar_search_mcp.server; import server"; then
        error "Shared CPU runtime dependency verification failed"
        exit 1
    fi

    info "Installing Playwright Chromium binary (~280 MB)..."
    "$CPU_PROJECT_DIR/.venv/bin/playwright" install chromium

    if ! command -v npm &>/dev/null; then
        error "npm is required for Format Conversion's pinned MathJax runtime"
        exit 1
    fi
    info "Installing pinned Format Conversion MathJax runtime (lifecycle scripts disabled)..."
    npm ci --prefix "$REPO_DIR/format-conversion" --ignore-scripts --no-audit --no-fund

    info "Installing Playwright Chromium system libs (may prompt for sudo)..."
    "$CPU_PROJECT_DIR/.venv/bin/playwright" install-deps chromium 2>/dev/null || \
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
    echo "  Python: $CPU_PYTHON"
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
    echo '    "command": ["'$ASR_PYTHON'", "'$REPO_DIR'/asr/asr_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 1800000'
    echo '  },'
    echo ""
fi

if $INSTALL_OCR; then
    echo -e "${CYAN}  # === OCR (Document Parsing) ===${NC}"
    echo '  "ocr": {'
    echo '    "type": "local",'
    echo '    "command": ["'$OCR_PYTHON'", "'$REPO_DIR'/ocr/ocr_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 1800000'
    echo '  },'
    echo ""
fi

if $INSTALL_CPU; then
    echo -e "${CYAN}  # === Browser Fetch (Anti-bot Web Page Fetching) ===${NC}"
    echo '  "browser_fetch": {'
    echo '    "type": "local",'
    echo '    "command": ["'$CPU_PYTHON'", "'$REPO_DIR'/browser-fetch/browser_fetch_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 120000'
    echo '  },'
    echo ""
    echo -e "${CYAN}  # === Format Conversion ===${NC}"
    echo '  "format_conversion": {'
    echo '    "type": "local",'
    echo '    "command": ["'$CPU_PYTHON'", "'$REPO_DIR'/format-conversion/format_mcp_server.py"],'
    echo '    "enabled": true,'
    echo '    "timeout": 60000'
    echo '  },'
    echo ""
fi

if $INSTALL_ASR; then
    echo -e "${YELLOW}ASR uv interpreter:${NC} $ASR_PYTHON"
fi
if $INSTALL_OCR; then
    echo -e "${YELLOW}OCR uv interpreter:${NC} $OCR_PYTHON"
fi
if $INSTALL_CPU; then
    echo -e "${YELLOW}Shared CPU uv interpreter:${NC} $CPU_PYTHON"
fi

if $INSTALL_CPU; then
    echo ""
    echo "Google Scholar and academic-research are restored in mcp-local; register their MCP commands separately."
fi

echo ""
echo -e "${BOLD}Installation complete!${NC} Restart OpenCode to start using MCP tools."
echo "See README.md for details."
