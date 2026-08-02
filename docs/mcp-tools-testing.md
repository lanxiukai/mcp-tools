# MCP Tools Usage and Testing Guide

This document integrates the API descriptions, usage instructions, and corresponding test file paths for the GPU MCP tools (Qwen3-ASR, generic OCR with PaddleOCR-VL, and Vision Local) and the standalone ASR Pipeline CLI. The complete timestamped ASR Pipeline is also exposed through the `transcribe_diarized` MCP tool. The root `test/` directory contains the repository's OCR, ASR, ASR Pipeline, Browser Fetch, and Vision Local test suites.

> For detailed format specifications of each tool, see the corresponding sub-project README:
> - [`asr/README.md`](../asr/README.md) — ASR audio formats, language support, chunking mechanism
> - [`ocr/README.md`](../ocr/README.md) — OCR image/PDF formats, output formats, formula handling
> - [`vision-local/README.md`](../vision-local/README.md) — local CUDA vision tools and resumable batch artifacts
> - [`asr-pipeline/README.md`](../asr-pipeline/README.md) — Pipeline stages, output formats, speaker diarization

---

## Table of Contents

- [1. Qwen3-ASR — Speech-to-Text](#1-qwen3-asr--speech-to-text)
- [2. OCR — Document Parsing](#2-ocr--document-parsing)
- [3. ASR Pipeline — Long Audio Transcription Pipeline (Optional)](#3-asr-pipeline--long-audio-transcription-pipeline-optional)
- [4. Smoke Tests](#4-smoke-tests)
- [5. Test Sample Directory](#5-test-sample-directory)

---

## 1. Qwen3-ASR — Speech-to-Text

### 1.1 Feature Overview

Transcribes audio files to text, supporting **52 languages** with automatic language detection. Model: Qwen3-ASR-1.7B, VRAM ~3.5 GB.

### 1.2 MCP Interface

| Call | Parameters | Return Value |
|------|------|--------|
| `transcribe_audio(file_path, language?)` | `file_path`: absolute audio path; `language`: optional `"en"` / `"zh"` etc. | `{"text": "...", "language": "zh"}` |
| `transcribe_diarized(file_path, language?, num_speakers?, context?)` | Full timestamped Pipeline; `num_speakers` is an optional exact count | Full text plus `segments[].{speaker,start,end,text,words}` |
| `transcribe_podcast(file_path, language?, num_speakers?)` | REST transcript plus optional pyannote timeline | Full text and separate speaker timeline; no speaker/text attribution |
| `asr_status()` | None | Service status (model name, GPU VRAM) |

**Parameter details**:
- `file_path` (required): Absolute path to local audio file. Supports WAV / MP3 / FLAC / OGG / M4A, etc.
- `language` (optional): Language hint. Both 2-letter ISO-639-1 codes (`"en"`, `"zh"`, `"ja"`, `"ko"`, ...) and full names (`"English"`, `"Chinese"`, `"Japanese"`, `"Korean"`, ...) are accepted — the MCP server normalizes them transparently. Leave empty for automatic language detection.

**Auto-wake**: The REST service starts in the background on first call (up to 60 seconds). The backend auto-releases GPU after 300 seconds of inactivity.

### 1.3 Usage Examples

```python
# English short sentence transcription (auto-detect language)
transcribe_audio("/home/user/interview.mp3")

# Chinese audio with explicit language specification
transcribe_audio("/home/user/meeting.wav", language="zh")

# Mixed Chinese-English with auto-detection
transcribe_audio("/home/user/mixed_talk.m4a")

# Multi-speaker interview: who said what (requires HF_TOKEN)
transcribe_diarized(
    "/home/user/interview.mp3",
    num_speakers=3,
    context="Qwen pyannote MCP",
)

# Faster full transcript plus a separate speaker timeline
transcribe_podcast("/home/user/podcast.mp3", num_speakers=3)

# Check service status
asr_status()
```

`transcribe_diarized` always enables word timestamps and is the MCP entry point
for speaker-attributed text. Omit `num_speakers` only when the exact count is
unknown, and treat automatic speaker counts as provisional. `transcribe_podcast`
does not map text onto its speaker timeline.

### 1.4 Test Files

| Scenario | Test File | Size | Content |
|------|----------|------|------|
| Smoke test | `mcp-tool-test/smoke-test/asr_smoke_test.wav` | 327 KB | 6-second English spoken short sentence, 22050 Hz mono |
| English podcast (single speaker) | `mcp-tool-test/asr/podcast/en_single/greatinventors_01_watt_steam.mp3` | 10.8 MB | James Watt and the steam engine (~24 min) |
| English speech | `mcp-tool-test/asr/podcast/en_dialogue/JFK_inaugural_address.mp3` | 11.1 MB | JFK inaugural address (~14 min) |
| Chinese-English daily (single speaker) | `mcp-tool-test/asr/daily/zh_en_single/*.wav` | Total 2.1 MB | CS-Dialogue short clips (1-5 sec) |
| Chinese-English podcast (multi-speaker) | `mcp-tool-test/asr/podcast/zh_en_dialogue/*.wav` | Total 1.8 MB | CS-Dialogue different speakers |

> **Expected pass criteria**: Smoke test returns `"The examination and testimony of the experts enabled the Commission to conclude that five shots may have been fired."`

---

## 2. OCR — Document Parsing

### 2.1 Feature Overview

Parses images/PDFs into Markdown with Chinese/English, handwriting, formulas, and tables. The stable tools are model-independent; the current backend is PaddleOCR-VL-1.6 0.9B. Tools return only job metadata and ordered Markdown artifact paths.

Current handwriting validation uses only the upright real samples and recorded references under `mcp-tool-test/ocr/handwriting/real/20260722/`. The synthetic `handwriting/generated/` directory is excluded.

### 2.2 MCP Interface

| Call | Parameters | Return Value |
|------|------|--------|
| `ocr_document(file_path)` | `file_path`: absolute document path | Artifact metadata: `{job_id, status, page_count, artifacts: [{chunk_index, source_pages, path, sha256}, ...]}` |
| `ocr_submit(file_path)` | `file_path`: absolute document path | Queue acknowledgement with `status: "queued"` |
| `ocr_wait(job_id, max_wait?)` | Job ID and optional wait seconds | Terminal artifact metadata |
| `ocr_status(job_id?)` | Optional job ID | Health without an ID; progress with an ID |

**Parameter details**:
- `file_path` (required): Absolute path to local image/PDF. Supports PNG / JPG / JPEG / BMP / TIFF / WEBP / PDF.
- No `output_format` or `save_markdown` parameters — the MCP frontend returns artifact metadata only. Read the `.md` files at the artifact paths for Markdown content.

**Auto-wake**: The REST service starts in the background on first call. OCR auto-start stops Qwen3-ASR to enforce GPU mutual exclusion. The backend releases the GPU after `OCR_IDLE_TIMEOUT` seconds of inactivity (default 30).

### 2.3 Usage Examples

```python
# Image → artifact metadata
ocr_document("/home/user/scan.jpg")

# PDF → artifact metadata (single worker, 24-page chunks)
ocr_document("/home/user/report.pdf")

# Handwriting → artifact metadata
ocr_document("/home/user/whiteboard.png")

# Submit only, poll separately (for work exceeding 30 min MCP transport ceiling)
result = ocr_submit("/home/user/large.pdf")
ocr_status(result["job_id"])                        # Check progress without blocking
# Poll with bounded waits under transport deadline; repeat if not yet terminal
ocr_wait(result["job_id"], max_wait=900)            # 15 min per call, under 30 min ceiling

# Check service status
ocr_status()
```

### 2.4 Test Files

| Scenario | Test File | Size | Content |
|------|----------|------|------|
| Smoke test | `mcp-tool-test/smoke-test/ocr_smoke_test.png` | 15 KB | Math formula image (contains 5 formulas including f(x)=x²+2x+1) |
| English printed | `mcp-tool-test/ocr/printed/en/us_constitution_page1.png` | 1.9 MB | US Constitution first page |
| Chinese printed | `mcp-tool-test/ocr/printed/zh/taipei_taxi_fare.jpg` | 1.0 MB | Modern horizontal Chinese fare table |
| Formula printed | `mcp-tool-test/ocr/printed/formulas/pure_math_blackboard.jpg` | 167 KB | Algebra/calculus formulas on blackboard |
| English handwriting | `mcp-tool-test/ocr/handwriting/en/willa_cather_letter.png` | 339 KB | 1905 cursive handwritten letter |
| Chinese calligraphy | `mcp-tool-test/ocr/handwriting/zh/boyuan_calligraphy.jpg` | 3.6 MB | Wang Xun's "Boyuan Tie" running script |
| Formula handwriting | `mcp-tool-test/ocr/handwriting/formulas/einstein_blackboard.jpg` | 846 KB | Einstein cosmology formulas on blackboard |
| Scanned PDF | `mcp-tool-test/ocr/pdf/scanned_chinese_yuzhidaao.pdf` | 2.5 MB | Qing dynasty woodblock print (96 pages, vertical Chinese) |
| Formula PDF | `mcp-tool-test/ocr/pdf/scanned_formulas_trigonometry.pdf` | 2.0 MB | Trigonometry textbook (1896, 135 pages) |

> **Expected pass criteria**: Smoke test returns artifact metadata (`job_id`, `status: "completed"`, `page_count: 1`, `artifacts` with a valid `.md` file path). The artifact file at the returned path contains structured Markdown with the heading "OCR Smoke Test - Math Formulas" and LaTeX representations of all math formulas (superscripts, fractions, etc.).

---

## 3. ASR Pipeline — Long Audio Transcription Pipeline (Optional)

### 3.1 Feature Overview

An offline batch processing CLI tool providing a **four-stage pipeline** and **speaker diarization** capability. Supports both long audio (via `--no-timestamps` fast mode for 2-3 hour podcasts) and word-level timestamp generation.

### 3.2 CLI Interface

```bash
conda run -n mcp-local-asr python asr-pipeline/pipeline.py <audio_file> [options]
```

| Parameter | Type | Description |
|------|------|------|
| `audio_file` | Required | Audio file path |
| `--language` / `-l` | Optional | Language: `English` / `Chinese`, auto-detect if omitted |
| `--output-dir` / `-o` | Optional | Output directory (default `./output/`) |
| `--context` / `-c` | Optional | Term injection, e.g. `--context "AI deep learning"` |
| `--format` / `-f` | Optional | Output format: `json` / `srt` / `txt` / `all` (default `all`) |
| `--no-diarize` | Optional | Skip speaker diarization |
| `--no-timestamps` | Optional | **Recommended for long audio**: skip word-level timestamps, 4×+ speedup; full text and speaker timeline remain separate |
| `--num-speakers` | Optional | Exact expected number of speakers; omit for automatic detection |
| `--max-new-tokens` | Optional | Generation token limit (default 4096, recommended 4096-8192 for long audio) |
| `--batch-size` | Optional | Inference batch size (default 1, can set to 2 for ≥16GB VRAM) |

**Pipeline stages**:
1. `preprocess.py` — ffmpeg transcoding to 16kHz mono WAV
2. `diarize.py` — pyannote.audio speaker diarization (requires `HF_TOKEN`)
3. `transcribe.py` — Qwen3-ASR transcription (use `--no-timestamps` to skip timestamp alignment for speed)
4. `merge.py` — Merge results, output JSON/SRT/TXT

> **Note**: Pipeline is a standalone CLI tool that loads models directly for inference — it does **not** require starting the ASR REST backend service.

> In `--no-timestamps` mode, TXT contains the complete unlabelled transcript
> and JSON stores the complete text separately from any diarization timeline.
> Run without that flag to validate speaker-attributed text.

### 3.3 Usage Examples

```bash
# English podcast (full pipeline with speaker diarization)
conda run -n mcp-local-asr python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./output/

# Long audio acceleration (recommended for 1h+): skip word-level timestamps
conda run -n mcp-local-asr python asr-pipeline/pipeline.py long_podcast.mp3 --language English --no-timestamps -o ./output/

# Chinese podcast + term injection
conda run -n mcp-local-asr python asr-pipeline/pipeline.py interview.mp3 --language Chinese \
  --context "AI deep learning large language models" -o ./output/

# Single-speaker lecture (skip diarization, faster)
conda run -n mcp-local-asr python asr-pipeline/pipeline.py lecture.wav --language English --no-diarize --no-timestamps -o ./output/

# Output JSON only
conda run -n mcp-local-asr python asr-pipeline/pipeline.py audio.mp3 --language English -f json -o ./output/
```

### 3.4 Output Artifacts

| Format | File | Content |
|------|------|------|
| JSON | `{basename}.json` | Structured data (metadata + segments + full_text) |
| SRT | `{basename}.srt` | Subtitle file (importable into video editors) |
| TXT | `{basename}.txt` | Plain text transcription |

### 3.5 Test Files

| Scenario | Test File | Size | Content |
|------|----------|------|------|
| Smoke test | `mcp-tool-test/smoke-test/pipeline_smoke_test.mp3` | 3.5 MB | Booker T. Washington speech (~3.5 min) |

```bash
# Smoke test command
conda run -n mcp-local-asr python asr-pipeline/pipeline.py \
  mcp-tool-test/smoke-test/pipeline_smoke_test.mp3 \
  --language English --no-diarize -o /tmp/pipeline_test/
```

> **Prerequisites**: Pipeline runs independently, no ASR REST service needed. Speaker diarization requires the `HF_TOKEN` environment variable and pyannote access — see [`docs/pyannote-setup.md`](pyannote-setup.md).

---

## 4. Smoke Tests

The `mcp-tool-test/smoke-test/` directory provides three minimal ASR/OCR fixtures. Vision Local adds a separate public seven-image suite under `mcp-tool-test/vision-local/`, covering portraits with and without glasses, a natural object photo, a chart, and formula text.

| File | Size | Tool | Expected Result |
|------|------|------|----------|
| `ocr_smoke_test.png` | 15 KB | Generic OCR / PaddleOCR-VL | Returns artifact metadata; read Markdown from the returned artifact path |
| `asr_smoke_test.wav` | 327 KB | Qwen3-ASR | Returns 6-second English short sentence transcription |
| `pipeline_smoke_test.mp3` | 3.5 MB | ASR Pipeline | Generates JSON/SRT/TXT artifacts |
| `../vision-local/samples/*` | varies | Vision Local | Four correct eyewear labels plus non-empty general/chart/text results |

```bash
# Smoke test one-liner approach (requires corresponding backends running)
# OCR
ocr_document("mcp-tool-test/smoke-test/ocr_smoke_test.png")  # Returns artifact metadata; read .md at artifact path

# ASR
transcribe_audio("mcp-tool-test/smoke-test/asr_smoke_test.wav")

# Pipeline (recommend adding --no-timestamps for speed)
conda run -n mcp-local-asr python asr-pipeline/pipeline.py mcp-tool-test/smoke-test/pipeline_smoke_test.mp3 \
  --language English --no-diarize --no-timestamps -o /tmp/pipeline_test/

# Vision Local (real stdio MCP calls; output path must be new)
conda run -n mcp-local python test/vision_local/smoke_mcp.py \
  --output mcp-tool-test/vision-local/smoke-results.json
```

---

## 5. Test Sample Directory

Public test fixtures and generated local verification artifacts live under
`mcp-tool-test/`; its file count and size change as new result artifacts are
produced. Executable repository test suites live separately under the root
`test/` directory (`test/ocr`, `test/asr`, `test/asr_pipeline`,
`test/browser_fetch`, `test/vision_local`).

```
mcp-tool-test/
├── README.md                  # Detailed sample directory description
├── smoke-test/                # Three minimal ASR/OCR/Pipeline fixtures
├── vision-local/              # Public local-vision samples and generated smoke/batch artifacts
├── format-conversion/         # Local conversion inputs and generated outputs
├── ocr/                       # OCR images, PDFs, source manifests, and local helpers
│   ├── printed/               #   English, Chinese, and formula samples
│   ├── handwriting/           #   Legacy, generated, and current real samples
│   │   └── real/20260722/     #   Ten genuine samples used by current validation
│   └── pdf/                   #   Born-digital and scanned documents
└── asr/                       # ASR audio fixtures
    ├── daily/zh_en_single/    #   Chinese-English daily single-speaker
    ├── daily/zh_en_dialogue/  #   Chinese-English daily multi-speaker
    └── podcast/               #   Podcast scenarios
        ├── en_single/         #     English single-speaker
        ├── en_dialogue/       #     English long-form speech
        ├── zh_en_single/      #     Chinese-English single-speaker
        └── zh_en_dialogue/    #     Chinese-English multi-speaker
```

Samples come from public/free-license sources with varying terms, including
Public Domain, CC0, CC-BY, CC-BY-SA, and CC-BY-NC-SA. See
[`mcp-tool-test/README.md`](../mcp-tool-test/README.md) and the per-suite source
manifests for attribution and reuse requirements.
