#!/usr/bin/env python3
"""Resumable concurrent eyewear classification for two labeled directories."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vision_runtime import (
    SUPPORTED_IMAGE_TYPES,
    VisionSettings,
    classify_eyewear,
    ensure_server,
    load_settings,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def natural_key(path: Path) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", path.name.lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def image_id(path: Path) -> int | None:
    match = re.search(r"\d+", path.stem)
    return int(match.group()) if match else None


def list_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Input directory not found: {directory}")
    return sorted(
        (
            path.resolve()
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_TYPES
        ),
        key=natural_key,
    )


def write_json_atomic(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_latest_records(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return latest
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if isinstance(record, dict) and isinstance(record.get("file"), str):
            latest[record["file"]] = record
    return latest


def classify_one(
    path: Path,
    source_label: str,
    expected: bool,
    *,
    max_edge: int,
    retries: int,
    settings: VisionSettings,
) -> dict[str, Any]:
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            prediction = classify_eyewear(
                path,
                max_edge=max_edge,
                settings=settings,
            )
            predicted = prediction["wearing_glasses"]
            return {
                "file": str(path),
                "filename": path.name,
                "image_id": image_id(path),
                "source_label": source_label,
                "expected_wearing_glasses": expected,
                "predicted_wearing_glasses": predicted,
                "misclassified": predicted is not expected,
                "confidence": prediction["confidence"],
                "latency_ms": prediction["latency_ms"],
                "attempts": attempt,
                "error": None,
                "completed_at": utc_now(),
            }
        except Exception as exc:  # Each failed image is preserved as a structured record.
            last_error = exc
            if attempt <= retries:
                time.sleep(min(4, 2 ** (attempt - 1)))

    return {
        "file": str(path),
        "filename": path.name,
        "image_id": image_id(path),
        "source_label": source_label,
        "expected_wearing_glasses": expected,
        "predicted_wearing_glasses": None,
        "misclassified": None,
        "confidence": None,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "attempts": retries + 1,
        "error": str(last_error),
        "completed_at": utc_now(),
    }


def summarize(
    records: list[dict[str, Any]],
    *,
    started_at: str,
    elapsed_seconds: float,
    input_dirs: dict[str, str],
) -> dict[str, Any]:
    successful = [record for record in records if record.get("error") is None]
    errors = [record for record in records if record.get("error") is not None]
    mistakes = [record for record in successful if record.get("misclassified") is True]
    g_mistakes = [record for record in mistakes if record["source_label"] == "G"]
    ng_mistakes = [record for record in mistakes if record["source_label"] == "NG"]
    return {
        "status": "completed" if not errors else "completed_with_errors",
        "started_at": started_at,
        "completed_at": utc_now(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "throughput_images_per_second": (
            round(len(successful) / elapsed_seconds, 3) if elapsed_seconds else None
        ),
        "input_directories": input_dirs,
        "total_images": len(records),
        "successful_images": len(successful),
        "error_images": len(errors),
        "misclassified_images": len(mistakes),
        "g_without_glasses": len(g_mistakes),
        "ng_with_glasses": len(ng_mistakes),
        "misclassified_filenames": {
            "G": [record["filename"] for record in g_mistakes],
            "NG": [record["filename"] for record in ng_mistakes],
        },
        "error_filenames": [record["filename"] for record in errors],
    }


def write_final_artifacts(
    output_dir: Path,
    latest: dict[str, dict[str, Any]],
    *,
    started_at: str,
    elapsed_seconds: float,
    input_dirs: dict[str, str],
) -> dict[str, Any]:
    records = sorted(latest.values(), key=lambda item: (item["source_label"], natural_key(Path(item["filename"]))))
    mistakes = [record for record in records if record.get("misclassified") is True]
    summary = summarize(
        records,
        started_at=started_at,
        elapsed_seconds=elapsed_seconds,
        input_dirs=input_dirs,
    )
    write_json_atomic(output_dir / "summary.json", summary)
    write_json_atomic(output_dir / "misclassified.json", mistakes)

    with (output_dir / "misclassified.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_label",
                "image_id",
                "filename",
                "expected_wearing_glasses",
                "predicted_wearing_glasses",
                "confidence",
                "file",
            ],
        )
        writer.writeheader()
        writer.writerows({key: record.get(key) for key in writer.fieldnames} for record in mistakes)
    return summary


def run(args: argparse.Namespace) -> int:
    g_dir = args.g_dir.expanduser().resolve()
    ng_dir = args.ng_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    g_images = list_images(g_dir)
    ng_images = list_images(ng_dir)
    work = [(path, "G", True) for path in g_images]
    work.extend((path, "NG", False) for path in ng_images)
    work.sort(key=lambda item: (item[1], natural_key(item[0])))
    if not work:
        raise ValueError("No supported images found")

    results_path = output_dir / "results.jsonl"
    latest = load_latest_records(results_path) if args.resume else {}
    pending = [
        item
        for item in work
        if str(item[0]) not in latest or latest[str(item[0])].get("error") is not None
    ]
    if not args.resume and results_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite {results_path}; use a new output directory or --resume"
        )

    settings = load_settings("batch")
    started_at = utc_now()
    run_started = time.perf_counter()
    manifest = {
        "status": "running",
        "started_at": started_at,
        "pid": os.getpid(),
        "input_directories": {"G": str(g_dir), "NG": str(ng_dir)},
        "input_counts": {"G": len(g_images), "NG": len(ng_images), "total": len(work)},
        "pending_at_start": len(pending),
        "resume": args.resume,
        "concurrency": args.concurrency,
        "max_edge": args.max_edge,
        "retries": args.retries,
        "runtime": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
            if key != "log_path"
        },
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    write_json_atomic(
        output_dir / "progress.json",
        {"status": "starting_backend", "completed": len(work) - len(pending), "total": len(work)},
    )
    ensure_server(settings)

    mode = "a" if results_path.exists() and args.resume else "w"
    completed_this_run = 0
    with results_path.open(mode, encoding="utf-8", buffering=1) as output:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(
                    classify_one,
                    path,
                    source_label,
                    expected,
                    max_edge=args.max_edge,
                    retries=args.retries,
                    settings=settings,
                ): path
                for path, source_label, expected in pending
            }
            for future in as_completed(futures):
                record = future.result()
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                latest[record["file"]] = record
                completed_this_run += 1
                completed_total = len(work) - len(pending) + completed_this_run
                elapsed = time.perf_counter() - run_started
                if completed_this_run % args.progress_every == 0 or completed_total == len(work):
                    rate = completed_this_run / elapsed if elapsed else 0.0
                    remaining = len(work) - completed_total
                    progress = {
                        "status": "running",
                        "completed": completed_total,
                        "total": len(work),
                        "errors": sum(record.get("error") is not None for record in latest.values()),
                        "rate_images_per_second": round(rate, 3),
                        "eta_seconds": round(remaining / rate) if rate else None,
                        "updated_at": utc_now(),
                    }
                    write_json_atomic(output_dir / "progress.json", progress)
                    print(
                        f"[{completed_total}/{len(work)}] {rate:.2f} image/s; "
                        f"errors={progress['errors']}; eta={progress['eta_seconds']}s",
                        flush=True,
                    )

    elapsed_seconds = time.perf_counter() - run_started
    summary = write_final_artifacts(
        output_dir,
        latest,
        started_at=started_at,
        elapsed_seconds=elapsed_seconds,
        input_dirs={"G": str(g_dir), "NG": str(ng_dir)},
    )
    write_json_atomic(
        output_dir / "progress.json",
        {
            "status": summary["status"],
            "completed": summary["successful_images"] + summary["error_images"],
            "total": summary["total_images"],
            "errors": summary["error_images"],
            "updated_at": utc_now(),
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["error_images"] == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--g-dir", type=Path, required=True, help="Directory labeled as wearing glasses")
    parser.add_argument("--ng-dir", type=Path, required=True, help="Directory labeled as not wearing glasses")
    parser.add_argument("--output-dir", type=Path, required=True, help="New or resumable artifact directory")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-edge", type=int, default=512)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume successful records from an existing results.jsonl (default: true)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    for name in ("concurrency", "max_edge", "progress_every"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.retries < 0:
        parser.error("--retries must be non-negative")
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Interrupted; rerun with the same output directory to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
