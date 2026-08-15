# Runtime Environment Records

This directory stores the active uv projects and reproducible records for the
three historical or current Conda runtime profiles owned by this
repository:

- `mcp-local` for shared CPU-side MCP tools;
- `mcp-local-asr` for Qwen3-ASR and speaker diarization;
- `mcp-local-ocr` for PaddleOCR-VL and PP-DocLayoutV3.

`install.sh` remains the active provisioning workflow. The Conda records capture
the exact tested state, including Conda artifacts, pip package versions,
CUDA-specific package indexes, checksums, and repository revision.

All three profile directories own independent uv projects:

- `.python-version` pins Python 3.12.13;
- each `pyproject.toml` declares only its direct runtime dependencies;
- `uv.lock` is the reproducible package lock;
- `.venv/` is project-local, ignored by Git, and safe to recreate.

Restore and verify them from the repository root:

```bash
uv sync --project environments/mcp-local-asr --locked
uv sync --project environments/mcp-local-asr --check
uv run --project environments/mcp-local-asr python -c \
  "import torch; print(torch.__version__, torch.cuda.is_available())"

uv sync --project environments/mcp-local --locked
uv sync --project environments/mcp-local --check
uv run --project environments/mcp-local python -c \
  "import fitz, mcp, playwright, weasyprint; print('mcp-local ready')"

uv sync --project environments/mcp-local-ocr --locked
uv sync --project environments/mcp-local-ocr --check
uv run --project environments/mcp-local-ocr python -c \
  "import paddle, torch; print(paddle.version.cuda(), torch.version.cuda)"
```

System FFmpeg is an external dependency of the ASR uv project. The shared CPU
project keeps Chromium, system browser libraries, fonts, the pinned MathJax
Node runtime, and the Vision Local llama.cpp backend outside Python. The OCR uv
project uses PaddlePaddle 3.2.1 and PyTorch 2.7.1 from CUDA 12.6 indexes. Those
wheels agree on the CUDA package set except for NCCL metadata, so the manifest
keeps PaddlePaddle's NCCL 2.25.1 override for this single-GPU workload. Refreshes
must repeat separate PaddlePaddle and PyTorch GPU checks plus real layout and
recognition inference. The validated target also completed a one-process NCCL
all-reduce with the overridden 2.25.1 runtime.

Existing Conda records remain intact for recovery comparison. ASR uses uv for
provisioning and client configuration; shared CPU and OCR client wiring are
migrated separately. `install.sh --ocr-only` still provisions the active Conda
runtime and does not select the parallel uv profile.

The unified manager and documentation live in the sibling
`ai-agent-framework` repository at `config/conda/`. Do not edit generated lock
files manually; refresh them after an intentional runtime update and successful
verification.
