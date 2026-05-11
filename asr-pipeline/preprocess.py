"""Audio preprocessing: resample arbitrary audio to 16kHz mono WAV via ffmpeg."""

import os
import shutil
import subprocess
import sys
import tempfile
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locate ffmpeg — may live inside the conda environment's bin/
# ---------------------------------------------------------------------------

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")

if _FFMPEG is None:
    _conda_bin = os.path.join(os.path.dirname(sys.executable), "ffmpeg")
    if os.path.isfile(_conda_bin):
        _FFMPEG = _conda_bin
if _FFPROBE is None:
    _conda_bin = os.path.join(os.path.dirname(sys.executable), "ffprobe")
    if os.path.isfile(_conda_bin):
        _FFPROBE = _conda_bin


def _is_16k_mono_wav(audio_path: str) -> bool:
    """Quick check: does the file already meet the target format?

    Uses ffprobe for reliable detection (handles edge cases soundfile misses).
    """
    if not os.path.isfile(audio_path):
        return False
    if not audio_path.lower().endswith(".wav"):
        return False
    try:
        info = validate_audio(audio_path)
        return info["sample_rate"] == 16000 and info["channels"] == 1
    except Exception:
        return False


def preprocess_audio(
    input_path: str,
    output_dir: str | None = None,
) -> str:
    """Convert *input_path* to 16kHz mono s16le WAV via ffmpeg.

    If the input is already a 16kHz mono WAV, returns the path unchanged
    (idempotent).  Otherwise writes a converted copy to *output_dir* (defaults
    to a temp directory) and returns the new path.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Short-circuit if already target format
    if _is_16k_mono_wav(input_path):
        logger.info("Input already 16kHz mono WAV — skipping conversion.")
        return input_path

    # Determine output path
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="asr_preprocess_")
    os.makedirs(output_dir, exist_ok=True)

    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{basename}_16k.wav")

    # Run ffmpeg
    if _FFMPEG is None:
        raise RuntimeError(
            "ffmpeg not found. Please install ffmpeg: sudo apt install ffmpeg"
        )

    cmd = [
        _FFMPEG,
        "-y",                     # overwrite
        "-i", input_path,
        "-ar", "16000",           # 16 kHz sample rate
        "-ac", "1",               # mono
        "-sample_fmt", "s16",     # 16-bit signed PCM
        "-loglevel", "error",     # suppress non-error output
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Please install ffmpeg: sudo apt install ffmpeg"
        ) from None
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise RuntimeError(
            f"ffmpeg conversion failed for {input_path}: {stderr.strip()}"
        ) from exc

    logger.info("Preprocessed => %s", output_path)
    return output_path


def get_audio_duration(wav_path: str) -> float:
    """Return audio duration in seconds using ffprobe (most reliable).

    Falls back to soundfile if ffprobe is not available.
    """
    if _FFPROBE is not None:
        try:
            result = subprocess.run(
                [
                    _FFPROBE,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    wav_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return float(result.stdout.strip())
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
            pass  # fall through to soundfile

    # Fallback to soundfile
    import soundfile as sf
    info = sf.info(wav_path)
    return info.duration


def validate_audio(wav_path: str) -> dict:
    """Return basic audio metadata: sample_rate, channels, duration_sec.

    Raises FileNotFoundError if *wav_path* does not exist.
    """
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    import soundfile as sf
    info = sf.info(wav_path)
    return {
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "duration_sec": info.duration,
    }
