#!/usr/bin/env python3
"""Convert a Markdown file to PDF using markdown-it-py + WeasyPrint (Chromium also available).

Usage:
    md2pdf.py <input.md> [output.pdf] [--theme print|sepia|one-dark-pro]

Dependencies:
    uv sync --project environments/mcp-local --locked

Fonts (optional but recommended):
    ~/.local/share/fonts/NotoSansSC-Regular.ttf   — Chinese text
    ~/.local/share/fonts/NotoEmoji-Regular.ttf    — emoji rendering
    Missing → auto-fallback to system sans-serif with degraded emoji support.
"""

import argparse
import logging
import sys
from pathlib import Path

from converter import convert_markdown_to_pdf
from link_cli import add_link_arguments, print_link_inspection, read_pdf_targets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Markdown to a styled PDF.",
    )
    parser.add_argument("input", help="Input Markdown file")
    parser.add_argument(
        "output", nargs="?",
        help="Output PDF file (default: input path with a .pdf suffix)",
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
    add_link_arguments(parser)
    args = parser.parse_args()

    md_path = Path(args.input)
    if not md_path.exists():
        print(f"Error: file not found: {md_path}")
        sys.exit(1)

    pdf_path = Path(args.output) if args.output else md_path.with_suffix('.pdf')

    # Enable logging to see font warnings from converter
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    try:
        targets = read_pdf_targets(args)
        if args.inspect_links:
            print_link_inspection(md_path, targets)
            return
        report = convert_markdown_to_pdf(
            str(md_path), str(pdf_path), theme=args.theme,
            pdf_targets=targets, link_policy=args.link_policy,
        )
        print(report)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
