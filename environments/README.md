# Runtime Environment Records

This directory stores the active ASR uv project and reproducible records for
the three historical or current Conda runtime profiles owned by this
repository:

- `mcp-local` for shared CPU-side MCP tools;
- `mcp-local-asr` for Qwen3-ASR and speaker diarization;
- `mcp-local-ocr` for PaddleOCR-VL and PP-DocLayoutV3.

`install.sh` remains the declarative provisioning workflow. The Conda records
capture the exact tested state, including Conda artifacts, pip package versions,
CUDA-specific package indexes, checksums, and repository revision.

The `mcp-local-asr` directory owns the active ASR uv project:

- `.python-version` pins Python 3.12.13;
- `pyproject.toml` declares the direct ASR and CUDA dependencies;
- `uv.lock` is the reproducible package lock;
- `.venv/` is project-local, ignored by Git, and safe to recreate.

Restore and verify it from the repository root:

```bash
uv sync --project environments/mcp-local-asr --locked
uv sync --project environments/mcp-local-asr --check
uv run --project environments/mcp-local-asr python -c \
  "import torch; print(torch.__version__, torch.cuda.is_available())"
```

System FFmpeg is an external dependency of this uv project. The existing Conda
record remains intact for recovery comparison, but ASR provisioning, launchers,
and MCP client configuration use the project-local uv interpreter.

The unified manager and documentation live in the sibling
`ai-agent-framework` repository at `config/conda/`. Do not edit generated lock
files manually; refresh them after an intentional runtime update and successful
verification.
