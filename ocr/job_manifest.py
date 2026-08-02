"""Typed, validated on-disk contract for durable OCR jobs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, NewType

from pydantic import BaseModel, ConfigDict, Field


MANIFEST_VERSION: Final = 1
JobId = NewType("JobId", str)


class ChunkStatus(StrEnum):
    """Persistent lifecycle state for an independently resumable chunk."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


class SourceKind(StrEnum):
    """Source type used to determine how chunks are staged."""

    PDF = "pdf"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class InvalidManifestError(Exception):
    """Raised when a manifest does not satisfy the durable job contract."""

    reason: str

    def __str__(self) -> str:
        return f"invalid OCR job manifest: {self.reason}"


@dataclass(frozen=True, slots=True)
class ChunkManifest:
    """One independently processable source-page group and its artifacts."""

    index: int
    source_pages: tuple[int, ...]
    staged_path: Path
    artifact_path: Path
    status: ChunkStatus
    artifact_digest: str | None

    def pending(self) -> ChunkManifest:
        """Return this chunk reset for deterministic retry."""
        return replace(self, status=ChunkStatus.PENDING, artifact_digest=None)


@dataclass(frozen=True, slots=True)
class JobManifest:
    """The complete, relative-path-only state for one durable OCR job."""

    job_id: JobId
    source_kind: SourceKind
    input_path: Path
    page_count: int
    chunks: tuple[ChunkManifest, ...]

    def replace_chunk(self, replacement: ChunkManifest) -> JobManifest:
        """Return a copy containing the chunk with the same stable index."""
        chunks = tuple(
            replacement if chunk.index == replacement.index else chunk for chunk in self.chunks
        )
        return replace(self, chunks=chunks)

    def to_json(self) -> str:
        """Encode the typed manifest through its validated wire model."""
        wire = _ManifestWire(
            version=MANIFEST_VERSION,
            job_id=str(self.job_id),
            source_kind=self.source_kind,
            input_path=str(self.input_path),
            page_count=self.page_count,
            chunks=tuple(
                _ChunkWire(
                    index=chunk.index,
                    source_pages=chunk.source_pages,
                    staged_path=str(chunk.staged_path),
                    artifact_path=str(chunk.artifact_path),
                    status=chunk.status,
                    artifact_digest=chunk.artifact_digest,
                )
                for chunk in self.chunks
            ),
        )
        return wire.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, payload: str) -> JobManifest:
        """Parse an untrusted manifest file into a fully typed value object."""
        try:
            wire = _ManifestWire.model_validate_json(payload)
        except ValueError as error:
            raise InvalidManifestError(reason=str(error)) from error
        if wire.version != MANIFEST_VERSION:
            raise InvalidManifestError(reason=f"unsupported version {wire.version}")
        _validate_job_id(wire.job_id)
        _validate_relative_path(wire.input_path)
        chunks = tuple(_chunk_from_wire(chunk) for chunk in wire.chunks)
        _validate_chunk_sequence(chunks=chunks, page_count=wire.page_count)
        return cls(
            job_id=JobId(wire.job_id),
            source_kind=wire.source_kind,
            input_path=Path(wire.input_path),
            page_count=wire.page_count,
            chunks=chunks,
        )


class _ChunkWire(BaseModel):
    """Validated JSON shape for a chunk manifest entry."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)
    source_pages: tuple[int, ...] = Field(min_length=1)
    staged_path: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    status: ChunkStatus
    artifact_digest: str | None = None


class _ManifestWire(BaseModel):
    """Validated JSON shape for the job manifest file."""

    model_config = ConfigDict(frozen=True)

    version: int
    job_id: str = Field(min_length=1)
    source_kind: SourceKind
    input_path: str = Field(min_length=1)
    page_count: int = Field(ge=1)
    chunks: tuple[_ChunkWire, ...] = Field(min_length=1)


def _chunk_from_wire(wire: _ChunkWire) -> ChunkManifest:
    _validate_relative_path(wire.staged_path)
    _validate_relative_path(wire.artifact_path)
    if any(page < 1 for page in wire.source_pages):
        raise InvalidManifestError(reason=f"chunk {wire.index} has a non-positive source page")
    return ChunkManifest(
        index=wire.index,
        source_pages=wire.source_pages,
        staged_path=Path(wire.staged_path),
        artifact_path=Path(wire.artifact_path),
        status=wire.status,
        artifact_digest=wire.artifact_digest,
    )


def _validate_job_id(job_id: str) -> None:
    if "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
        raise InvalidManifestError(reason=f"unsafe job id {job_id!r}")


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise InvalidManifestError(reason=f"path must be relative and contained: {value!r}")


def _validate_chunk_sequence(*, chunks: tuple[ChunkManifest, ...], page_count: int) -> None:
    expected_pages = tuple(range(1, page_count + 1))
    actual_pages = tuple(page for chunk in chunks for page in chunk.source_pages)
    expected_indexes = tuple(range(1, len(chunks) + 1))
    actual_indexes = tuple(chunk.index for chunk in chunks)
    if actual_indexes != expected_indexes:
        raise InvalidManifestError(reason="chunk indexes must be consecutive starting at one")
    if actual_pages != expected_pages:
        raise InvalidManifestError(reason="chunk page mappings must cover source pages exactly once")
