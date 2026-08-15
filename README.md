# MCP Tools — Ready-to-Use AI Agent Toolkit

A local MCP (Model Context Protocol) toolkit providing AI agents with document OCR, audio transcription, image understanding, web search, and document format conversion capabilities. The GPU tools run inference locally — no cloud API fees. Web search uses an external API. All tools work standalone (no MCP client dependency) via their underlying HTTP backends or CLI interfaces.

## Tool Overview

| Tool | Function | Model / Runtime | VRAM |
|---|---|---|---|
| **Brave Websearch** | Web, news, local, video, image search + AI summarizer | Node.js + Brave Search API | No GPU needed |
| **Qwen3-ASR** | Speech-to-text (52 languages), speaker diarization | Qwen3-ASR-1.7B | ~3.5 GB |
| **OCR** | Replaceable local document parsing (image/PDF → Markdown, incl. tables/formulas) | PP-DocLayoutV3 + PaddleOCR-VL-1.6 0.9B | ~3.9 GB idle / ~7.7 GB dense-PDF peak |
| **Vision Local** | Image analysis + structured/batch eyewear classification | Q4 vision model + llama.cpp CUDA | ~7–9 GB |
| **ASR Pipeline** | Long-audio speaker-attributed transcription (MCP + CLI) | Qwen3-ASR + pyannote | ~6 GB |
| **Format Conversion** | Document format conversion (MD/HTML→PDF, PDF→Text) | Chromium + WeasyPrint + PyMuPDF | CPU only |
| **Browser Fetch** | Anti-bot web page fetching (JS-rendered, Cloudflare-protected) → Markdown | nodriver + Playwright + trafilatura | CPU only |

For MCP interfaces, opencode.jsonc configuration, and API parameters, see [`docs/tools-reference.md`](docs/tools-reference.md). For sub-project file structure and manual run instructions, see each sub-project's README.

> **PDF processing priority**: Call `pdf_to_text` first (millisecond-level text extraction) → call `ocr_document` when text is empty/incomplete or formula/table structure matters. Avoid unnecessary GPU inference on born-digital PDFs.

## Prerequisites

- **OS**: Linux (Ubuntu 22.04+) or WSL2
- **GPU**: NVIDIA GPU, ≥ 12 GB VRAM recommended
- **CUDA**: 12.4+ (the locked OCR profile uses CUDA 12.6 wheels)
- **uv**: Required for all three repository-local Python projects
- **FFmpeg**: System installation required by ASR for preprocessing and fallback decoding

## Runtime Environments

`install.sh` provisions these three isolated runtimes from locked uv projects.
The script never deletes an environment, and rerunning it restores the selected
project-local `.venv` from its lock.

| Environment | Tools | Manager and runtime |
|---|---|---|
| `mcp-local` | Browser Fetch, Format Conversion, Vision Local frontend, research MCP packages | uv; shared CPU Python runtime |
| `mcp-local-ocr` | Generic OCR: PaddleOCR-VL-1.6 + PP-DocLayoutV3 | uv; PyTorch and PaddlePaddle CUDA 12.6 |
| `mcp-local-asr` | Qwen3-ASR, ASR Pipeline | uv; GPU, Transformers 4.57.6 |

Environment sources and recovery records are stored under
[`environments/`](environments/). The Python 3.12.13 uv projects live at
`environments/mcp-local-asr/`, `environments/mcp-local/`, and
`environments/mcp-local-ocr/`; each project-local `.venv` is restored from its
own lock. Installers, launchers, and MCP clients use those uv interpreters; the
retired Conda profiles and their exact records are not recovery sources.

Vision Local shares the lightweight `mcp-local` frontend environment and auto-starts a repository-local CUDA llama.cpp backend; build it once with `bash vision-local/install_runtime.sh`. The uv project also restores the published Google Scholar and academic-research MCP packages; their MCP registrations remain separate.

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/lanxiukai/mcp-tools.git
cd mcp-tools

# 2. One-click install
bash install.sh
# Or select one runtime: --asr-only, --ocr-only, or --cpu-only.
# --browser-only remains a compatibility alias for --cpu-only.

# 3. Configure OpenCode
# Copy the config snippet output by install.sh into the "mcp" block of ~/.config/opencode/opencode.jsonc

