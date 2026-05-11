#!/usr/bin/env python3
"""Podcast ASR Pipeline — one-command offline audio transcription with speaker diarization.

Usage::

    python pipeline.py input.mp3 --language English --output-dir ./out
    python pipeline.py a.mp3 b.wav --no-diarize --format srt
    python pipeline.py --help

Version: 0.1.0
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time

# Ensure the hyphenated package directory is importable (same trick as tests).
_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import preprocess  # noqa: E402
import diarize as _diarize_mod  # noqa: E402
import transcribe as _transcribe_mod  # noqa: E402
import merge as _merge_mod  # noqa: E402

__version__ = "0.1.0"

logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Per-file pipeline runner
# ---------------------------------------------------------------------------

def _run_single(
    input_path: str,
    output_dir: str,
    language: str | None,
    context: str,
    num_speakers: int | None,
    no_diarize: bool,
    formats: set[str],
    device: str,
    hf_token: str | None,
) -> int:
    """Run the full pipeline on one audio file.  Returns exit code."""
    basename = os.path.splitext(os.path.basename(input_path))[0]
    t_start = time.monotonic()

    # Stage 1: Preprocess
    print(f"[1/4] Preprocessing {basename} ...", end=" ", flush=True)
    t0 = time.monotonic()
    try:
        wav = preprocess.preprocess_audio(input_path)
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        return 1
    duration = preprocess.get_audio_duration(wav)
    print(f"done ({time.monotonic() - t0:.1f}s, duration={duration:.0f}s)")

    # Stage 2: Diarization (optional)
    speaker_segments: list[dict] = []
    if not no_diarize:
        print("[2/4] Speaker diarization ...", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            speaker_segments = _diarize_mod.run_diarization(
                wav,
                hf_token=hf_token,
                num_speakers=num_speakers,
                device=device,
            )
            print(
                f"done ({time.monotonic() - t0:.1f}s, "
                f"{len(speaker_segments)} segments)"
            )
        except Exception as exc:
            print(f"\n  ERROR: {exc}")
            return 1
    else:
        print("[2/4] Skipping diarization (--no-diarize)")

    # Stage 3: ASR + Forced Alignment
    print("[3/4] ASR transcription + alignment ...", end=" ", flush=True)
    t0 = time.monotonic()
    try:
        asr_result = _transcribe_mod.run_transcription(
            wav,
            language=language,
            context=context,
            device=device,
        )
        print(
            f"done ({time.monotonic() - t0:.1f}s, "
            f"{len(asr_result['words'])} words)"
        )
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        return 1

    # Stage 4: Merge + Output
    print("[4/4] Merging and writing outputs ...", end=" ", flush=True)
    t0 = time.monotonic()
    try:
        segments = _merge_mod.merge_diarization_asr(
            speaker_segments, asr_result["words"]
        )
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        return 1

    os.makedirs(output_dir, exist_ok=True)

    num_speakers_count = (
        num_speakers or len({s["speaker"] for s in speaker_segments}) or 1
    )

    if "json" in formats:
        _merge_mod.to_json(
            segments,
            os.path.join(output_dir, f"{basename}.json"),
            duration_sec=duration,
            language=asr_result.get("language", ""),
            num_speakers=num_speakers_count,
        )
    if "srt" in formats:
        _merge_mod.to_srt(segments, os.path.join(output_dir, f"{basename}.srt"))
    if "txt" in formats:
        _merge_mod.to_txt(segments, os.path.join(output_dir, f"{basename}.txt"))

    total = time.monotonic() - t_start
    print(f"done ({time.monotonic() - t0:.1f}s)")
    print(f"  Total time: {total:.1f}s  |  Output: {output_dir}/")
    return 0


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Transcribe podcast/long audio with speaker diarization "
                    "and word-level timestamps.",
    )
    p.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}",
    )
    p.add_argument(
        "input", nargs="+",
        help="Input audio file(s) or '-' for stdin (PCM data piped via stdin).",
    )
    p.add_argument(
        "-l", "--language",
        choices=["English", "Chinese"],
        default=None,
        help="Force recognition language (default: auto-detect).",
    )
    p.add_argument(
        "-c", "--context", type=str, default="",
        help="Space-separated domain terms for ASR context injection "
             "(e.g. 'EBITDA ROI non-GAAP').",
    )
    p.add_argument(
        "-o", "--output-dir", type=str, default="./output/",
        help="Output directory for JSON/SRT/TXT files (default: ./output/).",
    )
    p.add_argument(
        "-n", "--num-speakers", type=int, default=None,
        help="Constrain maximum number of speakers for diarization.",
    )
    p.add_argument(
        "--no-diarize", action="store_true",
        help="Skip speaker diarization. All text will be attributed to SPEAKER_00.",
    )
    p.add_argument(
        "-f", "--format", type=str, default="all",
        choices=["json", "srt", "txt", "all"],
        help="Output format(s) to produce (default: all).",
    )
    p.add_argument(
        "--device", type=str, default="cuda:0",
        help="Torch device for diarization and ASR (default: cuda:0).",
    )
    p.add_argument(
        "--hf-token", type=str, default=None,
        help="HuggingFace token for pyannote (defaults to HF_TOKEN env var).",
    )
    return p


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve formats
    if args.format == "all":
        formats = {"json", "srt", "txt"}
    else:
        formats = {args.format}

    exit_code = 0

    for input_path in args.input:
        # Handle stdin
        if input_path == "-":
            tmpdir = tempfile.mkdtemp(prefix="asr_stdin_")
            input_path = os.path.join(tmpdir, "stdin_audio.wav")
            logger.info("Reading PCM from stdin → %s", input_path)
            data = sys.stdin.buffer.read()
            if not data:
                print("ERROR: stdin is empty", file=sys.stderr)
                return 2
            with open(input_path, "wb") as f:
                f.write(data)

        if not os.path.isfile(input_path):
            print(f"ERROR: file not found: {input_path}", file=sys.stderr)
            return 2

        ec = _run_single(
            input_path=input_path,
            output_dir=args.output_dir,
            language=args.language,
            context=args.context,
            num_speakers=args.num_speakers,
            no_diarize=args.no_diarize,
            formats=formats,
            device=args.device,
            hf_token=args.hf_token,
        )
        if ec != 0:
            exit_code = ec

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
