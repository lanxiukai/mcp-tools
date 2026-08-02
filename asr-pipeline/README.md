# ASR Pipeline — Podcast Long-Form Audio Transcription

Offline batch CLI tool that transcribes 2–3 hour podcasts / meetings / interviews into structured text with **speaker labels** and **word-level timestamps**. Built-in 480s chunking strategy keeps VRAM under control on 12 GB cards.

> **MCP routing**: `transcribe_audio` is for quick one-shot transcription.
> Agents that need speaker-attributed text should call `transcribe_diarized`,
> which exposes this complete timestamped pipeline through MCP. Use this CLI
> directly for batch processing, file exports, or explicit output-format
> control.

> For the ASR MCP tool API, see [`docs/tools-reference.md`](../docs/tools-reference.md). This README covers the standalone CLI.

## Files

| File | Purpose |
|---|---|
| `pipeline.py` | Pipeline entry point (orchestrates preprocess → diarize → transcribe → merge) |
| `preprocess.py` | Audio preprocessing (ffmpeg → 16 kHz mono WAV) |
| `diarize.py` | Speaker diarization (pyannote.audio, requires HF_TOKEN) |
| `transcribe.py` | ASR transcription (Qwen3-ASR + optional forced aligner) |
| `merge.py` | Merge speaker labels with ASR segments |
| `__init__.py` | Package marker |
| `../test/asr_pipeline/test_pipeline.py` | pytest suite |
| `../test/asr_pipeline/test_model_resolution.py` | Model-resolution tests |

---

## Quick Start

```bash
# Basic: English podcast, all output formats
conda run -n mcp-local-asr python asr-pipeline/pipeline.py \
    podcast.mp3 --language English -o ./output/

# Long audio (recommended for ≥1h): skip word-level timestamps for 4×+ speedup
conda run -n mcp-local-asr python asr-pipeline/pipeline.py long_podcast.mp3 \
    --language English --no-timestamps -o ./out/

# Chinese podcast + term injection
conda run -n mcp-local-asr python asr-pipeline/pipeline.py interview.mp3 --language Chinese \
    --context "neural network backpropagation gradient descent" -o ./out/

# Subtitles only
conda run -n mcp-local-asr python asr-pipeline/pipeline.py lecture.wav -f srt -o ./out/

# Skip speaker diarization (single-speaker lecture)
conda run -n mcp-local-asr python asr-pipeline/pipeline.py speech.mp3 --no-diarize

# Multi-file batch
conda run -n mcp-local-asr python asr-pipeline/pipeline.py ep1.mp3 ep2.mp3 ep3.mp3 \
    --language English -o ./batch-out/

# Set the exact expected speaker count to 2
conda run -n mcp-local-asr python asr-pipeline/pipeline.py meeting.wav --language English --num-speakers 2 -o ./out/
```

---

## Input Formats

### Audio files

| Feature | Description |
|---|---|
| Supported formats | Any ffmpeg-compatible format (MP3, WAV, FLAC, OGG, M4A, AAC, WMA, OPUS, …) |
| Original parameters | Any sample rate / channel count / bit depth |
| Duration limit | None (disk and GPU VRAM are the practical bound) |
| Multi-file input | `conda run -n mcp-local-asr python asr-pipeline/pipeline.py a.mp3 b.wav c.flac` — files processed sequentially |

### Standard input (stdin)

PCM audio data via pipe is also accepted (saved as a temporary WAV before entering the pipeline):

```bash
cat audio.pcm | conda run -n mcp-local-asr python asr-pipeline/pipeline.py - --language English -o ./output/
```

---

## Four-Stage Pipeline

```
[1/4] Preprocess     → Audio format unification (ffmpeg)
[2/4] Diarize        → Speaker diarization (pyannote.audio, optionally skipped)
[3/4] Transcribe     → ASR transcription + word-level timestamp alignment (Qwen3-ASR)
                       (Use --no-timestamps to skip alignment for major speedup)
[4/4] Merge & Output → Merge results + write files (JSON / SRT / TXT)
```

Stages run sequentially; failure aborts the pipeline with a non-zero exit code.

**Chunking strategy**: Stage 3 auto-splits long audio into ≤480-second chunks, transcribes each independently, and concatenates. This keeps a 12 GB GPU safe on 2+ hour audio.

### Stage 1 — Preprocessing details

`preprocess.py` uses ffmpeg to normalize any audio:

