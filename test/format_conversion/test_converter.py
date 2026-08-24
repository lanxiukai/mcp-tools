#!/usr/bin/env python3
"""Focused regression tests for document format conversion helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FORMAT_CONVERSION_DIR = _REPO_ROOT / "format-conversion"
sys.path.insert(0, str(_FORMAT_CONVERSION_DIR))

import converter  # noqa: E402
import format_mcp_server  # noqa: E402


class MarkdownCssTests(unittest.TestCase):
    def test_table_cells_wrap_long_content_to_preserve_print_scale(self) -> None:
        css = converter._build_css({
            "Noto Sans SC": None,
            "Noto Emoji": None,
        })

        self.assertIn("overflow-wrap: anywhere;", css)


class ResponsiveMathJaxSvgTests(unittest.TestCase):
    def test_equation_that_fits_is_unchanged(self) -> None:
        markup = (
            '<div class="mathjax-block">'
            '<svg style="vertical-align: -1ex; min-width: 80ex;" '
            'width="100%" height="3ex" data-mjx-viewBox="0 -1 80 3">'
            '<defs></defs><g id="equation"></g></svg></div>'
        )

        self.assertEqual(converter._make_mathjax_svg_responsive(markup), markup)


class SvgToPngTests(unittest.TestCase):
    SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">
<rect width="120" height="80" fill="#e53935"/>
</svg>"""

    def test_svg_renders_to_png_at_its_intrinsic_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vector.svg"
            output = Path(directory) / "vector.png"
            source.write_text(self.SVG, encoding="utf-8")

            dimensions = converter.convert_svg_to_png(str(source), str(output))

            self.assertEqual(dimensions, (120, 80))
            self.assertTrue(output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (120, 80))
                self.assertEqual(image.convert("RGB").getpixel((60, 40)), (229, 57, 53))

    def test_svg_can_scale_or_preserve_aspect_ratio_from_one_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "vector.svg"
            source.write_text(self.SVG, encoding="utf-8")

            scaled = root / "scaled.png"
            resized = root / "resized.png"
            self.assertEqual(
                converter.convert_svg_to_png(str(source), str(scaled), scale=2),
                (240, 160),
            )
            self.assertEqual(
                converter.convert_svg_to_png(
                    str(source), str(resized), output_width=60,
                ),
                (60, 40),
            )

            with Image.open(scaled) as image:
                self.assertEqual(image.size, (240, 160))
            with Image.open(resized) as image:
                self.assertEqual(image.size, (60, 40))

    def test_viewbox_dimensions_and_background_color_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "viewbox.svg"
            output = Path(directory) / "viewbox.png"
            source.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'viewBox="0 0 40 20"></svg>',
                encoding="utf-8",
            )

            dimensions = converter.convert_svg_to_png(
                str(source), str(output), background_color="#ffffff",
            )

            self.assertEqual(dimensions, (40, 20))
            with Image.open(output) as image:
                self.assertEqual(image.convert("RGB").getpixel((20, 10)), (255, 255, 255))

    def test_external_file_resource_is_not_fetched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "external.svg"
            output = root / "external.png"
            source.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink" width="20" height="20">'
                '<image width="20" height="20" '
                'xlink:href="file:///definitely/not-readable.png"/></svg>',
                encoding="utf-8",
            )

            with mock.patch(
                "cairosvg.parser.fetch",
                side_effect=AssertionError("external fetch attempted"),
            ) as fetch:
                converter.convert_svg_to_png(str(source), str(output))

            fetch.assert_not_called()
            with Image.open(output) as image:
                self.assertEqual(image.size, (20, 20))

    def test_renderer_receives_bytes_and_safe_resource_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vector.svg"
            output = Path(directory) / "vector.png"
            source.write_text(self.SVG, encoding="utf-8")

            with mock.patch.object(
                converter.cairosvg,
                "svg2png",
                wraps=converter.cairosvg.svg2png,
            ) as render:
                converter.convert_svg_to_png(str(source), str(output))

            kwargs = render.call_args.kwargs
            self.assertEqual(kwargs["bytestring"], self.SVG.encode("utf-8"))
            self.assertIs(kwargs["unsafe"], False)
            self.assertNotIn("url", kwargs)

    def test_mcp_handler_derives_output_path_and_returns_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mcp vector.svg"
            source.write_text(self.SVG, encoding="utf-8")

            result = format_mcp_server.svg_to_png(str(source), output_width=30)

            output = source.with_suffix(".png")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["output_path"], str(output))
            self.assertEqual((result["width"], result["height"]), (30, 20))
            self.assertEqual(result["external_resources"], "blocked")
            self.assertEqual(result["size_bytes"], output.stat().st_size)

    def test_overwide_equation_scales_without_changing_the_svg_viewbox(self) -> None:
        markup = (
            '<div class="mathjax-block">'
            '<svg style="vertical-align: -2ex; min-width: 116ex;" '
            'width="100%" height="6ex" data-mjx-viewBox="0 -1603 51268 2706">'
            '<defs></defs><g id="equation"></g></svg></div>'
        )

        result = converter._make_mathjax_svg_responsive(markup)

        self.assertNotIn("min-width", result)
        self.assertIn('width="100%"', result)
        self.assertIn('height="4.5517ex"', result)
        self.assertIn('vertical-align: -1.5172ex', result)
        self.assertIn('data-mjx-viewBox="0 -1603 51268 2706"', result)
        self.assertNotIn(' viewBox="', result)
        self.assertIn('<g transform="scale(0.758621)"><g id="equation">', result)

    def test_malformed_equation_is_not_partially_rewritten(self) -> None:
        markup = (
            '<svg style="vertical-align: -2ex; min-width: 116ex;" '
            'width="100%" height="6ex" data-mjx-viewBox="0 -1603 51268 2706">'
            '<g id="equation"></g></svg>'
        )

        self.assertEqual(converter._make_mathjax_svg_responsive(markup), markup)


if __name__ == "__main__":
    unittest.main()
