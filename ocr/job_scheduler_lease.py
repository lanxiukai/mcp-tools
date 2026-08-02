"""Process-lifetime advisory lease for one durable OCR scheduler root."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import TextIO

from ocr.job_scheduler_types import JobRootInUseError


class JobRootLease:
    """Hold a nonblocking exclusive advisory lock until the scheduler stops."""

    def __init__(self, root: Path) -> None:
        self._path = root / ".scheduler.lock"
        self._file: TextIO | None = None

    def acquire(self) -> None:
        """Create and acquire the root lock or raise a typed contention error."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._path.open("a", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock_file.close()
            raise JobRootInUseError(root=self._path.parent) from error
        self._file = lock_file

    def release(self) -> None:
        """Release the held advisory lock, if any."""
        lock_file = self._file
        if lock_file is None:
            return
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            self._file = None
