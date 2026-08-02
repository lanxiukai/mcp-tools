"""
Qwen3-ASR MCP Server — Expose speech-to-text to OpenCode agents

Tools:
    transcribe_audio    — Transcribe audio files (auto-starts ASR server)
    transcribe_diarized — Full pipeline with speaker-attributed text
    transcribe_podcast  — Full transcript plus a separate speaker timeline
    asr_status          — Check ASR server status

Auto-start logic:
    When transcribe_audio is called, the MCP server checks if ASR is online:
    - Online → transcribe directly
    - Offline → auto-run asr/qwen3_asr_start.sh, wait for readiness, then transcribe

Usage (opencode.jsonc):
    "asr": {
      "command": "<YOUR-PYTHON>",
      "args": ["<REPO-DIR>/asr/asr_mcp_server.py"],
      "enabled": true
    }
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent.parent
START_SCRIPT = REPO_DIR / "asr" / "qwen3_asr_start.sh"
ASR_HOST = os.environ.get("ASR_HOST", "localhost")
ASR_PORT = int(os.environ.get("ASR_PORT", "8000"))
ASR_LOG_FILE = os.environ.get("ASR_LOG_FILE", "/tmp/qwen3-asr-server.log")

# ---------------------------------------------------------------------------
# MCP Server instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Qwen3-ASR",
    json_response=True,
    instructions="Speech-to-text transcription via Qwen3-ASR-1.7B. "
                  "Four tools available: "
                  "(1) transcribe_audio — fast transcription for any audio; "
                  "(2) transcribe_diarized — the full offline pipeline for "
                  "speaker-attributed text with word timestamps (needs HF_TOKEN); "
                  "(3) transcribe_podcast — full transcription plus a separate "
                  "speaker timeline for multi-person podcasts/meetings (needs HF_TOKEN); "
                  "(4) asr_status — check server health. "
                  "Use transcribe_diarized when you need to know who said what. "
                  "Use transcribe_audio for single-speaker or when speed matters most. "
                  "Use transcribe_podcast only when a separate transcript and speaker "
                  "timeline are sufficient. Specify language for clearly monolingual "
                  "audio; omit it for Mandarin-English code-switching. Treat "
                  "num_speakers as an exact count, not a maximum. All transcription "
                  "tools support long audio. "
                  "The ASR server is auto-started on first use.",
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _health_url() -> str:
    return f"http://{ASR_HOST}:{ASR_PORT}/health"


def _transcribe_url() -> str:
    return f"http://{ASR_HOST}:{ASR_PORT}/v1/audio/transcriptions"


def _check_asr_health(timeout: float = 3.0) -> bool:
    """Quick check if ASR server is online"""
    try:
        req = urllib.request.Request(_health_url())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_asr_server() -> bool:
    """Start ASR server in background, poll until ready (max 60s)"""
    if not START_SCRIPT.exists():
        sys.stderr.write(f"[asr_mcp] Start script not found: {START_SCRIPT}\n")
        return False

    sys.stderr.write(f"[asr_mcp] Starting ASR server: {START_SCRIPT}\n")
    env = os.environ.copy()
    env["ASR_PYTHON"] = sys.executable  # always point to the interpreter we're already using
    subprocess.Popen(
        ["bash", str(START_SCRIPT), "start"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Brief pause then check for immediate failure
    time.sleep(0.5)
    try:
        result = subprocess.run(
            ["bash", str(START_SCRIPT), "status"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            sys.stderr.write(f"[asr_mcp] ASR server failed to start: {result.stderr.strip()}\n")
            return False
    except subprocess.TimeoutExpired:
        pass  # status check hung — continue polling health below

    max_wait = 60
    for i in range(max_wait):
        time.sleep(1)
        if _check_asr_health(timeout=1.0):
            sys.stderr.write(f"[asr_mcp] ASR server ready after {i + 1}s\n")
            return True
    sys.stderr.write("[asr_mcp] ASR server startup timed out\n")
    return False


def _stop_competing_servers():
    """Stop other GPU-hungry model servers before starting ASR.

    On a 12 GB GPU, only one model can fit at a time.  Kill the
    OCR server to free VRAM, then pause briefly for the GPU driver
    to reclaim the memory.
    """
    competing = [
        REPO_DIR / "ocr" / "ocr_start.sh",
    ]
    for script in competing:
        if script.exists():
            subprocess.run(
                ["bash", str(script), "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
    time.sleep(1)  # brief wait for GPU memory reclamation


def _ensure_asr_ready() -> bool:
    """Ensure ASR server is online: check first, auto-start if offline (kill competing GPU servers first)"""
    if _check_asr_health():
        return True
    _stop_competing_servers()
    sys.stderr.write("[asr_mcp] ASR server not running, auto-starting...\n")
    return _start_asr_server()


def _stop_asr_backend() -> bool:
    """Stop the resident REST backend before loading the offline pipeline.

    The MCP frontend remains alive. A later ``transcribe_audio`` call will
    auto-start the REST backend again.
    """
    if not _check_asr_health(timeout=1.0):
        return True
    try:
        result = subprocess.run(
            ["bash", str(START_SCRIPT), "stop"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"[asr_mcp] Failed to stop ASR backend: {exc}\n")
        return False

    if result.returncode != 0:
        sys.stderr.write(
            f"[asr_mcp] ASR backend stop command failed: "
            f"{result.stderr.strip() or result.stdout.strip()}\n"
        )
        return False

    for _ in range(20):
        if not _check_asr_health(timeout=0.25):
            return True
        time.sleep(0.25)
    sys.stderr.write("[asr_mcp] ASR backend did not stop within 5s\n")
    return False


# The underlying qwen_asr library accepts only full language names
# (e.g. 'English', 'Chinese'), but our public API documents 2-letter
# codes (e.g. 'en', 'zh') and ISO-639-1 codes are common in MCP usage.
# Normalize both forms transparently so callers can use either.
_LANGUAGE_ALIASES = {
    # 2-letter ISO-639-1 → qwen_asr full names
    "en": "English",
    "zh": "Chinese",
    "yue": "Cantonese",
    "ar": "Arabic",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "id": "Indonesian",
    "it": "Italian",
    "ko": "Korean",
    "ru": "Russian",
    "th": "Thai",
    "vi": "Vietnamese",
    "ja": "Japanese",
    "tr": "Turkish",
    "hi": "Hindi",
    "ms": "Malay",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "tl": "Filipino",
    "fa": "Persian",
    "el": "Greek",
    "ro": "Romanian",
    "hu": "Hungarian",
    "mk": "Macedonian",
}


def _normalize_language(language: Optional[str]) -> Optional[str]:
    """Map 2-letter / lowercase / mixed-case language inputs to qwen_asr's expected
    full-name form. Pass-through for inputs that already look correct so unknown
    codes still surface a clear error from the backend.
    """
    if not language:
        return language
    key = language.strip().lower()
    if key in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[key]
    # Already a full name like 'English' → title-case to be safe
    return language.strip().title()


def _transcribe_file(file_path: str, language: Optional[str] = None, timeout: int = 1800) -> dict:
    """Call ASR REST API to transcribe audio file (long audio timeout 30 min)"""
    from urllib.request import Request, urlopen

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a regular file: {file_path}"}

    language = _normalize_language(language)

    with open(path, "rb") as f:
        audio_data = f.read()

    boundary = "----Qwen3ASRMCPBoundary"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += audio_data
    body += b"\r\n"
    if language:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="language"\r\n\r\n'.encode()
        body += f"{language}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()

    req = Request(
        _transcribe_url(),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        return {"error": f"API call failed: {e}"}

    return result


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def transcribe_audio(
    file_path: str,
    language: Optional[str] = None,
) -> dict:
    """Transcribe an audio file to text using Qwen3-ASR-1.7B.

    Fast, single-pass transcription.  Use this for:
      - Single-speaker audio (lectures, monologues, voice memos)
      - Quick text extraction when speaker identity doesn't matter
      - Any audio where you just need the transcript

    For multi-person podcasts/meetings where you need to know WHO
    said WHAT, use transcribe_diarized() instead. It runs the full
    offline pipeline with word timestamps and speaker attribution
    (requires HF_TOKEN).

    Supports WAV, MP3, FLAC, OGG, and other common audio formats.
    Supports 52 languages including Chinese, English, Japanese, Korean, etc.
    Handles long audio (2h+) via automatic 480s chunking.
    The ASR server is automatically started if not running.

    Args:
        file_path: Absolute path to the audio file (e.g. /home/user/audio.wav)
        language: Optional language hint.  Both 2-letter ISO-639-1 codes
                  ('en', 'zh', 'ja', 'ko', ...) and full names
                  ('English', 'Chinese', 'Japanese', 'Korean', ...) are
                  accepted.  Leave empty for automatic language detection.

    Returns:
        A dict with keys:
          - text: Transcribed text
          - language: Detected or specified language
          - error: Error message if transcription failed
    """
    if not _ensure_asr_ready():
        return {"error": f"Failed to start ASR server. Check logs at {ASR_LOG_FILE}"}

    result = _transcribe_file(file_path, language=language)

    return result


@mcp.tool()
def asr_status() -> dict:
    """Check the status of the Qwen3-ASR server.

    Returns server health info including GPU memory usage.
    """
    if not _check_asr_health(timeout=2.0):
        return {"status": "offline", "message": "ASR server is not running"}

    try:
        req = urllib.request.Request(_health_url())
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            info = json.loads(resp.read().decode())
        return info
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Pipeline path — allow importing from the hyphenated asr-pipeline/ directory
# ---------------------------------------------------------------------------
_PIPELINE_DIR = str(REPO_DIR / "asr-pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# Do NOT eagerly import — pyannote/torch are heavy. Import on first use
# inside pipeline-backed tools so that plain transcribe_audio() stays fast.


@mcp.tool()
def transcribe_diarized(
    file_path: str,
    language: Optional[str] = None,
    num_speakers: Optional[int] = None,
    context: str = "",
) -> dict:
    """Transcribe multi-speaker audio and return speaker-attributed text.

    This is the complete offline ASR Pipeline exposed as an MCP tool:
      1. Normalize audio to 16 kHz mono WAV.
      2. Run pyannote speaker diarization.
      3. Run Qwen3-ASR with the forced aligner for word timestamps.
      4. Merge words into speaker-attributed transcript segments.

    Use this tool when the user needs to know WHO said WHAT. It always enables
    timestamps because speaker/text attribution is not reliable without them.
    The resident REST ASR backend is stopped first to avoid loading duplicate
    model copies on a 12 GB GPU; later transcribe_audio calls auto-start it.

    Args:
        file_path: Absolute path to the audio file.
        language: Optional language hint. Accepts ISO codes such as ``en`` and
                  ``zh`` or full names. Specify it for clearly monolingual
                  audio to avoid auto-detection generation long tails; omit it
                  for Mandarin-English code-switching audio.
        num_speakers: Optional exact expected speaker count. Omit for automatic
                      detection, then treat the detected count as provisional.
        context: Optional space-separated domain terms or proper nouns.

    Returns:
        A dict with keys:
          - text: Full transcribed text
          - language: Detected or specified language
          - duration_sec: Audio duration in seconds
          - elapsed_sec: End-to-end pipeline time
          - num_speakers: Number of detected speakers
          - speaker_text_attribution: True on success
          - segments: List of {speaker, start, end, text, words}
          - error: Actionable error message if a stage failed
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a regular file: {file_path}"}
    if num_speakers is not None and num_speakers < 1:
        return {"error": "num_speakers must be a positive integer"}

    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        return {
            "error": (
                "HF_TOKEN is required for speaker-attributed transcription. "
                "Configure HF_TOKEN and pyannote model access, then retry."
            ),
            "speaker_text_attribution": False,
        }

    language = _normalize_language(language)
    started = time.monotonic()

    if not _stop_asr_backend():
        return {
            "error": (
                "Failed to stop the resident ASR backend before loading the "
                "offline pipeline. Check the ASR server log and retry."
            ),
            "speaker_text_attribution": False,
        }
    _stop_competing_servers()

    try:
        import diarize as _diarize_mod
        import merge as _merge_mod
        import preprocess as _pre
        import transcribe as _transcribe_mod
    except Exception as exc:
        return {
            "error": f"Failed to import ASR Pipeline dependencies: {exc}",
            "speaker_text_attribution": False,
        }

    with tempfile.TemporaryDirectory(prefix="asr_diarized_") as tmpdir:
        try:
            wav_path = _pre.preprocess_audio(file_path, output_dir=tmpdir)
            duration = _pre.get_audio_duration(wav_path)
        except Exception as exc:
            return {
                "error": f"Preprocessing failed: {exc}",
                "speaker_text_attribution": False,
            }

        try:
            speaker_segments = _diarize_mod.run_diarization(
                wav_path,
                hf_token=hf_token,
                num_speakers=num_speakers,
                device="cuda:0",
            )
        except Exception as exc:
            return {
                "error": f"Speaker diarization failed: {exc}",
                "speaker_text_attribution": False,
            }
        if not speaker_segments:
            return {
                "error": "Speaker diarization returned no speech segments.",
                "speaker_text_attribution": False,
            }

        try:
            asr_result = _transcribe_mod.run_transcription(
                wav_path,
                language=language,
                context=context,
                device="cuda:0",
                max_new_tokens=4096,
                max_inference_batch_size=1,
                return_timestamps=True,
            )
        except Exception as exc:
            return {
                "error": f"Timestamped transcription failed: {exc}",
                "speaker_text_attribution": False,
            }

        words = asr_result.get("words", [])
        if not words:
            return {
                "error": (
                    "Timestamped transcription returned no word timestamps; "
                    "speaker text attribution could not be produced."
                ),
                "text": asr_result.get("text", ""),
                "language": asr_result.get("language", language or "unknown"),
                "duration_sec": duration,
                "speaker_text_attribution": False,
                "segments": [],
            }

        try:
            segments = _merge_mod.merge_diarization_asr(
                speaker_segments,
                words,
            )
        except Exception as exc:
            return {
                "error": f"Speaker/text merge failed: {exc}",
                "speaker_text_attribution": False,
            }

    return {
        "text": asr_result.get("text", ""),
        "language": asr_result.get("language", language or "unknown"),
        "duration_sec": duration,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "num_speakers": len({segment["speaker"] for segment in speaker_segments}),
        "speaker_text_attribution": True,
        "segments": segments,
    }


