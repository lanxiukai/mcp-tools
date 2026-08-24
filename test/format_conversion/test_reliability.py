"""Reliability regressions for document conversion boundaries and failures."""

from __future__ import annotations

import concurrent.futures
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fitz


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FORMAT_DIRECTORY = REPOSITORY_ROOT / "format-conversion"
sys.path.insert(0, str(FORMAT_DIRECTORY))

import converter  # noqa: E402


class FormatConversionReliabilityTests(unittest.TestCase):
    def test_empty_markdown_renders_a_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "empty.md"
            output = Path(directory) / "empty.pdf"
            source.write_text("", encoding="utf-8")

            converter.convert_markdown_to_pdf(
                str(source),
                str(output),
                engine="weasyprint",
            )

            with fitz.open(output) as document:
                self.assertEqual(document.page_count, 1)

    def test_complex_unicode_markdown_renders_with_missing_fonts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Unicode 路径 with spaces"
            root.mkdir()
            source = root / "报告 😀.md"
            output = root / "报告 😀.pdf"
            source.write_text(
                "# 稳定性 😀\n\n"
                + "| 列 | 值 |\n|---|---|\n"
                + "\n".join(f"| {index} | {'x' * 200} |" for index in range(5))
                + "\n\n"
                + "一条很长的行" * 100,
                encoding="utf-8",
            )

            with mock.patch.object(
                converter,
                "_check_fonts",
                return_value={"Noto Sans SC": None, "Noto Emoji": None},
            ), mock.patch.object(
                converter,
                "_discover_mathjax_node_path",
                return_value=None,
            ):
                converter.convert_markdown_to_pdf(
                    str(source),
                    str(output),
                    engine="weasyprint",
                )

            self.assertGreater(output.stat().st_size, 1000)
            with fitz.open(output) as document:
                self.assertGreaterEqual(document.page_count, 1)

    @unittest.skipUnless(
        os.environ.get("MCP_TOOLS_LONG_TESTS") == "1",
        "set MCP_TOOLS_LONG_TESTS=1 for large document stress",
    )
    def test_large_table_and_very_long_line_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "large.md"
            output = Path(directory) / "large.pdf"
            source.write_text(
                "| Key | Value |\n|---|---|\n"
                + "\n".join(f"| {index} | {'x' * 2000} |" for index in range(20))
                + "\n\n"
                + "very-long-line" * 2000,
                encoding="utf-8",
            )

            converter.convert_markdown_to_pdf(
                str(source),
                str(output),
                engine="weasyprint",
            )

            with fitz.open(output) as document:
                self.assertGreaterEqual(document.page_count, 1)

    def test_empty_html_renders_a_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "empty.html"
            output = Path(directory) / "empty.pdf"
            source.write_text("", encoding="utf-8")

            converter.convert_html_to_pdf(
                str(source),
                str(output),
                engine="weasyprint",
            )

            with fitz.open(output) as document:
                self.assertEqual(document.page_count, 1)

    def test_many_page_pdf_text_extraction_preserves_page_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "many pages.pdf"
            with fitz.open() as document:
                for page_number in range(60):
                    page = document.new_page()
                    page.insert_text((36, 72), f"PAGE-{page_number:03d}")
                document.save(source)

            text = converter.convert_pdf_to_text(str(source))

            positions = [text.index(f"PAGE-{page_number:03d}") for page_number in range(60)]
            self.assertEqual(positions, sorted(positions))

    def test_image_only_pdf_returns_empty_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scanned.pdf"
            with fitz.open() as document:
                document.new_page()
                document.save(source)

            self.assertEqual(converter.convert_pdf_to_text(str(source)), "")

    def test_corrupt_and_encrypted_pdfs_fail_with_source_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "corrupt.pdf"
            encrypted = root / "encrypted.pdf"
            corrupt.write_bytes(b"%PDF-1.7\ninvalid\n")
            with fitz.open() as document:
                page = document.new_page()
                page.insert_text((36, 72), "secret")
                document.save(
                    encrypted,
                    encryption=fitz.PDF_ENCRYPT_AES_256,
                    owner_pw="owner",
                    user_pw="reader",
                )

            with self.assertRaises(Exception) as corrupt_error:
                converter.convert_pdf_to_text(str(corrupt))
            with self.assertRaises(Exception) as encrypted_error:
                converter.convert_pdf_to_text(str(encrypted))

            self.assertIn("corrupt.pdf", str(corrupt_error.exception))
            self.assertTrue(
                "encrypt" in str(encrypted_error.exception).lower()
                or "password" in str(encrypted_error.exception).lower(),
                encrypted_error.exception,
            )

    def test_invalid_input_paths_are_actionable(self) -> None:
        missing = "/definitely/missing/reliability-input"
        with self.assertRaisesRegex(FileNotFoundError, "Markdown file not found"):
            converter.convert_markdown_to_pdf(missing + ".md", "/tmp/unused.pdf")
        with self.assertRaisesRegex(FileNotFoundError, "HTML file not found"):
            converter.convert_html_to_pdf(missing + ".html", "/tmp/unused.pdf")
        with self.assertRaisesRegex(FileNotFoundError, "PDF file not found"):
            converter.convert_pdf_to_text(missing + ".pdf")
        with self.assertRaisesRegex(FileNotFoundError, "SVG file not found"):
            converter.convert_svg_to_png(missing + ".svg", "/tmp/unused.png")

    def test_svg_failure_preserves_previous_png_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "malformed.svg"
            output = root / "output.png"
            source.write_text("<svg><not-closed>", encoding="utf-8")
            output.write_bytes(b"previous-valid-output")

            with self.assertRaises(Exception):
                converter.convert_svg_to_png(str(source), str(output))

            self.assertEqual(output.read_bytes(), b"previous-valid-output")
            self.assertEqual(tuple(root.glob(".*.tmp.png")), ())

    def test_svg_rejects_unsafe_or_oversized_render_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unsafe.svg"
            output = root / "output.png"
            source.write_text(
                '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                "<text>&xxe;</text></svg>",
                encoding="utf-8",
            )

            with self.assertRaises(Exception):
                converter.convert_svg_to_png(str(source), str(output))
            self.assertFalse(output.exists())

            source.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'width="9000" height="10"></svg>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "8192"):
                converter.convert_svg_to_png(str(source), str(output))

            source.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'width="8000" height="5000"></svg>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "32,000,000"):
                converter.convert_svg_to_png(str(source), str(output))

            with mock.patch.object(converter, "MAX_SVG_INPUT_BYTES", 16):
                with self.assertRaisesRegex(ValueError, "16-byte"):
                    converter.convert_svg_to_png(str(source), str(output))

    def test_svg_parameter_validation_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.svg"
            source.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'viewBox="0 0 100 50"></svg>',
                encoding="utf-8",
            )
            output = Path(directory) / "output.png"

            with self.assertRaisesRegex(ValueError, "scale"):
                converter.convert_svg_to_png(
                    str(source), str(output), scale=float("nan"),
                )
            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                converter.convert_svg_to_png(
                    str(source), str(output), scale=2, output_width=100,
                )
            with self.assertRaisesRegex(ValueError, "PNG"):
                converter.convert_svg_to_png(str(source), str(output.with_suffix(".jpg")))

    def test_missing_mathjax_and_malformed_latex_degrade_to_plain_text(self) -> None:
        source = (
            "Inline $x^2 + y^2$ and malformed $\\frac{1}{$ plus "
            "$$" + " + ".join(f"x_{{{index}}}" for index in range(500)) + "$$"
        )
        with mock.patch.object(
            converter,
            "_discover_mathjax_node_path",
            return_value=None,
        ), mock.patch.object(converter.subprocess, "run") as run:
            rendered = converter._convert_math_to_mathjax_svg(source)

        self.assertEqual(rendered, source)
        run.assert_not_called()

    def test_repeated_and_parallel_weasyprint_conversions_leave_no_temporary_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "repeat.md"
            source.write_text("# Repeat\n\n稳定 😀\n", encoding="utf-8")

            for _ in range(10):
                converter.convert_markdown_to_pdf(
                    str(source),
                    str(root / "same-output.pdf"),
                    engine="weasyprint",
                )

            outputs = tuple(root / f"parallel-{index}.pdf" for index in range(4))
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                tuple(
                    executor.map(
                        lambda output: converter.convert_markdown_to_pdf(
                            str(source),
                            str(output),
                            engine="weasyprint",
                        ),
                        outputs,
                    )
                )

            for output in (root / "same-output.pdf", *outputs):
                with fitz.open(output) as document:
                    self.assertGreaterEqual(document.page_count, 1)
            self.assertEqual(tuple(root.glob(".*.tmp")), ())

    def test_output_permission_failure_preserves_previous_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.html"
            output = root / "output.pdf"
            source.write_text("<p>hello</p>", encoding="utf-8")
            output.write_bytes(b"previous-valid-output")

            with mock.patch.object(
                converter.tempfile,
                "mkstemp",
                side_effect=PermissionError("read-only output directory"),
            ):
                with self.assertRaisesRegex(PermissionError, "read-only"):
                    converter.convert_html_to_pdf(
                        str(source),
                        str(output),
                        engine="weasyprint",
                    )

            self.assertEqual(output.read_bytes(), b"previous-valid-output")

    def test_missing_chromium_dependency_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.html"
            source.write_text("<p>hello</p>", encoding="utf-8")

            with mock.patch.object(converter, "_check_playwright", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "playwright install chromium"):
                    converter.convert_html_to_pdf(
                        str(source),
                        str(Path(directory) / "output.pdf"),
                        engine="chromium",
                    )

    def test_markdown_chromium_temporary_html_is_removed_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.md"
            source.write_text("# heading", encoding="utf-8")
            temporary_html: Path | None = None

            def fail(source_path: str, *_args, **_kwargs) -> None:
                nonlocal temporary_html
                temporary_html = Path(source_path)
                self.assertTrue(temporary_html.is_file())
                raise RuntimeError("simulated Chromium crash")

            with mock.patch.object(converter, "convert_html_to_pdf", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "simulated Chromium crash"):
                    converter.convert_markdown_to_pdf(
                        str(source),
                        str(Path(directory) / "output.pdf"),
                        engine="chromium",
                    )

            self.assertIsNotNone(temporary_html)
            self.assertFalse(temporary_html.exists())

    def test_chromium_failure_does_not_replace_a_previous_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.html"
            output = root / "output.pdf"
            source.write_text("<p>hello</p>", encoding="utf-8")
            output.write_bytes(b"previous-valid-output")

            def fail(_source: Path, destination: Path, *_args, **_kwargs) -> None:
                destination.write_bytes(b"partial")
                raise RuntimeError("simulated Chromium crash")

            with mock.patch.object(
                converter,
                "_convert_html_to_pdf_chromium",
                side_effect=fail,
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated Chromium crash"):
                    converter.convert_html_to_pdf(
                        str(source),
                        str(output),
                        engine="chromium",
                    )

            self.assertEqual(output.read_bytes(), b"previous-valid-output")

    def test_weasyprint_failure_does_not_replace_a_previous_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.html"
            output = root / "output.pdf"
            source.write_text("<p>hello</p>", encoding="utf-8")
            output.write_bytes(b"previous-valid-output")

            def fail(_source: Path, destination: Path, *_args, **_kwargs) -> None:
                destination.write_bytes(b"partial")
                raise RuntimeError("simulated WeasyPrint crash")

            with mock.patch.object(
                converter,
                "_convert_html_to_pdf_weasyprint",
                side_effect=fail,
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated WeasyPrint crash"):
                    converter.convert_html_to_pdf(
                        str(source),
                        str(output),
                        engine="weasyprint",
                    )

            self.assertEqual(output.read_bytes(), b"previous-valid-output")


if __name__ == "__main__":
    unittest.main()
