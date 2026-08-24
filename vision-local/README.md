# Vision Local — GPU Image Analysis MCP

`vision_local` is a model-neutral MCP service for local image understanding. It
exposes generic visual tools over stdio and includes a resumable concurrent CLI
for large labeled image collections. Interactive and high-detail tools default
to Qwen3.5-9B, batch coarse passes use Qwen3.5-4B, and
`VISION_LOCAL_PROFILE=8gb` selects a separately bounded interactive 4B profile.

## Reference runtime choice

The deployment uses two instruction-tuned `UD-Q4_K_XL` model snapshots across
three runtime profiles:

| Profile | Use | Model / projector | Runtime bounds | 8 GB status |
|---|---|---|---|---|
| `default` | Complex/high-resolution interactive requests | 9B (5.97 GB) / BF16 projector (0.92 GB) | 8192 context, 4 slots, 99 GPU layers | **Not validated or advertised for 8 GB** |
| `batch` | Concurrent coarse-pass collections | 4B (2.91 GB) / BF16 projector (0.68 GB) | 4096 context, 4 slots, 99 GPU layers | **Not validated or advertised for 8 GB** |
| `8gb` | Bounded interactive requests | Same pinned 4B / BF16 projector | 2048 context, 1 slot, 512 output tokens, 20 GPU layers, batch 256, ubatch 128 | **Validated** at a 5518 MiB maximum across two whole-device runs with an 8000 MiB test ceiling |

The RTX 4070 Ti is Ada (compute capability 8.9), so it does not have Blackwell
FP4 Tensor Cores. NVFP4 checkpoints can be stored on this machine but do not
receive native FP4 acceleration. Unsloth's UD-Q4_K_XL dynamic 4-bit quant with
a CUDA llama.cpp build is the practical reference path while retaining the
vision projector at BF16. The default 9B and concurrent batch profiles remain
12 GB reference configurations, not 8 GB compatibility claims.

Model sources and validated revisions:

- 9B default: <https://huggingface.co/unsloth/Qwen3.5-9B-GGUF>, revision `3885219b6810b007914f3a7950a8d1b469d598a5`.
- 4B batch/8gb: <https://huggingface.co/unsloth/Qwen3.5-4B-GGUF>, revision `e87f176479d0855a907a41277aca2f8ee7a09523`.

## Files

| File | Purpose |
|---|---|
| `vision_local_mcp_server.py` | FastMCP stdio frontend and detached batch-job tools |
| `vision_runtime.py` | Backend lifecycle, image normalization, local HTTP client, structured classification |
| `batch_classify.py` | Concurrent, resumable directory audit with JSONL/JSON/CSV artifacts |
| `verify_misclassified.py` | 1024-pixel second pass over only coarse disagreements |
| `install_runtime.sh` | Reproducible repository-local CUDA llama.cpp build; no system install |

The generated llama.cpp source and build tree live under `../.runtime/`, which is Git-ignored. At backend startup, Vision Local automatically adds the repository-local CUDA 13 libraries from `environments/mcp-local-asr` when present, followed by standard system CUDA and WSL driver locations. This keeps the runtime usable when the build toolkit is no longer installed system-wide.

## Provisioning

Build pinned llama.cpp release `b10451` for the detected NVIDIA architecture:

```bash
bash vision-local/install_runtime.sh
```

Set `VISION_LOCAL_CUDA_ARCHITECTURES` to a CMake CUDA architecture such as `89`
when native detection is unavailable or a cross-target build is required. The
reference RTX 4070 Ti build used `89`; this is no longer hardcoded for every
user.

If `nvcc` is not installed system-wide, the installer uses `uv` to create an
isolated build environment pinned to CUDA compiler 13.3.73, cuBLAS 13.6.1.10,
and CCCL 13.3.3.4.1. These packages are build-only dependencies and are not
added to the `mcp-local` runtime lock. The resulting server resolves CUDA 13
runtime libraries from the `mcp-local-asr` profile by default.

