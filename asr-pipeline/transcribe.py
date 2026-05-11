"""ASR transcription with forced-alignment word-level timestamps.

Wraps Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B into a simple function
that returns a dict with full text and per-word timing.
"""

import logging

import torch

from qwen_asr import Qwen3ASRModel

logger = logging.getLogger(__name__)


def run_transcription(
    audio_path: str,
    language: str | None = None,
    context: str = "",
    device: str = "cuda:0",
    dtype: str = "bfloat16",
) -> dict:
    """Transcribe audio with word-level timestamps.

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

    Returns
    -------
    dict
        ``{"text": "...", "language": "English", "words": [{"word": "...",
        "start": 0.0, "end": 0.5}, ...]}``
    """
    logger.info("Loading Qwen3-ASR-1.7B + ForcedAligner on %s ...", device)

    model: Qwen3ASRModel = Qwen3ASRModel.from_pretrained(
        "Qwen/Qwen3-ASR-1.7B",
        dtype=dtype,
        forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
        forced_aligner_kwargs={"dtype": dtype},
        max_inference_batch_size=2,
        max_new_tokens=256,
    )

    try:
        logger.info("Running transcription on %s ...", audio_path)
        transcribe_kwargs = {"audio": audio_path, "return_time_stamps": True}
        if language:
            transcribe_kwargs["language"] = language
        if context:
            transcribe_kwargs["context"] = context
            logger.info("ASR context injection: %s", context)

        raw_chunks = model.transcribe(**transcribe_kwargs)

        # Combine chunks
        all_text: list[str] = []
        all_words: list[dict] = []
        detected_lang = ""

        for chunk in raw_chunks:
            all_text.append(chunk.text)
            if not detected_lang:
                detected_lang = chunk.language
            for item in chunk.time_stamps:
                all_words.append({
                    "word": item.text,
                    "start": round(item.start_time, 3),
                    "end": round(item.end_time, 3),
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
