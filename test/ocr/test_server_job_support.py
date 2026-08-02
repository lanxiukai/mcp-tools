from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.job_scheduler import ChunkSucceeded
from ocr.server_job_support import ModelChunkExecutor, ModelPrediction


class FakeModel:
    """CPU-only model substitute that never initializes OCR weights."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def predict(self, file_path: str) -> ModelPrediction:
        self.paths.append(file_path)
        return {
            "page_count": 1,
            "markdown": "# recognized\n",
            "pages": [{"page_index": 0, "markdown": "# recognized\n"}],
        }


class TestModelChunkExecutor(TestCase):
    def test_executor_adapts_model_output_for_the_single_scheduler_worker(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            source = Path(directory) / "chunk.png"
            source.write_text("fixture", encoding="utf-8")
            model = FakeModel()
            executor = ModelChunkExecutor(model)

            # When
            outcome = executor.execute(source)

            # Then
            self.assertEqual(outcome, ChunkSucceeded(markdown="# recognized\n"))
            self.assertEqual(model.paths, [str(source)])
