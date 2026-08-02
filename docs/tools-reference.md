# Tools Reference Manual

This document lists the APIs, configuration, model descriptions, and performance data for six MCP services plus the standalone ASR Pipeline CLI. For usage instructions, test files, and smoke tests, see [`docs/mcp-tools-testing.md`](mcp-tools-testing.md). For per-tool deep dives (formats, troubleshooting, internals), see each sub-project's README.

---

## 1. Qwen3-ASR — Speech-to-Text

Call `transcribe_audio()` to transcribe audio files to text, supporting 52 languages. Short audio responds in seconds; long audio (2h+) is automatically handled via 480s chunking + GPU acceleration.

```python
# Agent direct call
transcribe_audio("/home/user/interview.mp3")               # Auto language detection
transcribe_audio("/home/user/meeting.wav", language="zh")  # Specify Chinese
transcribe_audio("/home/user/long_podcast.mp3", language="en")  # Long audio also supported

# Podcast mode: transcription + speaker diarization (requires HF_TOKEN)
transcribe_podcast("/home/user/podcast.mp3", language="en", num_speakers=3)

# Full pipeline: speaker-attributed text + word timestamps (requires HF_TOKEN)
transcribe_diarized(
    "/home/user/interview.mp3",
    num_speakers=3,  # exact expected count; omit for provisional auto-detection
    context="Qwen pyannote MCP",
)

asr_status()                                                # Check service status
```

> The `language` parameter accepts both 2-letter ISO-639-1 codes (`"en"`, `"zh"`, `"ja"`, `"ko"`, ...) and full names (`"English"`, `"Chinese"`, `"Japanese"`, `"Korean"`, ...). The MCP server normalizes them transparently — internally `qwen_asr` only knows the full names.
> Specify the language for clearly monolingual audio to avoid automatic-language generation long tails. Omit it for Mandarin-English code-switching audio.

> `transcribe_diarized` runs the complete offline pipeline and returns
> speaker-attributed text in `segments[].text` with word timestamps. It stops
> the resident REST backend before loading the offline model copy on a 12 GB
> GPU; later `transcribe_audio` calls auto-start the backend again.
>
> `transcribe_podcast` returns the full transcript and a separate speaker
> timeline. Because the REST backend has no word timestamps, it returns
> `speaker_text_attribution: false`; use `transcribe_diarized` for “who said
> what.” Missing or failed diarization is
> reported through `diarization_status` and `diarization_error` instead of a
> silent zero-speaker result. `num_speakers` is an exact expected count.

**Model**: Qwen3-ASR-1.7B (~3.4 GB). Source precedence: explicit `--model` → complete repository-local `models/safetensors/Qwen/Qwen3-ASR-1.7B` → Hugging Face fallback. The local directory is selected only when its `config.json`, `model.safetensors.index.json` with valid `weight_map`, and every indexed shard are present and non-empty.

**opencode.jsonc configuration**:
```jsonc
"asr": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/asr/asr_mcp_server.py"],
  "enabled": true,
  "timeout": 10800000
}
```

---

## 2. OCR — Replaceable Local Document Recognition

Call `ocr_document()` to submit images or PDFs to the durable serialized job queue. The stable MCP layer is model-independent; the current backend is local `PaddlePaddle/PaddleOCR-VL-1.6`. Tools return job metadata and ordered Markdown artifact paths, which agents read separately.

```python
ocr_document("/home/user/report.pdf")                 # Submit + wait
ocr_document("/home/user/whiteboard.png")             # Same for images

# Async mode for long documents
result = ocr_submit("/home/user/large.pdf")            # Immediate queue acknowledgement
ocr_status(job_id=result["job_id"])                   # Non-blocking progress
ocr_wait(result["job_id"], max_wait=900)              # Bounded wait

ocr_status()                                           # Model/GPU/queue health
```

Artifact return shape (completed job):

```json
{
  "job_id": "a1b2c3d4...",
  "status": "completed",
  "page_count": 27,
  "artifacts": [
    {"chunk_index": 1, "source_pages": [1,...,24], "path": "/.../chunk-001.md", "sha256": "abc123..."},
    {"chunk_index": 2, "source_pages": [25,26,27], "path": "/.../chunk-002.md", "sha256": "def456..."}
  ]
}
```

