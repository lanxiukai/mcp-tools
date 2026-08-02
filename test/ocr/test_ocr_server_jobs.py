from __future__ import annotations

import anyio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi import HTTPException

from ocr.ocr_server import app, ocr_model, submit_document
from ocr.job_scheduler import ChunkSucceeded, DurableJobScheduler, JobSchedulerConfig


class ImmediateExecutor:
    def execute(self, source: Path) -> ChunkSucceeded:
        return ChunkSucceeded(markdown="# unused\n")


class UploadFixture:
    def __init__(self, filename: str, payload: bytes) -> None:
        self.filename = filename
        self._payload = payload

    async def read(self, size: int) -> bytes:
        payload = self._payload
        self._payload = b""
        return payload


async def _submit(upload: UploadFixture) -> HTTPException | None:
    try:
        await submit_document(file=upload)
    except HTTPException as error:
        return error
    return None


class TestSubmitDocument(TestCase):
    def test_submit_returns_http_429_when_the_durable_queue_is_full(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing.png"
            existing.write_text("existing", encoding="utf-8")
            scheduler = DurableJobScheduler(
                JobSchedulerConfig(root=root / "jobs", queue_capacity=1),
                ImmediateExecutor(),
            )
            scheduler.submit(existing)
            original_model = ocr_model.model
            had_scheduler = hasattr(app.state, "scheduler")
            original_scheduler = app.state.scheduler if had_scheduler else None
            ocr_model.model = object()
            app.state.scheduler = scheduler
            try:
                # When
                error = anyio.run(_submit, UploadFixture("overflow.png", b"overflow"))

                # Then
                if error is None:
                    self.fail("submit_document returned successfully despite a full queue")
                self.assertEqual(error.status_code, 429)
            finally:
                ocr_model.model = original_model
                if had_scheduler:
                    app.state.scheduler = original_scheduler
                else:
                    delattr(app.state, "scheduler")
