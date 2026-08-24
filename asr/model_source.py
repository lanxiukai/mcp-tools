"""Resolve the Qwen3-ASR model source without touching the network."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

HUB_MODEL_ID: Final[str] = "Qwen/Qwen3-ASR-1.7B"
COMPACT_HUB_MODEL_ID: Final[str] = "Qwen/Qwen3-ASR-0.6B"
LOCAL_MODEL_RELATIVE_PATH: Final[Path] = Path(
    "models/safetensors/Qwen/Qwen3-ASR-1.7B"
)
COMPACT_LOCAL_MODEL_RELATIVE_PATH: Final[Path] = Path(
    "models/safetensors/Qwen/Qwen3-ASR-0.6B"
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


@dataclass(frozen=True)
class AsrRuntimeProfile:
    """One bounded ASR runtime configuration."""

    name: str
    hub_model_id: str
    local_model_relative_path: Path
    max_chunk_seconds: int
    max_new_tokens: int
    cuda_memory_limit_mib: int | None


RUNTIME_PROFILES: Final[dict[str, AsrRuntimeProfile]] = {
    "default": AsrRuntimeProfile(
        name="default",
        hub_model_id=HUB_MODEL_ID,
        local_model_relative_path=LOCAL_MODEL_RELATIVE_PATH,
        max_chunk_seconds=480,
        max_new_tokens=4096,
        cuda_memory_limit_mib=None,
    ),
    "8gb": AsrRuntimeProfile(
        name="8gb",
        hub_model_id=COMPACT_HUB_MODEL_ID,
        local_model_relative_path=COMPACT_LOCAL_MODEL_RELATIVE_PATH,
        max_chunk_seconds=60,
        max_new_tokens=1024,
        cuda_memory_limit_mib=6144,
    ),
}


def resolve_runtime_profile(profile: str) -> AsrRuntimeProfile:
    """Return a named profile or fail before any model is loaded."""
    try:
        return RUNTIME_PROFILES[profile]
    except KeyError as exc:
        choices = ", ".join(RUNTIME_PROFILES)
        raise ValueError(
            f"Unknown ASR profile {profile!r}; expected one of: {choices}"
        ) from exc


def resolve_model_source(
    explicit_model: str | None,
    repository_root: Path,
    *,
    profile: str = "default",
) -> str:
    """Return an explicit source or the complete local model, else the Hub ID."""
    if explicit_model is not None:
        return explicit_model

    runtime_profile = resolve_runtime_profile(profile)
    local_model = repository_root / runtime_profile.local_model_relative_path
    if _is_complete_model_directory(local_model):
        return str(local_model)
    return runtime_profile.hub_model_id


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

    if _is_nonempty_regular_file(model_directory / "model.safetensors"):
        return True

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
