# Vision Local — GPU Image Analysis MCP

`vision_local` is a model-neutral MCP service for local image understanding. It exposes generic visual tools over stdio and includes a resumable concurrent CLI for large labeled image collections. Interactive and high-detail tools default to Qwen3.5-9B, while batch coarse passes automatically use the lighter Qwen3.5-4B profile.

## Runtime choice for this workstation

The deployment uses two instruction-tuned `UD-Q4_K_XL` GGUF vision profiles:

| Profile | Use | Model | Projector | Local directory |
|---|---|---:|---:|---|
| default | Small numbers of images and complex/high-resolution requests | `Qwen3.5-9B-UD-Q4_K_XL.gguf` (5.97 GB) | `mmproj-BF16.gguf` (0.92 GB) | `../../hf-models/models/gguf/unsloth/Qwen3.5-9B-GGUF/` |
| batch | Large coarse-pass image collections | `Qwen3.5-4B-UD-Q4_K_XL.gguf` (2.91 GB) | `mmproj-BF16.gguf` (0.68 GB) | `../../hf-models/models/gguf/unsloth/Qwen3.5-4B-GGUF/` |

The RTX 4070 Ti is Ada (compute capability 8.9), so it does not have Blackwell FP4 Tensor Cores. NVFP4 checkpoints can be stored on this machine but do not receive native FP4 acceleration. Unsloth's UD-Q4_K_XL dynamic 4-bit quant with a CUDA llama.cpp build is the practical high-throughput path that fits the 12 GB VRAM budget while retaining the vision projector at BF16.

Model sources and validated revisions:

- 9B default: <https://huggingface.co/unsloth/Qwen3.5-9B-GGUF>, revision `3885219b6810b007914f3a7950a8d1b469d598a5`.
- 4B batch: <https://huggingface.co/unsloth/Qwen3.5-4B-GGUF>, revision `e87f176479d0855a907a41277aca2f8ee7a09523`.

## Files

| File | Purpose |
|---|---|
| `vision_local_mcp_server.py` | FastMCP stdio frontend and detached batch-job tools |
| `vision_runtime.py` | Backend lifecycle, image normalization, local HTTP client, structured classification |
| `batch_classify.py` | Concurrent, resumable directory audit with JSONL/JSON/CSV artifacts |
| `verify_misclassified.py` | 1024-pixel second pass over only coarse disagreements |
| `install_runtime.sh` | Reproducible repository-local CUDA llama.cpp build; no system install |

The generated llama.cpp source and build tree live under `../.runtime/`, which is Git-ignored.

## Provisioning

Build llama.cpp release `b9637` for the RTX 4070 Ti (SM 8.9):

```bash
bash vision-local/install_runtime.sh
```

Download only the two required files for each profile with the existing Hugging Face environment:

```bash
conda run -n hfdownload hf download \
  unsloth/Qwen3.5-9B-GGUF \
  --include Qwen3.5-9B-UD-Q4_K_XL.gguf \
  --include mmproj-BF16.gguf \
  --revision 3885219b6810b007914f3a7950a8d1b469d598a5 \
  --local-dir ../hf-models/models/gguf/unsloth/Qwen3.5-9B-GGUF

conda run -n hfdownload hf download \
  unsloth/Qwen3.5-4B-GGUF \
  --include Qwen3.5-4B-UD-Q4_K_XL.gguf \
  --include mmproj-BF16.gguf \
  --revision e87f176479d0855a907a41277aca2f8ee7a09523 \
  --local-dir ../hf-models/models/gguf/unsloth/Qwen3.5-4B-GGUF
```

If the CLI entry point in that environment is unavailable, use `snapshot_download` from its installed `huggingface_hub`; both routes preserve local metadata and resume partial files.

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

The first interactive inference starts the default 9B backend on `127.0.0.1:8003`. A batch audit starts and reuses the 4B backend on `127.0.0.1:8004`. Neither is exposed to the network. Both use a five-minute idle sleep; on a 12 GB GPU, avoid waking both profiles at the same time because their combined weights, projectors, and caches can exceed available VRAM.

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
conda run -n mcp-local python \
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
conda run -n mcp-local python \
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
| `VISION_LOCAL_MODEL_PATH` | sibling `hf-models` UD-Q4_K_XL file |
| `VISION_LOCAL_MMPROJ_PATH` | sibling `hf-models` BF16 projector |
| `VISION_LOCAL_HOST` / `VISION_LOCAL_PORT` | `127.0.0.1` / `8003` |
| `VISION_LOCAL_CONTEXT_SIZE` | `8192` total across slots |
| `VISION_LOCAL_PARALLEL` | `4` |
| `VISION_LOCAL_IMAGE_MAX_TOKENS` | `1024` cap; 512-pixel fast inputs normally use about 256 |
| `VISION_LOCAL_SLEEP_IDLE_SECONDS` | `300`; unload model/KV cache, auto-wake on next inference |
| `VISION_LOCAL_STARTUP_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_REQUEST_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_LOG_PATH` | `/tmp/vision_local_llama_server.log` |

The batch profile uses the same suffixes under `VISION_LOCAL_BATCH_*`, with these profile-specific defaults:

| Variable | Default |
|---|---|
| `VISION_LOCAL_BATCH_MODEL_PATH` | sibling `Qwen3.5-4B-UD-Q4_K_XL.gguf` |
| `VISION_LOCAL_BATCH_MMPROJ_PATH` | sibling 4B `mmproj-BF16.gguf` |
| `VISION_LOCAL_BATCH_HOST` / `VISION_LOCAL_BATCH_PORT` | `127.0.0.1` / `8004` |
| `VISION_LOCAL_BATCH_CONTEXT_SIZE` | `4096` total across slots |
| `VISION_LOCAL_BATCH_PARALLEL` | `4` |
| `VISION_LOCAL_BATCH_IMAGE_MAX_TOKENS` | `512` |
| `VISION_LOCAL_BATCH_SLEEP_IDLE_SECONDS` | `300` |
| `VISION_LOCAL_BATCH_STARTUP_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_BATCH_REQUEST_TIMEOUT` | `180` seconds |
| `VISION_LOCAL_BATCH_LOG_PATH` | `/tmp/vision_local_batch_llama_server.log` |

`VISION_LOCAL_SERVER_BINARY` remains shared by both profiles unless `VISION_LOCAL_BATCH_SERVER_BINARY` is set explicitly.

The MCP frontend runs in the existing `mcp-local` Python environment, which already provides FastMCP and Pillow. It does not add PyTorch, Transformers, or vLLM to that shared environment.
