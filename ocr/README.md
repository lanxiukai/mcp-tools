# OCR — Replaceable Local Document Recognition

This directory provides a model-independent OCR MCP and REST service. The stable client surface is named `ocr`; the current backend uses PP-DocLayoutV3 for ordered page segmentation and `PaddlePaddle/PaddleOCR-VL-1.6` through Transformers 5.8 for element recognition.

The service recognizes multilingual printed text, handwriting, formulas, tables, charts, seals, and mixed image/PDF documents. Jobs are serialized on one GPU, split into durable 24-page chunks, and returned as Markdown artifact metadata rather than embedding large OCR output in MCP responses.

## Stable interfaces

| Layer | Stable entry point | Purpose |
|---|---|---|
| MCP | `ocr_mcp_server.py` | Model-neutral stdio tools |
| REST | `ocr_server.py` | Loopback FastAPI backend and durable queue |
| Launcher | `ocr_start.sh` | `start`, `--fg`, `stop`, `status`, `check` |
| Model adapter | `model_adapter.py` | Backend-specific load, prompt, preprocessing, and decoding |
| Layout worker | `paddle_layout_worker.py` | Short-lived, isolated PaddlePaddle page segmentation |
| PDF staging | `pdf_staging.py` | Shared page planning and local chunk creation |
| Protocol | `ocr_mcp_protocol.py` | Typed artifact-only MCP responses |

## MCP tools

### `ocr_document(file_path)`

Submit an image or PDF, wait for completion, and return durable artifact metadata.

### `ocr_submit(file_path)`

Submit immediately and return a job ID. Use this for long PDFs that may exceed one MCP transport window.

### `ocr_wait(job_id, max_wait=1800)`

Wait for a queued/running job. Bound `max_wait` below the client transport timeout and repeat if necessary. If the backend was interrupted, this call initiates a non-blocking restart; retry the same job ID to reuse verified chunks and resume pending work.

### `ocr_status(job_id="")`

Without a job ID, return service/model/GPU health. With a job ID, return queue progress and published artifacts.

Completed jobs return this shape:

```json
{
  "job_id": "437823bc136c4b66821213df52db9f67",
  "status": "completed",
  "page_count": 27,
  "artifacts": [
    {
      "chunk_index": 1,
      "source_pages": [1, 2, 3],
      "path": "/home/user/.local/state/ocr/jobs/.../chunks/chunk-001.md",
      "sha256": "..."
    }
  ]
}
```

Read the returned `.md` files separately. The MCP never copies output beside the source document.

## Model resolution

Default resolution order:

1. Explicit `OCR_MODEL_NAME` local directory or model ID.
2. `OCR_MODEL_ROOT/<OCR_MODEL_NAME>` when `OCR_MODEL_ROOT` is set.
3. Complete local snapshot at `~/project/hf-models/models/safetensors/PaddlePaddle/PaddleOCR-VL-1.6`.
4. Hugging Face model ID `PaddlePaddle/PaddleOCR-VL-1.6`.

The downloaded local model is loaded with `local_files_only=True`. Transformers' built-in PaddleOCR-VL implementation is used by default; `OCR_TRUST_REMOTE_CODE=1` is an explicit opt-in for a future backend that requires repository code.

Dense pages are not sent to the element-level recognizer as one unbounded prompt. A short-lived PaddlePaddle subprocess detects regions and reading order, exits to release its CUDA runtime, then the resident PyTorch process recognizes ordered crops in bounded batches. Both processes use the `mcp-local-ocr` environment; the resident server does not load PaddlePaddle, while layout runs in a separate process (some PaddleX optional dependencies may import Torch transitively). Formula, table, chart, and seal labels are routed to their specialized prompts. If layout detection is unavailable, the adapter falls back to bounded horizontal tiles.

