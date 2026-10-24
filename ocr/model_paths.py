"""Resolve OCR model locations without depending on a developer workspace."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from model_cache import model_cache_root


DEFAULT_MODEL_ID: Final = "PaddlePaddle/PaddleOCR-VL-1.6"
DEFAULT_LAYOUT_MODEL_ID: Final = "PaddlePaddle/PP-DocLayoutV3"


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def ocr_model_root(
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, bool]:
    """Return the OCR search root and whether it was explicitly configured."""
    environment = _environment(environ)
    configured = environment.get("OCR_MODEL_ROOT", "").strip()
    if configured:
        return _absolute_path(configured), True
    return model_cache_root(environment) / "safetensors", False


def _looks_like_local_path(value: str) -> bool:
    return value.startswith(("/", "./", "../", "~"))


def resolve_model_path(
    model_name: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, bool]:
    """Resolve a recognition model and report whether loading must stay offline."""
    requested = model_name.strip()
    if not requested:
        raise ValueError("OCR model name must not be empty")

    candidate = _absolute_path(requested)
    if candidate.is_dir():
        return str(candidate), True
    if candidate.exists() or _looks_like_local_path(requested):
        raise FileNotFoundError(
            f"Configured OCR model directory does not exist: {candidate}. "
            "Set OCR_MODEL_NAME to an existing directory or a Hugging Face model ID."
        )

    environment = _environment(environ)
    root, explicitly_configured = ocr_model_root(environment)
    rooted = root / requested
    if rooted.is_dir():
        return str(rooted.resolve()), True
    if explicitly_configured:
        raise FileNotFoundError(
            f"OCR_MODEL_ROOT is set, but the model directory was not found: {rooted}. "
            "Populate that directory or unset OCR_MODEL_ROOT to use the Hugging Face cache."
        )

    return requested, False


def resolve_layout_model_path(
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve an optional local layout snapshot, otherwise use PaddleX caching."""
    environment = _environment(environ)
    configured = environment.get("OCR_LAYOUT_MODEL", "").strip()
    if configured:
        path = _absolute_path(configured)
        if not path.is_dir():
            raise FileNotFoundError(
                f"OCR_LAYOUT_MODEL does not exist or is not a directory: {path}"
            )
        return path

    root, ocr_root_configured = ocr_model_root(environment)
    candidate = root / DEFAULT_LAYOUT_MODEL_ID
    if candidate.is_dir():
        return candidate.resolve()

    return None
