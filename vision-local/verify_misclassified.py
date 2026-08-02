#!/usr/bin/env python3
"""High-resolution second pass for coarse eyewear misclassification candidates."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from batch_classify import load_latest_records, natural_key, write_json_atomic
from vision_runtime import ensure_server, load_settings, verify_eyewear


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_one(candidate: dict[str, Any], retries: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            result = verify_eyewear(candidate["file"])
            expected = candidate["expected_wearing_glasses"]
            return {
                "file": candidate["file"],
                "filename": candidate["filename"],
                "image_id": candidate.get("image_id"),
                "source_label": candidate["source_label"],
                "expected_wearing_glasses": expected,
                "coarse_predicted_wearing_glasses": candidate[
                    "predicted_wearing_glasses"
                ],
                "verified_wearing_glasses": result["wearing_glasses"],
                "verified_misclassified": result["wearing_glasses"] is not expected,
                "confidence": result["confidence"],
                "visual_cues": result["visual_cues"],
                "latency_ms": result["latency_ms"],
                "attempts": attempt,
                "error": None,
                "completed_at": utc_now(),
            }
        except Exception as exc:
            last_error = exc
            if attempt <= retries:
                time.sleep(min(4, 2 ** (attempt - 1)))
    return {
        "file": candidate["file"],
        "filename": candidate["filename"],
        "image_id": candidate.get("image_id"),
        "source_label": candidate["source_label"],
        "expected_wearing_glasses": candidate["expected_wearing_glasses"],
        "coarse_predicted_wearing_glasses": candidate[
            "predicted_wearing_glasses"
        ],
        "verified_wearing_glasses": None,
        "verified_misclassified": None,
        "confidence": None,
        "visual_cues": None,
        "latency_ms": None,
        "attempts": retries + 1,
        "error": str(last_error),
        "completed_at": utc_now(),
    }


def run(args: argparse.Namespace) -> int:
    results_path = args.results_jsonl.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not results_path.is_file():
        raise FileNotFoundError(f"Coarse results not found: {results_path}")
    if not output_dir.is_dir():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    coarse_latest = load_latest_records(results_path)
    candidates = sorted(
        (
            record
            for record in coarse_latest.values()
            if record.get("error") is None and record.get("misclassified") is True
        ),
        key=lambda item: (item["source_label"], natural_key(Path(item["filename"]))),
    )
    verification_path = output_dir / "verification-results.jsonl"
    verified_latest = load_latest_records(verification_path) if args.resume else {}
    if verification_path.exists() and not args.resume:
        raise FileExistsError(
            f"Refusing to overwrite {verification_path}; use --resume or a new output directory"
        )
    pending = [
        item
        for item in candidates
        if item["file"] not in verified_latest
        or verified_latest[item["file"]].get("error") is not None
    ]

    settings = load_settings()
    if settings.image_max_tokens < 1024:
        raise ValueError(
            "High-resolution verification requires VISION_LOCAL_IMAGE_MAX_TOKENS >= 1024"
        )
    ensure_server(settings)

    started = time.perf_counter()
    mode = "a" if verification_path.exists() and args.resume else "w"
    with verification_path.open(mode, encoding="utf-8", buffering=1) as output:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(verify_one, candidate, args.retries): candidate
                for candidate in pending
            }
            for index, future in enumerate(as_completed(futures), 1):
                record = future.result()
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                verified_latest[record["file"]] = record
                print(
                    f"[{index}/{len(pending)}] {record['filename']}: "
                    f"verified={record['verified_wearing_glasses']} "
                    f"error={record['error']}",
                    flush=True,
                )

    records = sorted(
        verified_latest.values(),
        key=lambda item: (item["source_label"], natural_key(Path(item["filename"]))),
    )
    errors = [record for record in records if record.get("error")]
    flagged = [
        record for record in records if record.get("verified_misclassified") is True
    ]
    resolved = [
        record for record in records if record.get("verified_misclassified") is False
    ]
    summary = {
        "status": "completed" if not errors else "completed_with_errors",
        "interpretation": "machine_generated_review_queue",
        "human_review_required": True,
        "completed_at": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "coarse_results_jsonl": str(results_path),
        "coarse_candidate_count": len(candidates),
        "verified_candidate_count": len(records) - len(errors),
        "verification_error_count": len(errors),
        "machine_flagged_for_review_count": len(flagged),
        "machine_resolved_count": len(resolved),
        "machine_flagged_filenames": {
            "G": [r["filename"] for r in flagged if r["source_label"] == "G"],
            "NG": [r["filename"] for r in flagged if r["source_label"] == "NG"],
        },
        "verification_error_filenames": [record["filename"] for record in errors],
    }
    write_json_atomic(output_dir / "verification-summary.json", summary)
    write_json_atomic(output_dir / "verified-misclassified.json", flagged)
    with (output_dir / "verified-misclassified.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fields = [
            "source_label",
            "image_id",
            "filename",
            "expected_wearing_glasses",
            "verified_wearing_glasses",
            "confidence",
            "visual_cues",
            "file",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: record.get(field) for field in fields} for record in flagged)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--resume", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 4:
        parser.error("--concurrency must be between 1 and 4")
    if args.retries < 0:
        parser.error("--retries must be non-negative")
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Interrupted; rerun with --resume to continue.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
