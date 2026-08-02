"""PDF page planning and local staging shared by OCR job workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ChunkPlanningError(Exception):
    """Raised when source pages cannot be partitioned into valid chunks."""

    reason: str

    def __str__(self) -> str:
        return f"cannot plan PDF chunks: {self.reason}"


@dataclass(frozen=True, slots=True)
class PdfStagingError(Exception):
    """Raised when a selected PDF subset cannot be created."""

    source_pdf: Path
    reason: str

    def __str__(self) -> str:
        return f"cannot stage {self.source_pdf}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    """One source-page group that becomes one submitted PDF."""

    index: int
    source_pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class StagedChunk:
    """A local page-subset PDF paired with its source page mapping."""

    plan: ChunkPlan
    pdf_path: Path


def plan_chunks(source_pages: tuple[int, ...], *, pages_per_job: int) -> tuple[ChunkPlan, ...]:
    """Partition already-selected source pages without dropping or duplicating one."""
    if pages_per_job < 1:
        raise ChunkPlanningError(reason="pages_per_job must be positive")
    if len(source_pages) == 0:
        raise ChunkPlanningError(reason="no pages selected")
    return tuple(
        ChunkPlan(index=index, source_pages=source_pages[offset : offset + pages_per_job])
        for index, offset in enumerate(range(0, len(source_pages), pages_per_job), start=1)
    )


def source_page_count(source_pdf: Path) -> int:
    """Read PDF metadata only; importing PyMuPDF here never loads OCR model weights."""
    fitz = _load_fitz(source_pdf)
    with fitz.open(source_pdf) as document:
        return len(document)


def stage_chunk_pdfs(
    source_pdf: Path,
    chunks: tuple[ChunkPlan, ...],
    staging_directory: Path,
) -> tuple[StagedChunk, ...]:
    """Write page-subset PDFs under staging without modifying the input fixture."""
    fitz = _load_fitz(source_pdf)
    staging_directory.mkdir(parents=True, exist_ok=True)
    staged: list[StagedChunk] = []
    try:
        with fitz.open(source_pdf) as source:
            for chunk in chunks:
                staged_path = staging_directory / f"chunk-{chunk.index:03d}.pdf"
                with fitz.open() as subset:
                    for source_page in chunk.source_pages:
                        subset.insert_pdf(source, from_page=source_page - 1, to_page=source_page - 1)
                    subset.save(staged_path)
                staged.append(StagedChunk(plan=chunk, pdf_path=staged_path))
    except (OSError, RuntimeError) as error:
        raise PdfStagingError(source_pdf=source_pdf, reason=str(error)) from error
    return tuple(staged)


def _load_fitz(source_pdf: Path):
    try:
        import fitz
    except ImportError as error:
        raise PdfStagingError(source_pdf=source_pdf, reason="PyMuPDF (fitz) is required") from error
    return fitz
