"""Resolve the Qwen3-ASR model source without touching the network."""

import json
from pathlib import Path
from typing import Final

HUB_MODEL_ID: Final[str] = "Qwen/Qwen3-ASR-1.7B"
LOCAL_MODEL_RELATIVE_PATH: Final[Path] = Path(
    "models/safetensors/Qwen/Qwen3-ASR-1.7B"
)
MODEL_METADATA_FILENAME: Final[str] = "config.json"
WEIGHT_INDEX_FILENAME: Final[str] = "model.safetensors.index.json"
FORCED_ALIGNER_HUB_MODEL_ID: Final[str] = "Qwen/Qwen3-ForcedAligner-0.6B"
FORCED_ALIGNER_LOCAL_MODEL_RELATIVE_PATH: Final[Path] = Path(
    "models/safetensors/Qwen/Qwen3-ForcedAligner-0.6B"
)
FORCED_ALIGNER_REQUIRED_FILENAMES: Final[tuple[str, ...]] = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
)


def resolve_model_source(explicit_model: str | None, repository_root: Path) -> str:
    """Return an explicit source or the complete local model, else the Hub ID."""
    if explicit_model is not None:
        return explicit_model

    local_model = repository_root / LOCAL_MODEL_RELATIVE_PATH
    if _is_complete_model_directory(local_model):
        return str(local_model)
    return HUB_MODEL_ID


def resolve_forced_aligner_source(
    explicit_model: str | None, repository_root: Path
) -> str:
    """Return an explicit source or complete local aligner, else the Hub ID."""
    if explicit_model is not None:
        return explicit_model

    local_model = repository_root / FORCED_ALIGNER_LOCAL_MODEL_RELATIVE_PATH
    if all(
        _is_nonempty_regular_file(local_model / filename)
        for filename in FORCED_ALIGNER_REQUIRED_FILENAMES
    ):
        return str(local_model)
    return FORCED_ALIGNER_HUB_MODEL_ID


def _is_complete_model_directory(model_directory: Path) -> bool:
    """Check that required metadata and all indexed Safetensors shards are usable."""
    if not _is_nonempty_regular_file(model_directory / MODEL_METADATA_FILENAME):
        return False

    try:
        index_data = json.loads(
            (model_directory / WEIGHT_INDEX_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    if not isinstance(index_data, dict):
        return False
    weight_map = index_data.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        return False

    for shard_name in weight_map.values():
        if not isinstance(shard_name, str) or not shard_name:
            return False
        if not _is_nonempty_regular_file(model_directory / shard_name):
            return False
    return True


def _is_nonempty_regular_file(path: Path) -> bool:
    """Return whether a path is a readable, non-empty regular file."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
