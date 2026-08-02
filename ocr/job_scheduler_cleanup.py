"""Thread lifecycle for periodic durable completed-job cleanup."""

from __future__ import annotations

import threading
from collections.abc import Callable

from ocr.job_manifest import JobId

class PeriodicJobCleanup:
    """Run scheduler cleanup on its own lightweight, non-inference thread."""

    def __init__(self, interval_seconds: float, cleanup: Callable[[], tuple[JobId, ...]]) -> None:
        self._interval_seconds = interval_seconds
        self._cleanup = cleanup
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start periodic cleanup for the current scheduler lifetime."""
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="ocr-job-cleanup", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Stop and join the cleanup thread when it was successfully started."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._cleanup()
