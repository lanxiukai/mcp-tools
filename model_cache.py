"""Shared model storage and Hugging Face cache configuration.

The repository's models directory may be a symlink to a shared model store.
Configure caches before importing Hugging Face, Transformers, or pyannote.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent


def model_cache_root(
    environ: Mapping[str, str] | None = None,
    *,
    repository_root: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get("MCP_TOOLS_MODEL_DIR", "").strip()
    root = Path(configured).expanduser() if configured else (
        (repository_root or REPOSITORY_ROOT) / "models"
    )
    return root.resolve()


def configure_model_cache() -> Path:
    """Keep model caches below the shared root without relocating credentials."""
    root = model_cache_root()
    cache = root / ".cache" / "huggingface"
    for variable, directory in (
        ("HF_HUB_CACHE", "hub"),
        ("HUGGINGFACE_HUB_CACHE", "hub"),
        ("HF_XET_CACHE", "xet"),
        ("HF_ASSETS_CACHE", "assets"),
    ):
        os.environ[variable] = str(cache / directory)
    # An inherited legacy override must not redirect Transformers elsewhere.
    os.environ.pop("TRANSFORMERS_CACHE", None)
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(root / ".cache" / "paddlex")
    return root
