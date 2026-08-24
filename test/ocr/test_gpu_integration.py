"""Opt-in real-GPU OCR page-boundary and queue integration tests."""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import fitz
from PIL import Image


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SMOKE_IMAGE = REPOSITORY_ROOT / "mcp-tool-test/smoke-test/ocr_smoke_test.png"
CHINESE_IMAGE = REPOSITORY_ROOT / "mcp-tool-test/ocr/printed/zh/taipei_taxi_fare.jpg"
HANDWRITING_IMAGE = (
    REPOSITORY_ROOT
    / "mcp-tool-test/ocr/handwriting/generated/20260722/bilingual_notes.png"
)
SERVER_URL = os.environ.get("OCR_TEST_SERVER_URL", "http://127.0.0.1:8002").rstrip("/")


def _request_json(path: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"{SERVER_URL}{path}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _submit(path: Path) -> tuple[int, dict[str, Any]]:
    boundary = f"----ocr-reliability-{uuid.uuid4().hex}"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    request = urllib.request.Request(
        f"{SERVER_URL}/v1/ocr/submit",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _wait(job_id: str, *, timeout: float = 900) -> tuple[dict[str, Any], float, float]:
    started = time.monotonic()
    peak_allocated = 0.0
    peak_reserved = 0.0
    while time.monotonic() - started < timeout:
        status_code, status = _request_json(f"/v1/ocr/jobs/{job_id}")
        if status_code != 200:
            raise AssertionError(status)
        _, health = _request_json("/health")
        gpu = health.get("gpu_info", {})
        peak_allocated = max(peak_allocated, float(gpu.get("memory_allocated_gb", 0)))
        peak_reserved = max(peak_reserved, float(gpu.get("memory_reserved_gb", 0)))
        if status["status"] in {"completed", "failed"}:
            return status, peak_allocated, peak_reserved
        time.sleep(0.5)
    raise AssertionError(f"OCR job {job_id} did not finish within {timeout}s")


def _make_scanned_pdf(path: Path, pages: int) -> None:
    document = fitz.open()
    image_reference = 0
    for _ in range(pages):
        page = document.new_page(width=612, height=792)
        image_reference = page.insert_image(
            fitz.Rect(54, 210, 558, 582),
            filename=str(SMOKE_IMAGE),
            xref=image_reference,
        )
    document.save(path)
    document.close()


def _expected_page_chunks(total: int) -> list[list[int]]:
    return [list(range(start, min(start + 24, total + 1))) for start in range(1, total + 1, 24)]


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_OCR_GPU_INTEGRATION") == "1",
    "set MCP_TOOLS_OCR_GPU_INTEGRATION=1 with a running OCR server",
)
class OcrGpuIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        status_code, health = _request_json("/health")
        self.assertEqual(status_code, 200, health)
        self.assertEqual(health["device"], "cuda")

    def test_real_scanned_pdf_chunk_boundaries(self) -> None:
        configured = os.environ.get("OCR_GPU_PAGE_COUNTS", "1,23,24,25,48,49,96")
        page_counts = tuple(int(value) for value in configured.split(","))
        metrics: dict[str, Any] = {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pages in page_counts:
                with self.subTest(pages=pages):
                    source = root / f"scanned-{pages}.pdf"
                    _make_scanned_pdf(source, pages)
                    submitted_at = time.monotonic()
                    code, submitted = _submit(source)
                    self.assertEqual(code, 200, submitted)
                    self.assertEqual(submitted["total_pages"], pages)
                    self.assertEqual(
                        [artifact["source_pages"] for artifact in submitted["artifacts"]],
                        _expected_page_chunks(pages),
                    )
                    status, peak_allocated, peak_reserved = _wait(submitted["job_id"])
                    elapsed = time.monotonic() - submitted_at
                    self.assertEqual(status["status"], "completed", status)
                    self.assertEqual(status["progress"], {"current": pages, "total": pages})
                    result_code, result = _request_json(
                        f"/v1/ocr/jobs/{submitted['job_id']}/result"
                    )
                    self.assertEqual(result_code, 200, result)
                    self.assertEqual(result["page_count"], pages)
                    self.assertTrue(all(artifact["sha256"] for artifact in result["artifacts"]))
                    metrics[str(pages)] = {
                        "elapsed_seconds": round(elapsed, 3),
                        "peak_allocated_gib": peak_allocated,
                        "peak_reserved_gib": peak_reserved,
                    }
        print("OCR_PAGE_BOUNDARY_METRICS=" + json.dumps(metrics, sort_keys=True))

    def test_real_queue_capacity_burst_is_bounded_and_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lead_pdf = Path(directory) / "lead-25-pages.pdf"
            _make_scanned_pdf(lead_pdf, 25)
            code, lead = _submit(lead_pdf)
            self.assertEqual(code, 200, lead)

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                submissions = tuple(executor.map(lambda _: _submit(SMOKE_IMAGE), range(10)))

            accepted = [payload for code, payload in submissions if code == 200]
            rejected = [payload for code, payload in submissions if code == 429]
            self.assertGreaterEqual(len(accepted), 1)
            self.assertGreaterEqual(len(rejected), 1)
            self.assertEqual(len({item["job_id"] for item in accepted}), len(accepted))
            self.assertTrue(
                all("queue is full" in item.get("detail", "").lower() for item in rejected),
                rejected,
            )

            lead_status, _, _ = _wait(lead["job_id"])
            self.assertEqual(lead_status["status"], "completed", lead_status)
            for submission in accepted:
                status, _, _ = _wait(submission["job_id"])
                self.assertEqual(status["status"], "completed", status)

        print(
            "OCR_QUEUE_METRICS="
            + json.dumps({"accepted": len(accepted), "rejected_full": len(rejected)})
        )

    def test_real_image_variants_and_structured_failures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocr reliability 文档 ") as directory:
            root = Path(directory)
            unicode_image = root / "公式 image with spaces.png"
            rotated_image = root / "rotated-90.png"
            ultrawide_image = root / "ultrawide-16384x64.png"
            blank_image = root / "blank.png"
            corrupt_pdf = root / "corrupt.pdf"
            unsupported = root / "unsupported.txt"
            shutil.copyfile(SMOKE_IMAGE, unicode_image)
            with Image.open(SMOKE_IMAGE) as source:
                source.transpose(Image.Transpose.ROTATE_90).save(rotated_image)
                strip = source.convert("L").resize((400, 64))
            ultrawide = Image.new("L", (16384, 64), color=255)
            ultrawide.paste(strip, (7992, 0))
            ultrawide.save(ultrawide_image)
            Image.new("L", (1024, 1024), color=255).save(blank_image)
            corrupt_pdf.write_bytes(b"%PDF-1.7\nthis is not a valid PDF\n")
            unsupported.write_text("not an OCR input", encoding="utf-8")

            for source in (
                unicode_image,
                rotated_image,
                CHINESE_IMAGE,
                HANDWRITING_IMAGE,
            ):
                with self.subTest(source=source.name):
                    code, submitted = _submit(source)
                    self.assertEqual(code, 200, submitted)
                    status, _, _ = _wait(submitted["job_id"], timeout=300)
                    self.assertEqual(status["status"], "completed", status)
                    for artifact in status["artifacts"]:
                        artifact_path = Path(artifact["path"])
                        self.assertTrue(artifact["sha256"])
                        self.assertTrue(artifact_path.is_file())
                        self.assertTrue(artifact_path.read_text(encoding="utf-8").strip())

            ultrawide_code, ultrawide_submission = _submit(ultrawide_image)
            self.assertEqual(ultrawide_code, 200, ultrawide_submission)
            ultrawide_status, _, _ = _wait(ultrawide_submission["job_id"], timeout=300)
            self.assertEqual(ultrawide_status["status"], "failed", ultrawide_status)
            self.assertIn("aspect ratio", ultrawide_status["error"].lower())

            blank_code, blank_submission = _submit(blank_image)
            self.assertEqual(blank_code, 200, blank_submission)
            blank_status, _, _ = _wait(blank_submission["job_id"], timeout=300)
            self.assertIn(blank_status["status"], {"completed", "failed"})
            if blank_status["status"] == "failed":
                self.assertTrue(blank_status["error"])

            for invalid_source in (corrupt_pdf, unsupported):
                with self.subTest(invalid=invalid_source.name):
                    code, failure = _submit(invalid_source)
                    self.assertEqual(code, 400, failure)
                    self.assertTrue(failure.get("detail"), failure)


if __name__ == "__main__":
    unittest.main()
