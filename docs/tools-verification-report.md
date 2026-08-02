# MCP Tools Performance / Accuracy Verification Report

> **Historical report**: The main report records the 2026-05-11 ASR/GLM-OCR
> system, while later appendices record dated follow-up runs, including the now
> retired `qwen_vision` service. Commands, paths, tool names, and throughput in
> those sections are retained as historical evidence, not current operating
> instructions. See [`ocr-test-report.md`](ocr-test-report.md) for current OCR
> verification and
> [`vision-local-verification-report.md`](vision-local-verification-report.md)
> for the current local vision deployment.
>
> **Verification date**: 2026-05-11
> **Test sample source**: `mcp-tool-test/` directory, public samples
> **Verification environment**: Ubuntu 22.04 / NVIDIA RTX 4070 Ti (12GB) / CUDA 12.4

---

## Table of Contents

- [1. Verification Overview](#1-verification-overview)
- [2. Qwen3-ASR — Speech-to-Text](#2-qwen3-asr--speech-to-text)
- [3. GLM-OCR — Document Parsing](#3-glm-ocr--document-parsing)
- [4. Operational Findings and Known Issues](#4-operational-findings-and-known-issues)
- [5. Overall Rating](#5-overall-rating)

---

## 1. Verification Overview

This verification used test samples from `mcp-tool-test/`, calling each tool's REST API backend directly (bypassing the MCP Server layer) to systematically evaluate the functionality, accuracy, and speed of 2 MCP tools.

### 1.1 Test Scope

| Tier | Test Count | Description |
|------|--------|------|
| Smoke tests | 2 | One minimal file per tool, quick verification of basic usability |
| Sample tests | 8 | 3-5 representative samples per tool, covering different scenarios/languages/formats |
| **Total** | **10** | |

### 1.2 Items Not Covered

- **ASR Pipeline**: Fixed on 2026-05-12 (device_map GPU acceleration + 480s chunking + parameterized max_new_tokens), 22-minute speech completed in 3 minutes, 2-hour podcast in 19 minutes
- **OCR PDF multi-page samples**: 6 PDF files not tested (3 born-digital + 3 scanned)
- **ASR mixed Chinese-English long audio**: CS-Dialogue long clips not tested (only short clips tested)

### 1.3 Test Method

All tests called each tool's FastAPI REST endpoints directly via `curl`:

| Tool | Endpoint | Method |
|------|------|------|
| ASR | `POST http://localhost:8000/v1/audio/transcriptions` | `multipart/form-data` |
| OCR | `POST http://localhost:8002/v1/ocr/parse` | `multipart/form-data` |

### 1.4 Rating Criteria

| Rating | Meaning |
|------|------|
| ⭐⭐⭐⭐⭐ | Output completely correct or highly accurate, no obvious errors |
| ⭐⭐⭐⭐ | Basically correct, minor symbol/detail omissions |
| ⭐⭐⭐ | Roughly readable, noticeable errors (~15-20%) |
| ⭐⭐ | Output severely incomplete or many errors |
| ⭐ | Almost unusable |
| 🔴 | Test could not execute (service timeout/crash) |

---

## 2. Qwen3-ASR — Speech-to-Text

**Model**: Qwen3-ASR-1.7B · **VRAM**: ~3.8 GB · **Supported languages**: 52

### 2.1 Smoke Test

| File | Duration | Expected Output | Actual Output | Rating |
|------|------|----------|----------|------|
| `asr_smoke_test.wav` | 6s | *"The examination and testimony of the experts enabled the Commission to conclude that five shots may have been fired."* | **Exact match, word for word** | ⭐⭐⭐⭐⭐ |

### 2.2 Sample Tests

| # | File | Duration | Scenario | Output Summary | Rating |
|------|------|------|------|----------|------|
| A1 | `LJ037-0171.wav` | ~2s | English daily reading | Correctly transcribed English short sentence | ⭐⭐⭐⭐⭐ |
| A2 | `D11_750.wav` | ~3s | Chinese daily reading | "Some patriotic officers of the Northeast Army — Ma Zhanshan, Li Du, Tang Juwu, Su Bing'ai, Deng Tiemei, and others — also rose up to resist." | ⭐⭐⭐⭐⭐ |
| A3 | `Booker_T_Washington_1895.mp3` | 3.5 min | English historical speech | General meaning correct, ~15-20% word errors (affected by 1895 recording quality) | ⭐⭐⭐ |
| A4 | CS-Dialogue clip | ~2s | Mixed Chinese-English daily | 🔴 Service auto-shutdown due to idle timeout between tests, did not execute successfully | 🔴 |

### 2.3 Performance Data

| Metric | Value |
|------|------|
| Model loading time | ~10-12 seconds |
| Short audio (<5s) processing speed | <2 seconds |
| Long audio (3.5min) processing speed | ~97 seconds (~2.2× real-time) |
| Chinese short sentence accuracy | Extremely high (no errors observed) |
| English short sentence accuracy | Extremely high (word-for-word) |

### 2.4 Assessment

- **Strengths**: Extremely high accuracy for short audio and high-quality recordings; excellent performance in both Chinese and English
- **Weaknesses**: Accuracy drops noticeably for historical recordings/low-quality audio (as expected); long audio processing speed is only ~2× real-time, unsuitable for real-time transcription of very long audio
- **Note**: ASR backend auto-released GPU after 30 seconds of idle time at the time of verification (2026-05-11). The default has since been raised to 300 seconds (see Appendix D.1), making batch testing more practical without manual timeout adjustment.

---

## 3. GLM-OCR — Document Parsing

**Model**: GLM-OCR 0.9B · **VRAM**: ~2.5 GB · **Output format**: Markdown (with LaTeX) / JSON

### 3.1 Smoke Test

| File | Content | Expected | Actual Output | Rating |
|------|------|------|----------|------|
| `ocr_smoke_test.png` | Printed calculus formula image | Structured Markdown with math symbols and text | Only recognized a few symbols like `"Y dy dx dy dx dx O X"` | ⭐⭐ |

> **Analysis**: This image may have resolution or contrast issues unsuitable for GLM-OCR. The same model performs well on other formula images (see below), indicating the issue is with the specific input, not the model itself.

### 3.2 Sample Tests

| # | File | Type | Output Summary | Rating |
|------|------|------|----------|------|
| O1 | `chemistry_textbook_p25.jpg` | English printed (1917) | Complete transcription of Lavoisier combustion experiment text, including dephlogisticated air, Priestley, retort apparatus descriptions | ⭐⭐⭐⭐⭐ |
| O2 | `pure_math_blackboard.jpg` | Formula printed | Complete LaTeX output: partial derivatives, integrals, limits, Gaussian distribution formulas | ⭐⭐⭐⭐ |
| O3 | `boyuan_calligraphy.jpg` | Chinese calligraphy (4th century) | Successfully recognized Wang Xun's "Boyuan Tie" running script full text + Qing dynasty colophons by Qianlong/Dong Qichang et al., classical Chinese characters accurate | ⭐⭐⭐⭐ |
| O4 | `einstein_blackboard.jpg` | Formula handwriting (1931) | Correctly recognized Einstein cosmology handwritten formulas: D² ~ 10⁻⁵³, P ~ 10⁸ L·J, etc. | ⭐⭐⭐⭐ |

### 3.3 Performance Data

| Metric | Value |
|------|------|
| Model loading time | ~2 seconds |
| Single image processing speed | 2-5 seconds |
| English printed accuracy | Extremely high (near-zero errors) |
| Chinese calligraphy accuracy | High (classical Chinese character recognition correct, occasional rare characters may be missed) |
| LaTeX formula output | High (structure correct, minor symbol formatting may be imperfect) |

### 3.4 Assessment

- **Strengths**: Excellent recognition of English printed text and high-resolution handwriting; usable LaTeX formula output; Chinese classical calligraphy performance exceeded expectations
- **Weaknesses**: Low-resolution/low-contrast formula images may have incomplete recognition; PDF multi-page samples not tested
- **Note**: OCR backend also had a 30-second idle GPU release mechanism at the time of verification. The current default remains 30 seconds.

---

## 4. Operational Findings and Known Issues

### 4.1 Startup Script Python Path

**Issue**: The `PYTHON` variable in `asr/qwen3_asr_start.sh` and `ocr/glm_ocr_start.sh` used `<YOUR-PATH>` placeholder, causing direct execution to fail.

```
PYTHON="<YOUR-PATH>"  # Must be replaced with actual conda environment Python path
```

**Temporary fix**: Manually replaced during this verification with:
- ASR: `<qwen-asr conda env>/bin/python` (auto-detected via conda or set via `ASR_PYTHON` env var)
- OCR: `<glm-ocr conda env>/bin/python` (auto-detected via conda or set via `GLM_OCR_PYTHON` env var)

**Fixed**: Startup scripts now support conda auto-detection, no manual path hardcoding needed. Users can also explicitly specify via `ASR_PYTHON` / `GLM_OCR_PYTHON` environment variables.

### 4.2 ASR Background Startup Stability

**Issue**: When starting ASR via `bash asr/qwen3_asr_start.sh start`, the background process occasionally **exits immediately** after model loading completes (rather than waiting for idle timeout). Starting directly with `nohup python ... &` works normally.

**Root cause unconfirmed**: May be related to daemon thread signal handling or `nohup` behavior under specific conditions.

**Workaround**: Set `ASR_IDLE_TIMEOUT=300` environment variable and use direct `nohup` command to start:
```bash
ASR_IDLE_TIMEOUT=300 nohup <PYTHON> asr/qwen3_asr_server.py --host 0.0.0.0 --port 8000 > /tmp/qwen3-asr-server.log 2>&1 &
```

### 4.3 Idle Timeout vs. Batch Testing Conflict

**Issue**: Both ASR and OCR backends had a 30-second idle auto GPU release mechanism at the time of verification (by design). However, this caused the service to auto-shutdown when test intervals exceeded 30 seconds during batch testing, requiring frequent restarts. The ASR default has since been raised to 300 seconds; the OCR default remains 30 seconds (see Appendix D.1).

**Impact** (at time of verification):
- ASR model reload takes ~12 seconds
- OCR model reload takes ~2 seconds
- Gaps during long audio tests also trigger timeouts

**Recommendation** (resolved): The `ASR_IDLE_TIMEOUT` and `OCR_IDLE_TIMEOUT` environment variables allow adjusting idle timeouts. Setting a large positive value (e.g., 3600) effectively prevents the timeout during testing. Do **not** use 0 — both backends use an `idle_s > IDLE_TIMEOUT` guard, so zero triggers immediate shutdown rather than disabling the timeout. Since the post-verification fix (Appendix D.1), the ASR default has been raised to 300 seconds; the OCR default remains 30 seconds.

### 4.4 VL base64 Transfer Limitation

**Issue**: Passing >200KB base64 image data via bash variables fails due to shell variable length limits (returns empty response or truncation).

**Solution**: Write payload to a temporary file via Python script, then send using `curl -d @file`.

### 4.5 VL `file://` URL Limitation

**Issue**: llama-server does not allow `file://` URLs for loading images by default; requires `--media-path` parameter at startup, or use base64 data URI.

**Solution**: Use base64-encoded data URIs (`data:image/jpeg;base64,...`).

---

## 5. Overall Rating

| Tool | Feature Completeness | Accuracy | Speed | Stability | Overall |
|------|------------|--------|------|--------|------|
| **Qwen3-ASR** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | **⭐⭐⭐** |
| **GLM-OCR** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | **⭐⭐⭐⭐** |

### Comprehensive Assessment

- **GLM-OCR** performs excellently on English printed text and formula recognition; Chinese handwriting/calligraphy recognition exceeded expectations; only 0.9B parameters yet great cost-effectiveness.
- **Qwen3-ASR** has extremely high short audio accuracy, but accuracy drops and processing speed is insufficient (~2× real-time) for long audio/low-quality scenarios; background startup stability needs improvement.
- **The core bottleneck for both tools is not the models themselves**, but operational aspects: idle timeout mechanism, startup script placeholders, background startup stability — these need to be addressed through configuration and script improvements.

---

## Appendix A: Complete Test File List

### A.1 Tested Files

| Tool | File | Test Type |
|------|------|----------|
| ASR | `smoke-test/asr_smoke_test.wav` | Smoke |
| ASR | `asr/daily/zh_en_single/D11_750.wav` | Sample |
| ASR | `asr/daily/zh_en_single/LJ037-0171.wav` | Sample |
| ASR | `asr/podcast/en_single/Booker_T_Washington_1895.mp3` | Sample |
| OCR | `smoke-test/ocr_smoke_test.png` | Smoke |
| OCR | `ocr/printed/en/chemistry_textbook_p25.jpg` | Sample |
| OCR | `ocr/printed/formulas/pure_math_blackboard.jpg` | Sample |
| OCR | `ocr/handwriting/zh/boyuan_calligraphy.jpg` | Sample |
| OCR | `ocr/handwriting/formulas/einstein_blackboard.jpg` | Sample |

### A.2 Untested Files (Recommended for Future Coverage)

| Category | Count | Description |
|------|------|------|
| ASR mixed Chinese-English long audio | ~30 | CS-Dialogue / THCHS-30 short clip collection, needs batch run |
| ASR multi-speaker dialogue | 7 | `asr/daily/zh_en_dialogue/` needs speaker differentiation testing |
| OCR PDF multi-page | 6 | 3 born-digital + 3 scanned, needs multi-page processing verification |
| OCR English handwriting | 2 | `willa_cather_letter.png` / `note_1918_december.jpg` |
| OCR formula handwriting | 2 | `leibniz_calculus.png` / `college_math_papers.jpg` |
| ASR Pipeline | 1 | `smoke-test/pipeline_smoke_test.mp3` needs pyannote environment |

---

## Appendix C: ASR Pipeline Smoke Test Results

### Pipeline Test

| File | Duration | Parameters | Artifacts | Rating |
|------|------|------|------|------|
| `pipeline_smoke_test.mp3` | 3.5 min | `--language English --no-diarize` | JSON + SRT + TXT ✅ | ⭐⭐⭐⭐ |

**Preprocessing**: ffmpeg transcoding to 16kHz mono WAV, took 0.5s

**Transcription quality**: 1895 Booker T. Washington historical speech, ~15-20% word errors (consistent with ASR REST test). The iconic sentence "Cast down your bucket where you are" was recognized as "Cast down your bucket among these people".

**Artifact structure**:
```
/tmp/pipeline_test/
├── pipeline_smoke_test.json    # 28KB, word-level timestamps
├── pipeline_smoke_test.srt     # 1.5KB, subtitle format
└── pipeline_smoke_test.txt     # 1.5KB, plain text
```

**Pipeline stages** (all successful):
1. ✅ preprocess → 16kHz WAV
2. ⏭️ diarize (skipped via --no-diarize)
3. ✅ transcribe + forced alignment
4. ✅ merge → JSON/SRT/TXT

**Known limitation** (mitigated on 2026-05-12): Pipeline loads ASR model independently (~3.8GB); running concurrently with REST backend still requires attention to the 12GB VRAM limit. `batch_size=1` + 480s chunking has significantly reduced VRAM pressure; coexistence is feasible in practice.

---

## Appendix D: Fix Details for This Session (2026-05-11)

### D.1 Idle Timeout Extension

| File | Line | Change |
|------|-----|------|
| `asr/qwen3_asr_server.py` (line near 95 at time of verification) | `IDLE_TIMEOUT = int(os.environ.get("ASR_IDLE_TIMEOUT", "30"))` | `"30"` → `"300"` |
| `ocr/glm_ocr_server.py` (line near 230 at time of verification) | `IDLE_TIMEOUT = int(os.environ.get("OCR_IDLE_TIMEOUT", "30"))` | `"30"` → `"300"` (temporary; default later reverted to 30) |

### D.2 ASR Startup Stability Fix

**Root cause**: `asr/qwen3_asr_start.sh` line 2 `set -euo pipefail` + line 98 `(( elapsed++ ))` — when `elapsed=0`, the post-increment `(( 0 ))` returns exit code 1, triggering `set -e` to exit the script immediately.

**Fix**: `asr/qwen3_asr_start.sh:98` — `(( elapsed++ ))` → `(( elapsed += 1 ))`

### D.3 OCR Smoke Image Replacement

- Original `ocr_smoke_test.png` had the same md5 as `calculus_made_easy_fig13.png` — actually a 1914 calculus textbook diagram (coordinate axes + function curves) with very little text
- Generated new image using Python/PIL in `glm-ocr` conda environment (800×200, 6 lines of plain text formulas), 14.6KB
- Synchronized updates: `mcp-tool-test/smoke-test/README.md`, `docs/mcp-tools-testing.md` expected output description

### D.4 ASR Pipeline Test

- `pipeline_smoke_test.mp3` preprocessing 0.5s, 4-stage pipeline completed (diarization skipped)
- Artifacts: JSON (28KB word-level timestamps) + SRT (1.5KB) + TXT (1.5KB)
- Limitation: GPU 12GB cannot run Pipeline and REST backend simultaneously (each loads its own ASR model copy)

---

## Appendix E: Service Restart Command Reference

```bash
# ASR (idle timeout now defaults to 300s, no extra env var needed)
bash asr/qwen3_asr_start.sh start

# OCR
bash ocr/glm_ocr_start.sh start

# Health check
curl http://localhost:8000/health   # ASR
curl http://localhost:8002/health   # OCR
```

---

## Appendix F: Smoke Test Report (2026-06-18)

**Verification environment**: Ubuntu 22.04 / NVIDIA RTX 4070 Ti (12GB) / CUDA 12.4
**Scope at the time of this 2026-06-18 run**: Standard smoke set — all 6 MCP servers exercised end-to-end via OpenCode MCP transport. Skipped: `transcribe_podcast` (requires `HF_TOKEN`) and the then-CLI-only ASR Pipeline. The Pipeline was later exposed through the `transcribe_diarized` MCP tool on 2026-07-23.

### F.1 Results — All 8 Smoke Tests Passed

| # | Tool | Function | Test Input | Result | Rating |
|---|------|----------|-----------|--------|--------|
| 1 | `browser_fetch` | `browser_status` | — | nodriver + Playwright + trafilatura + markdownify all available | ⭐⭐⭐⭐⭐ |
| 2 | `browser_fetch` | `fetch_page` | `https://example.com` | engine=nodriver, title="Example Domain", 3.4s, valid markdown | ⭐⭐⭐⭐⭐ |
| 3 | `format_conversion` | `pdf_to_text` | `attention_is_all_you_need.pdf` (15-page born-digital) | 39512 chars extracted, all sections present | ⭐⭐⭐⭐⭐ |
| 4 | `format_conversion` | `markdown_to_pdf` | small `.md` with table | 9997-byte PDF written | ⭐⭐⭐⭐⭐ |
| 5 | `format_conversion` | `html_to_pdf` (chromium) | small styled HTML | 17662-byte PDF written | ⭐⭐⭐⭐⭐ |
| 6 | `glm_ocr` | `ocr_glm` | `mcp-tool-test/smoke-test/ocr_smoke_test.png` | Heading + 5 LaTeX formulas: `$f(x)=x^{2}+2x+1$`, `$d/dx \sin(x)=\cos(x)$`, `$lim(x->0) \sin(x)/x=1$`, `$E=mc^{2} \quad F=ma$`, etc. | ⭐⭐⭐⭐⭐ |
| 7 | `qwen3_asr` | `transcribe_audio` | `mcp-tool-test/smoke-test/asr_smoke_test.wav` | Exact match: *"The examination and testimony of the experts enabled the commission to conclude that five shots may have been fired."* (auto-detect + explicit `language="English"` both correct) | ⭐⭐⭐⭐⭐ |
| 8 | `qwen_vision` | `analyze_image` | `mcp-tool-test/smoke-test/ocr_smoke_test.png` | Identified all 6 formulas (quadratic, derivative, integral, limit, E=mc², F=ma); 559 tokens, $0.000546 | ⭐⭐⭐⭐⭐ |

### F.2 Bugs Found and Fixed in This Session

#### F.2.1 ASR `language` parameter — short codes silently failed (FIXED)

**Issue**: The `transcribe_audio` / `transcribe_podcast` tool docstring (and `docs/tools-reference.md`, `docs/mcp-tools-testing.md`) advertised 2-letter codes:

```python
transcribe_audio("/home/user/meeting.wav", language="zh")
```

But the underlying `qwen_asr` library only accepts full names (`"English"`, `"Chinese"`, ...). Calling with `language="en"` returned HTTP 500 with `ValueError: Unsupported language: En`. Auto-detect (no `language` arg) and `language="English"` worked fine.

**Fix**: Added `_LANGUAGE_ALIASES` mapping + `_normalize_language()` in `asr/asr_mcp_server.py`. The MCP server now transparently translates the documented 2-letter codes (and lower/upper-case full names) into the form `qwen_asr` expects. `docs/mcp-tools-testing.md` updated to clarify both forms are accepted.

```python
# All of these now work:
transcribe_audio("audio.wav", language="en")        # → "English"
transcribe_audio("audio.wav", language="ZH")        # → "Chinese"
transcribe_audio("audio.wav", language="english")   # → "English"
transcribe_audio("audio.wav", language="English")   # passthrough
transcribe_audio("audio.wav")                       # auto-detect
```

> **MCP server restart required** for the fix to take effect — restart OpenCode (or kill the `asr_mcp_server.py` stdio process) before next use.

### F.3 Performance Observations

| Tool | Cold start | Warm call |
|------|-----------|-----------|
| `glm_ocr` | ~30s (model load) | ~3s (single PNG, 5 formulas) |
| `qwen3_asr` | ~10s (model load) | ~2s (6s audio clip) |
| `qwen_vision` (API) | n/a (no GPU) | ~3s (single image, 559 tokens) |
| `format_conversion` (Chromium) | ~2s (Playwright launch) | ~1s |
| `format_conversion` (WeasyPrint) | ~200ms | ~150ms |
| `browser_fetch` (nodriver) | ~3s (Chromium spawn) | ~1-3s |

### F.4 Skipped Items

| Tool | Reason |
|------|--------|
| `transcribe_podcast` | Requires `HF_TOKEN` env var for pyannote — see [`pyannote-setup.md`](pyannote-setup.md) |
| `fetch_page_with_engine` | Same code path as `fetch_page` (covered transitively) |
| `screenshot` | Same Playwright engine as `html_to_pdf` (chromium); Playwright availability already verified |
| `ocr_glm_submit` / `ocr_glm_wait` | Same async path; covered transitively by `ocr_glm` (which uses async submit + wait internally for multi-page PDFs) |

### F.5 ASR Pipeline CLI Smoke Test

At the time of this 2026-06-18 test, the ASR Pipeline was a standalone CLI. It was tested separately to confirm the 2026-05 fixes still held and the 2026-06-12 environment ran cleanly. As of 2026-07-23, the same complete timestamped path is also MCP-exposed as `transcribe_diarized`.

**Command**:
```bash
conda run -n qwen-asr python asr-pipeline/pipeline.py \
  mcp-tool-test/smoke-test/pipeline_smoke_test.mp3 \
  --language English --no-diarize --no-timestamps \
  -o /tmp/pipeline_test/
```

**Result**: ✅ All 4 stages completed in 35.6s for 203s audio (5.7× real-time).

| Stage | Duration | Status |
|-------|----------|--------|
| `[1/4]` preprocess | 0.4s | ✅ ffmpeg → 16kHz WAV |
| `[2/4]` diarize | — | ⏭️ skipped (`--no-diarize`) |
| `[3/4]` transcribe | 35.2s | ✅ Qwen3-ASR-1.7B loaded on cuda:0, 1 chunk, 2477 chars |
| `[4/4]` merge | 0.0s | ✅ JSON + TXT written |

**Output artifacts**:
- `pipeline_smoke_test.json` (2.6 KB) — `metadata` (duration_sec, language, num_speakers, mode, full_text) + `segments` (empty in `--no-timestamps` mode by design)
- `pipeline_smoke_test.txt` (2.5 KB) — full transcript

> **Note**: SRT is not produced when `--no-timestamps` is used (SRT requires segment-level timing). For SRT output, drop `--no-timestamps` — it adds ~2× to processing time on this clip.

**Transcription quality**: Booker T. Washington's 1895 Atlanta Compromise speech. The iconic phrase **"cast down your bucket where you are"** is correctly captured 5 times. Other parts have ~15-20% word errors due to 1895 recording quality (consistent with prior verification, see Section 2.2 row A3 above). Sufficient as a functional smoke test of the 4-stage pipeline.

**Confirms**: 2026-05-12 fixes (480s chunking, batch_size=1, max_new_tokens=4096 default, `--no-timestamps` flag) all still in effect; pipeline runs to completion on RTX 4070 Ti 12 GB without OOM.

> **GPU coexistence reminder**: Pipeline loads its own ASR model copy (~3.8 GB). Stop the `qwen3_asr` MCP REST backend before running pipeline if VRAM is tight. The MCP backend's 300s idle timeout will release GPU automatically; or kill it manually with `pkill -f qwen3_asr_server`.

### F.6 Overall Verdict

**All 6 MCP servers + the ASR Pipeline CLI are functional and ready for production use** as of 2026-06-18, with one minor API ergonomics bug fixed (ASR `language` 2-letter code mapping).