The shared model root defaults to `$XDG_CACHE_HOME/mcp-tools/models`, or
`~/.cache/mcp-tools/models` when `XDG_CACHE_HOME` is unset. Override the shared
OCR/Vision root with `MCP_TOOLS_MODEL_DIR`, or set `VISION_LOCAL_MODEL_DIR` to a
Vision-specific root.

Download only the two required files for each profile with an isolated,
versioned Hugging Face CLI:

```bash
MODEL_ROOT="${MCP_TOOLS_MODEL_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/mcp-tools/models}"
VISION_MODELS="${VISION_LOCAL_MODEL_DIR:-$MODEL_ROOT/vision}"

uvx --from huggingface-hub==0.36.2 hf download \
  unsloth/Qwen3.5-9B-GGUF \
  Qwen3.5-9B-UD-Q4_K_XL.gguf \
  mmproj-BF16.gguf \
  --revision 3885219b6810b007914f3a7950a8d1b469d598a5 \
  --local-dir "$VISION_MODELS/Qwen3.5-9B-GGUF"

uvx --from huggingface-hub==0.36.2 hf download \
  unsloth/Qwen3.5-4B-GGUF \
  Qwen3.5-4B-UD-Q4_K_XL.gguf \
  mmproj-BF16.gguf \
  --revision e87f176479d0855a907a41277aca2f8ee7a09523 \
  --local-dir "$VISION_MODELS/Qwen3.5-4B-GGUF"
```

Run `bin/mcp-tools doctor` afterward. It reports the expected server, model, and
projector paths without starting inference. Existing sibling `hf-models`
directories remain a warned compatibility fallback only; new installations do
not need a neighboring repository.

## MCP tools

| Tool | Result |
|---|---|
| `vision_status` | Backend readiness and artifact checks without forcing model load |
| `analyze_image` | General image Q&A with a custom prompt |
| `extract_text_from_image` | Visible-text transcription preserving reading order |
| `analyze_chart` | Chart type, axes, legend, values, trends, and custom question |
| `classify_eyewear` | Fast 512-pixel schema-constrained eyewear classification |
| `verify_eyewear` | 1024-pixel second pass with visible cues for thin/rimless frames |
| `classify_eyewear_batch` | Detached labeled-directory audit; returns output and log paths |
| `eyewear_batch_status` | Progress and final artifact lookup for a submitted job |

The first interactive inference starts the selected backend: default 9B on
`127.0.0.1:8003`, or bounded 4B on `127.0.0.1:8005` when
`VISION_LOCAL_PROFILE=8gb`. A batch audit uses the separate 4B backend on
`127.0.0.1:8004`. None is exposed to the network. Keep ASR, OCR, and every
Vision profile serialized; do not wake two heavyweight backends together on a
limited-VRAM machine.

Select the bounded profile in the MCP client's environment or before a manual
run:

```bash
export VISION_LOCAL_PROFILE=8gb
bin/mcp-tools vision-local
```

The measured 8 GB case used the fixed runtime defaults below. Raising a
memory-relevant `VISION_LOCAL_8GB_*` value is rejected. Replacing either model
artifact makes the recorded result inapplicable. The high-detail verification
CLI and the four-slot batch profile were not included in this dedicated
measurement.

## Efficient batch processing

The CLI applies six optimizations for large local collections:

1. Load the 4B batch weights once and keep four continuous-batching slots warm.
2. Resize portraits in memory to a 512-pixel longest edge, reducing image tokens and transfer size.
3. Issue four concurrent HTTP requests to overlap image encoding, prompt evaluation, and short decoding.
4. Constrain output to a two-field JSON schema and disable thinking, limiting decode work to 32 tokens.
5. Append every result to `results.jsonl`; completed images are skipped on resume and transient failures are retried.
6. Recheck only coarse disagreements at 1024 pixels, preserving efficiency while catching thin/rimless frames missed by the fast pass.