PaddleOCR-VL supports the backend tasks `ocr`, `table`, `formula`, `chart`, `spotting`, and `seal`. The durable document service defaults to `OCR_TASK=ocr`, which performed better on mixed pages in local smoke tests. Model-specific task selection remains backend configuration rather than part of the stable MCP contract.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OCR_HOST` | `127.0.0.1` | REST bind/client host |
| `OCR_PORT` | `8002` | REST port |
| `OCR_PYTHON` | auto-detected | `mcp-local-ocr` interpreter |
| `OCR_MODEL_NAME` | local PaddleOCR-VL snapshot, then Hub ID | Model directory or ID |
| `OCR_MODEL_ROOT` | unset | Optional root for replaceable models |
| `OCR_TASK` | `ocr` | Backend recognition task |
| `OCR_MAX_NEW_TOKENS` | `512` | Per-element token ceiling |
| `OCR_MAX_GENERATION_SECONDS` | `60` | Per-batch generation time ceiling |
| `OCR_RECOGNITION_BATCH_SIZE` | `4` | Element crops decoded together on GPU |
| `OCR_PAGE_BATCH_SIZE` | `4` | Consecutive pages pooled for crop batching |
| `OCR_USE_KV_CACHE` | `1` | Reuse attention keys/values during autoregressive decoding; set `0` only for compatibility diagnosis |
| `OCR_PDF_DPI` | `200` | PDF render resolution before layout/recognition |
| `OCR_ATTENTION` | model default (`sdpa`) | Optional explicit attention implementation |
| `OCR_TRUST_REMOTE_CODE` | `0` | Opt into custom repository code only when required |
| `OCR_USE_LAYOUT` | `1` | Use isolated page layout detection |
| `OCR_LAYOUT_PYTHON` | same as `OCR_PYTHON` | Optional alternate interpreter for the isolated layout subprocess |
| `OCR_LAYOUT_MODEL` | `~/project/hf-models/models/safetensors/PaddlePaddle/PP-DocLayoutV3` | Local layout model snapshot |
| `OCR_LAYOUT_DEVICE` | `gpu:0` | Layout inference device |
| `OCR_LAYOUT_THRESHOLD` | `0.5` | Minimum layout-region confidence |
| `OCR_LAYOUT_TIMEOUT` | `300` | Layout subprocess timeout in seconds |
| `OCR_LAYOUT_MIN_HEIGHT` | `384` | Shorter single-line images skip page layout |
| `OCR_FALLBACK_TILE_HEIGHT` | `1200` | Tile height when layout yields no usable regions |
| `OCR_IDLE_TIMEOUT` | `30` | Seconds before an idle backend releases the GPU |
| `OCR_JOB_ROOT` | `$XDG_STATE_HOME/ocr/jobs` | Durable job/artifact directory |
| `OCR_QUEUE_CAPACITY` | `8` | Maximum pending jobs |
| `OCR_JOB_TTL_SECONDS` | `3600` | Completed artifact retention |

## Performance

The default decoder now explicitly enables the Transformers KV cache. On the
local RTX 4070 Ti, a controlled 15-page comparison reduced recognition from
419.04 seconds (27.94 seconds/page) to 67.53 seconds (4.50 seconds/page), while
also avoiding incomplete text caused by the 60-second per-batch ceiling. Normal
production runs completed the 15- and 30-page fixtures in 58.73 and 112.34
seconds respectively (3.92 and 3.75 seconds/page).

Keep `OCR_USE_KV_CACHE=1` unless diagnosing a backend compatibility problem.
Increasing crop batches from four to eight did not help the representative page.
FlashAttention2 2.8.3.post1 was also tested with identical output and VRAM use,
but its three-run median was 12.22 seconds versus SDPA's 9.64 seconds, so SDPA
remains the default.

The current single-document path intentionally keeps PDF rendering, isolated
layout, and recognition sequential. On the 15-page fixture, rendering took 1.85
seconds and layout took 6.79 seconds; nearly all layout time was one-time model
startup, since a one-page layout took 6.65 seconds. A persistent cross-runtime
pipeline could therefore save only about the rendering time for this workload
while adding Paddle/PyTorch GPU contention and recovery complexity. A separate
vLLM service is likewise reserved for a future concurrent-throughput workload,
not the current serialized single-GPU queue.

## Run

```bash
# Verify Python, CUDA, model, and imports
bash ocr/ocr_start.sh check

