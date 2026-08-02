"""Typed artifact-only response protocol for the generic OCR MCP frontend."""

from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict

McpScalar: TypeAlias = str | int | float | bool | None
ArtifactPayload: TypeAlias = dict[str, str | int | None | list[int]]
McpPayloadValue: TypeAlias = McpScalar | list[ArtifactPayload] | dict[str, int] | dict[str, McpScalar]
JsonObject: TypeAlias = dict[str, McpPayloadValue]


class ArtifactMetadata(BaseModel):
    """Durable Markdown artifact location and integrity metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_index: int
    source_pages: tuple[int, ...]
    path: str
    sha256: str | None


class JobProgressMetadata(BaseModel):
    """Completed source pages relative to the durable job total."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current: int
    total: int


class JobSubmissionMetadata(BaseModel):
    """Durable queue acknowledgement returned by the submit endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    job_id: str
    status: str
    filename: str
    total_pages: int
    artifacts: tuple[ArtifactMetadata, ...]


class JobStatusMetadata(BaseModel):
    """Durable job status without embedded artifact content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: str
    progress: JobProgressMetadata
    filename: str
    error: str | None
    artifacts: tuple[ArtifactMetadata, ...]


class JobResultMetadata(BaseModel):
    """Terminal durable job result without embedded artifact content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: str
    page_count: int
    artifacts: tuple[ArtifactMetadata, ...]


class ServerErrorMetadata(BaseModel):
    """Structured HTTP error emitted by the FastAPI job endpoints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detail: str


class HealthMetadata(BaseModel):
    """Server health response, preserving optional queue metadata from newer servers."""

    model_config = ConfigDict(frozen=True, extra="allow")

    status: str


def model_payload(model: BaseModel) -> JsonObject:
    """Serialize a validated response model into the MCP JSON payload shape."""
    return model.model_dump(mode="json")