```
Original audio (any format / parameters)
  → ffmpeg
  → 16 kHz sample rate (-ar 16000)
  → Mono (-ac 1)
  → 16-bit signed PCM (-sample_fmt s16)
  → WAV output
```

| Attribute | Conversion target | Notes |
|---|---|---|
| Sample rate | **16,000 Hz** | Matches Qwen3-ASR expectation |
| Channels | **1 (mono)** | Multi-channel mixed down |
| Bit depth | **16-bit signed PCM** | Standard integer format |
| Container | **WAV** | Lossless uncompressed |

**Idempotency**: If the input is already a 16 kHz mono WAV, preprocessing is skipped and the source file is reused as-is.

**Dependency**: System `ffmpeg`. The script auto-locates it inside the active conda env's `bin/` first.

---

## Output Formats

### Format selection

| `--format` | Output file | Content |
|---|---|---|
| `json` | `{basename}.json` | Structured data with word-level timestamps + speaker segments |
| `srt` | `{basename}.srt` | Standard subtitle format |
| `txt` | `{basename}.txt` | Speaker-annotated text in timestamp mode; complete unlabelled transcript in `--no-timestamps` mode |
| `all` (default) | All three above | Generate all |

### JSON structure (with timestamps mode, default)

```json
{
  "metadata": {
    "duration_sec": 3600.5,
    "language": "English",
    "num_speakers": 3
  },
  "segments": [
    {
      "speaker": "SPEAKER_00",
      "start": 0.0,
      "end": 12.5,
      "text": "Welcome to today's episode...",
      "words": [
        {"word": "Welcome", "start": 0.0, "end": 0.6},
        {"word": "to", "start": 0.6, "end": 0.8}
      ]
    }
  ]
}
```

### JSON structure (`--no-timestamps` mode, recommended for long audio)

```json
{
  "metadata": {
    "duration_sec": 6640.0,
    "language": "English",
    "num_speakers": 1,
    "mode": "no_timestamps",
    "full_text": "Chapter One of Great Inventors...",
    "speaker_text_attribution": false,
    "speaker_timeline_available": true
  },
  "segments": [
    {
      "speaker": "SPEAKER_00",
      "start": 0.0,
      "end": 12.5,
      "text": "",
      "words": []
    }
  ]
}
```

| Field | Description |
|---|---|
| `metadata.mode` | `"no_timestamps"` indicates fast mode (only present with `--no-timestamps`) |
| `metadata.full_text` | Complete transcription (only present with `--no-timestamps`) |
| `metadata.speaker_text_attribution` | `false` in fast mode because there are no word timestamps to map text onto speakers |
| `metadata.speaker_timeline_available` | Whether diarization produced a separate speaker timeline |
| `segments[].speaker` | Speaker identifier (`SPEAKER_00`, `SPEAKER_01`, …) |
| `segments[].start` / `end` | Speaker segment start/end (seconds) |
| `segments[].text` | Transcription for that segment in timestamp mode; empty in `--no-timestamps` mode |
| `segments[].words[]` | Word-level timestamps (`word`, `start`, `end`) — only in timestamp mode |

In `--no-timestamps` mode, JSON keeps the complete transcript and the speaker
timeline as separate data. TXT contains the complete unlabelled transcript;
SRT is not produced. Run without `--no-timestamps` when you need reliable
speaker-attributed text.

### SRT structure

Each subtitle entry corresponds to one speaker segment:

```
1
00:00:00,000 --> 00:00:02,500
[SPEAKER_00] Good morning everyone.

2
00:00:02,500 --> 00:00:05,800
[SPEAKER_01] Thanks for joining us today.
```

### TXT structure

One speaker segment per line:

```
[SPEAKER_00] Good morning everyone.
[SPEAKER_01] Thanks for joining us today.
[SPEAKER_00] Let's begin with the first topic...
```

---

## Language Support

| `--language` | Description |
|---|---|
| (omitted) | Auto-detect (Qwen3-ASR detects automatically) |
| `English` | Force English |
| `Chinese` | Force Chinese |

> The pipeline CLI exposes only `English` and `Chinese` via `choices=`, but the underlying Qwen3-ASR supports 52 languages. For other languages, edit `pipeline.py`'s `choices` list or pass the language tag directly.

---

## Speaker Diarization (Stage 2 — Optional)

### Dependencies

