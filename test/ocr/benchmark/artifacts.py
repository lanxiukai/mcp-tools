"""Machine-readable OCR benchmark artifact creation and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from test.ocr.benchmark.plan import reassemble_pages
from test.ocr.benchmark.rest import ChunkOutcome, GpuSample
from test.ocr.benchmark.runner import MetricRecord


class GpuArtifact(TypedDict):
    elapsed_seconds: float
    allocated_mb: float | None
    reserved_mb: float | None
    error: str | None


class JobArtifact(TypedDict):
    repetition: int
    chunk_index: int
    source_pages: list[int]
    staged_pdf: str
    status: str
    error: str | None
    returned_page_count: int
    returned_page_indexes: list[int | None]
    metrics: MetricRecord
    gpu_samples: list[GpuArtifact]


class SummaryArtifact(TypedDict):
    source_pdf: str
    selected_pages: list[int]
    repetitions: int
    job_count: int
    completed_jobs: int
    failed_jobs: int
    validation_errors: list[str]


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    """A chunk outcome labeled with the repetition that produced it."""

    repetition: int
    outcome: ChunkOutcome


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    """Files written for one benchmark invocation."""

    jsonl: Path
    summary_json: Path
    summary_markdown: Path


def write_artifacts(
    output_directory: Path,
    source_pdf: Path,
    selected_pages: tuple[int, ...],
    records: tuple[RecordedOutcome, ...],
) -> ArtifactPaths:
    """Write job JSONL plus aggregate JSON/Markdown and retain validation failures."""
    output_directory.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_directory / "jobs.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(_job_record(record), sort_keys=True) + "\n")
    summary = _summary(source_pdf, selected_pages, records)
    summary_json_path = output_directory / "summary.json"
    summary_json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_markdown_path = output_directory / "summary.md"
    summary_markdown_path.write_text(_summary_markdown(summary), encoding="utf-8")
    return ArtifactPaths(jsonl_path, summary_json_path, summary_markdown_path)


def _job_record(record: RecordedOutcome) -> JobArtifact:
    outcome = record.outcome
    return {
        "repetition": record.repetition,
        "chunk_index": outcome.chunk.plan.index,
        "source_pages": list(outcome.chunk.plan.source_pages),
        "staged_pdf": str(outcome.chunk.pdf_path),
        "status": outcome.status,
        "error": outcome.error,
        "returned_page_count": len(outcome.pages),
        "returned_page_indexes": [page.returned_page_index for page in outcome.pages],
        "metrics": outcome.metrics.as_record(),
        "gpu_samples": [_gpu_record(sample) for sample in outcome.gpu_samples],
    }


def _gpu_record(sample: GpuSample) -> GpuArtifact:
    return {
        "elapsed_seconds": sample.elapsed_seconds,
        "allocated_mb": sample.allocated_mb,
        "reserved_mb": sample.reserved_mb,
        "error": sample.error,
    }


def _summary(
    source_pdf: Path,
    selected_pages: tuple[int, ...],
    records: tuple[RecordedOutcome, ...],
) -> SummaryArtifact:
    repetitions = sorted({record.repetition for record in records})
    errors = _validation_errors(selected_pages, records, repetitions)
    completed = sum(record.outcome.status == "completed" for record in records)
    return {
        "source_pdf": str(source_pdf),
        "selected_pages": list(selected_pages),
        "repetitions": len(repetitions),
        "job_count": len(records),
        "completed_jobs": completed,
        "failed_jobs": len(records) - completed,
        "validation_errors": errors,
    }


def _validation_errors(
    selected_pages: tuple[int, ...],
    records: tuple[RecordedOutcome, ...],
    repetitions: list[int],
) -> list[str]:
    errors: list[str] = []
    for repetition in repetitions:
        pages = tuple(
            page
            for record in records
            if record.repetition == repetition
            for page in record.outcome.pages
        )
        assembled = reassemble_pages(pages)
        actual = tuple(page.source_page for page in assembled)
        if len(actual) != len(selected_pages):
            errors.append(
                f"repetition {repetition}: expected {len(selected_pages)} pages, received {len(actual)}"
            )
        if actual != selected_pages:
            errors.append(f"repetition {repetition}: source page order differs from selection")
    return errors


def _summary_markdown(summary: SummaryArtifact) -> str:
    error_lines = "\n".join(f"- {error}" for error in summary["validation_errors"]) or "- none"
    return (
        "# OCR REST benchmark summary\n\n"
        f"- Source: `{summary['source_pdf']}`\n"
        f"- Selected pages: `{summary['selected_pages']}`\n"
        f"- Repetitions: {summary['repetitions']}\n"
        f"- Jobs: {summary['job_count']} (completed: {summary['completed_jobs']}, failed: {summary['failed_jobs']})\n\n"
        "## Validation errors\n\n"
        f"{error_lines}\n"
    )