# 4. Restart OpenCode and you're ready to go
```

> **ASR model source**: At startup, Qwen3-ASR resolves the model in this order: an explicit `--model` value → complete repository-local `models/safetensors/Qwen/Qwen3-ASR-1.7B` directory → Hugging Face automatic download. The local directory is considered complete only when `config.json`, `model.safetensors.index.json` with a valid `weight_map`, and every indexed shard are present and non-empty. On first use without a local copy, Hugging Face automatically downloads model weights to the cache; you can also run `bash install.sh` to pre-download.

## Documentation

Each sub-project has its own README with file structure, manual-run instructions, and deep-dive format/model details. Cross-cutting docs live in `docs/`.

| Document | Content |
|---|---|
| [`docs/tools-reference.md`](docs/tools-reference.md) | Tool APIs, opencode.jsonc config, model descriptions & performance data |
| [`docs/mcp-tools-testing.md`](docs/mcp-tools-testing.md) | Usage guide, test samples, smoke tests |
| [`docs/tools-verification-report.md`](docs/tools-verification-report.md) | Performance/accuracy verification report (RTX 4070 Ti) |
| [`docs/vision-local-verification-report.md`](docs/vision-local-verification-report.md) | Vision Local deployment, smoke test, and 4,500-image audit report |
| [`docs/ocr-test-report.md`](docs/ocr-test-report.md) | Current PaddleOCR-VL migration, handwriting, formula, and staged PDF results |
| [`docs/pyannote-setup.md`](docs/pyannote-setup.md) | pyannote / HF_TOKEN setup for speaker diarization |
| [`brave-websearch/run.sh`](brave-websearch/run.sh) | Brave Websearch: MCP server launcher (see header comments for full docs) |
| [`asr/README.md`](asr/README.md) | Qwen3-ASR: file structure, manual run, audio formats, language support, chunking, troubleshooting |
| [`ocr/README.md`](ocr/README.md) | Generic OCR: stable tools, PaddleOCR-VL adapter, jobs, formats, and model switching |
| [`asr-pipeline/README.md`](asr-pipeline/README.md) | ASR Pipeline: 4-stage pipeline, JSON/SRT/TXT output, diarization, full CLI reference |
| [`vision-local/README.md`](vision-local/README.md) | Vision Local: CUDA runtime, generic MCP tools, resumable batch classification |
| [`format-conversion/README.md`](format-conversion/README.md) | Format Conversion: MCP interface, module API, CLI usage, engine comparison |
| [`browser-fetch/README.md`](browser-fetch/README.md) | Browser Fetch: tool API, engine selection (nodriver/Playwright), Cloudflare/Upwork guidance |

## Directory Structure

```
mcp-tools/
├── brave-websearch/  # Brave Search MCP launcher (Node.js + API, no GPU)
├── asr/              # Qwen3-ASR → README
├── ocr/              # Generic OCR / PaddleOCR-VL adapter → README
├── vision-local/     # Local generic vision MCP + batch classifier → README
├── asr-pipeline/     # Podcast pipeline used by MCP + standalone CLI → README
├── format-conversion/ # Format Conversion → README
├── browser-fetch/    # Browser Fetch → README
├── environments/     # locked uv projects
├── test/             # Test suites (OCR, ASR, ASR Pipeline, Browser Fetch)
├── docs/             # Tool reference, testing, verification docs
├── install.sh        # One-click install script
└── README.md
```

## Architecture

Most MCP tools consist of an MCP stdio frontend + FastAPI GPU backend. The frontend auto-starts the backend on first invocation. **Brave Websearch** is different — a thin bash launcher that runs the official `@brave/brave-search-mcp-server` npm package via npx, requiring only Node.js and an API key (no local GPU, no Python environment). See each sub-project README for details.

## Recent Changes

- **v0.9.0 (2026-08-07)** — Added selectable print, sepia, and One Dark Pro-inspired Markdown-to-PDF themes and exact recovery records for all managed Conda environments.
- **v0.8.1 (2026-08-02)** — Hardened the OCR runtime and model resolution, moved benchmark support under `test/`, and improved local MathJax and CJK/emoji font support.
- **v0.8.0 (2026-07-23)** — Migrated OCR to PaddleOCR-VL, introduced the local vision MCP and resumable batch processing, and added speaker-attributed ASR.

See [`CHANGELOG.md`](CHANGELOG.md) for the complete version history.

## License

MIT — see [`LICENSE`](./LICENSE).

All source code, MCP server implementations, and documentation in this repository are MIT-licensed. Free to use, modify, and distribute — just retain the copyright notice.
