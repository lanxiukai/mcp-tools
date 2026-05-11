"""Speaker diarization: wrapper around pyannote speaker-diarization-3.1."""

import os
import logging

import torch

from pyannote.audio import Pipeline

logger = logging.getLogger(__name__)


def run_diarization(
    audio_path: str,
    hf_token: str | None = None,
    num_speakers: int | None = None,
    device: str = "cuda",
) -> list[dict]:
    """Run speaker diarization on a 16kHz mono WAV file.

    Returns a list of speaker segments, each a dict with ``start``, ``end``,
    and ``speaker`` keys.  Segments are sorted by start time and guaranteed
    non-overlapping (by pyannote).

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

    logger.info("Loading pyannote speaker-diarization-3.1 ...")
    try:
        pipeline: Pipeline = Pipeline.from_pretrained(
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
                "https://hf.co/pyannote/speaker-diarization-3.1, and "
                "https://hf.co/pyannote/speaker-diarization-community-1\n"
                "  2. Your HF_TOKEN is valid"
            ) from exc
        raise

    segments: list[dict] = []

    try:
        pipeline.to(torch.device(device))
        logger.info("Running diarization on %s ...", os.path.basename(audio_path))

        # pyannote 4.x: __call__ kwargs include num_speakers
        call_kwargs: dict = {}
        if num_speakers is not None:
            call_kwargs["num_speakers"] = num_speakers

        raw_output = pipeline(audio_path, **call_kwargs)

        # pyannote 4.x returns DiarizeOutput; 3.x returns Annotation directly.
        # Access the Annotation via .speaker_diarization when available.
        if hasattr(raw_output, "speaker_diarization"):
            annotation = raw_output.speaker_diarization
        else:
            annotation = raw_output  # 3.x backward compat

        # Build segment list from Annotation
        for segment, _, speaker in annotation.itertracks(yield_label=True):
            segments.append({
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "speaker": speaker,
            })

    except torch.cuda.OutOfMemoryError:
        raise RuntimeError(
            "CUDA out of memory during diarization. "
            "Try using device='cpu' for the diarization stage."
        )
    finally:
        # Release the pipeline and free GPU memory
        del pipeline
        torch.cuda.empty_cache()
        logger.info("Diarization complete — %d segments", len(segments))

    # Segments from pyannote are already sorted by start time
    return segments
