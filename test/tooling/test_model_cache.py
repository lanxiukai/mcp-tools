from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from model_cache import configure_model_cache, model_cache_root


def test_repository_model_symlink_resolves_to_shared_storage(tmp_path: Path) -> None:
    repository = tmp_path / "mcp-tools"
    repository.mkdir()
    shared = tmp_path / "hf-models" / "models"
    shared.mkdir(parents=True)
    (repository / "models").symlink_to(shared, target_is_directory=True)
    assert model_cache_root({}, repository_root=repository) == shared


def test_cache_configuration_replaces_legacy_paths_without_moving_auth(tmp_path: Path) -> None:
    root = tmp_path / "models"
    with patch.dict(os.environ, {
        "MCP_TOOLS_MODEL_DIR": str(root),
        "HF_HOME": str(tmp_path / "authentication"),
        "HF_TOKEN_PATH": str(tmp_path / "token"),
        "HF_HUB_CACHE": "/old/cache",
        "TRANSFORMERS_CACHE": "/old/transformers",
    }, clear=True):
        assert configure_model_cache() == root
        assert os.environ["HF_HUB_CACHE"] == str(root / ".cache/huggingface/hub")
        assert os.environ["HF_XET_CACHE"] == str(root / ".cache/huggingface/xet")
        assert os.environ["HF_ASSETS_CACHE"] == str(root / ".cache/huggingface/assets")
        assert os.environ["HF_HOME"] == str(tmp_path / "authentication")
        assert os.environ["HF_TOKEN_PATH"] == str(tmp_path / "token")
        assert "TRANSFORMERS_CACHE" not in os.environ
    assert not root.exists()
