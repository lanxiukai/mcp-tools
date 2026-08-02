from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.pdf_staging import ChunkPlan, StagedChunk
from test.ocr.benchmark.artifacts import RecordedOutcome, write_artifacts
from test.ocr.benchmark.cli import _settings_from_args, build_parser
from test.ocr.benchmark.plan import PageResult
from test.ocr.benchmark.rest import ChunkOutcome, GpuSample
from test.ocr.benchmark.runner import JobMetrics


class TestBenchmarkArtifactsAndCli(TestCase):
    def test_artifacts_persist_returned_staged_page_indexes(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outcome = ChunkOutcome(
                chunk=StagedChunk(ChunkPlan(index=1, source_pages=(3, 8)), root / "chunk.pdf"),
                status="completed",
                error=None,
                pages=(
                    PageResult(source_page=3, markdown="three", returned_page_index=0),
                    PageResult(source_page=8, markdown="eight", returned_page_index=1),
                ),
                metrics=JobMetrics(0.1, 0.2, 0.3, 0.4, 0.5, 1.5),
                gpu_samples=(GpuSample(0.1, None, None, None),),
            )

            # When
            paths = write_artifacts(root / "artifacts", root / "source.pdf", (3, 8), (RecordedOutcome(1, outcome),))
            record = json.loads(paths.jsonl.read_text(encoding="utf-8"))

            # Then
            self.assertEqual(record["returned_page_indexes"], [0, 1])
            self.assertTrue(paths.summary_json.is_file())
            self.assertTrue(paths.summary_markdown.is_file())

    def test_cli_help_imports_without_ocr_model_server(self) -> None:
        # Given / When
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy, sys; sys.argv = ['test.ocr.benchmark.cli', '--help']; "
                "\ntry: runpy.run_module('test.ocr.benchmark.cli', run_name='__main__') "
                "\nexcept SystemExit as error: "
                "\n    raise SystemExit(0 if error.code == 0 and 'ocr.ocr_server' not in sys.modules else 1)",
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )

        # Then
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("bounded client concurrency", completed.stdout)
        self.assertNotIn("ocr_server", completed.stderr)

    def test_cli_rejects_invalid_numeric_arguments(self) -> None:
        # Given
        parser = build_parser()
        with TemporaryDirectory() as directory:
            source_pdf = Path(directory) / "source.pdf"
            source_pdf.write_bytes(b"%PDF-test")
            invalid_options = (("--max-pages", "0"), ("--pages-per-job", "0"), ("--concurrency", "0"), ("--repetitions", "0"), ("--timeout", "0"), ("--sample-interval", "0"))

            # When / Then
            for option, value in invalid_options:
                with self.subTest(option=option):
                    args = parser.parse_args([str(source_pdf), option, value])
                    with self.assertRaises(SystemExit):
                        _settings_from_args(args, parser)