@mcp.tool()
def transcribe_podcast(
    file_path: str,
    language: Optional[str] = None,
    num_speakers: Optional[int] = None,
) -> dict:
    """Transcribe podcast/long audio and return a separate speaker timeline.

    This REST-backed tool returns the full transcript and diarization timeline,
    but cannot map transcript words to speakers because the REST API does not
    provide word timestamps. For speaker-attributed text (WHO said WHAT), use
    transcribe_diarized(). For single-speaker audio, use transcribe_audio()
    instead (it's faster).

    Two-stage pipeline:
      1. ASR transcription via the REST API (fast, auto-chunked at 480s).
      2. Speaker diarization via pyannote (requires HF_TOKEN env var).

    Total time for 2h audio: ~20–25 min on RTX 4070 Ti 12 GB.

    Args:
        file_path: Absolute path to the audio file.
        language: Optional language hint.  Both 2-letter ISO-639-1 codes
                  ('en', 'zh', ...) and full names ('English', 'Chinese', ...)
                  are accepted.  Auto-detect if empty.
        num_speakers: Optional exact expected number of speakers.

    Returns:
        A dict with keys:
          - text: Full transcribed text
          - language: Detected or specified language
          - duration_sec: Audio duration in seconds
          - num_speakers: Number of detected speakers (0 if diarization skipped)
          - segments: List of {speaker, start, end} dicts
          - diarization_status: completed, skipped, or failed
          - diarization_error: Actionable reason when skipped or failed
          - speaker_text_attribution: Always false for this REST-backed tool
          - error: Error message if something failed
    """
    if num_speakers is not None and num_speakers < 1:
        return {"error": "num_speakers must be a positive integer"}

    # ---- stage 1: ASR via REST API ----
    if not _ensure_asr_ready():
        return {"error": f"Failed to start ASR server. Check logs at {ASR_LOG_FILE}"}

    # Preprocess audio to 16kHz WAV so diarization gets the right format
    import preprocess as _pre
    try:
        wav_path = _pre.preprocess_audio(file_path)
        duration = _pre.get_audio_duration(wav_path)
    except Exception as exc:
        return {"error": f"Preprocessing failed: {exc}"}

    # Transcribe via REST API
    sys.stderr.write(f"[asr_mcp] Transcribing via REST API ...\n")
    asr_result = _transcribe_file(file_path, language=language)
    if "error" in asr_result:
        return asr_result

    full_text = asr_result.get("text", "")
    detected_lang = asr_result.get("language", "")

    # ---- stage 2: diarization (optional) ----
    speaker_segments: list[dict] = []
    num_spk = 0
    diarization_status = "skipped"
    diarization_error: str | None = None

    hf_token = os.environ.get("HF_TOKEN", "")
    if hf_token:
        try:
            import diarize as _diarize_mod
            sys.stderr.write(f"[asr_mcp] Running speaker diarization ...\n")
            speaker_segments = _diarize_mod.run_diarization(
                wav_path,
                hf_token=hf_token,
                num_speakers=num_speakers,
                device="cuda",
            )
            num_spk = len({s["speaker"] for s in speaker_segments})
            diarization_status = "completed"
            sys.stderr.write(
                f"[asr_mcp] Diarization: {len(speaker_segments)} segments, "
                f"{num_spk} speakers\n"
            )
        except Exception as exc:
            sys.stderr.write(f"[asr_mcp] Diarization failed: {exc}\n")
            diarization_status = "failed"
            diarization_error = str(exc)
            # Continue without diarization — the transcript is still useful.
    else:
        diarization_error = (
            "HF_TOKEN is not set; speaker diarization was skipped. "
            "Configure HF_TOKEN and pyannote model access, then retry."
        )

    return {
        "text": full_text,
        "language": detected_lang or "unknown",
        "duration_sec": duration,
        "num_speakers": num_spk,
        "diarization_status": diarization_status,
        "diarization_error": diarization_error,
        "speaker_text_attribution": False,
        "attribution_note": (
            "The full transcript and speaker timeline are separate because the "
            "REST API has no word timestamps. Use transcribe_diarized for "
            "speaker-attributed text."
        ),
        "segments": [
            {
                "speaker": s["speaker"],
                "start": s["start"],
                "end": s["end"],
            }
            for s in speaker_segments
        ],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