**Models**: PP-DocLayoutV3 detects ordered page elements in a short-lived isolated process; PaddleOCR-VL-1.6 0.9B recognizes the crops, preferring `~/project/hf-models/models/safetensors/PaddlePaddle/PaddleOCR-VL-1.6`. On the reference RTX 4070 Ti, the recognizer process used about 3.9 GiB after inference, about 6.2 GiB during a four-crop handwriting batch, and up to about 7.7 GiB on dense PDFs. Measured GPU utilization was 50–86% for the handwriting batch and 92–96% for dense PDF recognition.

**Architecture**: The MCP/REST boundary, durable scheduler, and model adapter are separate. A single worker owns recognition inference. The adapter runs isolated layout detection once per job chunk, releases that process, then recognizes ordered element crops in batches of four. PDFs are split into 24-page chunks; images and PDFs up to 24 pages produce one artifact. Artifacts live under `OCR_JOB_ROOT` (default `$XDG_STATE_HOME/ocr/jobs`) and are retained for `OCR_JOB_TTL_SECONDS` (default 3600 s). Restart reuses digest-verified completed chunks.

The scheduler holds a root-level advisory lock (`.scheduler.lock`). Expired completed jobs are purged at startup and every 60 seconds. Background startup also uses a PID-scoped advisory lock so simultaneous MCP clients cannot race to launch duplicate backends. `ocr_wait` can initiate a non-blocking backend restart and resume the same durable job ID. OCR auto-start stops Qwen3-ASR to enforce shared-GPU exclusion. The frontend connects to `OCR_HOST` (default `127.0.0.1`) and `OCR_PORT` (default `8002`).

**opencode.jsonc configuration**:
```jsonc
"ocr": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/ocr/ocr_mcp_server.py"],
  "enabled": true,
  "timeout": 1800000
}
```

> **MCP transport ceiling**: The 30-minute timeout (`1800000` ms) is per call. For work that may exceed it, call `ocr_submit()` and then repeat `ocr_wait(max_wait=900)` / `ocr_status()`. Each invocation receives a fresh transport window. Use the current measured report rather than historical GLM throughput estimates: [`ocr-test-report.md`](ocr-test-report.md).

---

## 3. ASR Pipeline — Podcast Long Audio Transcription

An offline batch processing CLI tool that transcribes 2-3 hour podcast audio into structured text with **speaker annotations** and **word-level timestamps**. Built-in 480s chunking strategy ensures stable operation on 12GB VRAM.

```bash
# Basic usage
conda run -n mcp-local-asr python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./output/

# Long audio acceleration (recommended for 1h+): skip word-level timestamps, 4×+ speedup
conda run -n mcp-local-asr python asr-pipeline/pipeline.py long_podcast.mp3 --language English --no-timestamps -o ./output/

# Multi-speaker conversation + exact expected speaker count
conda run -n mcp-local-asr python asr-pipeline/pipeline.py meeting.mp3 --language English --num-speakers 3 -o ./output/

# Chinese podcast + term injection
conda run -n mcp-local-asr python asr-pipeline/pipeline.py interview.mp3 --language Chinese --context "AI deep learning" -o ./output/

# Skip speaker diarization
conda run -n mcp-local-asr python asr-pipeline/pipeline.py lecture.wav --no-diarize

# Custom token budget (recommended 4096 for 2h+ audio)
conda run -n mcp-local-asr python asr-pipeline/pipeline.py podcast.mp3 --language English --max-new-tokens 4096

# Output format selection
conda run -n mcp-local-asr python asr-pipeline/pipeline.py audio.mp3 --format json  # json/srt/txt/all
```

### Key Parameters

| Parameter | Default | Description |
|---|---|---|
| `--no-timestamps` | off | Skip word-level timestamps; full text and speaker timeline remain separate |
| `--no-diarize` | off | Skip speaker diarization (faster for single-speaker content) |
| `--num-speakers` N | auto | Exact expected number of speakers |
| `--max-new-tokens` | 4096 | Generation token limit, recommended 4096-8192 for long audio |
| `--batch-size` | 1 | Inference batch size, can set to 2 for ≥16GB VRAM |

