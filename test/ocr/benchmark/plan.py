"""Page selection and result ordering for OCR benchmark runs."""

from __future__ import annotations

from dataclasses import dataclass

from ocr.pdf_staging import (
    ChunkPlan,
    ChunkPlanningError,
    PdfStagingError,
    StagedChunk,
    plan_chunks,
    source_page_count,
    stage_chunk_pdfs,
)

__all__ = [
    "ChunkPlan",
    "ChunkPlanningError",
    "PageRangeError",
    "PageResult",
    "PdfStagingError",
    "StagedChunk",
    "parse_page_ranges",
    "plan_chunks",
    "reassemble_pages",
    "source_page_count",
    "stage_chunk_pdfs",
]


@dataclass(frozen=True, slots=True)
class PageRangeError(Exception):
    """Raised when a page-range expression cannot select source pages."""

    expression: str
    reason: str

    def __str__(self) -> str:
        return f"invalid page range {self.expression!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class PageResult:
    """OCR output mapped back to its one-based source page."""

    source_page: int
    markdown: str
    returned_page_index: int | None = None


def parse_page_ranges(expression: str, *, total_pages: int) -> tuple[int, ...]:
    """Parse one-based ``1-3,5`` syntax into ordered, unique source pages."""
    if total_pages < 1:
        raise PageRangeError(expression=expression, reason="source PDF has no pages")
    if expression.strip() == "":
        return tuple(range(1, total_pages + 1))

    selected: set[int] = set()
    for raw_part in expression.split(","):
        part = raw_part.strip()
        if part == "":
            raise PageRangeError(expression=expression, reason="empty range component")
        selected.update(_parse_component(part, expression, total_pages))
    return tuple(sorted(selected))


def _parse_component(component: str, expression: str, total_pages: int) -> tuple[int, ...]:
    if "-" not in component:
        return (_parse_page(component, expression, total_pages),)
    start_text, end_text = component.split("-", maxsplit=1)
    start = _parse_page(start_text, expression, total_pages)
    end = _parse_page(end_text, expression, total_pages)
    if start > end:
        raise PageRangeError(expression=expression, reason=f"descending span {component!r}")
    return tuple(range(start, end + 1))


def _parse_page(text: str, expression: str, total_pages: int) -> int:
    try:
        page = int(text)
    except ValueError as error:
        raise PageRangeError(expression=expression, reason=f"non-integer page {text!r}") from error
    if page < 1 or page > total_pages:
        raise PageRangeError(
            expression=expression,
            reason=f"page {page} is outside 1-{total_pages}",
        )
    return page


def reassemble_pages(page_results: tuple[PageResult, ...]) -> tuple[PageResult, ...]:
    """Return OCR pages in source order regardless of job completion order."""
    return tuple(sorted(page_results, key=lambda page: page.source_page))