Example:

```bash
uv run --project environments/mcp-local python \
  vision-local/batch_classify.py \
  --g-dir /path/to/G \
  --ng-dir /path/to/NG \
  --output-dir /path/to/new-output \
  --concurrency 4
```

Each output directory contains:

| Artifact | Content |
|---|---|
| `manifest.json` | Inputs, runtime parameters, PID, and label mapping |
| `progress.json` | Atomic live progress, throughput, ETA, and error count |
| `results.jsonl` | One durable record per attempt; latest record wins on resume |
| `summary.json` | Counts and grouped misclassified filenames |
| `misclassified.json` | Full structured records for suspected label errors |
| `misclassified.csv` | Review-friendly filename and expected/predicted columns |

After the 4B coarse pass, verify only its candidates with the default 9B profile and 1024-token image cap:

```bash
uv run --project environments/mcp-local python \
  vision-local/verify_misclassified.py \
  --results-jsonl /path/to/output/results.jsonl \
  --output-dir /path/to/output
```

This adds `verification-results.jsonl`, `verification-summary.json`, `verified-misclassified.json`, and `verified-misclassified.csv`. Treat the verified files as a machine-generated review queue, not confirmed ground truth; the coarse files remain as an auditable first-stage record. The measured audit found that exceptionally thin or rimless glasses can still be missed with high confidence, so review candidates at original resolution before changing labels.

For the requested audit, `G` means the person is expected to wear glasses and `NG` means the person is expected not to. Therefore, suspected errors are `G` images predicted `false` and `NG` images predicted `true`. Model output is an automated review aid; low-confidence and borderline occlusion cases still merit human inspection.

The measured deployment and complete 4,500-image audit are documented in [`../docs/vision-local-verification-report.md`](../docs/vision-local-verification-report.md).

## Configuration

The default profile keeps the existing `VISION_LOCAL_*` interface:

| Variable | Default |
|---|---|
| `VISION_LOCAL_SERVER_BINARY` | `../.runtime/llama.cpp-build/bin/llama-server` |
| `MCP_TOOLS_MODEL_DIR` | `$XDG_CACHE_HOME/mcp-tools/models` or `~/.cache/mcp-tools/models` |
| `VISION_LOCAL_MODEL_DIR` | `MCP_TOOLS_MODEL_DIR/vision` |
| `VISION_LOCAL_CUDA_ARCHITECTURES` | `native` during `install_runtime.sh`; set a CMake architecture explicitly for cross-target builds |
| `VISION_LOCAL_CUDA_LIBRARY_PATH` | Optional colon-separated CUDA library override; automatic discovery is used when unset |
| `VISION_LOCAL_MODEL_PATH` | `VISION_LOCAL_MODEL_DIR/Qwen3.5-9B-GGUF/Qwen3.5-9B-UD-Q4_K_XL.gguf` |
| `VISION_LOCAL_MMPROJ_PATH` | `VISION_LOCAL_MODEL_DIR/Qwen3.5-9B-GGUF/mmproj-BF16.gguf` |
| `VISION_LOCAL_HOST` / `VISION_LOCAL_PORT` | `127.0.0.1` / `8003` |
| `VISION_LOCAL_CONTEXT_SIZE` | `8192` total across slots |
| `VISION_LOCAL_PARALLEL` | `4` |
| `VISION_LOCAL_IMAGE_MAX_TOKENS` | `1024` cap; 512-pixel fast inputs normally use about 256 |
| `VISION_LOCAL_MAX_OUTPUT_TOKENS` | `4096` |
| `VISION_LOCAL_GPU_LAYERS` | `99` |
| `VISION_LOCAL_BATCH_SIZE` / `VISION_LOCAL_UBATCH_SIZE` | `512` / `256` |
| `VISION_LOCAL_SLEEP_IDLE_SECONDS` | `300`; unload model/KV cache, auto-wake on next inference |
| `VISION_LOCAL_STARTUP_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_REQUEST_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_LOG_PATH` | `/tmp/vision_local_llama_server.log` |