### Measured Performance (RTX 4070 Ti 12GB)

| Scenario | Processing Time | Throughput |
|---|---|---|
| 22 min speech (with diarization) | ~4 min | 5.7× |
| 2 hour podcast (with diarization, 1002 segments) | ~23 min | 5.2× |
| 2 hour podcast (without diarization) | ~19 min | 5.9× |

**Outputs**: JSON (metadata + segments + full_text), SRT (subtitles), TXT (plain text)

**Speaker diarization** requires pyannote.audio access:
1. Accept model terms at [hf.co/pyannote](https://hf.co/pyannote)
2. Set the `HF_TOKEN` environment variable

---

## 4. Format Conversion — Document Format Conversion

A pure CPU toolset providing Markdown/HTML → PDF and PDF → plain text.

### HTML → PDF

```python
html_to_pdf("/home/user/doc.html")                      # Default engine="chromium"
html_to_pdf("/home/user/doc.html", engine="weasyprint")  # Lightweight backend (for simple documents)
html_to_pdf("/home/user/doc.html", engine="chromium")    # Pixel-identical to Chrome
```

**Engine comparison**:

| Dimension | WeasyPrint | Chromium |
|---|---|---|
| flex/grid layout | Partial support (not aligned with Chrome) | Fully identical |
| Page numbers | CSS `@page @bottom-center` | CSS `@page @bottom-center` (Chrome 131+) |
| Dependencies | cairo/pango (~30 MB) | Playwright + Chromium (~300 MB) |
| Cold start | ~200 ms | ~1-2 s |
| Use case | Simple documents, Paged Media | Modern web layouts, flex/grid visual fidelity |

### Markdown → PDF

```python
markdown_to_pdf("/home/user/doc.md")                     # markdown-it-py + Chromium (default in MCP tool)
markdown_to_pdf("/home/user/doc.md", engine="weasyprint")  # Lightweight backend (for simple documents)
```

LaTeX is pre-rendered through the repository-local, lockfile-pinned MathJax v4
runtime for both PDF engines. Font discovery uses fontconfig-aware Noto CJK and
emoji fallbacks; run `bash install.sh --cpu-only` to provision these shared
runtime dependencies.

### PDF → Text

```python
pdf_to_text("/home/user/report.pdf")           # Default: auto-saves .txt alongside source
pdf_to_text("/home/user/report.pdf", save_text=False)  # Return text only, no file saved
```

### opencode.jsonc Configuration

```jsonc
"format_conversion": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/format-conversion/format_mcp_server.py"],
  "enabled": true,
  "timeout": 120000
}
```

---

## 5. Vision Local — Image Analysis and Batch Classification

A model-neutral local GPU service backed by a persistent llama.cpp server. The default Q4 vision checkpoint and BF16 projector fit the RTX 4070 Ti 12 GB budget; the service name and environment variables do not expose the model name, so the backend can be replaced later.

```python
# General image analysis
analyze_image("/home/user/screenshot.png")
analyze_image("/home/user/diagram.jpg", prompt="Describe the components and data flow in this architecture diagram")

# Text extraction (OCR-style)
extract_text_from_image("/home/user/slide.png")

# Chart analysis
analyze_chart("/home/user/sales-chart.png")

# Schema-constrained portrait classification
classify_eyewear("/home/user/portrait.png")
verify_eyewear("/home/user/subtle-rimless-frames.png")

# Submit and inspect a resumable directory audit
classify_eyewear_batch("/data/G", "/data/NG", "/new/output", concurrency=4)
eyewear_batch_status("/new/output")
```

**Runtime**: UD-Q4_K_XL GGUF + BF16 vision projectors on CUDA llama.cpp backends. Interactive, OCR, chart, single-image classification, and high-resolution verification tools default to Qwen3.5-9B on port 8003. `classify_eyewear_batch` automatically uses Qwen3.5-4B on port 8004. See [`vision-local/README.md`](../vision-local/README.md) for fixed revisions and provisioning commands.

**Environment variables**:

| Variable | Required | Default | Description |
|---|---|---|---|
| `VISION_LOCAL_MODEL_PATH` | | sibling `hf-models` Q4 path | Replaceable model weights |
| `VISION_LOCAL_MMPROJ_PATH` | | sibling `hf-models` BF16 path | Replaceable vision projector |
| `VISION_LOCAL_PARALLEL` | | `4` | Continuous-batching slots |
| `VISION_LOCAL_IMAGE_MAX_TOKENS` | | `1024` | Cap supporting 512-pixel fast and 1024-pixel verification passes |
| `VISION_LOCAL_BATCH_MODEL_PATH` | | sibling 4B Q4 path | Batch-only model weights |
| `VISION_LOCAL_BATCH_MMPROJ_PATH` | | sibling 4B BF16 path | Batch-only vision projector |
| `VISION_LOCAL_BATCH_PORT` | | `8004` | Independent batch backend port |
| `VISION_LOCAL_BATCH_CONTEXT_SIZE` | | `4096` | Batch context shared by four slots |
| `VISION_LOCAL_BATCH_IMAGE_MAX_TOKENS` | | `512` | Batch image-token cap |

**opencode.jsonc configuration**:

```jsonc
"vision_local": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/vision-local/vision_local_mcp_server.py"],
  "enabled": true,
  "timeout": 600000
}
```

> `<YOUR-PYTHON>` should be the existing `mcp-local` interpreter, which provides FastMCP and Pillow. The first call to a profile performs its model cold start; later calls reuse that profile's backend.

**Agent permissions**:

```jsonc
"analyze_image": "allow",
"extract_text_from_image": "allow",
"analyze_chart": "allow",
"vision_status": "allow",
"classify_eyewear": "allow",
"verify_eyewear": "allow",
"classify_eyewear_batch": "allow",
"eyewear_batch_status": "allow"
```

**Use cases**:
- Screenshot understanding (UI layout/text/issue diagnosis)
- Chart data extraction (bar/line/pie charts → structured analysis)
- Document/slide text extraction
- Single-portrait eyewear classification
- Two-stage eyewear audits: 512-pixel coarse scan, then 1024-pixel candidate verification with visible cues
- Large labeled-directory audits with resumable JSONL and JSON/CSV review artifacts
- Providing visual context for text-only models

> Use `ocr_document` for PDFs. Vision Local currently accepts raster images; this keeps its hot path focused on low-latency visual analysis and avoids duplicating the staged PDF/OCR pipeline.

---

## 6. Browser Fetch — Anti-Bot Web Page Fetching

Renders web pages in a real Chrome browser (via nodriver / Playwright) and returns clean Markdown. Solves the gap left by plain `webfetch` for JavaScript-heavy SPAs, Cloudflare-protected pages, and sites with bot detection.

```python
# Default — fetch a public page as Markdown (auto engine: nodriver -> playwright fallback)
fetch_page("https://example.com")
# → {"content": "# Example Domain\n\n...", "mode": "markdown",
#    "title": "Example Domain", "engine": "nodriver",
#    "final_url": "https://example.com/", "html_size": 1256, ...}

# Cloudflare-protected page (extra wait for challenge to resolve)
fetch_page("https://www.somesite.com/article", wait_seconds=4)

# Login-walled site (Upwork freelancer profile)
fetch_page("https://www.upwork.com/freelancers/~01253f14599071aeb2",
           cookies_path="/home/me/.config/upwork-cookies.json",
           proxy_url="http://user:pass@residential-proxy.example.com:7777",
           wait_seconds=4, timeout=60)

# Get raw rendered HTML
fetch_page("https://news.ycombinator.com", mode="html")

# Force a specific engine (debugging)
fetch_page_with_engine("https://example.com", engine="playwright")

# Screenshot (Playwright engine)
screenshot("https://example.com", output_path="/tmp/example.png", full_page=True)

# Health check — see which engines are installed
browser_status()
```

**Engines**:
- **nodriver** (primary, AGPL-3.0): drives Chrome via raw CDP, no Playwright protocol artifacts. Best stealth in 2026 anti-detect benchmarks; bypasses Cloudflare Bot Management.
- **Playwright** (fallback, Apache-2.0): Chromium with stealth args + init script. Used when nodriver fails or for the `screenshot` tool.

**Output modes** (`mode=` param): `markdown` (default, trafilatura main-content extraction) / `markdown_full` (markdownify full-page) / `html` (raw rendered) / `text` (plain text via trafilatura).

**Key parameters** (all tools):

| Param | Default | Purpose |
|---|---|---|
| `timeout` | 30 | Per-page timeout (seconds) |
| `wait_until` | `"networkidle"` | Playwright load-complete signal |
| `wait_seconds` | 1.5 | Extra sleep for SPA hydration / Cloudflare challenge |
| `headless` | `true` | Set false + use Xvfb for max stealth |
| `cookies_path` | `""` | JSON cookie file from a logged-in browser session |
| `proxy_url` | `""` | e.g. `http://user:pass@host:port` (residential for Upwork-class sites) |
| `user_agent` | Chrome 131 UA | Override default |

**Site-specific**: Sites like Upwork combine three layers — Cloudflare Bot Management (nodriver bypasses), datacenter IP blocking (need `proxy_url`), and login walls (need `cookies_path`). All three params are required for full Upwork access. Without them you'll get the public preview only — still a meaningful improvement over plain `webfetch`.

**opencode.jsonc configuration**:

```jsonc
"browser_fetch": {
  "type": "local",
  "command": ["<YOUR-PYTHON>", "<REPO-DIR>/browser-fetch/browser_fetch_mcp_server.py"],
  "enabled": true,
  "timeout": 120000
}
```

`<YOUR-PYTHON>` = `<CONDA-ENV-DIR>/envs/mcp-local/bin/python` (or wherever your `mcp-local` env lives).

**Agent permissions**:

```jsonc
"fetch_page": "allow",
"fetch_page_with_engine": "allow",
"screenshot": "allow",
"browser_status": "allow"
```

**Environment variables**:

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_FETCH_TIMEOUT` | `30` | Default per-page timeout (seconds) |
| `BROWSER_FETCH_HEADLESS` | `true` | Default headless mode |
| `BROWSER_FETCH_USER_AGENT` | Chrome 131 | Override default UA |
| `BROWSER_FETCH_SCREENSHOT_DIR` | `/tmp/browser-fetch` | Default screenshot output directory |
| `BROWSER_FETCH_LOG_LEVEL` | `INFO` | `INFO` or `DEBUG` |

**Use cases**:
- Reading articles / profiles / SPAs that JS-render after page load
- Bypassing Cloudflare's "Just a moment..." challenge
- Scraping login-walled content (with exported cookies + residential proxy)
- Capturing PNG screenshots of fully-rendered pages

> See [`browser-fetch/README.md`](../browser-fetch/README.md) for the full guide, including cookie handling, Chromium dependency troubleshooting, and the engine-selection rationale.

---

## 7. Brave Websearch — Search API MCP

An external search service exposed through Brave's official MCP package. It
does not use the local GPU. The repository launcher validates Node.js, `npx`,
and `BRAVE_API_KEY`, enables standard proxy-environment support, and starts the
server over stdio.

As verified against the official package documentation on 2026-08-02, the
upstream server provides web, local, place, image, video, news, LLM-context,
and summarizer capabilities. Because `run.sh` launches the current package
through `npx`, use `BRAVE_MCP_ENABLED_TOOLS` or
`BRAVE_MCP_DISABLED_TOOLS` when a stable subset is required.

**opencode.jsonc configuration**:

```jsonc
"brave_websearch": {
  "type": "local",
  "command": ["<REPO-DIR>/brave-websearch/run.sh"],
  "env": {
    "BRAVE_API_KEY": "<BRAVE-API-KEY>"
  },
  "enabled": true,
  "timeout": 30000
}
```

**Requirements**:

- Node.js 22 or newer and `npx`.
- A Brave Search API key supplied through the process environment.
- Optional standard proxy variables such as `HTTPS_PROXY`; the launcher sets
  `NODE_USE_ENV_PROXY=1`.

See the launcher documentation in
[`brave-websearch/run.sh`](../brave-websearch/run.sh) and the
[official Brave Search MCP repository](https://github.com/brave/brave-search-mcp-server)
for the current tool schemas and plan-specific API limits.
