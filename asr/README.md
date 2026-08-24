# Qwen3-ASR — Speech-to-Text

Local speech recognition service with selectable Qwen3-ASR 1.7B and 0.6B
profiles, supporting 52 languages. It provides MCP tools (`transcribe_audio` /
`transcribe_diarized` / `transcribe_podcast` / `asr_status`) and an HTTP REST
API. The default 1.7B profile is a 12 GiB reference configuration; only the
separate 0.6B `8gb` REST profile is tested against the repository's 8 GB
whole-device ceiling.

> For the MCP tool API, parameters, and OpenCode config, see [`docs/tools-reference.md`](../docs/tools-reference.md). This README documents the file structure, manual run instructions, and underlying audio-format / model details.

## Files

| File | Purpose |
|---|---|
| `qwen3_asr_server.py` | FastAPI REST backend (GPU inference, port 8000) |
| `asr_mcp_server.py` | MCP stdio frontend (auto-wakes REST backend) |
| `qwen3_asr_start.sh` | Standalone start/stop script (`start` / `--fg` / `stop` / `status`) |
| `model_source.py` | Resolves model source without network: explicit `--model` → complete local directory → Hugging Face fallback |
| `../test/asr/test_model_source.py` | Tests profile-aware explicit, local, fallback, and incomplete model-source resolution |

## Manual Usage

```bash
# Restore the active repository-local uv runtime
uv sync --project environments/mcp-local-asr --locked

# Start the REST backend with that runtime
bash asr/qwen3_asr_start.sh start

# Select the bounded 0.6B profile instead
ASR_PROFILE=8gb bash asr/qwen3_asr_start.sh start

# REST API request
curl -F "file=@audio.mp3" -F "language=Chinese" http://localhost:8000/v1/audio/transcriptions

# MCP method (auto-connected after opencode.jsonc config, no manual startup needed)
```

> **Note on `language`**: The REST endpoint accepts only full language names (`English`, `Chinese`, `Japanese`, ...). The MCP frontend (`asr_mcp_server.py`) accepts both 2-letter ISO codes (`en`, `zh`, ...) and full names — it normalizes transparently.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ASR_PORT` | `8000` | REST service port |
| `ASR_HOST` | `localhost` | REST service address |
| `ASR_PROFILE` | `default` | `default` selects Qwen3-ASR-1.7B; `8gb` selects the bounded Qwen3-ASR-0.6B REST profile |
| `ASR_MODEL` | Profile model | Explicit local model directory or Hugging Face model ID; a custom model is outside the recorded 8 GB validation |
| `ASR_MAX_CHUNK_SECONDS` | `480` / `60` | Per-call audio chunk bound for `default` / `8gb`; the `8gb` maximum cannot be increased |
| `ASR_MAX_NEW_TOKENS` | `4096` / `1024` | Generation bound for `default` / `8gb`; the `8gb` maximum cannot be increased |
| `ASR_CUDA_MEMORY_LIMIT_MIB` | Unset / `6144` | PyTorch allocator limit for `default` / `8gb`; the `8gb` maximum cannot be increased |
| `ASR_IDLE_TIMEOUT` | `300` | Idle GPU release timeout (seconds) |
| `ASR_TEMP_ROOT` | System temporary directory | Parent for PID-scoped upload, decode, and chunk directories |
| `ASR_PYTHON` | Repository-local `.venv/bin/python` | Optional explicit interpreter override for diagnostics or relocated checkouts |
| `HF_TOKEN` | — | Required by `transcribe_diarized` and `transcribe_podcast` for pyannote speaker diarization |

---

## Audio Format Support

The underlying loading logic lives in `load_audio_any()` from `qwen_asr/inference/utils.py`:

| Input Method | Loading Library | Supported Formats | Notes |
|---|---|---|---|
| Local file path | Server wrapper + Qwen loader | WAV, MP3, FLAC, OGG, M4A/AAC, WMA, etc. | Formats unsupported by libsndfile are normalized to WAV with system FFmpeg |
| URL / Base64 | `soundfile.read()` (libsndfile) | WAV, FLAC, OGG | **Does not support MP3** (libsndfile lacks an MP3 decoder) |

> Via this repo's `qwen3_asr_server.py`, file uploads are saved below a
> PID-scoped service directory. Graceful shutdown removes that directory and a
> later startup reclaims directories belonging to dead ASR processes. The
> wrapper reads formats supported by libsndfile directly and converts other
> formats, including M4A/AAC, to a temporary WAV with system FFmpeg before
> invoking Qwen3-ASR. Direct URL passing to the underlying library does not use
> this wrapper.

### Audio Parameters

| Parameter | Value | Description |
|---|---|---|
| Sample rate | **16,000 Hz** | Auto-resampled, original 16 kHz not required |
| Channels | **Mono** | Auto-converted (multi-channel averaged) |
| Bit depth | **float32** | Normalized to [-1, 1] |
| Max input per model call | **480 seconds (`default`) / 60 seconds (`8gb`)** | Server-side explicit chunking; longer uploads are split and concatenated |
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
aligner and treats `num_speakers` as an exact expected count. If pyannote
returns no speech or cannot produce that exact count, the tool reports an
actionable error before transcription instead of fabricating a speaker. To fit
the reference 12 GB GPU, it stops the resident REST ASR backend before loading
the offline pipeline; a later `transcribe_audio` call auto-starts the backend
again.

`transcribe_podcast` returns the complete transcript and, when `HF_TOKEN` is
configured, a separate pyannote speaker timeline. The REST backend does not
produce word timestamps, so this tool explicitly returns
`speaker_text_attribution: false`; it does not claim which speaker said each
piece of text. `diarization_status` is `completed`, `skipped`, or `failed`, and
`diarization_error` explains skipped/failed diarization instead of silently
returning zero speakers. `num_speakers`, when supplied, is the exact expected
speaker count. An unmet exact count sets `diarization_status: failed`, clears
the unusable timeline, and preserves the independently useful transcript.

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

Before calling the model, `qwen3_asr_server.py` splits audio exceeding the
selected profile limit, transcribes each chunk independently, then concatenates
the text. The default 1.7B profile uses 480-second chunks. The bounded 0.6B
profile uses 60-second chunks and rejects attempts to raise that limit.

### Library internal auto-chunking

`split_audio_into_chunks()` (from the `qwen_asr` library):

- Target chunk length: `max_chunk_sec` (passed at call time).
- Boundary search: finds the **lowest energy point** within ±5 seconds of the cut point, avoiding mid-speech splits.
- Adjacent chunks have **no overlap, no gap**; concatenation reconstructs the original audio.
- A trailing chunk shorter than 0.5 seconds is auto zero-padded.

---

## Model Details

| Profile | Model | Chunk / output bounds | CUDA allocator limit | 8 GB status |
|---|---|---|---:|---|
| `default` | `Qwen/Qwen3-ASR-1.7B` | 480 seconds / 4096 tokens | Unset | **Not validated or advertised for 8 GB**; earlier whole-device diagnostics reached 11,977 MiB |
| `8gb` | `Qwen/Qwen3-ASR-0.6B` | 60 seconds / 1024 tokens | 6144 MiB | **Validated** at a 4658 MiB maximum across two whole-device runs with an 8000 MiB test ceiling |

Both profiles use inference batch size 1 and bfloat16 by default. At startup,
model-source precedence is an explicit `--model`/`ASR_MODEL` value, then the
complete profile-local directory, then that profile's Hugging Face model ID.
The local directories are:

- `models/safetensors/Qwen/Qwen3-ASR-1.7B` for `default`;
- `models/safetensors/Qwen/Qwen3-ASR-0.6B` for `8gb`.

A local directory is complete when it has a non-empty `config.json` plus either
a non-empty `model.safetensors`, or a valid non-empty
`model.safetensors.index.json` whose referenced shards are all present and
non-empty. Any incomplete directory falls back to the corresponding Hub ID.

The recorded 8 GB run used official Qwen3-ASR-0.6B revision
`5eb144179a02acc5e5ba31e748d22b0cf3e303b0`; its `model.safetensors` SHA-256 was
`79d6cbd4c98c7bbffe9db2edac07f56cd6637d0d5944b27f6c2b8353840323ea`.
Download that exact snapshot before relying on the recorded result:

```bash
uv run --project environments/mcp-local-asr --locked hf download \
  Qwen/Qwen3-ASR-0.6B \
  --revision 5eb144179a02acc5e5ba31e748d22b0cf3e303b0 \
  --local-dir models/safetensors/Qwen/Qwen3-ASR-0.6B
