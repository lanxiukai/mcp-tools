"""Tests for ForcedAligner model-source resolution.

Covers ``resolve_forced_aligner_source()`` — the ForcedAligner variant of the
shared model-source resolver from ``asr.model_source``.
"""

from pathlib import Path

from asr.model_source import (
    FORCED_ALIGNER_HUB_MODEL_ID,
    FORCED_ALIGNER_LOCAL_MODEL_RELATIVE_PATH,
    resolve_forced_aligner_source,
)


def _complete_fa_local_model(repository_root: Path) -> Path:
    """Create a complete ForcedAligner local directory fixture."""
    model_dir = repository_root / FORCED_ALIGNER_LOCAL_MODEL_RELATIVE_PATH
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"fa-weights")
    (model_dir / "preprocessor_config.json").write_text("{}")
    (model_dir / "tokenizer_config.json").write_text("{}")
    (model_dir / "vocab.json").write_text("{}")
    (model_dir / "merges.txt").write_text("# merges")
    return model_dir


class TestResolveForcedAlignerSource:
    """ForcedAligner model source resolution tests."""

    def test_resolver_function_is_importable_and_callable(self) -> None:
        """The resolver is importable from ``asr.model_source`` and callable."""
        assert callable(resolve_forced_aligner_source)

    def test_returns_explicit_model_unchanged(self, tmp_path: Path) -> None:
        """Given an explicit model, when resolving, then preserve it."""
        explicit = "custom-org/custom-fa"
        result = resolve_forced_aligner_source(explicit, tmp_path)
        assert result == explicit

    def test_returns_explicit_local_model_unchanged(self, tmp_path: Path) -> None:
        explicit = str(tmp_path / "custom-local-fa")
        result = resolve_forced_aligner_source(explicit, tmp_path)
        assert result == explicit

    def test_prefers_complete_local_model_when_no_explicit(
        self, tmp_path: Path
    ) -> None:
        """Given a complete local ForcedAligner directory with all 6 required
        files, when no explicit source is given, then resolve to local."""
        local_fa = _complete_fa_local_model(tmp_path)
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == str(local_fa)

    def test_falls_back_to_hub_when_local_model_is_absent(
        self, tmp_path: Path
    ) -> None:
        """Given no local ForcedAligner directory, when resolving, then Hub."""
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_config_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir missing ``config.json``, when resolving,
        then fall back to Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "config.json").unlink()
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_model_safetensors_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir with no ``model.safetensors``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "model.safetensors").unlink()
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_preprocessor_config_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir missing ``preprocessor_config.json``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "preprocessor_config.json").unlink()
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_tokenizer_config_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir missing ``tokenizer_config.json``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "tokenizer_config.json").unlink()
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_vocab_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir missing ``vocab.json``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "vocab.json").unlink()
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_merges_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir missing ``merges.txt``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "merges.txt").unlink()
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_config_is_empty(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir with empty ``config.json``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "config.json").write_text("")
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID

    def test_falls_back_to_hub_when_model_safetensors_is_empty(
        self, tmp_path: Path
    ) -> None:
        """Given a local FA dir with empty ``model.safetensors``, then Hub."""
        model_dir = _complete_fa_local_model(tmp_path)
        (model_dir / "model.safetensors").write_bytes(b"")
        result = resolve_forced_aligner_source(None, tmp_path)
        assert result == FORCED_ALIGNER_HUB_MODEL_ID
