"""File-backed durable OCR jobs with page staging and restart recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from uuid import uuid4

from ocr.pdf_staging import (
    ChunkPlanningError,
    PdfStagingError,
    plan_chunks,
    source_page_count,
    stage_chunk_pdfs,
)
from ocr.job_files import atomic_copy_file, atomic_write_text, sha256_file
from ocr.job_manifest import ChunkManifest, ChunkStatus, JobId, JobManifest, SourceKind


PAGES_PER_CHUNK: Final = 24


@dataclass(frozen=True, slots=True)
class JobSourceError(Exception):
    """Raised when a submitted source cannot become a durable job."""

    source: Path
    reason: str

    def __str__(self) -> str:
        return f"cannot create OCR job for {self.source}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ChunkNotFoundError(Exception):
    """Raised when a job does not contain the requested chunk index."""

    job_id: JobId
    chunk_index: int

    def __str__(self) -> str:
        return f"job {self.job_id} has no chunk {self.chunk_index}"


class JobStore:
    """Owns isolated on-disk job directories below one caller-selected root."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def create(self, source: Path) -> JobManifest:
        """Copy and stage one PDF or image without modifying the caller's source."""
        if not source.is_file():
            raise JobSourceError(source=source, reason="source file does not exist")
        job_id = JobId(uuid4().hex)
        job_directory = self.job_directory(job_id)
        input_path = Path("input") / source.name
        atomic_copy_file(source, job_directory / input_path)
        source_kind = _source_kind(source)
        match source_kind:
            case SourceKind.PDF:
                page_count, chunks = _stage_pdf(
                    source=job_directory / input_path,
                    job_directory=job_directory,
                )
            case SourceKind.IMAGE:
                page_count, chunks = _stage_image(
                    source=job_directory / input_path,
                    job_directory=job_directory,
                )
        manifest = JobManifest(
            job_id=job_id,
            source_kind=source_kind,
            input_path=input_path,
            page_count=page_count,
            chunks=chunks,
        )
        self._write_manifest(manifest)
        return manifest

    def load(self, job_id: JobId) -> JobManifest:
        """Read and validate one persisted manifest."""
        manifest_path = self.job_directory(job_id) / "manifest.json"
        return JobManifest.from_json(manifest_path.read_text(encoding="utf-8"))

    def recover(self, job_id: JobId) -> JobManifest:
        """Reset interrupted or invalid completed chunks deterministically."""
        manifest = self.load(job_id)
        job_directory = self.job_directory(job_id)
        recovered = replace(
            manifest,
            chunks=tuple(_recover_chunk(chunk, job_directory) for chunk in manifest.chunks),
        )
        if recovered != manifest:
            self._write_manifest(recovered)
        return recovered

    def mark_running(self, job_id: JobId, *, chunk_index: int) -> JobManifest:
        """Persist that a worker started a pending chunk."""
        manifest = self.load(job_id)
        chunk = _find_chunk(manifest, chunk_index)
        updated = manifest.replace_chunk(replace(chunk, status=ChunkStatus.RUNNING, artifact_digest=None))
        self._write_manifest(updated)
        return updated

    def complete_chunk(self, job_id: JobId, *, chunk_index: int, markdown: str) -> JobManifest:
        """Durably publish Markdown before its completion state becomes visible."""
        manifest = self.load(job_id)
        chunk = _find_chunk(manifest, chunk_index)
        artifact = self.job_directory(job_id) / chunk.artifact_path
        atomic_write_text(artifact, markdown)
        completed = replace(
            chunk,
            status=ChunkStatus.COMPLETED,
            artifact_digest=sha256_file(artifact),
        )
        updated = manifest.replace_chunk(completed)
        self._write_manifest(updated)
        return updated

    def job_directory(self, job_id: JobId) -> Path:
        """Return the predictable isolated directory for a job identifier."""
        return self._root / str(job_id)

    def _write_manifest(self, manifest: JobManifest) -> None:
        """Atomically publish the only mutable job coordination record."""
        atomic_write_text(self.job_directory(manifest.job_id) / "manifest.json", manifest.to_json())


def _source_kind(source: Path) -> SourceKind:
    return SourceKind.PDF if source.suffix.casefold() == ".pdf" else SourceKind.IMAGE


def _stage_pdf(*, source: Path, job_directory: Path) -> tuple[int, tuple[ChunkManifest, ...]]:
    try:
        page_count = source_page_count(source)
        plans = plan_chunks(tuple(range(1, page_count + 1)), pages_per_job=PAGES_PER_CHUNK)
        staged_chunks = stage_chunk_pdfs(source, plans, job_directory / "chunks")
    except (ChunkPlanningError, PdfStagingError) as error:
        raise JobSourceError(source=source, reason=str(error)) from error
    chunks = tuple(
        _new_chunk(
            index=staged.plan.index,
            source_pages=staged.plan.source_pages,
            staged_path=staged.pdf_path.relative_to(job_directory),
        )
        for staged in staged_chunks
    )
    return page_count, chunks


def _stage_image(*, source: Path, job_directory: Path) -> tuple[int, tuple[ChunkManifest, ...]]:
    suffix = source.suffix.casefold() or ".img"
    staged_path = Path("chunks") / f"chunk-001{suffix}"
    atomic_copy_file(source, job_directory / staged_path)
    return 1, (_new_chunk(index=1, source_pages=(1,), staged_path=staged_path),)


def _new_chunk(*, index: int, source_pages: tuple[int, ...], staged_path: Path) -> ChunkManifest:
    return ChunkManifest(
        index=index,
        source_pages=source_pages,
        staged_path=staged_path,
        artifact_path=Path("chunks") / f"chunk-{index:03d}.md",
        status=ChunkStatus.PENDING,
        artifact_digest=None,
    )


def _recover_chunk(chunk: ChunkManifest, job_directory: Path) -> ChunkManifest:
    match chunk.status:
        case ChunkStatus.PENDING:
            return chunk
        case ChunkStatus.RUNNING:
            return chunk.pending()
        case ChunkStatus.COMPLETED:
            artifact = job_directory / chunk.artifact_path
            is_reusable = artifact.is_file() and chunk.artifact_digest == sha256_file(artifact)
            return chunk if is_reusable else chunk.pending()


def _find_chunk(manifest: JobManifest, chunk_index: int) -> ChunkManifest:
    for chunk in manifest.chunks:
        if chunk.index == chunk_index:
            return chunk
    raise ChunkNotFoundError(job_id=manifest.job_id, chunk_index=chunk_index)