```

The 8 GB claim covers the REST-backed `transcribe_audio` path. The
speaker-attributed `transcribe_diarized` path still loads the default 1.7B model
plus the forced aligner and therefore fails closed when `ASR_PROFILE=8gb`.
`transcribe_podcast` releases the REST ASR backend before starting GPU
diarization so its heavyweight stages do not overlap, but the combined tool was
not part of the dedicated 8 GB measurement.

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
Selected Qwen3-ASR profile (complete local directory or Hugging Face fallback)
```

- **REST port**: `8000` (override with `ASR_PORT`)
- **Idle timeout**: 300 seconds of inactivity triggers auto-exit and GPU release (override with `ASR_IDLE_TIMEOUT`)
- **Logs**: `/tmp/qwen3-asr-server.log`
- **Concurrency**: FastAPI offloads synchronous model work from the event loop,
  so health checks remain responsive. A process lock serializes GPU inference;
  overlapping transcription requests wait rather than execute in parallel.
  For batch processing, submit sequentially.
- **Loopback transport**: the MCP frontend bypasses environment HTTP proxies
  for `localhost` and loopback IP addresses. Non-loopback ASR hosts retain the
  normal proxy behavior.

---

## Known Issues and Troubleshooting

### Common pitfalls

| Symptom | Root Cause | Solution |
|---|---|---|
| Long audio transcription truncated | Generation bound is too small for the chunk | Keep the validated profile defaults; the server uses 4096 tokens for `default` and 1024 for `8gb` |
| `NoBackendError` (ffmpeg not found) | System FFmpeg is missing or unavailable on `PATH` | Install system FFmpeg, verify `ffmpeg -version`, and restart the service |
| MCP tools offline after OpenCode restart | The ASR REST service (`localhost:8000`) is an independent process, not auto-recovered with OpenCode | After restart, manually run `bash asr/qwen3_asr_start.sh start`. The MCP frontend has built-in auto-wake, but the OpenCode sandbox may restrict `subprocess.Popen` — manual startup is more reliable. |
| Trailing sentences end with "…" | Generation hit `max_new_tokens` and was force-stopped | Reduce the chunk size; do not raise the bounded `8gb` maxima |
| M4A/AAC decode failure | System FFmpeg is missing or unavailable on `PATH` | Install FFmpeg and restart the ASR process; the REST wrapper uses it to normalize formats unsupported by libsndfile |

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
- Run `uv sync --project environments/mcp-local-asr --locked` and confirm `environments/mcp-local-asr/.venv/bin/python` exists
