"""Resolve OCR model locations without depending on a developer workspace."""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Final


DEFAULT_MODEL_ID: Final = "PaddlePaddle/PaddleOCR-VL-1.6"
DEFAULT_LAYOUT_MODEL_ID: Final = "PaddlePaddle/PP-DocLayoutV3"
_WARNED_LEGACY_PATHS: set[Path] = set()


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def model_cache_root(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return the user-controlled mcp-tools model root."""
    environment = _environment(environ)
    configured = environment.get("MCP_TOOLS_MODEL_DIR", "").strip()
    if configured:
        return _absolute_path(configured)

    cache_home = environment.get("XDG_CACHE_HOME", "").strip()
    base = (
        _absolute_path(cache_home) if cache_home else (home or Path.home()) / ".cache"
    )
    return _absolute_path(base / "mcp-tools" / "models")


def ocr_model_root(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> tuple[Path, bool]:
    """Return the OCR search root and whether it was explicitly configured."""
    environment = _environment(environ)
    configured = environment.get("OCR_MODEL_ROOT", "").strip()
    if configured:
        return _absolute_path(configured), True
    return model_cache_root(environment, home=home) / "ocr", False


def _legacy_model_root(home: Path | None = None) -> Path:
    return (
        _absolute_path(home or Path.home())
        / "project"
        / "hf-models"
        / "models"
        / "safetensors"
    )


def _warn_legacy(path: Path, replacement: str) -> None:
    if path in _WARNED_LEGACY_PATHS:
        return
    _WARNED_LEGACY_PATHS.add(path)
    warnings.warn(
        f"Using deprecated model fallback {path}. {replacement}",
        FutureWarning,
        stacklevel=3,
    )


def _looks_like_local_path(value: str) -> bool:
    return value.startswith(("/", "./", "../", "~"))


def resolve_model_path(
    model_name: str,
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
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
    root, explicitly_configured = ocr_model_root(environment, home=home)
    rooted = root / requested
    if rooted.is_dir():
        return str(rooted.resolve()), True
    if explicitly_configured:
        raise FileNotFoundError(
            f"OCR_MODEL_ROOT is set, but the model directory was not found: {rooted}. "
            "Populate that directory or unset OCR_MODEL_ROOT to use the Hugging Face cache."
        )

    generic_root_configured = bool(
        environment.get("MCP_TOOLS_MODEL_DIR", "").strip()
    )
    legacy = _legacy_model_root(home) / requested
    if (
        not generic_root_configured
        and requested == DEFAULT_MODEL_ID
        and legacy.is_dir()
    ):
        _warn_legacy(
            legacy,
            "Move it below MCP_TOOLS_MODEL_DIR/ocr or set OCR_MODEL_ROOT explicitly; "
            "the legacy fallback will be removed in a future release.",
        )
        return str(legacy.resolve()), True

    return requested, False


def resolve_layout_model_path(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
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

    root, ocr_root_configured = ocr_model_root(environment, home=home)
    candidate = root / DEFAULT_LAYOUT_MODEL_ID
    if candidate.is_dir():
        return candidate.resolve()

    generic_root_configured = bool(
        environment.get("MCP_TOOLS_MODEL_DIR", "").strip()
    )
    legacy = _legacy_model_root(home) / DEFAULT_LAYOUT_MODEL_ID
    if not ocr_root_configured and not generic_root_configured and legacy.is_dir():
        _warn_legacy(
            legacy,
            "Move it below MCP_TOOLS_MODEL_DIR/ocr or set OCR_LAYOUT_MODEL explicitly; "
            "the legacy fallback will be removed in a future release.",
        )
        return legacy.resolve()

    return None
