from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from PIL import Image


VISION_DIR = Path(__file__).resolve().parents[2] / "vision-local"
sys.path.insert(0, str(VISION_DIR))

import batch_classify  # noqa: E402
import vision_runtime  # noqa: E402


class VisionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model_environment = patch.dict(
            os.environ,
            {"MCP_TOOLS_MODEL_DIR": "/tmp/mcp-tools-test-models"},
        )
        self.model_environment.start()

    def tearDown(self) -> None:
        self.model_environment.stop()

    def test_image_data_url_resizes_and_records_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "portrait.png"
            Image.new("RGB", (1200, 800), "white").save(source)
            data_url, metadata = vision_runtime.image_data_url(source, max_edge=300)

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
        self.assertEqual(metadata["original_width"], 1200)
        self.assertEqual(metadata["original_height"], 800)
        self.assertEqual(metadata["input_width"], 300)
        self.assertEqual(metadata["input_height"], 200)

    def test_parse_json_object_accepts_wrapped_output(self) -> None:
        result = vision_runtime._parse_json_object(
            'result: {"wearing_glasses": true, "confidence": "high"}'
        )
        self.assertTrue(result["wearing_glasses"])
        self.assertEqual(result["confidence"], "high")

    def test_server_command_uses_cuda_friendly_parallel_defaults(self) -> None:
        settings = vision_runtime.load_settings()
        command = vision_runtime.build_server_command(settings)
        self.assertEqual(settings.profile, "default")
        self.assertEqual(settings.model_path.name, "Qwen3.5-9B-UD-Q4_K_XL.gguf")
        self.assertEqual(settings.port, 8003)
        self.assertIn("--n-gpu-layers", command)
        self.assertEqual(command[command.index("--parallel") + 1], "4")
        self.assertEqual(command[command.index("--image-max-tokens") + 1], "1024")
        self.assertEqual(command[command.index("--sleep-idle-seconds") + 1], "300")
        self.assertIn("--reasoning", command)

    def test_batch_profile_uses_independent_4b_backend(self) -> None:
        settings = vision_runtime.load_settings("batch")
        command = vision_runtime.build_server_command(settings)
        self.assertEqual(settings.profile, "batch")
        self.assertEqual(settings.model_path.name, "Qwen3.5-4B-UD-Q4_K_XL.gguf")
        self.assertEqual(settings.mmproj_path.parent, settings.model_path.parent)
        self.assertEqual(settings.port, 8004)
        self.assertEqual(settings.context_size, 4096)
        self.assertEqual(command[command.index("--image-max-tokens") + 1], "512")

    def test_environment_can_override_model_without_model_named_server(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VISION_LOCAL_MODEL_PATH": "/tmp/custom-model.gguf",
                "VISION_LOCAL_MMPROJ_PATH": "/tmp/custom-mmproj.gguf",
            },
            clear=False,
        ):
            settings = vision_runtime.load_settings()
        self.assertEqual(settings.model_path, Path("/tmp/custom-model.gguf"))
        self.assertEqual(settings.mmproj_path, Path("/tmp/custom-mmproj.gguf"))

    def test_default_model_override_does_not_leak_into_batch_profile(self) -> None:
        with patch.dict(
            "os.environ",
            {"VISION_LOCAL_MODEL_PATH": "/tmp/custom-default.gguf"},
            clear=False,
        ):
            default_settings = vision_runtime.load_settings()
            batch_settings = vision_runtime.load_settings("batch")
        self.assertEqual(default_settings.model_path, Path("/tmp/custom-default.gguf"))
        self.assertEqual(batch_settings.model_path.name, "Qwen3.5-4B-UD-Q4_K_XL.gguf")

    def test_generic_model_root_controls_both_profile_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MCP_TOOLS_MODEL_DIR": directory},
            clear=True,
        ):
            default_settings = vision_runtime.load_settings()
            batch_settings = vision_runtime.load_settings("batch")

        root = Path(directory) / "vision"
        self.assertEqual(
            default_settings.model_path,
            root / "Qwen3.5-9B-GGUF" / "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        )
        self.assertEqual(
            batch_settings.model_path,
            root / "Qwen3.5-4B-GGUF" / "Qwen3.5-4B-UD-Q4_K_XL.gguf",
        )

    def test_vision_model_dir_override_is_profile_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"VISION_LOCAL_MODEL_DIR": directory},
            clear=True,
        ):
            settings = vision_runtime.load_settings()
        self.assertEqual(
            settings.mmproj_path,
            Path(directory) / "Qwen3.5-9B-GGUF" / "mmproj-BF16.gguf",
        )

    def test_legacy_sibling_directory_emits_compatibility_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sibling_root = Path(directory) / "hf-models"
            profile = sibling_root / "Qwen3.5-9B-GGUF"
            profile.mkdir(parents=True)
            (profile / "Qwen3.5-9B-UD-Q4_K_XL.gguf").touch()
            (profile / "mmproj-BF16.gguf").touch()
            with (
                patch.dict(
                    os.environ,
                    {"XDG_CACHE_HOME": str(Path(directory) / "empty-cache")},
                    clear=True,
                ),
                patch.object(vision_runtime, "LEGACY_SIBLING_ROOT", sibling_root),
                warnings.catch_warnings(record=True) as captured,
            ):
                warnings.simplefilter("always")
                settings = vision_runtime.load_settings()

        self.assertEqual(settings.model_path.parent, profile)
        self.assertTrue(
            any("deprecated sibling model directory" in str(item.message) for item in captured)
        )

    def test_exact_model_pair_suppresses_legacy_fallback_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sibling_root = Path(directory) / "hf-models"
            profile = sibling_root / "Qwen3.5-9B-GGUF"
            profile.mkdir(parents=True)
            (profile / "Qwen3.5-9B-UD-Q4_K_XL.gguf").touch()
            (profile / "mmproj-BF16.gguf").touch()
            exact_model = Path(directory) / "exact-model.gguf"
            exact_projector = Path(directory) / "exact-mmproj.gguf"
            with (
                patch.dict(
                    os.environ,
                    {
                        "VISION_LOCAL_MODEL_PATH": str(exact_model),
                        "VISION_LOCAL_MMPROJ_PATH": str(exact_projector),
                    },
                    clear=True,
                ),
                patch.object(vision_runtime, "LEGACY_SIBLING_ROOT", sibling_root),
                warnings.catch_warnings(record=True) as captured,
            ):
                warnings.simplefilter("always")
                settings = vision_runtime.load_settings()

        self.assertEqual(settings.model_path, exact_model)
        self.assertEqual(settings.mmproj_path, exact_projector)
        self.assertEqual(captured, [])

    def test_server_environment_uses_explicit_cuda_library_path(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VISION_LOCAL_CUDA_LIBRARY_PATH": "/opt/cuda/lib:/opt/cuda/compat",
                "LD_LIBRARY_PATH": "/existing/lib",
            },
            clear=True,
        ):
            environment = vision_runtime.build_server_environment()

        self.assertEqual(
            environment["LD_LIBRARY_PATH"],
            os.pathsep.join(
                ["/opt/cuda/lib", "/opt/cuda/compat", "/existing/lib"]
            ),
        )

    def test_server_environment_discovers_repository_cuda_runtime(self) -> None:
        discovered = [Path("/repo/cuda/lib"), Path("/usr/lib/wsl/lib")]
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                vision_runtime,
                "_default_cuda_library_dirs",
                return_value=discovered,
            ),
        ):
            environment = vision_runtime.build_server_environment()

        self.assertEqual(
            environment["LD_LIBRARY_PATH"],
            os.pathsep.join(str(path) for path in discovered),
        )


class BatchArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model_environment = patch.dict(
            os.environ,
            {"MCP_TOOLS_MODEL_DIR": "/tmp/mcp-tools-test-models"},
        )
        self.model_environment.start()

    def tearDown(self) -> None:
        self.model_environment.stop()

    def test_classify_one_passes_batch_settings_to_runtime(self) -> None:
        settings = vision_runtime.load_settings("batch")
        image_path = Path("/tmp/face.png")
        prediction = {
            "wearing_glasses": True,
            "confidence": "high",
            "latency_ms": 10,
        }
        with patch.object(
            batch_classify,
            "classify_eyewear",
            return_value=prediction,
        ) as classify:
            result = batch_classify.classify_one(
                image_path,
                "G",
                True,
                max_edge=512,
                retries=0,
                settings=settings,
            )
        classify.assert_called_once_with(
            image_path,
            max_edge=512,
            settings=settings,
        )
        self.assertFalse(result["misclassified"])

    def test_list_images_ignores_non_images_and_sorts_naturally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("face-10.png", "face-2.png"):
                Image.new("RGB", (16, 16), "white").save(root / name)
            (root / "notes.txt").write_text("ignored", encoding="utf-8")
            names = [path.name for path in batch_classify.list_images(root)]
        self.assertEqual(names, ["face-2.png", "face-10.png"])

    def test_latest_jsonl_record_wins_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.jsonl"
            rows = [
                {"file": "/tmp/face-1.png", "error": "transient"},
                {"file": "/tmp/face-1.png", "error": None, "misclassified": False},
            ]
            results.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            latest = batch_classify.load_latest_records(results)
        self.assertIsNone(latest["/tmp/face-1.png"]["error"])


if __name__ == "__main__":
    unittest.main()
