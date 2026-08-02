"""Durable, same-filesystem file primitives for OCR job artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_text(destination: Path, content: str) -> None:
    """Replace a UTF-8 file only after its bytes have reached stable storage."""
    atomic_write_bytes(destination, content.encode("utf-8"))


def atomic_write_bytes(destination: Path, content: bytes) -> None:
    """Write via a temp file in the destination directory, fsync, then replace."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    _durably_replace(temporary_path, destination)


def atomic_copy_file(source: Path, destination: Path) -> None:
    """Copy an input or staged image without exposing a partial destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, tempfile.NamedTemporaryFile(
        dir=destination.parent,
        delete=False,
    ) as temporary:
        shutil.copyfileobj(input_file, temporary)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    _durably_replace(temporary_path, destination)


def sha256_file(path: Path) -> str:
    """Return a stable lowercase digest for an on-disk artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _durably_replace(temporary_path: Path, destination: Path) -> None:
    os.replace(temporary_path, destination)
    directory_descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
