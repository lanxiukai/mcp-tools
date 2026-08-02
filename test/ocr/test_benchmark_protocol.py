from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.pdf_staging import ChunkPlan, StagedChunk
from test.ocr.benchmark.protocol import HttpReply, OcrResult, RestFailure, ReturnedPage, result_from_reply
from test.ocr.benchmark.rest import _map_result_pages


class TestBenchmarkProtocol(TestCase):
    def test_result_parser_preserves_returned_page_indexes(self) -> None:
        # Given
        reply = HttpReply(
            status_code=200,
            payload={
                "page_count": 2,
                "pages": [
                    {"page_index": 0, "markdown": "first"},
                    {"page_index": 1, "markdown": "second"},
                ],
            },
            error=None,
        )

        # When
        result = result_from_reply(reply)

        # Then
        if isinstance(result, RestFailure):
            self.fail(result.message)
        self.assertEqual(tuple(page.page_index for page in result.pages), (0, 1))
        self.assertEqual(tuple(page.markdown for page in result.pages), ("first", "second"))

    def test_result_parser_rejects_page_missing_index(self) -> None:
        # Given
        reply = HttpReply(
            status_code=200,
            payload={"page_count": 1, "pages": [{"markdown": "first"}]},
            error=None,
        )

        # When
        result = result_from_reply(reply)

        # Then
        if not isinstance(result, RestFailure):
            self.fail("page without page_index parsed as an OCR result")
        self.assertEqual(result.message, "response field 'page_index' must be an integer")

    def test_mapping_rejects_nonsequential_returned_page_indexes(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            chunk = StagedChunk(
                plan=ChunkPlan(index=1, source_pages=(4, 9)),
                pdf_path=Path(directory) / "chunk.pdf",
            )
            invalid_results = (
                OcrResult(page_count=2, pages=(ReturnedPage(0, "first"), ReturnedPage(0, "second"))),
                OcrResult(page_count=2, pages=(ReturnedPage(0, "first"), ReturnedPage(2, "second"))),
                OcrResult(page_count=2, pages=(ReturnedPage(1, "second"), ReturnedPage(0, "first"))),
            )

            # When / Then
            for result in invalid_results:
                self.assertIsNone(_map_result_pages(chunk, result))
