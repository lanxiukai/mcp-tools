# Runtime Environment Records

This directory stores the three active, reproducible uv runtime projects owned
by this repository:

- `mcp-local` for shared CPU-side MCP tools;
- `mcp-local-asr` for Qwen3-ASR and speaker diarization;
- `mcp-local-ocr` for PaddleOCR-VL and PP-DocLayoutV3.

`install.sh` remains the active provisioning workflow. Each uv lock captures
the resolved Python packages and CUDA-specific package indexes.

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

If the default uv cache is read-only in a sandbox, append `--no-cache` to the
three `uv sync --check` commands. This changes only transient cache handling;
it does not update a lock or environment.

Run tests through each profile's Python module entry point so the repository
root remains on `sys.path` and suites do not accidentally share dependencies:

```bash
# Canonical CPU-safe contributor gate (also used by CI)
scripts/check.sh

# Individual profile suites
environments/mcp-local/.venv/bin/python -m pytest -q \
  test/format_conversion test/browser_fetch/test_cpu_installer.py \
  test/vision_local/test_vision_runtime.py test/ocr/test_model_paths.py \
  test/tooling/test_doctor.py
environments/mcp-local-asr/.venv/bin/python -m pytest -q \
  test/asr test/asr_pipeline
environments/mcp-local-ocr/.venv/bin/python -m pytest -q test/ocr
```

The OCR suite uses loopback HTTP fixtures, and real MCP stdio discovery needs
an asyncio event loop. Run those checks outside sandboxes that prohibit socket
creation; a sandbox `PermissionError` or handshake timeout does not indicate a
broken uv environment.

System FFmpeg is an external dependency of the ASR uv project. The shared CPU
project keeps Chromium, system browser libraries, fonts, the pinned MathJax
Node runtime, and the Vision Local llama.cpp backend outside Python. The OCR uv
project uses PaddlePaddle 3.2.1 and PyTorch 2.7.1 from CUDA 12.6 indexes. Those
wheels agree on the CUDA package set except for NCCL metadata, so the manifest
keeps PaddlePaddle's NCCL 2.25.1 override for this single-GPU workload. Refreshes
must repeat separate PaddlePaddle and PyTorch GPU checks plus real layout and
recognition inference. The validated target also completed a one-process NCCL
all-reduce with the overridden 2.25.1 runtime.

All three installers, launchers, and client registrations use these uv
projects. Their retired Conda environments and exact recovery records were
removed after full uv validation. Do not edit `uv.lock` manually; refresh it
only after an intentional dependency change and successful verification.

The profiles directly pin `pydantic-settings` 2.14.2 while MCP 1.x leaves the
generic `FastMCP.Settings.lifespan` annotation unresolved. Version 2.15.0
reports that upstream issue on every server startup; remove the compatibility
pin only after a stable MCP release rebuilds the settings model.
