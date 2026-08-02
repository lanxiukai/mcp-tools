# Qwen3-ASR — Speech-to-Text

Local speech recognition service based on Qwen3-ASR-1.7B, supporting 52 languages. Provides MCP tools (`transcribe_audio` / `transcribe_diarized` / `transcribe_podcast` / `asr_status`) and an HTTP REST API.

> For the MCP tool API, parameters, and OpenCode config, see [`docs/tools-reference.md`](../docs/tools-reference.md). This README documents the file structure, manual run instructions, and underlying audio-format / model details.

## Files

| File | Purpose |
|---|---|
| `qwen3_asr_server.py` | FastAPI REST backend (GPU inference, port 8000) |
| `asr_mcp_server.py` | MCP stdio frontend (auto-wakes REST backend) |
| `qwen3_asr_start.sh` | Standalone start/stop script (`start` / `--fg` / `stop` / `status`) |
| `model_source.py` | Resolves model source without network: explicit `--model` → complete local directory → Hugging Face fallback |
| `../test/asr/test_model_source.py` | Tests for model-source resolution (11 cases covering explicit, local, fallback, and all incompleteness modes) |

## Manual Usage

```bash
# REST API method
conda run -n mcp-local-asr python asr/qwen3_asr_server.py
curl -F "file=@audio.mp3" -F "language=Chinese" http://localhost:8000/v1/audio/transcriptions

# MCP method (auto-connected after opencode.jsonc config, no manual startup needed)
```

> **Note on `language`**: The REST endpoint accepts only full language names (`English`, `Chinese`, `Japanese`, ...). The MCP frontend (`asr_mcp_server.py`) accepts both 2-letter ISO codes (`en`, `zh`, ...) and full names — it normalizes transparently.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ASR_PORT` | `8000` | REST service port |
| `ASR_HOST` | `localhost` | REST service address |
| `ASR_IDLE_TIMEOUT` | `300` | Idle GPU release timeout (seconds) |
| `HF_TOKEN` | — | Required by `transcribe_diarized` and `transcribe_podcast` for pyannote speaker diarization |

---

## Audio Format Support

The underlying loading logic lives in `load_audio_any()` from `qwen_asr/inference/utils.py`:

| Input Method | Loading Library | Supported Formats | Notes |
|---|---|---|---|
| Local file path | `librosa.load()` | WAV, MP3, FLAC, OGG, M4A/AAC, WMA, etc. | Depends on system `ffmpeg` / `audioread` |
| URL / Base64 | `soundfile.read()` (libsndfile) | WAV, FLAC, OGG | **Does not support MP3** (libsndfile lacks an MP3 decoder) |

> Via this repo's `qwen3_asr_server.py`, file uploads are saved as a local temp file and loaded with `librosa` — therefore MP3 is supported. Direct URL passing does not support MP3.

### Audio Parameters

| Parameter | Value | Description |
|---|---|---|
| Sample rate | **16,000 Hz** | Auto-resampled, original 16 kHz not required |
| Channels | **Mono** | Auto-converted (multi-channel averaged) |
| Bit depth | **float32** | Normalized to [-1, 1] |
| Max input (ASR) | **480 seconds (8 min)** | Server-side explicit chunking. Library internal limit 1200 seconds |
| Min input | **0.5 seconds** | Auto zero-padded if shorter |
| Max timestamp alignment | **180 seconds (3 min)** | Only relevant for `Qwen3-ForcedAligner` |

### Input Types

`model.transcribe(audio=...)` accepts:

| Type | Example |
|---|---|
| Local file path | `"/path/to/speech.wav"` |
| URL | `"https://example.com/audio.flac"` |
| Base64 | `"data:audio/wav;base64,..."` or plain base64 string |
| `(np.ndarray, sr)` tuple | `(waveform_float32, 16000)` |

---

## Language Support

Qwen3-ASR supports **52 languages** including Chinese, English, Japanese, Korean, French, German, Spanish, Russian, Arabic, Portuguese, Italian, Dutch, Polish, Turkish, Vietnamese, Thai, Indonesian, Malay, Hindi, Bengali, and more.

**Auto-detection**: When `language` is omitted, the model detects the audio language automatically. Explicit specification can improve accuracy.

| `language` value (MCP) | Effective Behavior |
|---|---|
| Not provided / `None` | Auto-detect language |
| `"en"` or `"English"` | Force English |
| `"zh"` or `"Chinese"` | Force Chinese |
| `"ja"` or `"Japanese"` | Force Japanese |
| `"ko"` or `"Korean"` | Force Korean |

> The MCP frontend transparently maps 2-letter ISO codes to the full names that the underlying `qwen_asr` library expects.

---

## REST API Response Formats

`POST /v1/audio/transcriptions` supports three `response_format` values:

| Format | Return Example |
|---|---|
| `json` (default) | `{"text": "Hello world", "language": "en"}` |
| `text` | Plain text: `Hello world` (`Content-Type: text/plain`) |
| `verbose_json` | Adds `task`, `duration`, and `segments`; the current REST backend returns `duration: 0.0` and an empty `segments` list because it does not run the forced aligner |

The MCP tool `transcribe_audio` always returns `json`. To request the expanded REST response shape, call the REST API directly:

```bash
curl -F file=@audio.wav -F response_format=verbose_json \
     http://localhost:8000/v1/audio/transcriptions
