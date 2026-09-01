#!/usr/bin/env python3
"""Convert an HTML file to PDF using Chromium (default) or WeasyPrint.

Usage:
    html2pdf.py <input.html> [output.pdf] [--theme print|sepia|one-dark-pro]

Dependencies:
    uv sync --project environments/mcp-local --locked

Fonts (optional but recommended):
    ~/.local/share/fonts/NotoSansSC-Regular.ttf   — Chinese text
    ~/.local/share/fonts/NotoEmoji-Regular.ttf    — emoji rendering
    Missing → auto-fallback to system sans-serif with degraded emoji support.

Preserves ordinary HTML styles. Recognized portable analytics reports receive
a scoped A4 print profile. Emoji are wrapped in .emoji spans and rendered with
an isolated emoji font — never touching the text font stack.
"""

import argparse
import logging
import sys
from pathlib import Path

from converter import convert_html_to_pdf


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert HTML to a print-aware PDF.",
    )
    parser.add_argument("input", help="Input HTML file")
    parser.add_argument(
        "output", nargs="?",
        help="Output PDF file (default: input path with a .pdf suffix)",
    )
    parser.add_argument(
        "--engine",
        choices=("chromium", "weasyprint"),
        default="chromium",
        help="Rendering backend (default: chromium)",
    )
    parser.add_argument(
        "--theme",
        choices=("print", "sepia", "one-dark-pro"),
        default="print",
        help=(
            "PDF colors: print (white), sepia (warm), "
            "or one-dark-pro (Night Flat)"
        ),
    )
    args = parser.parse_args()

    html_path = Path(args.input)
    if not html_path.exists():
        print(f"Error: file not found: {html_path}")
        sys.exit(1)

    pdf_path = Path(args.output) if args.output else html_path.with_suffix('.pdf')

    # Enable logging to see font warnings from converter
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    try:
        convert_html_to_pdf(
            str(html_path),
            str(pdf_path),
            engine=args.engine,
            theme=args.theme,
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
