#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if (($# > 0)); then
    printf 'Usage: bin/mcp-tools doctor\n' >&2
    exit 2
fi

failures=0
warnings=0

section() { printf '\n== %s ==\n' "$1"; }
ok() { printf '[OK] %s\n' "$1"; }
info() { printf '[INFO] %s\n' "$1"; }
optional() { printf '[OPTIONAL] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; ((warnings += 1)); }
fail() { printf '[FAIL] %s\n' "$1"; ((failures += 1)); }

expand_user_path() {
    local value="$1"
    if [[ "$value" == "~" ]]; then
        printf '%s\n' "${HOME:-~}"
    elif [[ "$value" == "~/"* ]]; then
        printf '%s/%s\n' "${HOME:-~}" "${value:2}"
    else
        printf '%s\n' "$value"
    fi
}

absolute_path() {
    local expanded
    expanded="$(expand_user_path "$1")"
    if command -v realpath >/dev/null 2>&1; then
        realpath -m -- "$expanded"
    elif [[ "$expanded" == /* ]]; then
        printf '%s\n' "$expanded"
    else
        printf '%s/%s\n' "$PWD" "$expanded"
    fi
}

configured_or_default_model_root() {
    if [[ -n "${MCP_TOOLS_MODEL_DIR:-}" ]]; then
        absolute_path "$MCP_TOOLS_MODEL_DIR"
    elif [[ -n "${XDG_CACHE_HOME:-}" ]]; then
        absolute_path "$(expand_user_path "$XDG_CACHE_HOME")/mcp-tools/models"
    else
        absolute_path "${HOME:-~}/.cache/mcp-tools/models"
    fi
}

check_local_artifact() {
    local label="$1"
    local path="$2"
    local setup_hint="$3"
    if [[ -f "$path" ]]; then
        ok "$label: $path"
    else
        warn "$label missing: $path. $setup_hint"
    fi
}

section "Repository"
if command -v uv >/dev/null 2>&1; then
    ok "uv: $(uv --version)"
else
    fail "uv is not installed. Install it from https://docs.astral.sh/uv/."
fi

entrypoints=(
    "Format Conversion|format-conversion/format_mcp_server.py"
    "Browser Fetch|browser-fetch/browser_fetch_mcp_server.py"
    "ASR|asr/asr_mcp_server.py"
    "OCR|ocr/ocr_mcp_server.py"
    "Vision Local|vision-local/vision_local_mcp_server.py"
    "Brave Websearch|brave-websearch/run.sh"
)
for entry in "${entrypoints[@]}"; do
    label="${entry%%|*}"
    relative_path="${entry#*|}"
    if [[ -f "${REPO_DIR}/${relative_path}" ]]; then
        ok "$label entrypoint: $relative_path"
    else
        fail "$label entrypoint is missing: $relative_path"
    fi
done

cpu_python="${REPO_DIR}/environments/mcp-local/.venv/bin/python"
asr_python="${REPO_DIR}/environments/mcp-local-asr/.venv/bin/python"
ocr_python="${REPO_DIR}/environments/mcp-local-ocr/.venv/bin/python"
if [[ -x "$cpu_python" ]]; then
    if PYTHONNOUSERSITE=1 "$cpu_python" -c "import fitz, mcp, PIL" >/dev/null 2>&1; then
        ok "CPU profile is ready: environments/mcp-local"
    else
        fail "CPU profile exists but core imports fail. Run: uv sync --project environments/mcp-local --locked"
    fi
else
    fail "CPU profile is missing. Run: uv sync --project environments/mcp-local --locked"
fi
if [[ -x "$asr_python" ]]; then
    ok "Optional ASR profile is installed"
else
    optional "ASR profile is not installed; run bash install.sh --asr-only when needed"
fi
if [[ -x "$ocr_python" ]]; then
    ok "Optional OCR profile is installed"
else
    optional "OCR profile is not installed; run bash install.sh --ocr-only when needed"
fi

section "External runtimes and credentials"
if command -v ffmpeg >/dev/null 2>&1; then
    ok "FFmpeg: $(ffmpeg -version 2>/dev/null | head -n 1)"
else
    optional "FFmpeg is unavailable; ASR audio conversion is disabled"
fi
if command -v node >/dev/null 2>&1 && command -v npx >/dev/null 2>&1; then
    ok "Node.js: $(node --version); npx is available"
else
    optional "Node.js 22+ and npx are required only for Brave Websearch"
fi
if [[ -n "${HF_TOKEN:-}" ]]; then
    ok "HF_TOKEN: configured"
else
    optional "HF_TOKEN is not configured; speaker diarization is unavailable"
fi
if [[ -n "${BRAVE_API_KEY:-}" ]]; then
    ok "BRAVE_API_KEY: configured"
else
    optional "BRAVE_API_KEY is not configured; Brave Websearch is unavailable"
fi
if command -v nvidia-smi >/dev/null 2>&1; then
    if gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)" && [[ -n "$gpu_name" ]]; then
        ok "NVIDIA GPU: $gpu_name"
    else
        optional "nvidia-smi is present but GPU access is unavailable"
    fi
elif [[ -e /dev/dxg ]]; then
    optional "WSL GPU interface detected; verify the Windows NVIDIA driver with nvidia-smi"
else
    optional "No NVIDIA GPU detected; OCR, ASR, and Vision inference are unavailable"
fi

section "Model locations"
model_root="$(configured_or_default_model_root)"
if [[ -n "${MCP_TOOLS_MODEL_DIR:-}" ]]; then
    info "MCP_TOOLS_MODEL_DIR: $model_root (configured)"
else
    info "MCP_TOOLS_MODEL_DIR: $model_root (default)"
fi

ocr_model="${OCR_MODEL_NAME:-PaddlePaddle/PaddleOCR-VL-1.6}"
if [[ "$ocr_model" == /* || "$ocr_model" == ./* || "$ocr_model" == ../* || "$ocr_model" == ~/* ]]; then
    ocr_model_path="$(absolute_path "$ocr_model")"
    if [[ -d "$ocr_model_path" ]]; then
        ok "OCR recognizer: $ocr_model_path"
    else
        warn "OCR_MODEL_NAME points to a missing directory: $ocr_model_path"
    fi
else
    ocr_root="$(absolute_path "${OCR_MODEL_ROOT:-$model_root/ocr}")"
    ocr_candidate="$ocr_root/$ocr_model"
    legacy_ocr="$(absolute_path "${HOME:-~}/project/hf-models/models/safetensors/$ocr_model")"
    if [[ -d "$ocr_candidate" ]]; then
        ok "OCR recognizer: $ocr_candidate"
    elif [[ -n "${OCR_MODEL_ROOT:-}" ]]; then
        warn "OCR_MODEL_ROOT is configured but the requested model is missing: $ocr_candidate"
    elif [[ -z "${MCP_TOOLS_MODEL_DIR:-}" && "$ocr_model" == "PaddlePaddle/PaddleOCR-VL-1.6" && -d "$legacy_ocr" ]]; then
        warn "OCR recognizer uses deprecated compatibility fallback: $legacy_ocr"
    else
        info "OCR recognizer will use the Hugging Face cache/Hub model: $ocr_model"
    fi
fi

if [[ -n "${OCR_LAYOUT_MODEL:-}" ]]; then
    layout_path="$(absolute_path "$OCR_LAYOUT_MODEL")"
    if [[ -d "$layout_path" ]]; then
        ok "OCR layout model: $layout_path"
    else
        warn "OCR_LAYOUT_MODEL points to a missing directory: $layout_path"
    fi
else
    ocr_root="$(absolute_path "${OCR_MODEL_ROOT:-$model_root/ocr}")"
    layout_candidate="$ocr_root/PaddlePaddle/PP-DocLayoutV3"
    legacy_layout="$(absolute_path "${HOME:-~}/project/hf-models/models/safetensors/PaddlePaddle/PP-DocLayoutV3")"
    if [[ -d "$layout_candidate" ]]; then
        ok "OCR layout model: $layout_candidate"
    elif [[ -z "${OCR_MODEL_ROOT:-}${MCP_TOOLS_MODEL_DIR:-}" && -d "$legacy_layout" ]]; then
        warn "OCR layout uses deprecated compatibility fallback: $legacy_layout"
    else
        info "OCR layout will use the PaddleX managed cache/download"
    fi
fi

vision_root="$(absolute_path "${VISION_LOCAL_MODEL_DIR:-$model_root/vision}")"
legacy_vision_root="$(absolute_path "${REPO_DIR}/../hf-models/models/gguf/unsloth")"
default_profile_dir="$vision_root/Qwen3.5-9B-GGUF"
batch_profile_dir="$vision_root/Qwen3.5-4B-GGUF"
default_exact_pair=false
batch_exact_pair=false
if [[ -n "${VISION_LOCAL_MODEL_PATH:-}" && -n "${VISION_LOCAL_MMPROJ_PATH:-}" ]]; then
    default_exact_pair=true
fi
if [[ -n "${VISION_LOCAL_BATCH_MODEL_PATH:-}" && -n "${VISION_LOCAL_BATCH_MMPROJ_PATH:-}" ]]; then
    batch_exact_pair=true
fi

if [[ -z "${VISION_LOCAL_MODEL_DIR:-}${MCP_TOOLS_MODEL_DIR:-}" && "$default_exact_pair" == false && ! -e "$default_profile_dir" && -f "$legacy_vision_root/Qwen3.5-9B-GGUF/Qwen3.5-9B-UD-Q4_K_XL.gguf" && -f "$legacy_vision_root/Qwen3.5-9B-GGUF/mmproj-BF16.gguf" ]]; then
    warn "Vision default profile uses deprecated sibling hf-models compatibility fallback"
    default_profile_dir="$legacy_vision_root/Qwen3.5-9B-GGUF"
fi
if [[ -z "${VISION_LOCAL_MODEL_DIR:-}${MCP_TOOLS_MODEL_DIR:-}" && "$batch_exact_pair" == false && ! -e "$batch_profile_dir" && -f "$legacy_vision_root/Qwen3.5-4B-GGUF/Qwen3.5-4B-UD-Q4_K_XL.gguf" && -f "$legacy_vision_root/Qwen3.5-4B-GGUF/mmproj-BF16.gguf" ]]; then
    warn "Vision batch profile uses deprecated sibling hf-models compatibility fallback"
    batch_profile_dir="$legacy_vision_root/Qwen3.5-4B-GGUF"
fi

default_model="$(absolute_path "${VISION_LOCAL_MODEL_PATH:-$default_profile_dir/Qwen3.5-9B-UD-Q4_K_XL.gguf}")"
default_projector="$(absolute_path "${VISION_LOCAL_MMPROJ_PATH:-$default_profile_dir/mmproj-BF16.gguf}")"
batch_model="$(absolute_path "${VISION_LOCAL_BATCH_MODEL_PATH:-$batch_profile_dir/Qwen3.5-4B-UD-Q4_K_XL.gguf}")"
batch_projector="$(absolute_path "${VISION_LOCAL_BATCH_MMPROJ_PATH:-$batch_profile_dir/mmproj-BF16.gguf}")"

check_local_artifact "Vision default model" "$default_model" "See vision-local/README.md."
check_local_artifact "Vision default projector" "$default_projector" "See vision-local/README.md."
check_local_artifact "Vision batch model" "$batch_model" "See vision-local/README.md."
check_local_artifact "Vision batch projector" "$batch_projector" "See vision-local/README.md."

vision_server="${VISION_LOCAL_SERVER_BINARY:-$REPO_DIR/.runtime/llama.cpp-build/bin/llama-server}"
vision_server="$(absolute_path "$vision_server")"
if [[ -x "$vision_server" ]]; then
    ok "Vision llama.cpp server: $vision_server"
else
    warn "Vision llama.cpp server is missing: $vision_server. Run: bash vision-local/install_runtime.sh"
fi

section "Summary"
if ((failures > 0)); then
    printf '[FAIL] %d blocking failure(s), %d warning(s)\n' "$failures" "$warnings"
    exit 1
fi
printf '[OK] No blocking failures; %d warning(s), optional capabilities may be unavailable\n' "$warnings"
