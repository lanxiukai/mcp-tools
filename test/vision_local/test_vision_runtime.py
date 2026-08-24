from __future__ import annotations

import argparse
import json
import os
import shutil
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
import vision_local_mcp_server  # noqa: E402
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

    def test_image_data_url_normalizes_supported_modes_and_aspect_ratios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Unicode 图像 with spaces"
            root.mkdir()
            cases = (
                ("transparent.png", "RGBA", (1, 64), (255, 0, 0, 0)),
                ("grayscale.webp", "L", (64, 1), 128),
                ("tiny.jpeg", "RGB", (1, 1), "white"),
            )
            for filename, mode, size, color in cases:
                with self.subTest(filename=filename):
                    source = root / filename
                    Image.new(mode, size, color).save(source)

                    data_url, metadata = vision_runtime.image_data_url(
                        source,
                        max_edge=32,
                    )

                    self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
                    self.assertEqual(metadata["original_width"], size[0])
                    self.assertEqual(metadata["original_height"], size[1])
                    self.assertLessEqual(max(metadata["input_width"], metadata["input_height"]), 32)

    def test_corrupt_missing_and_unsupported_images_have_structured_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "corrupt.png"
            unsupported = root / "image.tiff"
            corrupt.write_bytes(b"not an image")
            unsupported.write_bytes(b"not an image")

            corrupt_result = vision_local_mcp_server.analyze_image(str(corrupt))
            missing_result = vision_local_mcp_server.analyze_image(
                str(root / "missing.png")
            )
            unsupported_result = vision_local_mcp_server.analyze_image(
                str(unsupported)
            )

        self.assertIn("error", corrupt_result)
        self.assertEqual(corrupt_result["error_type"], "UnidentifiedImageError")
        self.assertEqual(missing_result["error_type"], "FileNotFoundError")
        self.assertEqual(unsupported_result["error_type"], "ValueError")
        self.assertIn("Unsupported image type", unsupported_result["error"])

    def test_analyze_image_accepts_documented_boundaries_and_rejects_overflow(self) -> None:
        expected = {"text": "ok"}
        with patch.object(
            vision_local_mcp_server,
            "runtime_analyze_image",
            return_value=expected,
        ) as analyze:
            minimum = vision_local_mcp_server.analyze_image(
                "/tmp/image.png",
                prompt="x",
                max_tokens=1,
                max_edge=128,
            )
            maximum = vision_local_mcp_server.analyze_image(
                "/tmp/image.png",
                prompt="long " * 10_000,
                max_tokens=4096,
                max_edge=2048,
            )
            empty = vision_local_mcp_server.analyze_image(
                "/tmp/image.png",
                prompt="   ",
            )
            too_many_tokens = vision_local_mcp_server.analyze_image(
                "/tmp/image.png",
                max_tokens=4097,
            )
            too_large = vision_local_mcp_server.analyze_image(
                "/tmp/image.png",
                max_edge=2049,
            )

        self.assertEqual(minimum, expected)
        self.assertEqual(maximum, expected)
        self.assertEqual(analyze.call_count, 2)
        for result in (empty, too_many_tokens, too_large):
            self.assertEqual(result["error_type"], "ValueError")

    def test_eyewear_schema_rejects_invalid_model_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "portrait.png"
            Image.new("RGB", (32, 32), "white").save(source)
            with patch.object(
                vision_runtime,
                "_chat",
                return_value=(
                    '{"wearing_glasses":"yes","confidence":"certain"}',
                    {},
                ),
            ):
                with self.assertRaisesRegex(ValueError, "wearing_glasses"):
                    vision_runtime.classify_eyewear(source)

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
    @staticmethod
    def _batch_args(
        g_dir: Path,
        ng_dir: Path,
        output_dir: Path,
        *,
        resume: bool,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            g_dir=g_dir,
            ng_dir=ng_dir,
            output_dir=output_dir,
            concurrency=1,
            max_edge=512,
            retries=0,
            progress_every=1,
            resume=resume,
        )

    @staticmethod
    def _record(path: Path, source_label: str) -> dict:
        expected = source_label == "G"
        return {
            "file": str(path.resolve()),
            "filename": path.name,
            "image_id": None,
            "source_label": source_label,
            "expected_wearing_glasses": expected,
            "predicted_wearing_glasses": expected,
            "misclassified": False,
            "confidence": "high",
            "latency_ms": 1,
            "attempts": 1,
            "error": None,
            "completed_at": batch_classify.utc_now(),
        }

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

    def test_resume_ignores_only_a_truncated_final_jsonl_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.jsonl"
            completed = {
                "file": "/tmp/face-1.png",
                "error": None,
                "misclassified": False,
            }
            results.write_text(
                json.dumps(completed) + "\n" + '{"file":"/tmp/face-2.png"',
                encoding="utf-8",
            )

            latest = batch_classify.load_latest_records(results)

        self.assertEqual(latest, {completed["file"]: completed})

    def test_resume_rejects_a_malformed_middle_jsonl_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.jsonl"
            results.write_text(
                "\n".join(
                    [
                        json.dumps({"file": "/tmp/face-1.png", "error": None}),
                        '{"file": broken}',
                        json.dumps({"file": "/tmp/face-2.png", "error": None}),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Invalid JSONL.*:2"):
                batch_classify.load_latest_records(results)

    def test_atomic_json_replace_failure_preserves_destination_and_removes_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "progress.json"
            destination.write_text('{"status":"previous"}\n', encoding="utf-8")

            with patch.object(
                Path,
                "replace",
                side_effect=OSError("simulated atomic replace failure"),
            ), self.assertRaisesRegex(OSError, "atomic replace failure"):
                batch_classify.write_json_atomic(
                    destination,
                    {"status": "replacement"},
                )

            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                '{"status":"previous"}\n',
            )
            self.assertFalse((Path(directory) / "progress.json.tmp").exists())

    def test_resume_rebuilds_missing_or_malformed_coordination_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g_dir = root / "G"
            ng_dir = root / "NG"
            g_dir.mkdir()
            ng_dir.mkdir()
            g_image = g_dir / "glasses.png"
            ng_image = ng_dir / "no-glasses.png"
            Image.new("RGB", (8, 8), "white").save(g_image)
            Image.new("RGB", (8, 8), "black").save(ng_image)

            for state in ("missing", "malformed"):
                with self.subTest(state=state):
                    output_dir = root / f"output-{state}"
                    output_dir.mkdir()
                    records = (
                        self._record(g_image, "G"),
                        self._record(ng_image, "NG"),
                    )
                    (output_dir / "results.jsonl").write_text(
                        "".join(json.dumps(record) + "\n" for record in records),
                        encoding="utf-8",
                    )
                    if state == "malformed":
                        (output_dir / "manifest.json").write_text("{broken", encoding="utf-8")
                        (output_dir / "progress.json").write_text("{broken", encoding="utf-8")

                    with patch.object(batch_classify, "ensure_server"):
                        code = batch_classify.run(
                            self._batch_args(
                                g_dir,
                                ng_dir,
                                output_dir,
                                resume=True,
                            )
                        )

                    self.assertEqual(code, 0)
                    self.assertEqual(
                        json.loads((output_dir / "progress.json").read_text())["completed"],
                        2,
                    )
                    self.assertEqual(
                        json.loads((output_dir / "summary.json").read_text())["total_images"],
                        2,
                    )

    def test_existing_results_without_resume_are_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g_dir = root / "G"
            ng_dir = root / "NG"
            output_dir = root / "output"
            g_dir.mkdir()
            ng_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (8, 8), "white").save(g_dir / "face.png")
            Image.new("RGB", (8, 8), "black").save(ng_dir / "face.png")
            results = output_dir / "results.jsonl"
            results.write_text("previous\n", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                batch_classify.run(
                    self._batch_args(g_dir, ng_dir, output_dir, resume=False)
                )

            self.assertEqual(results.read_text(encoding="utf-8"), "previous\n")

    def test_read_only_output_directory_fails_before_backend_start(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("permission semantics cannot be asserted as root")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g_dir = root / "G"
            ng_dir = root / "NG"
            output_dir = root / "output"
            g_dir.mkdir()
            ng_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (8, 8), "white").save(g_dir / "face.png")
            output_dir.chmod(0o500)
            try:
                with patch.object(batch_classify, "ensure_server") as ensure, \
                        self.assertRaises(PermissionError):
                    batch_classify.run(
                        self._batch_args(g_dir, ng_dir, output_dir, resume=False)
                    )
                ensure.assert_not_called()
            finally:
                output_dir.chmod(0o700)

    def test_output_directory_deleted_during_work_fails_without_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g_dir = root / "G"
            ng_dir = root / "NG"
            output_dir = root / "output"
            g_dir.mkdir()
            ng_dir.mkdir()
            image = g_dir / "face.png"
            Image.new("RGB", (8, 8), "white").save(image)

            def delete_output(*_args, **_kwargs) -> dict:
                shutil.rmtree(output_dir)
                return self._record(image, "G")

            with patch.object(batch_classify, "ensure_server"), patch.object(
                batch_classify,
                "classify_one",
                side_effect=delete_output,
            ), self.assertRaises(OSError):
                batch_classify.run(
                    self._batch_args(g_dir, ng_dir, output_dir, resume=False)
                )

            self.assertFalse(output_dir.exists())

    def test_batch_status_does_not_treat_an_unrelated_reused_pid_as_alive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            (output_dir / "manifest.json").write_text(
                json.dumps({"status": "running", "pid": os.getpid()}),
                encoding="utf-8",
            )

            status = vision_local_mcp_server.eyewear_batch_status(str(output_dir))

        self.assertFalse(status["process_alive"])


if __name__ == "__main__":
    unittest.main()
