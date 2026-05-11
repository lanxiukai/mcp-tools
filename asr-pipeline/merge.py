"""Merge diarization speaker segments with ASR word timestamps.

Produces speaker-attributed transcript segments and three output formats:
JSON (structured), SRT (subtitles), and TXT (plain text with speaker labels).
"""

import json
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core merge algorithm
# ---------------------------------------------------------------------------

def _word_midpoint(word: dict) -> float:
    """Return the midpoint (seconds) of a word's time span."""
    return (word["start"] + word["end"]) / 2.0


def _overlap_duration(word: dict, seg: dict) -> float:
    """Return overlap duration between a word and a speaker segment."""
    overlap_start = max(word["start"], seg["start"])
    overlap_end = min(word["end"], seg["end"])
    return max(0.0, overlap_end - overlap_start)


def _find_speaker(word: dict, spk_segs: list[dict]) -> str:
    """Determine which speaker a word belongs to.

    Strategy:
    1. If the word's midpoint falls inside exactly one segment, use that.
    2. If midpoint is outside all segments (or between segments), assign
       to the speaker with the longest temporal overlap.
    3. If no overlap at all, return "SPEAKER_00" (fallback).
    """
    best_speaker = "SPEAKER_00"
    best_overlap = 0.0

    mid = _word_midpoint(word)

    for seg in spk_segs:
        # Perfect match: midpoint inside segment
        if seg["start"] <= mid <= seg["end"]:
            return seg["speaker"]

        # Otherwise compute overlap for fallback
        ov = _overlap_duration(word, seg)
        if ov > best_overlap:
            best_overlap = ov
            best_speaker = seg["speaker"]

    return best_speaker


def merge_diarization_asr(
    speaker_segments: list[dict],
    asr_words: list[dict],
) -> list[dict]:
    """Merge speaker segments and word timestamps into attributed segments.

    Parameters
    ----------
    speaker_segments : list[dict]
        List of ``{"start", "end", "speaker"}`` dicts from diarization.
    asr_words : list[dict]
        List of ``{"word", "start", "end"}`` dicts from transcription.

    Returns
    -------
    list[dict]
        Each element is a speaker-attributed segment::

            {
                "speaker": "SPEAKER_01",
                "start": 0.0,
                "end": 5.0,
                "text": "Hello world ...",
                "words": [{"word": "Hello", "start": 0.2, "end": 0.8}, ...]
            }
    """
    if not asr_words:
        return []

    # If no diarization, assign all words to SPEAKER_00 in one segment
    if not speaker_segments:
        return [{
            "speaker": "SPEAKER_00",
            "start": asr_words[0]["start"],
            "end": asr_words[-1]["end"],
            "text": " ".join(w["word"] for w in asr_words),
            "words": asr_words,
        }]

    # Build a flat list of (word dict, speaker)
    word_speaker_pairs: list[tuple[dict, str]] = []
    for w in asr_words:
        spk = _find_speaker(w, speaker_segments)
        word_speaker_pairs.append((w, spk))

    # Group consecutive words from the same speaker into segments
    segments: list[dict] = []
    current_speaker = ""
    current_words: list[dict] = []

    def _emit():
        nonlocal current_speaker, current_words
        if current_words:
            segments.append({
                "speaker": current_speaker,
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "text": " ".join(w["word"] for w in current_words),
                "words": current_words,
            })
            current_words = []
            current_speaker = ""

    for word, spk in word_speaker_pairs:
        if spk != current_speaker:
            _emit()
            current_speaker = spk
        current_words.append(word)
    _emit()

    logger.info("Merged %d words into %d speaker segments", len(asr_words), len(segments))
    return segments


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp ``HH:MM:SS,mmm``."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_json(
    segments: list[dict],
    output_path: str,
    duration_sec: float = 0.0,
    language: str = "",
    num_speakers: int = 0,
) -> None:
    """Write segments as a structured JSON file.

    Parameters
    ----------
    segments : list[dict]
        Output of :func:`merge_diarization_asr`.
    output_path : str
        Destination file path.
    duration_sec : float
        Total audio duration for metadata.
    language : str
        Detected/forced language for metadata.
    num_speakers : int
        Number of detected speakers for metadata.
    """
    data = {
        "metadata": {
            "duration_sec": duration_sec,
            "language": language,
            "num_speakers": num_speakers or len({s["speaker"] for s in segments}),
        },
        "segments": segments,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("JSON written to %s", output_path)


def to_srt(segments: list[dict], output_path: str) -> None:
    """Write segments as an SRT subtitle file.

    Each speaker segment becomes one subtitle block with the speaker label
    prefixed to the text (e.g. ``[SPEAKER_01] Hello world``).
    """
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(
            f"{_format_srt_time(seg['start'])} --> {_format_srt_time(seg['end'])}"
        )
        lines.append(f"[{seg['speaker']}] {seg['text']}")
        lines.append("")  # blank line separator

    content = "\n".join(lines) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("SRT written to %s", output_path)


def to_txt(segments: list[dict], output_path: str) -> None:
    """Write segments as a plain-text transcript with speaker labels.

    Each line follows the format: ``[SPEAKER_XX] <text>``
    """
    with open(output_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"[{seg['speaker']}] {seg['text']}\n")
    logger.info("TXT written to %s", output_path)
