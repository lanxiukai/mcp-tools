"""Speaker diarization: wrapper around pyannote speaker-diarization-3.1.

Long audio (>15 min) is automatically split into VRAM-safe chunks.
"""

import os
import logging
import tempfile
import shutil

import numpy as np
import soundfile as sf
import torch

from pyannote.audio import Pipeline

logger = logging.getLogger(__name__)

# Max audio duration per diarization call — keep VRAM within 12 GB budget
_DIARIZE_CHUNK_SEC = 900  # 15 minutes


def _load_pipeline(token: str):
    """Load pyannote Pipeline with auth error handling."""
    logger.info("Loading pyannote speaker-diarization-3.1 ...")
    try:
        return Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=token,
        )
    except Exception as exc:
        msg = str(exc).lower()
        exc_name = type(exc).__name__
        if any(kw in msg or kw in exc_name for kw in (
            "401", "403", "unauthorized", "gatedrepo",
        )):
            raise RuntimeError(
                "pyannote authentication failed. Ensure:\n"
                "  1. You have accepted the model terms at "
                "https://hf.co/pyannote/segmentation-3.0, "
                "https://hf.co/pyannote/speaker-diarization-3.1\n"
                "  2. Your HF_TOKEN is valid"
            ) from exc
        raise


def _diarize_chunk(
    pipeline: Pipeline,
    audio_path: str,
    device: torch.device,
    num_speakers: int | None = None,
    offset: float = 0.0,
) -> list[dict]:
    """Run diarization on one audio chunk, return offset-adjusted segments."""
    call_kwargs: dict = {}
    if num_speakers is not None:
        call_kwargs["num_speakers"] = num_speakers

    raw_output = pipeline(audio_path, **call_kwargs)

    if hasattr(raw_output, "speaker_diarization"):
        annotation = raw_output.speaker_diarization
    else:
        annotation = raw_output  # pyannote 3.x compat

    segments: list[dict] = []
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({
            "start": round(segment.start + offset, 3),
            "end": round(segment.end + offset, 3),
            "speaker": speaker,
        })
    return segments


def run_diarization(
    audio_path: str,
    hf_token: str | None = None,
    num_speakers: int | None = None,
    device: str = "cuda",
) -> list[dict]:
    """Run speaker diarization on a 16kHz mono WAV file.

    Long audio (>15 min) is automatically split into VRAM-safe chunks.
    Each chunk is diarized independently and segments are merged.

    Returns a list of speaker segments, each a dict with ``start``, ``end``,
    and ``speaker`` keys.  Segments are sorted by start time.

    Parameters
    ----------
    audio_path : str
        Path to a 16kHz mono WAV file.
    hf_token : str, optional
        HuggingFace access token.  Defaults to ``os.environ["HF_TOKEN"]``.
    num_speakers : int, optional
        Constrain the maximum number of speakers to detect.
    device : str
        ``"cuda"`` or ``"cpu"``.

    Raises
    ------
    RuntimeError
        If token is missing, HF auth fails, or CUDA OOM occurs.
    """
    token = hf_token or os.environ.get("HF_TOKEN", "")
    if not token:
        raise RuntimeError(
            "HuggingFace token required for diarization. "
            "Set the HF_TOKEN environment variable or pass hf_token=..."
        )

    # Read audio to determine length
    data, sr = sf.read(audio_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)  # mix to mono
    total_sec = len(data) / sr

    pipeline = _load_pipeline(token)
    try:
        pipeline.to(torch.device(device))

        if total_sec <= _DIARIZE_CHUNK_SEC:
            # Short audio — direct call
            logger.info("Running diarization on %s (%.0fs) ...", os.path.basename(audio_path), total_sec)
            segments = _diarize_chunk(pipeline, audio_path, torch.device(device), num_speakers)
        else:
            # Long audio — split into chunks
            chunk_samples = int(_DIARIZE_CHUNK_SEC * sr)
            num_chunks = (len(data) + chunk_samples - 1) // chunk_samples
            logger.info(
                "Long audio (%.0fs) — splitting diarization into %d chunks of ≤%ds",
                total_sec, num_chunks, _DIARIZE_CHUNK_SEC,
            )

            tmpdir = tempfile.mkdtemp(prefix="diarize_chunks_")
            all_segments: list[dict] = []

            try:
                for i in range(num_chunks):
                    start = i * chunk_samples
                    end = min(start + chunk_samples, len(data))
                    offset = start / sr

                    chunk_path = os.path.join(tmpdir, f"dchunk_{i:04d}.wav")
                    sf.write(chunk_path, data[start:end], sr, subtype="PCM_16")

                    logger.info("Diarizing chunk %d/%d (%.0f–%.0fs) ...",
                                i + 1, num_chunks, offset, end / sr)
                    chunk_segs = _diarize_chunk(
                        pipeline, chunk_path, torch.device(device),
                        num_speakers, offset=offset,
                    )
                    all_segments.extend(chunk_segs)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            segments = sorted(all_segments, key=lambda s: s["start"])

    except torch.cuda.OutOfMemoryError:
        raise RuntimeError(
            "CUDA out of memory during diarization. "
            "Try using device='cpu' for the diarization stage."
        )
    finally:
        del pipeline
        torch.cuda.empty_cache()
        logger.info("Diarization complete — %d segments", len(segments))

    return segments
