#!/usr/bin/env python3
"""Focused regression tests for document format conversion helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FORMAT_CONVERSION_DIR = _REPO_ROOT / "format-conversion"
sys.path.insert(0, str(_FORMAT_CONVERSION_DIR))

import converter  # noqa: E402


class ResponsiveMathJaxSvgTests(unittest.TestCase):
    def test_equation_that_fits_is_unchanged(self) -> None:
        markup = (
            '<div class="mathjax-block">'
            '<svg style="vertical-align: -1ex; min-width: 80ex;" '
            'width="100%" height="3ex" data-mjx-viewBox="0 -1 80 3">'
            '<defs></defs><g id="equation"></g></svg></div>'
        )

        self.assertEqual(converter._make_mathjax_svg_responsive(markup), markup)

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
