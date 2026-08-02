"""CLI entry point for bounded, staged OCR REST benchmarks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from ocr.pdf_staging import (
    ChunkPlanningError,
    PdfStagingError,
    StagedChunk,
    plan_chunks,
    source_page_count,
    stage_chunk_pdfs,
)
from test.ocr.benchmark.artifacts import RecordedOutcome, write_artifacts
from test.ocr.benchmark.plan import PageRangeError, parse_page_ranges
from test.ocr.benchmark.rest import ChunkOutcome, OcrRestClient, execute_staged_chunk
from test.ocr.benchmark.runner import ConcurrencyError, run_bounded


@dataclass(frozen=True, slots=True)
class BenchmarkSettings:
    """Validated benchmark inputs shared by staging and client execution."""

    source_pdf: Path
    page_ranges: str
    max_pages: int | None
    pages_per_job: int
    concurrency: int
    repetitions: int
    server_url: str
    timeout_seconds: float
    sample_interval_seconds: float
    output_directory: Path


def build_parser() -> argparse.ArgumentParser:
    """Construct the command interface without importing OCR model code."""
    parser = argparse.ArgumentParser(
        description="Benchmark generic OCR REST jobs with bounded client concurrency.",
        epilog=(
            "Example: OCR_IDLE_TIMEOUT=300 bash ocr/ocr_start.sh start && "
            "conda run -n mcp-local-ocr python -m test.ocr.benchmark.cli "
            "mcp-tool-test/ocr/pdf/attention_is_all_you_need.pdf "
            "--pages 1-4 --pages-per-job 1 --concurrency 1 --repetitions 1 --mode staged"
        ),
    )
    parser.add_argument("source_pdf", type=Path)
    parser.add_argument("--pages", default="", help="one-based pages, e.g. 1-3,8; default selects all")
    parser.add_argument("--max-pages", type=int, default=None, help="cap selected pages after range parsing")
    parser.add_argument("--pages-per-job", type=int, default=1, help="pages in each local staged PDF")
    parser.add_argument("--concurrency", type=int, default=1, help="maximum simultaneous client jobs")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--server-url", default="http://127.0.0.1:8002")
    parser.add_argument("--timeout", type=float, default=1800.0, dest="timeout_seconds")
    parser.add_argument("--sample-interval", type=float, default=2.0, dest="sample_interval_seconds")
    parser.add_argument("--output-dir", type=Path, default=Path("ocr-benchmark-artifacts"))
    parser.add_argument(
        "--mode",
        choices=("staged",),
        default="staged",
        help="staged writes local page-subset PDFs and never changes the source fixture",
    )
    return parser


def _settings_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> BenchmarkSettings:
    if not args.source_pdf.is_file():
        parser.error(f"source PDF does not exist: {args.source_pdf}")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be positive")
    if args.pages_per_job < 1:
        parser.error("--pages-per-job must be positive")
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    if args.timeout_seconds <= 0 or args.sample_interval_seconds <= 0:
        parser.error("--timeout and --sample-interval must be positive")
    return BenchmarkSettings(
        source_pdf=args.source_pdf,
        page_ranges=args.pages,
        max_pages=args.max_pages,
        pages_per_job=args.pages_per_job,
        concurrency=args.concurrency,
        repetitions=args.repetitions,
        server_url=args.server_url,
        timeout_seconds=args.timeout_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        output_directory=args.output_dir,
    )


def _execute(client: OcrRestClient, settings: BenchmarkSettings, chunk: StagedChunk) -> ChunkOutcome:
    return execute_staged_chunk(
        chunk,
        client=client,
        timeout_seconds=settings.timeout_seconds,
        sample_interval_seconds=settings.sample_interval_seconds,
    )


def run(settings: BenchmarkSettings) -> int:
    """Stage selected pages for each repetition, execute bounded jobs, and save artifacts."""
    total_pages = source_page_count(settings.source_pdf)
    selected_pages = parse_page_ranges(settings.page_ranges, total_pages=total_pages)
    if settings.max_pages is not None:
        selected_pages = selected_pages[: settings.max_pages]
    chunks = plan_chunks(selected_pages, pages_per_job=settings.pages_per_job)
    client = OcrRestClient(settings.server_url, settings.timeout_seconds)
    recorded: list[RecordedOutcome] = []
    for repetition in range(1, settings.repetitions + 1):
        staged = stage_chunk_pdfs(
            settings.source_pdf,
            chunks,
            settings.output_directory / "staged" / f"repetition-{repetition:03d}",
        )
        outcomes = run_bounded(
            staged,
            concurrency=settings.concurrency,
            execute=partial(_execute, client, settings),
        )
        recorded.extend(RecordedOutcome(repetition, outcome) for outcome in outcomes)
    artifacts = write_artifacts(
        settings.output_directory,
        settings.source_pdf,
        selected_pages,
        tuple(recorded),
    )
    print(f"JSONL: {artifacts.jsonl}")
    print(f"Summary JSON: {artifacts.summary_json}")
    print(f"Summary Markdown: {artifacts.summary_markdown}")
    return 0


def main() -> int:
    """Parse CLI arguments and render configuration failures as command errors."""
    parser = build_parser()
    settings = _settings_from_args(parser.parse_args(), parser)
    try:
        return run(settings)
    except (PageRangeError, ChunkPlanningError, PdfStagingError, ConcurrencyError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
