"""Replaceable OCR adapter using layout-guided PaddleOCR-VL recognition."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

import torch

from ocr.server_job_support import ModelPage, ModelPrediction

logger = logging.getLogger("ocr-model")

DEFAULT_MODEL_ID: Final = "PaddlePaddle/PaddleOCR-VL-1.6"
DEFAULT_LOCAL_MODEL: Final = (
    Path.home()
    / "project"
    / "hf-models"
    / "models"
    / "safetensors"
    / DEFAULT_MODEL_ID
)
DEFAULT_LAYOUT_PYTHON: Final = Path(sys.executable)
DEFAULT_LAYOUT_MODEL: Final = (
    Path.home()
    / "project"
    / "hf-models"
    / "models"
    / "safetensors"
    / "PaddlePaddle"
    / "PP-DocLayoutV3"
)
LAYOUT_WORKER: Final = Path(__file__).with_name("paddle_layout_worker.py")

TASK_PROMPTS: Final = {
    "ocr": "OCR:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
    "chart": "Chart Recognition:",
    "spotting": "Spotting:",
    "seal": "Seal Recognition:",
}
IGNORED_LAYOUT_LABELS: Final = frozenset({"header_image", "footer_image"})
FORMULA_LAYOUT_LABELS: Final = frozenset({"display_formula", "inline_formula"})
TITLE_LEVELS: Final = {"doc_title": 1, "paragraph_title": 2}


@dataclass(frozen=True, slots=True)
class LayoutRegion:
    """One ordered PP-DocLayoutV3 region in source-image coordinates."""

    label: str
    score: float
    coordinate: tuple[int, int, int, int]
    order: int


def default_model_name() -> str:
    """Prefer the complete repository-local snapshot, then the Hub model ID."""
    return str(DEFAULT_LOCAL_MODEL) if DEFAULT_LOCAL_MODEL.is_dir() else DEFAULT_MODEL_ID


def resolve_model_path(model_name: str) -> tuple[str, bool]:
    """Resolve an explicit path or model ID and say whether network access is disabled."""
    candidate = Path(model_name).expanduser()
    if candidate.is_dir():
        return str(candidate.resolve()), True

    model_root = os.environ.get("OCR_MODEL_ROOT", "").strip()
    if model_root:
        rooted = Path(model_root).expanduser() / model_name
        if rooted.is_dir():
            return str(rooted.resolve()), True

    if model_name == DEFAULT_MODEL_ID and DEFAULT_LOCAL_MODEL.is_dir():
        return str(DEFAULT_LOCAL_MODEL), True
    return model_name, False


def task_for_layout_label(label: str, default_task: str = "ocr") -> str:
    """Map replaceable layout labels to the backend's element-recognition tasks."""
    task_prompt(default_task)
    if default_task != "ocr":
        return default_task
    if label in FORMULA_LAYOUT_LABELS:
        return "formula"
    if label in {"table"}:
        return "table"
    if label in {"chart"}:
        return "chart"
    if label in {"seal"}:
        return "seal"
    return "ocr"


def format_layout_text(label: str, text: str) -> str:
    """Preserve reading order while adding minimal Markdown title structure."""
    text = text.strip()
    if label == "table" and "<fcel>" in text:
        text = _table_tokens_to_markdown(text)
    level = TITLE_LEVELS.get(label)
    if not text or level is None or text.startswith("#"):
        return text
    return f"{'#' * level} {text}"


def _table_tokens_to_markdown(text: str) -> str:
    """Convert PaddleOCR-VL's compact table-cell tokens to readable Markdown."""
    rows = []
    for raw_row in text.replace("<ecel>", "<fcel>").split("<nl>"):
        cells = [cell.strip() for cell in raw_row.split("<fcel>")[1:]]
        if cells:
            rows.append(cells)
    if not rows:
        return text.replace("<fcel>", "").replace("<ecel>", "").replace("<nl>", "\n").strip()
    if len(rows) == 1:
        return " | ".join(cell for cell in rows[0] if cell)
    columns = max(len(row) for row in rows)
    padded = [row + [""] * (columns - len(row)) for row in rows]
    rendered = ["| " + " | ".join(row) + " |" for row in padded]
    rendered.insert(1, "| " + " | ".join("---" for _ in range(columns)) + " |")
    return "\n".join(rendered)


