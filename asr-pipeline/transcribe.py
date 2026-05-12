"""ASR transcription, optionally with forced-alignment word-level timestamps.

Wraps Qwen3-ASR-1.7B (+ Qwen3-ForcedAligner-0.6B when timestamps enabled).
Long audio is automatically split into VRAM-safe chunks.
"""

import logging
import os
import tempfile

import numpy as np
import soundfile as sf
import torch

from qwen_asr import Qwen3ASRModel

logger = logging.getLogger(__name__)

# Max audio duration (seconds) to transcribe in a single model call.
# Splitting into smaller chunks keeps VRAM within 12 GB budget.
_MAX_CHUNK_SEC = 480  # 8 minutes per chunk — balance VRAM safety & speed


def _read_audio_mono_f32(audio_path: str) -> np.ndarray:
    """Read audio as float32 mono at native sample rate."""
    data, sr = sf.read(audio_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)  # mix to mono
    return data, sr


def _write_temp_wav(samples: np.ndarray, sample_rate: int,
                    tmpdir: str, label: str) -> str:
    """Write a numpy array to a temporary 16-bit WAV file."""
    path = os.path.join(tmpdir, f"{label}.wav")
    sf.write(path, samples, sample_rate, subtype="PCM_16")
    return path


def run_transcription(
    audio_path: str,
    language: str | None = None,
    context: str = "",
    device: str = "cuda:0",
    dtype: str = "bfloat16",
    max_new_tokens: int = 4096,
    max_inference_batch_size: int = 1,
    return_timestamps: bool = True,
) -> dict:
    """Transcribe audio, optionally with word-level timestamps.

    Parameters
    ----------
    audio_path : str
        Path to 16kHz mono WAV file.
    language : str, optional
        ``"English"``, ``"Chinese"``, or ``None`` (auto-detect).
    context : str
        Space-separated domain terms to improve recognition (e.g.
        ``"EBITDA ROI non-GAAP"``).
    device : str
        Torch device string (``"cuda:0"`` or ``"cpu"``).
    dtype : str
        Model dtype (``"bfloat16"`` or ``"float16"``).
    max_new_tokens : int
        Maximum tokens per generation step (default 4096).
        Increase to 8192+ for audio longer than 2 hours.
    max_inference_batch_size : int
        Number of audio chunks processed in parallel (default 1).
        Set to 2–4 on GPUs with ≥16 GB VRAM.
    return_timestamps : bool
        If ``True`` (default), load the forced aligner and produce word-level
        timestamps.  This is ~4× slower than plain ASR.  Set to ``False`` for
        fast transcription of long audio (e.g. 2+ hour podcasts).

    Returns
    -------
    dict
        ``{"text": "...", "language": "English", "words": [...]}``.
        ``words`` is an empty list when ``return_timestamps=False``.
    """
    # ---------- read and chunk audio ----------
    audio_data, sr = _read_audio_mono_f32(audio_path)
    total_samples = len(audio_data)
    total_sec = total_samples / sr
    chunk_samples = int(_MAX_CHUNK_SEC * sr)

    if total_sec <= _MAX_CHUNK_SEC:
        # Audio fits in one chunk — pass directly
        chunk_offsets = [0.0]
        chunk_paths = [audio_path]
    else:
        # Split into VRAM-safe chunks
        num_chunks = (total_samples + chunk_samples - 1) // chunk_samples
        logger.info(
            "Splitting %.0fs audio into %d chunks of ≤%ds each",
            total_sec, num_chunks, _MAX_CHUNK_SEC,
        )
        tmpdir = tempfile.mkdtemp(prefix="asr_chunks_")
        chunk_offsets: list[float] = []
        chunk_paths: list[str] = []
        for i in range(num_chunks):
            start = i * chunk_samples
            end = min(start + chunk_samples, total_samples)
            offset = start / sr
            chunk_offsets.append(offset)
            chunk_paths.append(
                _write_temp_wav(audio_data[start:end], sr, tmpdir, f"chunk_{i:04d}")
            )

    # ---------- decide whether to load forced aligner ----------
    if return_timestamps:
        _fa = "Qwen/Qwen3-ForcedAligner-0.6B"
        _fa_kw = {"dtype": dtype, "device_map": device}
        logger.info(
            "Loading Qwen3-ASR-1.7B + ForcedAligner on %s (max_new_tokens=%d) ...",
            device, max_new_tokens,
        )
    else:
        _fa = None
        _fa_kw = None
        logger.info(
            "Loading Qwen3-ASR-1.7B (no timestamps) on %s (max_new_tokens=%d) ...",
            device, max_new_tokens,
        )

    model: Qwen3ASRModel = Qwen3ASRModel.from_pretrained(
        "Qwen/Qwen3-ASR-1.7B",
        dtype=dtype,
        device_map=device,
        forced_aligner=_fa,
        forced_aligner_kwargs=_fa_kw,
        max_inference_batch_size=max_inference_batch_size,
        max_new_tokens=max_new_tokens,
    )

    try:
        # ---------- transcribe each chunk ----------
        all_text: list[str] = []
        all_words: list[dict] = []
        detected_lang = ""

        for idx, (chunk_path, offset) in enumerate(zip(chunk_paths, chunk_offsets)):
            logger.info(
                "Transcribing chunk %d/%d (offset=%.0fs) ...",
                idx + 1, len(chunk_paths), offset,
            )
            transcribe_kwargs: dict = {"audio": chunk_path}
            if language:
                transcribe_kwargs["language"] = language
            if context:
                transcribe_kwargs["context"] = context
            if return_timestamps:
                transcribe_kwargs["return_time_stamps"] = True

            raw_chunks = model.transcribe(**transcribe_kwargs)

            for chunk in raw_chunks:
                all_text.append(chunk.text)
                if not detected_lang:
                    detected_lang = chunk.language
                if return_timestamps and chunk.time_stamps is not None:
                    for item in chunk.time_stamps:
                        all_words.append({
                            "word": item.text,
                            "start": round(item.start_time + offset, 3),
                            "end": round(item.end_time + offset, 3),
                        })
                        if item.start_time >= item.end_time:
                            logger.warning(
                                "Invalid timestamp for word '%s': start=%.3f end=%.3f",
                                item.text, item.start_time, item.end_time,
                            )

    finally:
        del model
        torch.cuda.empty_cache()
        logger.info(
            "Transcription complete — %d chars, %d words",
            sum(len(t) for t in all_text), len(all_words),
        )

    return {
        "text": " ".join(all_text),
        "language": detected_lang,
        "words": all_words,
    }
