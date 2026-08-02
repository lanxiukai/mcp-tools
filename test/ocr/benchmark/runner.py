"""CPU-safe concurrency and timing primitives for the OCR benchmark harness."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar, TypedDict


class MetricRecord(TypedDict):
    """JSON-ready timing phases for one OCR job."""

    submit_seconds: float
    queue_seconds: float
    progress_seconds: float
    completion_seconds: float
    retrieval_seconds: float
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ConcurrencyError(Exception):
    """Raised when caller supplies an unusable client concurrency bound."""

    concurrency: int

    def __str__(self) -> str:
        return f"concurrency must be positive, got {self.concurrency}"


@dataclass(frozen=True, slots=True)
class JobMetrics:
    """Durations distinguishing upload, queue/progress, completion, and retrieval."""

    submit_seconds: float
    queue_seconds: float
    progress_seconds: float
    completion_seconds: float
    retrieval_seconds: float
    elapsed_seconds: float

    def as_record(self) -> MetricRecord:
        """Convert benchmark timing fields to a JSON-ready record."""
        return {
            "submit_seconds": self.submit_seconds,
            "queue_seconds": self.queue_seconds,
            "progress_seconds": self.progress_seconds,
            "completion_seconds": self.completion_seconds,
            "retrieval_seconds": self.retrieval_seconds,
            "elapsed_seconds": self.elapsed_seconds,
        }


T = TypeVar("T")
R = TypeVar("R")


def run_bounded(
    items: tuple[T, ...],
    *,
    concurrency: int,
    execute: Callable[[T], R],
) -> tuple[R, ...]:
    """Run work with at most ``concurrency`` active client workers, preserving input order."""
    if concurrency < 1:
        raise ConcurrencyError(concurrency=concurrency)
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="ocr-benchmark") as executor:
        futures = tuple(executor.submit(execute, item) for item in items)
        return tuple(future.result() for future in futures)
