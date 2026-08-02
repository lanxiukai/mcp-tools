"""Worker-only OCR-model adapter and direct-response artifact assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict

from ocr.job_scheduler import ArtifactResult, ChunkExecution, ChunkFailed, ChunkSucceeded


class ModelPage(TypedDict):
    """One page-level model result retained for legacy synchronous responses."""

    page_index: int
    markdown: str


class ModelPrediction(TypedDict):
    """The model result fields needed to persist one staged chunk artifact."""

    page_count: int
    markdown: str
    pages: list[ModelPage]


class ModelPredictor(Protocol):
    """Minimal model-independent OCR capability used by the scheduler worker."""

    def predict(self, file_path: str) -> ModelPrediction:
        """Run OCR for one staged PDF or image file."""
        ...


@dataclass(frozen=True, slots=True)
class ModelChunkExecutor:
    """Translate OCRModel outcomes into scheduler-owned chunk outcomes."""

    model: ModelPredictor

    def execute(self, source: Path) -> ChunkExecution:
        """Invoke the model only from the durable worker's execution boundary."""
        try:
            prediction = self.model.predict(str(source))
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            return ChunkFailed(error=str(error))
        return ChunkSucceeded(markdown=prediction["markdown"])


def assemble_markdown(result: ArtifactResult) -> str:
    """Load durable chunks only for the legacy synchronous parse response."""
    return "\n\n---\n\n".join(
        artifact.path.read_text(encoding="utf-8") for artifact in result.artifacts
    )