| Component | Notes |
|---|---|
| Model | `pyannote/speaker-diarization-3.1` |
| Permission | Accept the model terms at [hf.co/pyannote](https://hf.co/pyannote) |
| Token | Pass via `--hf-token` or the `HF_TOKEN` environment variable |

See [`docs/pyannote-setup.md`](../docs/pyannote-setup.md) for full HF_TOKEN setup.

### Parameters

| Parameter | Description |
|---|---|
| `--no-diarize` | Skip speaker diarization; all text assigned to `SPEAKER_00` |
| `--num-speakers N` | Exact expected number of speakers; omit for automatic detection |
| `--hf-token TOKEN` | HuggingFace token (defaults to the `HF_TOKEN` env var) |

**Long-audio chunking**: Stage 2 auto-splits audio longer than 15 minutes into ≤900-second chunks for individual processing, avoiding pyannote OOM. Cross-chunk segment merging has no gaps.

### Position in pipeline

```
preprocess → [diarize?] → transcribe → merge
                ↑
         Optional, skipped with --no-diarize.
         Long audio auto-split into ≤900s chunks.
```

Speaker diarization runs in Stage 2, before ASR transcription. The pyannote model and Qwen3-ASR model are loaded **serially** and do not co-occupy GPU.

---

## Context Injection

The `--context` parameter injects domain-specific terms to improve ASR accuracy:

```bash
# Finance podcast
conda run -n mcp-local-asr python asr-pipeline/pipeline.py finance.mp3 --language English \
    --context "EBITDA ROI NASDAQ non-GAAP" -o ./out/

# Tech interview
conda run -n mcp-local-asr python asr-pipeline/pipeline.py tech.mp3 --language Chinese \
    --context "large language model attention mechanism reinforcement learning" -o ./out/
```

Terms are space-separated and injected into Qwen3-ASR's recognition context. Significantly helps with proper nouns, abbreviations, and specialized terminology.

---

## CLI Reference

### Full parameter list

| Parameter | Required | Default | Description |
|---|---|---|---|
| `input` | Yes | — | Audio file path(s) (multiple allowed); `-` for stdin |
| `-l` / `--language` | No | Auto-detect | `English` or `Chinese` |
| `-o` / `--output-dir` | No | `./output/` | Output directory |
| `-f` / `--format` | No | `all` | `json` / `srt` / `txt` / `all` |
| `-c` / `--context` | No | `""` | Space-separated domain terms |
| `-n` / `--num-speakers` | No | Auto | Exact expected number of speakers |
| `--no-diarize` | No | false | Skip speaker diarization |
| `--device` | No | `cuda:0` | PyTorch device |
| `--hf-token` | No | `HF_TOKEN` env | HuggingFace token |
| `--max-new-tokens` | No | `4096` | Max generation tokens per step (recommend 4096–8192 for long audio) |
| `--batch-size` | No | `1` | ASR inference batch size (set to 2 only on ≥16 GB VRAM) |
| `--no-timestamps` | No | false | **Recommended for long audio** — skip word-level alignment, 4×+ speedup; full text and speaker timeline remain separate |

---

## Performance Reference

Measured on RTX 4070 Ti 12 GB:

| Scenario | Time | Notes |
|---|---|---|
| 22 min audio (`--no-timestamps`) | ~3 min | ~7× real-time |
| 2 hour audio (`--no-timestamps`) | ~20 min | ~6× real-time |
| 1 hour audio (with timestamps) | 20 – 60 min | Forced aligner is the bottleneck |
| 2 hour audio (with timestamps) | 60 – 180 min | Not recommended; use `--no-timestamps` |

> Actual time depends on GPU and audio content. In `--no-timestamps` mode, long audio is split into 480-second chunks and VRAM stays at ~5–6 GB.

---

## Dependencies

```
asr-pipeline/
├── pipeline.py        ← CLI entry point
├── preprocess.py      ← Calls ffmpeg (system dependency)
├── diarize.py         ← pyannote.audio (pip + HF_TOKEN)
├── transcribe.py      ← Qwen3-ASR (shares the mcp-local-asr conda environment)
├── merge.py           ← Pure Python, no external deps
└── __init__.py        ← Package marker
```

Test suites: `../test/asr_pipeline/test_pipeline.py` (pytest), `../test/asr_pipeline/test_model_resolution.py` (model-resolution tests).

| Dependency | Install | Purpose |
|---|---|---|
| `ffmpeg` | `sudo apt install ffmpeg` or via conda | Audio preprocessing |
| `pyannote.audio` | `pip install pyannote.audio` | Speaker diarization |
| Qwen3-ASR | Shares the `mcp-local-asr` conda environment with [`asr/`](../asr/) | ASR transcription + alignment |