The batch profile uses the same suffixes under `VISION_LOCAL_BATCH_*`, with these profile-specific defaults:

| Variable | Default |
|---|---|
| `VISION_LOCAL_BATCH_MODEL_PATH` | `VISION_LOCAL_MODEL_DIR/Qwen3.5-4B-GGUF/Qwen3.5-4B-UD-Q4_K_XL.gguf` |
| `VISION_LOCAL_BATCH_MMPROJ_PATH` | `VISION_LOCAL_MODEL_DIR/Qwen3.5-4B-GGUF/mmproj-BF16.gguf` |
| `VISION_LOCAL_BATCH_HOST` / `VISION_LOCAL_BATCH_PORT` | `127.0.0.1` / `8004` |
| `VISION_LOCAL_BATCH_CONTEXT_SIZE` | `4096` total across slots |
| `VISION_LOCAL_BATCH_PARALLEL` | `4` |
| `VISION_LOCAL_BATCH_IMAGE_MAX_TOKENS` | `512` |
| `VISION_LOCAL_BATCH_MAX_OUTPUT_TOKENS` | `512` |
| `VISION_LOCAL_BATCH_GPU_LAYERS` | `99` |
| `VISION_LOCAL_BATCH_BATCH_SIZE` / `VISION_LOCAL_BATCH_UBATCH_SIZE` | `512` / `256` |
| `VISION_LOCAL_BATCH_SLEEP_IDLE_SECONDS` | `300` |
| `VISION_LOCAL_BATCH_STARTUP_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_BATCH_REQUEST_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_BATCH_LOG_PATH` | `/tmp/vision_local_batch_llama_server.log` |

The bounded interactive profile is selected by `VISION_LOCAL_PROFILE=8gb` and
uses `VISION_LOCAL_8GB_*` overrides:

| Variable | Default / maximum |
|---|---|
| `VISION_LOCAL_8GB_MODEL_PATH` | `VISION_LOCAL_MODEL_DIR/Qwen3.5-4B-GGUF/Qwen3.5-4B-UD-Q4_K_XL.gguf` |
| `VISION_LOCAL_8GB_MMPROJ_PATH` | `VISION_LOCAL_MODEL_DIR/Qwen3.5-4B-GGUF/mmproj-BF16.gguf` |
| `VISION_LOCAL_8GB_HOST` / `VISION_LOCAL_8GB_PORT` | `127.0.0.1` / `8005` |
| `VISION_LOCAL_8GB_CONTEXT_SIZE` | `2048` |
| `VISION_LOCAL_8GB_PARALLEL` | `1` |
| `VISION_LOCAL_8GB_IMAGE_MAX_TOKENS` | `512` |
| `VISION_LOCAL_8GB_MAX_OUTPUT_TOKENS` | `512`; larger tool requests are capped |
| `VISION_LOCAL_8GB_GPU_LAYERS` | `20` |
| `VISION_LOCAL_8GB_BATCH_SIZE` / `VISION_LOCAL_8GB_UBATCH_SIZE` | `256` / `128` |
| `VISION_LOCAL_8GB_SLEEP_IDLE_SECONDS` | `300` |
| `VISION_LOCAL_8GB_STARTUP_TIMEOUT` / `VISION_LOCAL_8GB_REQUEST_TIMEOUT` | `180` / `180` seconds |
| `VISION_LOCAL_8GB_LOG_PATH` | `/tmp/vision_local_8gb_llama_server.log` |

`VISION_LOCAL_SERVER_BINARY` remains shared unless the selected non-default
profile sets its own `*_SERVER_BINARY` value.

The MCP frontend runs in the existing `mcp-local` Python environment, which already provides FastMCP and Pillow. It does not add PyTorch, Transformers, or vLLM to that shared environment.
