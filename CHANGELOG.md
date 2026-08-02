# Changelog

This file records the notable changes in each tagged release. The root
[`README.md`](README.md) contains only a short summary of recent releases.

## v0.8.1 — 2026-08-02

- Hardened the OCR runtime by aligning Miniforge and local
  PaddleOCR-VL/PP-DocLayoutV3 model resolution, restoring the CUDA 13 runtime
  library path, and updating recovery verification.
- Separated production PDF staging from the OCR benchmark harness, moved the
  benchmark support modules under `test/ocr/benchmark`, and updated the CLI,
  tests, and documentation.
- Pinned the repository-local MathJax v4 runtime, added fontconfig-aware Noto
  CJK/emoji discovery, improved SVG and emoji handling across Chromium and
  WeasyPrint, and extended the installer for the required Node runtime and
  system fonts.

## v0.8.0 — 2026-07-23

- Replaced GLM-OCR with local PaddleOCR-VL-1.6, introduced model-neutral
  `ocr_document`, `ocr_submit`, `ocr_wait`, and `ocr_status` tools, and added a
  replaceable model adapter with generic `OCR_*` configuration.
- Added real stdio MCP, multilingual handwriting, cross-domain formula, and
  staged PDF verification while removing duplicate model-specific entry
  points.
- Added the model-neutral `vision_local` MCP backed by a persistent CUDA
  llama.cpp server, including generic analysis, OCR, chart, status, and
  schema-constrained eyewear tools.
- Added resumable four-way concurrent vision batch processing with JSONL
  checkpoints and JSON/CSV misclassification artifacts.
- Exposed the timestamped speaker-diarization pipeline as
  `transcribe_diarized`, clarified `transcribe_podcast` timeline semantics, and
  hardened the isolated ASR runtime.

## v0.7.0 — 2026-07-14

- Consolidated unit, integration, benchmark, and smoke tests under the root
  `test/` directory: `test/asr`, `test/asr_pipeline`, `test/browser_fetch`, and
  `test/ocr`.
- Kept `mcp-tool-test/` as the separate local sample-fixture directory and
  updated documentation to distinguish it from executable test suites.
- Corrected tool counts, API signatures, environment-variable names, test
  paths, launcher descriptions, and OCR benchmark references across the
  repository documentation.

## v0.6.1 — 2026-07-06

- Fixed ASR timeout consistency, a `transcribe_podcast` merge import crash, a
  diarization cleanup `NameError`, and a transcription temporary-directory
  leak.
- Fixed OCR image submission, foreground startup, and scoped bytecode-cache
  cleanup.
- Hardened ASR and OCR launcher startup/error handling and synchronized related
  configuration documentation.
- Corrected Format Conversion defaults and removed dead formatting code.
- Audited subproject READMEs for runtime defaults, file tables, paths, and
  changelog coverage.

## v0.6.0 — 2026-07-04

- Added the Brave Websearch MCP launcher for web, news, local, video, image,
  and AI-summary searches through the official Brave Search package.
- Added launcher checks for Node.js, the Brave API key, `npx`, proxy support,
  and configurable tool enablement.
- Added Chromium support to `markdown_to_pdf` and made the MCP tool use it by
  default.
- Added safe fallback rendering for MathJax error SVGs.

## v0.5.0 — 2026-06-18

- Added the Browser Fetch MCP server with nodriver and Playwright engines for
  JavaScript-rendered and anti-bot pages.
- Replaced KaTeX with server-side MathJax rendering and fixed portable MathJax
  discovery, compound emoji handling, and currency/math delimiter ambiguity.
- Added two-letter ASR language-code mapping for all supported languages.
- Translated tracked project files to English, removed internal notes, and
  consolidated cross-cutting and subproject documentation.
- Completed an end-to-end smoke test of all six MCP servers and the ASR
  Pipeline CLI on an RTX 4070 Ti.

## v0.4.1 — 2026-06-12

- Fixed Markdown-to-PDF table corruption caused by dollar signs being
  misidentified as LaTeX delimiters.
- Added `_is_likely_math()` filtering so only expressions with LaTeX
  characteristics are passed to MathJax.

## v0.4.0 — 2026-06-10

- Added the Qwen Vision MCP tool with image analysis, text extraction, chart
  analysis, and PDF analysis through OpenRouter.
- Improved Markdown-to-PDF emoji handling, typography, page breaks, tables,
  and fallback replacement.

## v0.3.1 — 2026-06-08

- Removed the previous `qwen_vision` implementation and its documentation and
  tests.
- Removed obsolete AI Agent Framework references and made this repository
  standalone.

## v0.3.0 — 2026-05-23

- Added a Chromium/Playwright backend for document conversion and automatic
  text-file output from `pdf_to_text`.
- Added the GLM-OCR asynchronous submit/wait/status queue and non-blocking
  server startup, and increased its MCP timeout to 30 minutes.
- Upgraded WeasyPrint and refactored the converter into composable CSS helpers.

## v0.2.1 — 2026-05-13

- Split the root README into concise project guidance and detailed tool
  documentation.
- Removed the untracked local fixture directory from public test guidance and
  corrected project links, positioning, and licensing information.

## v0.2.0 — 2026-05-13

- Added long-audio ASR with 480-second transcription chunks and 900-second
  diarization chunks.
- Fixed generation limits, GPU device mapping, batch sizing, and long
  single-chunk memory use.
- Added `--no-timestamps`, `--max-new-tokens`, `--batch-size`, and the
  `transcribe_podcast` MCP tool.
- Updated MCP timeouts and documented RTX 4070 Ti performance for a two-hour
  podcast.

## v0.1.1 — 2026-05-12

- Fixed mutual-exclusion startup races that could exhaust GPU memory.
- Unified the 30-second idle timeout across GPU services.
- Fixed launcher argument shifting and `set -e` early-exit behavior.

## v0.1.0 — 2026-05-12

- Initial release with ASR, OCR, and ASR Pipeline tools, test samples, smoke
  tests, and one-click installation.
