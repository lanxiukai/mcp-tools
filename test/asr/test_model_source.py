from pathlib import Path

from asr.model_source import HUB_MODEL_ID, resolve_model_source


def _complete_local_model(repository_root: Path) -> Path:
    model_dir = repository_root / "models" / "safetensors" / "Qwen" / "Qwen3-ASR-1.7B"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors.index.json").write_text(
        '{"weight_map": {"encoder.weight": "model-00001-of-00001.safetensors"}}'
    )
    (model_dir / "model-00001-of-00001.safetensors").write_bytes(b"weights")
    return model_dir


class TestResolveModelSource:
    def test_returns_explicit_model_unchanged_when_provided(self, tmp_path: Path) -> None:
        """Given an explicit model, when resolving, then preserve it exactly."""
        explicit_model = "custom-org/custom-asr"

        model_source = resolve_model_source(explicit_model, tmp_path)

        assert model_source == explicit_model

    def test_returns_explicit_local_model_unchanged_when_provided(
        self, tmp_path: Path
    ) -> None:
        explicit_model = str(tmp_path / "custom-local-asr")

        model_source = resolve_model_source(explicit_model, tmp_path)

        assert model_source == explicit_model

    def test_prefers_complete_repository_local_model_when_no_explicit_model(
        self, tmp_path: Path
    ) -> None:
        """Given complete local model files, when no source is explicit, then use local."""
        local_model = _complete_local_model(tmp_path)

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == str(local_model)

    def test_falls_back_to_hub_when_local_model_is_absent(self, tmp_path: Path) -> None:
        """Given no local directory, when resolving defaults, then use the Hub ID."""

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_metadata_is_missing(self, tmp_path: Path) -> None:
        """Given a local directory without metadata, when resolving, then use Hub."""
        model_dir = tmp_path / "models" / "safetensors" / "Qwen" / "Qwen3-ASR-1.7B"
        model_dir.mkdir(parents=True)

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_metadata_is_empty(self, tmp_path: Path) -> None:
        model_dir = _complete_local_model(tmp_path)
        (model_dir / "config.json").write_text("")

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_weight_index_is_invalid(self, tmp_path: Path) -> None:
        """Given malformed index JSON, when resolving defaults, then use the Hub ID."""
        model_dir = _complete_local_model(tmp_path)
        (model_dir / "model.safetensors.index.json").write_text("not json")

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_weight_map_is_empty(self, tmp_path: Path) -> None:
        """Given an empty parsed weight map, when resolving defaults, then use Hub."""
        model_dir = _complete_local_model(tmp_path)
        (model_dir / "model.safetensors.index.json").write_text('{"weight_map": {}}')

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_indexed_shard_is_empty(self, tmp_path: Path) -> None:
        model_dir = _complete_local_model(tmp_path)
        shard = model_dir / "model-00001-of-00001.safetensors"
        shard.write_bytes(b"")

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_indexed_shard_is_missing(self, tmp_path: Path) -> None:
        model_dir = _complete_local_model(tmp_path)
        (model_dir / "model-00001-of-00001.safetensors").unlink()

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

    def test_falls_back_to_hub_when_indexed_shard_is_not_a_regular_file(
        self, tmp_path: Path
    ) -> None:
        model_dir = _complete_local_model(tmp_path)
        shard = model_dir / "model-00001-of-00001.safetensors"
        shard.unlink()
        shard.mkdir()

        model_source = resolve_model_source(None, tmp_path)

        assert model_source == HUB_MODEL_ID