# Background service
bash ocr/ocr_start.sh start

# Foreground service
bash ocr/ocr_start.sh --fg

# Health / stop
bash ocr/ocr_start.sh status
bash ocr/ocr_start.sh stop
```

Direct module execution:

```bash
/home/user/miniforge3/envs/mcp-local-ocr/bin/python -m ocr.ocr_server \
  --model /path/to/PaddleOCR-VL-1.6 \
  --host 127.0.0.1 \
  --port 8002
```

## Client registration

OpenCode:

```json
{
  "mcp": {
    "ocr": {
      "type": "local",
      "command": [
        "/home/user/miniforge3/envs/mcp-local-ocr/bin/python",
        "/path/to/mcp-tools/ocr/ocr_mcp_server.py"
      ],
      "enabled": true,
      "timeout": 1800000
    }
  }
}
```

Codex:

```toml
[mcp_servers.ocr]
command = "/home/user/miniforge3/envs/mcp-local-ocr/bin/python"
args = ["/path/to/mcp-tools/ocr/ocr_mcp_server.py"]
```

## REST endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Model, device, and GPU state |
| `GET /v1/models` | Loaded model |
| `POST /v1/ocr/parse` | Synchronous compatibility endpoint |
| `POST /v1/ocr/submit` | Durable job submission |
| `GET /v1/ocr/jobs/{job_id}` | Status/progress |
| `GET /v1/ocr/jobs/{job_id}/result` | Artifact-only terminal result |
| `GET /docs` | FastAPI schema UI |

## Formats and scheduling

Supported inputs: PNG, JPG/JPEG, BMP, TIFF, WEBP, and PDF.

- Images become one job chunk.
- PDFs are staged in ordered chunks of at most 24 pages.
- One non-daemon worker owns GPU inference; concurrent submissions queue FIFO.
- A scheduler lease prevents two processes from owning the same job root.
- The background launcher serializes concurrent auto-start attempts before writing its PID file.
- Completed chunks are SHA-256 verified and reusable after restart.
- OCR auto-start stops the local Qwen3-ASR backend to avoid shared-GPU contention.

For born-digital PDFs, use `pdf_to_text` first. Use OCR for scans, handwriting, missing text, or structure/formulas that plain extraction loses.

## Verification

```bash
PYTHONNOUSERSITE=1 /home/user/miniforge3/envs/mcp-local-ocr/bin/python \
  -m unittest discover -s test/ocr -p 'test_*.py'
```

The optional benchmark harness is test-only and runs from the repository root:

```bash
conda run -n mcp-local-ocr python -m test.ocr.benchmark.cli \
  mcp-tool-test/ocr/pdf/attention_is_all_you_need.pdf \
  --pages 1-4 --pages-per-job 1 --concurrency 1 --repetitions 1
```

Local fixtures are under `mcp-tool-test/ocr/` and `mcp-tool-test/smoke-test/`. The current PaddleOCR-VL migration and staged PDF results are documented in [`docs/ocr-test-report.md`](../docs/ocr-test-report.md).

## Runtime isolation

`mcp-local-ocr` contains both the CUDA 13 PyTorch recognizer and CUDA 12.6
PaddlePaddle layout dependencies. Runtime isolation remains process-based:
the resident server imports PyTorch/Transformers, while the short-lived layout
subprocess imports PaddlePaddle/PaddleX; PaddleX optional dependencies may also
import Torch transitively. The two stacks share some NVIDIA package paths, so
their pinned versions and the Paddle-first, PyTorch-last installation order are
part of the supported runtime. Use `bash install.sh --ocr-only` to reproduce
that order.

Official references:

- [PaddleOCR-VL-1.6 model card](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)
- [PaddleOCR-VL pipeline documentation](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