```

### Podcast MCP result semantics

Use `transcribe_diarized` when speaker-attributed text is required. It exposes
the complete offline pipeline as an MCP tool: preprocessing, pyannote
diarization, timestamped Qwen3-ASR transcription, and speaker/text merging. It
returns `speaker_text_attribution: true` plus
`segments[].{speaker,start,end,text,words}`. The tool always enables the forced
aligner and treats `num_speakers` as an exact expected count. To fit the
reference 12 GB GPU, it stops the resident REST ASR backend before loading the
offline pipeline; a later `transcribe_audio` call auto-starts the backend again.

`transcribe_podcast` returns the complete transcript and, when `HF_TOKEN` is
configured, a separate pyannote speaker timeline. The REST backend does not
produce word timestamps, so this tool explicitly returns
`speaker_text_attribution: false`; it does not claim which speaker said each
piece of text. `diarization_status` is `completed`, `skipped`, or `failed`, and
`diarization_error` explains skipped/failed diarization instead of silently
returning zero speakers. `num_speakers`, when supplied, is the exact expected
speaker count.

Use `transcribe_podcast` only when a complete transcript and separate speaker
timeline are sufficient.

---

## Audio Content Types

Qwen3-ASR can transcribe spoken speech, singing voices, and songs with background music:

| Type | WER Reference | Description |
|---|---|---|
| Speech | 1.6% – 5.9% | LibriSpeech, WenetSpeech, etc. |
| Singing voice | 3.1% – 6.0% | M4Singer, Opencpop, other a-cappella datasets |
| Songs with BGM | 13.9% – 14.6% | EntireSongs with background music |

---

## Long-Audio Chunking Mechanism

### Server-side explicit chunking

Before calling the model, `qwen3_asr_server.py` splits audio exceeding the limit into chunks of ≤ 480 seconds, transcribes each independently, then concatenates the text. This keeps 12 GB VRAM safe and avoids the shared-VRAM pressure of the library's internal 1200-second chunks.

### Library internal auto-chunking

`split_audio_into_chunks()` (from the `qwen_asr` library):

- Target chunk length: `max_chunk_sec` (passed at call time).
- Boundary search: finds the **lowest energy point** within ±5 seconds of the cut point, avoiding mid-speech splits.
- Adjacent chunks have **no overlap, no gap**; concatenation reconstructs the original audio.
- A trailing chunk shorter than 0.5 seconds is auto zero-padded.

---

## Model Details

| Attribute | Value |
|---|---|
| Model | `Qwen/Qwen3-ASR-1.7B` |
| Architecture | Transformer + CTC |
| Data type | bfloat16 (default) / float16 / float32 |
| Max inference batch size | 1 (`max_inference_batch_size=1`), safe for 12 GB VRAM |
| GPU VRAM usage | ~3.5 GB |
| Device | cuda:0 (default) / cpu |
| Source | Explicit `--model` → complete repository-local directory → Hugging Face fallback |

At startup, model-source precedence is: an explicit `--model` value (local directory or
Hub ID), then the complete local directory
`models/safetensors/Qwen/Qwen3-ASR-1.7B`, then `Qwen/Qwen3-ASR-1.7B` from Hugging Face.
The local directory is selected only when it is complete:
`config.json` is present and non-empty, `model.safetensors.index.json` is present with a
valid non-empty `weight_map`, and every shard file indexed in the weight map is present
and non-empty. Any missing or empty file causes fallback to the Hub.

### Why bfloat16 over float16?

bfloat16 has the same dynamic range as float32 (8-bit exponent). Both use the same VRAM during inference (2 bytes), but bfloat16 has higher tolerance for overflow with almost no precision loss. Qwen3-ASR using bfloat16 is the official recommendation.

---

## Service Architecture

```
OpenCode Agent
    │ MCP stdio
    ▼