class OCRModel:
    """Thread-confined OCR facade used by the durable job worker."""

    def __init__(self) -> None:
        self.model = None
        self.processor = None
        self.model_name = ""
        self.model_path = ""
        self.device = "cuda"
        self.task = os.environ.get("OCR_TASK", "ocr").strip().lower() or "ocr"
        self.max_new_tokens = int(os.environ.get("OCR_MAX_NEW_TOKENS", "512"))
        self.max_generation_seconds = float(os.environ.get("OCR_MAX_GENERATION_SECONDS", "60"))
        self.pdf_dpi = int(os.environ.get("OCR_PDF_DPI", "200"))
        self.recognition_batch_size = int(os.environ.get("OCR_RECOGNITION_BATCH_SIZE", "4"))
        self.page_batch_size = int(os.environ.get("OCR_PAGE_BATCH_SIZE", "4"))
        self.use_kv_cache = os.environ.get("OCR_USE_KV_CACHE", "1") != "0"
        self.trust_remote_code = os.environ.get("OCR_TRUST_REMOTE_CODE", "0") == "1"

        self.use_layout = os.environ.get("OCR_USE_LAYOUT", "1") != "0"
        self.layout_python = Path(
            os.environ.get("OCR_LAYOUT_PYTHON", str(DEFAULT_LAYOUT_PYTHON))
        ).expanduser()
        self.layout_model = Path(
            os.environ.get("OCR_LAYOUT_MODEL", str(DEFAULT_LAYOUT_MODEL))
        ).expanduser()
        self.layout_device = os.environ.get("OCR_LAYOUT_DEVICE", "gpu:0")
        self.layout_threshold = float(os.environ.get("OCR_LAYOUT_THRESHOLD", "0.5"))
        self.layout_timeout = float(os.environ.get("OCR_LAYOUT_TIMEOUT", "300"))
        self.layout_batch_size = int(os.environ.get("OCR_LAYOUT_BATCH_SIZE", "1"))
        self.layout_min_height = int(os.environ.get("OCR_LAYOUT_MIN_HEIGHT", "384"))
        self.fallback_tile_height = int(os.environ.get("OCR_FALLBACK_TILE_HEIGHT", "1200"))

        if self.max_new_tokens < 1 or self.recognition_batch_size < 1 or self.page_batch_size < 1:
            raise ValueError("OCR generation limits and batch size must be positive")
        if self.pdf_dpi < 72 or self.layout_min_height < 64 or self.fallback_tile_height < 128:
            raise ValueError("OCR image sizing configuration is unreasonably small")

    def load(self, model_name: str | None = None, device: str = "cuda") -> None:
        """Load a Transformers image-to-text model behind the stable OCR facade."""
        from transformers import AutoModelForImageTextToText, AutoProcessor

        requested_model = model_name or default_model_name()
        model_path, local_only = resolve_model_path(requested_model)
        task_prompt(self.task)
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested for OCR, but torch.cuda.is_available() is false")

        self.model_name = requested_model
        self.model_path = model_path
        self.device = device
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        attention = os.environ.get("OCR_ATTENTION", "").strip()

        logger.info(
            "Loading OCR recognizer %s (device=%s, attention=%s)",
            model_path,
            device,
            attention or "model-default",
        )
        started = time.time()
        self.processor = AutoProcessor.from_pretrained(
            model_path,
            local_files_only=local_only,
            trust_remote_code=self.trust_remote_code,
        )
        model_kwargs = {
            "dtype": dtype,
            "local_files_only": local_only,
            "trust_remote_code": self.trust_remote_code,
        }
        if attention:
            model_kwargs["attn_implementation"] = attention
        try:
            model = AutoModelForImageTextToText.from_pretrained(model_path, **model_kwargs)
        except (ImportError, RuntimeError, ValueError) as error:
            if not attention:
                raise
            logger.warning(
                "Attention backend %s failed (%s); retrying with the model default",
                attention,
                error,
            )
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            model_kwargs.pop("attn_implementation", None)
            model = AutoModelForImageTextToText.from_pretrained(model_path, **model_kwargs)

        self.model = model.to(device).eval()
        logger.info("OCR recognizer loaded in %.1fs", time.time() - started)

    def _predict_batch(self, images: Sequence, tasks: Sequence[str]) -> list[str]:
        if self.processor is None or self.model is None:
            raise RuntimeError("OCR model is not loaded")
        if len(images) != len(tasks) or not images:
            raise ValueError("OCR recognition batch must contain matching images and tasks")

        conversations = []
        for image, selected_task in zip(images, tasks, strict=True):
            prompt = task_prompt(selected_task)
            if image.mode != "RGB":
                image = image.convert("RGB")
            if selected_task == "spotting" and image.width < 1500 and image.height < 1500:
                from PIL import Image

                image = image.resize(
                    (image.width * 2, image.height * 2),
                    Image.Resampling.LANCZOS,
                )
            conversations.append(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": image},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ]
            )

        template_input = conversations[0] if len(conversations) == 1 else conversations
        max_pixels = (2048 if "spotting" in tasks else 1280) * 28 * 28
        image_processor = self.processor.image_processor
        minimum_pixels = getattr(image_processor, "min_pixels", None)
        if minimum_pixels is None:
            minimum_pixels = image_processor.size["shortest_edge"]
        inputs = self.processor.apply_chat_template(
            template_input,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            processor_kwargs={
                "padding": len(conversations) > 1,
                "images_kwargs": {
                    "size": {
                        "shortest_edge": minimum_pixels,
                        "longest_edge": max_pixels,
                    }
                }
            },
        ).to(next(self.model.parameters()).device)

        with torch.inference_mode():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                max_time=self.max_generation_seconds,
                do_sample=False,
                use_cache=self.use_kv_cache,
            )
        prompt_length = inputs["input_ids"].shape[-1]
        return [
            text.strip()
            for text in self.processor.batch_decode(
                outputs[:, prompt_length:],
                skip_special_tokens=True,
            )
        ]

    def predict_many(self, images: Sequence, tasks: Sequence[str]) -> list[str]:
        """Recognize ordered element crops in bounded GPU batches."""
        results: list[str] = []
        for start in range(0, len(images), self.recognition_batch_size):
            batch_images = images[start : start + self.recognition_batch_size]
            batch_tasks = tasks[start : start + self.recognition_batch_size]
            try:
                results.extend(self._predict_batch(batch_images, batch_tasks))
            except (RuntimeError, ValueError) as error:
                if len(batch_images) == 1:
                    raise
                logger.warning(
                    "OCR batch of %d failed (%s); retrying one crop at a time",
                    len(batch_images),
                    error,
                )
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                for image, task in zip(batch_images, batch_tasks, strict=True):
                    results.extend(self._predict_batch([image], [task]))
        return results

    def predict_single(self, image, task: str | None = None) -> str:
        """Recognize one PIL element image and return Markdown-compatible text."""
        selected_task = (task or self.task).strip().lower()
        return self.predict_many([image], [selected_task])[0]

    def _run_layout(self, image_paths: Sequence[Path], work_dir: Path) -> list[list[LayoutRegion]]:
        if not self.use_layout:
            return [[] for _ in image_paths]
        if not self.layout_python.is_file():
            raise RuntimeError(f"Layout Python not found: {self.layout_python}")
        if not self.layout_model.is_dir():
            raise RuntimeError(f"Layout model not found: {self.layout_model}")

        from PIL import Image

        candidate_indexes = []
        candidate_paths = []
        for page_index, image_path in enumerate(image_paths):
            with Image.open(image_path) as image:
                if image.height >= self.layout_min_height:
                    candidate_indexes.append(page_index)
                    candidate_paths.append(image_path)
        if not candidate_paths:
            return [[] for _ in image_paths]

        request_path = work_dir / "layout-request.json"
        output_path = work_dir / "layout-result.json"
        request_path.write_text(
            json.dumps(
                {
                    "images": [str(path) for path in candidate_paths],
                    "model_dir": str(self.layout_model),
                    "device": self.layout_device,
                    "threshold": self.layout_threshold,
                    "batch_size": self.layout_batch_size,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        started = time.time()
        completed = subprocess.run(
            [
                str(self.layout_python),
                str(LAYOUT_WORKER),
                "--request",
                str(request_path),
                "--output",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=self.layout_timeout,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
        if completed.stderr.strip():
            logger.debug("Layout worker stderr: %s", completed.stderr.strip())
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        raw_pages = payload.get("pages", [])
        if len(raw_pages) != len(candidate_paths):
            raise RuntimeError(
                f"Layout returned {len(raw_pages)} pages for {len(candidate_paths)} images"
            )

        detected_pages: list[list[LayoutRegion]] = []
        for candidate_index, raw_page in enumerate(raw_pages):
            if int(raw_page.get("page_index", -1)) != candidate_index:
                raise RuntimeError("Layout pages are not in source order")
            regions = [
                LayoutRegion(
                    label=str(box["label"]).lower(),
                    score=float(box["score"]),
                    coordinate=tuple(int(value) for value in box["coordinate"]),
                    order=int(box["order"]),
                )
                for box in raw_page.get("boxes", [])
            ]
            detected_pages.append(regions)
        pages: list[list[LayoutRegion]] = [[] for _ in image_paths]
        for page_index, regions in zip(candidate_indexes, detected_pages, strict=True):
            pages[page_index] = regions
        logger.info(
            "Layout detection completed for %d page(s) in %.2fs",
            len(candidate_paths),
            time.time() - started,
        )
        return pages

    def _fallback_regions(
        self,
        width: int,
        height: int,
        label: str = "text",
    ) -> list[LayoutRegion]:
        """Use bounded horizontal tiles when no usable layout regions exist."""
        if height <= self.fallback_tile_height:
            return [LayoutRegion(label, 1.0, (0, 0, width, height), 1)]
        regions = []
        for order, top in enumerate(range(0, height, self.fallback_tile_height), start=1):
            regions.append(
                LayoutRegion(
                    label,
                    1.0,
                    (0, top, width, min(height, top + self.fallback_tile_height)),
                    order,
                )
            )
        return regions

    @staticmethod
    def _looks_like_formula_line(image) -> bool:
        """Distinguish compact single-line notation from wide handwriting text."""
        if image.height >= 384 or image.width / max(image.height, 1) >= 8:
            return False
        grayscale = image.convert("L")
        grayscale.thumbnail((1000, 400))
        pixels = grayscale.load()
        minimum_ink = max(2, grayscale.width // 200)
        ink_rows = [
            sum(pixels[x, y] < 200 for x in range(grayscale.width)) >= minimum_ink
            for y in range(grayscale.height)
        ]
        dilated = [
            any(ink_rows[max(0, row - 2) : min(len(ink_rows), row + 3)])
            for row in range(len(ink_rows))
        ]
        bands = sum(
            is_ink and (row == 0 or not dilated[row - 1])
            for row, is_ink in enumerate(dilated)
        )
        return 0 < bands <= 3

    def _prepare_page(
        self,
        image_path: Path,
        regions: Sequence[LayoutRegion],
    ) -> tuple[list, list[str], list[str]]:
        from PIL import Image

        with Image.open(image_path) as source:
            source = source.convert("RGB")
            selected = [region for region in regions if region.label not in IGNORED_LAYOUT_LABELS]
            if not selected:
                label = "display_formula" if self._looks_like_formula_line(source) else "text"
                selected = self._fallback_regions(source.width, source.height, label)

            crops = []
            tasks = []
            labels = []
            for region in sorted(
                selected,
                key=lambda item: (item.order, item.coordinate[1], item.coordinate[0]),
            ):
                left, top, right, bottom = region.coordinate
                left = max(0, min(left, source.width))
                right = max(0, min(right, source.width))
                top = max(0, min(top, source.height))
                bottom = max(0, min(bottom, source.height))
                if right - left < 2 or bottom - top < 2:
                    continue
                padding = max(2, round(min(right - left, bottom - top) * 0.02))
                crop_box = (
                    max(0, left - padding),
                    max(0, top - padding),
                    min(source.width, right + padding),
                    min(source.height, bottom + padding),
                )
                crops.append(source.crop(crop_box))
                task = task_for_layout_label(region.label, self.task)
                if task == "table" and (bottom - top) * 6 < right - left:
                    task = "ocr"
                tasks.append(task)
                labels.append(region.label)

        return crops, tasks, labels

    @staticmethod
    def _render_page(labels: Sequence[str], texts: Sequence[str]) -> str:
        blocks = [
            format_layout_text(label, text)
            for label, text in zip(labels, texts, strict=True)
            if text.strip()
        ]
        return "\n\n".join(blocks)

    def _recognize_page(self, image_path: Path, regions: Sequence[LayoutRegion]) -> str:
        crops, tasks, labels = self._prepare_page(image_path, regions)
        if not crops:
            return ""
        return self._render_page(labels, self.predict_many(crops, tasks))

    def _predict_paths(self, image_paths: Sequence[Path], work_dir: Path) -> list[ModelPage]:
        try:
            layouts = self._run_layout(image_paths, work_dir)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            logger.warning("Layout detection failed (%s); using bounded page tiles", error)
            layouts = [[] for _ in image_paths]

        pages: list[ModelPage] = []
        for start in range(0, len(image_paths), self.page_batch_size):
            prepared = [
                self._prepare_page(image_paths[page_index], layouts[page_index])
                for page_index in range(start, min(len(image_paths), start + self.page_batch_size))
            ]
            flat_crops = [crop for crops, _, _ in prepared for crop in crops]
            flat_tasks = [task for _, tasks, _ in prepared for task in tasks]
            flat_texts = self.predict_many(flat_crops, flat_tasks) if flat_crops else []
            cursor = 0
            for offset, (crops, _, labels) in enumerate(prepared):
                page_texts = flat_texts[cursor : cursor + len(crops)]
                cursor += len(crops)
                pages.append(
                    {
                        "page_index": start + offset,
                        "markdown": self._render_page(labels, page_texts),
                    }
                )
        return pages

    def predict(self, file_path: str) -> ModelPrediction:
        """Recognize an image or rendered PDF with one stable return shape."""
        path = Path(file_path)
        return self._predict_pdf(path) if path.suffix.lower() == ".pdf" else self._predict_image(path)

    def _predict_image(self, image_path: Path) -> ModelPrediction:
        with tempfile.TemporaryDirectory(prefix="ocr-layout-") as directory:
            pages = self._predict_paths([image_path], Path(directory))
        markdown = pages[0]["markdown"]
        return {"page_count": 1, "markdown": markdown, "pages": pages}

    def _predict_pdf(self, pdf_path: Path) -> ModelPrediction:
        try:
            import fitz
        except ImportError as error:
            raise RuntimeError("PDF OCR requires pymupdf: pip install pymupdf") from error

        with tempfile.TemporaryDirectory(prefix="ocr-pages-") as directory:
            work_dir = Path(directory)
            image_paths = []
            with fitz.open(pdf_path) as document:
                for page_index, page in enumerate(document):
                    image_path = work_dir / f"page-{page_index + 1:04d}.png"
                    page.get_pixmap(dpi=self.pdf_dpi, alpha=False).save(image_path)
                    image_paths.append(image_path)
            pages = self._predict_paths(image_paths, work_dir)

        return {
            "page_count": len(pages),
            "markdown": "\n\n---\n\n".join(page["markdown"] for page in pages),
            "pages": pages,
        }


def task_prompt(task: str) -> str:
    """Validate and translate a model-neutral OCR task into a backend prompt."""
    try:
        return TASK_PROMPTS[task]
    except KeyError as error:
        supported = ", ".join(TASK_PROMPTS)
        raise ValueError(f"Unsupported OCR_TASK {task!r}; choose one of: {supported}") from error
