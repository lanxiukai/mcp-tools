from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.model_paths import (
    DEFAULT_LAYOUT_MODEL_ID,
    DEFAULT_MODEL_ID,
    model_cache_root,
    resolve_layout_model_path,
    resolve_model_path,
)


class TestModelPaths(TestCase):

    def test_project_model_root_override_wins(self) -> None:
        with TemporaryDirectory() as directory:
            configured = Path(directory) / "shared-models"
            root = model_cache_root(
                {
                    "MCP_TOOLS_MODEL_DIR": str(configured),
                    "XDG_CACHE_HOME": "/ignored",
                }
            )
        self.assertEqual(root, configured.resolve())

    def test_explicit_local_recognizer_path_is_offline(self) -> None:
        with TemporaryDirectory() as directory:
            model = Path(directory) / "model"
            model.mkdir()
            resolved, local_only = resolve_model_path(str(model), {})
        self.assertEqual(resolved, str(model.resolve()))
        self.assertTrue(local_only)

    def test_missing_explicit_recognizer_path_has_actionable_error(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaisesRegex(FileNotFoundError, "OCR_MODEL_NAME"):
                resolve_model_path(str(missing), {})

    def test_ocr_model_root_must_contain_the_requested_model(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "OCR_MODEL_ROOT is set"):
                resolve_model_path(
                    "owner/model",
                    {"OCR_MODEL_ROOT": directory},
                )

    def test_generic_model_root_resolves_recognizer_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            model = root / "safetensors" / DEFAULT_MODEL_ID
            model.mkdir(parents=True)
            resolved, local_only = resolve_model_path(
                DEFAULT_MODEL_ID,
                {"MCP_TOOLS_MODEL_DIR": str(root)},
            )
        self.assertEqual(resolved, str(model.resolve()))
        self.assertTrue(local_only)

    def test_hub_id_uses_library_cache_when_no_local_snapshot_exists(self) -> None:
        with TemporaryDirectory() as directory:
            resolved, local_only = resolve_model_path(
                DEFAULT_MODEL_ID,
                {"MCP_TOOLS_MODEL_DIR": str(Path(directory) / "models")},
            )
        self.assertEqual(resolved, DEFAULT_MODEL_ID)
        self.assertFalse(local_only)



    def test_layout_uses_generic_root_or_paddlex_cache(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            layout = root / "safetensors" / DEFAULT_LAYOUT_MODEL_ID
            layout.mkdir(parents=True)
            resolved = resolve_layout_model_path({"MCP_TOOLS_MODEL_DIR": str(root)})
            fallback = resolve_layout_model_path(
                {"MCP_TOOLS_MODEL_DIR": str(Path(directory) / "empty-models")},
            )
        self.assertEqual(resolved, layout.resolve())
        self.assertIsNone(fallback)


    def test_missing_explicit_layout_path_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-layout"
            with self.assertRaisesRegex(FileNotFoundError, "OCR_LAYOUT_MODEL"):
                resolve_layout_model_path({"OCR_LAYOUT_MODEL": str(missing)})
