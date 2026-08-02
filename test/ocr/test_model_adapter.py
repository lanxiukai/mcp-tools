from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import torch
from PIL import Image

from ocr.model_adapter import (
    DEFAULT_LAYOUT_PYTHON,
    LayoutRegion,
    OCRModel,
    format_layout_text,
    resolve_model_path,
    task_for_layout_label,
    task_prompt,
)
from ocr.paddle_layout_worker import _normalise_boxes


class TestModelAdapter(TestCase):
    def test_layout_defaults_to_the_ocr_interpreter(self) -> None:
        self.assertEqual(DEFAULT_LAYOUT_PYTHON, Path(sys.executable))

    def test_explicit_local_model_path_enables_offline_loading(self) -> None:
        with TemporaryDirectory() as directory:
            expected = Path(directory).resolve()
            model_path, local_only = resolve_model_path(directory)
        self.assertEqual(model_path, str(expected))
        self.assertTrue(local_only)

    def test_model_root_resolves_model_id_without_coupling_mcp_to_backend(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "owner" / "model"
            model.mkdir(parents=True)
            with patch.dict(os.environ, {"OCR_MODEL_ROOT": str(root)}, clear=True):
                model_path, local_only = resolve_model_path("owner/model")
        self.assertEqual(model_path, str(model.resolve()))
        self.assertTrue(local_only)

    def test_task_prompts_are_validated_at_adapter_boundary(self) -> None:
        self.assertEqual(task_prompt("ocr"), "OCR:")
        self.assertEqual(task_prompt("formula"), "Formula Recognition:")
        with self.assertRaisesRegex(ValueError, "Unsupported OCR_TASK"):
            task_prompt("future-unknown-task")

    def test_backend_tuning_is_environment_driven(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OCR_TASK": "table",
                "OCR_MAX_NEW_TOKENS": "1024",
                "OCR_MAX_GENERATION_SECONDS": "45",
                "OCR_PDF_DPI": "200",
                "OCR_RECOGNITION_BATCH_SIZE": "3",
                "OCR_PAGE_BATCH_SIZE": "2",
                "OCR_USE_KV_CACHE": "0",
                "OCR_TRUST_REMOTE_CODE": "1",
                "OCR_USE_LAYOUT": "0",
                "OCR_LAYOUT_THRESHOLD": "0.6",
                "OCR_LAYOUT_MIN_HEIGHT": "256",
            },
            clear=True,
        ):
            model = OCRModel()
        self.assertEqual(model.task, "table")
        self.assertEqual(model.max_new_tokens, 1024)
        self.assertEqual(model.max_generation_seconds, 45)
        self.assertEqual(model.pdf_dpi, 200)
        self.assertEqual(model.recognition_batch_size, 3)
        self.assertEqual(model.page_batch_size, 2)
        self.assertFalse(model.use_kv_cache)
        self.assertTrue(model.trust_remote_code)
        self.assertFalse(model.use_layout)
        self.assertEqual(model.layout_threshold, 0.6)
        self.assertEqual(model.layout_min_height, 256)

    def test_kv_cache_is_enabled_by_default_and_forwarded_to_generation(self) -> None:
        class TensorInputs(dict):
            def to(self, _device):
                return self

        with patch.dict(os.environ, {}, clear=True):
            model = OCRModel()
        model.model = MagicMock()
        model.model.parameters.return_value = iter(
            [SimpleNamespace(device=torch.device("cpu"))]
        )
        model.model.generate.return_value = torch.tensor([[1, 2, 3]])
        model.processor = MagicMock()
        model.processor.image_processor = SimpleNamespace(min_pixels=112896)
        model.processor.apply_chat_template.return_value = TensorInputs(
            input_ids=torch.tensor([[1, 2]])
        )
        model.processor.batch_decode.return_value = ["recognized"]

        result = model._predict_batch([Image.new("RGB", (32, 32), "white")], ["ocr"])

        self.assertTrue(model.use_kv_cache)
        self.assertEqual(result, ["recognized"])
        self.assertTrue(model.model.generate.call_args.kwargs["use_cache"])

    def test_layout_labels_select_replaceable_recognition_tasks(self) -> None:
        self.assertEqual(task_for_layout_label("display_formula"), "formula")
        self.assertEqual(task_for_layout_label("table"), "table")
        self.assertEqual(task_for_layout_label("chart"), "chart")
        self.assertEqual(task_for_layout_label("seal"), "seal")
        self.assertEqual(task_for_layout_label("text"), "ocr")
        self.assertEqual(task_for_layout_label("table", "spotting"), "spotting")

    def test_layout_titles_gain_only_minimal_markdown_structure(self) -> None:
        self.assertEqual(format_layout_text("doc_title", "A Title"), "# A Title")
        self.assertEqual(format_layout_text("paragraph_title", "Section"), "## Section")
        self.assertEqual(format_layout_text("paragraph_title", "## Existing"), "## Existing")
        self.assertEqual(format_layout_text("text", "Body"), "Body")
        self.assertEqual(
            format_layout_text(
                "table",
                "<fcel>Name<fcel>Value<nl><fcel>A<fcel>1<nl>",
            ),
            "| Name | Value |\n| --- | --- |\n| A | 1 |",
        )

    def test_layout_worker_normalises_missing_order_and_threshold(self) -> None:
        result = {
            "res": {
                "boxes": [
                    {
                        "label": "text",
                        "score": 0.8,
                        "coordinate": [10.4, 20.6, 30.2, 40.8],
                        "order": None,
                    },
                    {
                        "label": "noise",
                        "score": 0.2,
                        "coordinate": [0, 0, 1, 1],
                    },
                ]
            }
        }
        self.assertEqual(
            _normalise_boxes(result, 0.5),
            [
                {
                    "label": "text",
                    "score": 0.8,
                    "coordinate": [10, 21, 30, 41],
                    "order": 1,
                }
            ],
        )

    def test_page_recognition_crops_in_reading_order_and_routes_tasks(self) -> None:
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            model = OCRModel()
            regions = [
                LayoutRegion("table", 0.9, (40, 40, 90, 90), 2),
                LayoutRegion("doc_title", 0.9, (10, 10, 80, 30), 1),
            ]
            with patch.object(model, "predict_many", return_value=["Title", "| A |"]) as predict:
                markdown = model._recognize_page(image_path, regions)

        self.assertEqual(markdown, "# Title\n\n| A |")
        self.assertEqual(predict.call_args.args[1], ["ocr", "table"])

    def test_no_layout_regions_fall_back_to_bounded_tiles(self) -> None:
        with patch.dict(os.environ, {"OCR_FALLBACK_TILE_HEIGHT": "400"}, clear=True):
            model = OCRModel()
        regions = model._fallback_regions(600, 900)
        self.assertEqual([region.coordinate for region in regions], [
            (0, 0, 600, 400),
            (0, 400, 600, 800),
            (0, 800, 600, 900),
        ])

    def test_compact_formula_line_is_not_confused_with_wide_language_line(self) -> None:
        formula = Image.new("L", (400, 100), "white")
        for x in range(30, 370):
            formula.putpixel((x, 50), 0)
        language = Image.new("L", (1200, 100), "white")
        for x in range(30, 1170):
            language.putpixel((x, 50), 0)
        self.assertTrue(OCRModel._looks_like_formula_line(formula))
        self.assertFalse(OCRModel._looks_like_formula_line(language))

    def test_consecutive_pages_pool_crops_before_recognition(self) -> None:
        with patch.dict(os.environ, {"OCR_PAGE_BATCH_SIZE": "2"}, clear=True):
            model = OCRModel()
        crop_one = object()
        crop_two = object()
        with patch.object(model, "_run_layout", return_value=[[], []]), patch.object(
            model,
            "_prepare_page",
            side_effect=[
                ([crop_one], ["ocr"], ["text"]),
                ([crop_two], ["formula"], ["display_formula"]),
            ],
        ), patch.object(model, "predict_many", return_value=["one", "two"]) as predict:
            pages = model._predict_paths([Path("one.png"), Path("two.png")], Path("."))

        self.assertEqual([page["markdown"] for page in pages], ["one", "two"])
        self.assertEqual(predict.call_args.args, ([crop_one, crop_two], ["ocr", "formula"]))