MCP Server (asr_mcp_server.py)         ← Lightweight frontend, auto-wakes REST backend
    │ HTTP REST
    ▼
FastAPI Server (qwen3_asr_server.py)   ← GPU inference backend, independent start/stop script
    │
    ▼
Qwen3-ASR-1.7B (complete local directory or Hugging Face fallback)
```

- **REST port**: `8000` (override with `ASR_PORT`)
- **Idle timeout**: 300 seconds of inactivity triggers auto-exit and GPU release (override with `ASR_IDLE_TIMEOUT`)
- **Logs**: `/tmp/qwen3-asr-server.log`
- **Concurrency**: FastAPI is asyncio-based and accepts concurrent HTTP requests, but `model.transcribe()` is synchronous GPU inference — multiple requests are queued and processed serially. No requests are dropped, but they are not parallelized either. For batch processing, submit sequentially.

---

## Known Issues and Troubleshooting

### Common pitfalls

| Symptom | Root Cause | Solution |
|---|---|---|
| Long audio transcription truncated | Old `max_new_tokens=256` too small | `qwen3_asr_server.py` now uses `max_new_tokens=4096`, supporting ~10 min per chunk |
| `NoBackendError` (ffmpeg not found) | `nohup` startup lacks conda `bin/` in PATH | `qwen3_asr_start.sh` now auto-prepends conda `bin/` to PATH; if it persists, manually `export PATH="<CONDA-ENV>/bin:$PATH"` and restart |
| MCP tools offline after OpenCode restart | The ASR REST service (`localhost:8000`) is an independent process, not auto-recovered with OpenCode | After restart, manually run `bash asr/qwen3_asr_start.sh start`. The MCP frontend has built-in auto-wake, but the OpenCode sandbox may restrict `subprocess.Popen` — manual startup is more reliable. |
| Trailing sentences end with "…" | Generation hit `max_new_tokens` and was force-stopped | Increase `max_new_tokens` in `qwen3_asr_server.py` and restart |
| `librosa` warning when loading m4a | PySoundFile does not support m4a, falls back to audioread | **Normal**, does not affect results — make sure ffmpeg is on PATH |

### VRAM not released after auto-shutdown

Shutdown is graceful via SIGTERM, with `torch.cuda.empty_cache()` in the lifespan handler. In the rare case where VRAM is not released:

```bash
nvidia-smi                            # Check VRAM
bash asr/qwen3_asr_start.sh stop      # Force stop
```

### Adjusting idle timeout

Default 300 seconds. Override via `ASR_IDLE_TIMEOUT` (set before starting the service):

```bash
export ASR_IDLE_TIMEOUT=60     # 60 seconds
export ASR_IDLE_TIMEOUT=120    # 2 minutes
export ASR_IDLE_TIMEOUT=3600   # 1 hour (effectively always-on)
```

> Do **not** set `ASR_IDLE_TIMEOUT=0` — the shutdown guard is `idle_s > IDLE_TIMEOUT`, so zero triggers immediate shutdown at the first idle poll, not disable. Use a large positive value instead.

### Recovery after OpenCode restart

When OpenCode restarts: the MCP frontend (`asr_mcp_server.py`) is automatically launched by OpenCode via stdio, no manual action needed. However, the ASR REST service (`localhost:8000`) is an independent process and **does not auto-recover**.

Recommended:

```bash
# After OpenCode restart, start the ASR service first
bash asr/qwen3_asr_start.sh start

# Verify
curl localhost:8000/health
```

### Service crashes immediately after startup

- Foreground for visible errors: `bash asr/qwen3_asr_start.sh --fg`
- Check GPU VRAM: `nvidia-smi`
- Confirm the `mcp-local-asr` conda environment is activated and the `qwen-asr` package is installed
