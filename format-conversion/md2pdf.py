#!/usr/bin/env python3
"""Convert a Markdown file to PDF using markdown-it-py + WeasyPrint.

Usage:
    md2pdf.py <input.md> [output.pdf]

Dependencies (conda):
    conda install -c conda-forge weasyprint markdown-it-py

Fonts (optional but recommended):
    ~/.local/share/fonts/NotoSansSC-Regular.ttf   — Chinese text
    ~/.local/share/fonts/NotoEmoji-Regular.ttf    — emoji rendering
    Missing → auto-fallback to system sans-serif with degraded emoji support.
"""

import logging
import sys
from pathlib import Path

from converter import convert_markdown_to_pdf


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(f"Usage: {sys.argv[0]} <input.md> [output.pdf]")
        print(__doc__)
        sys.exit(0)

    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print(f"Error: file not found: {md_path}")
        sys.exit(1)

    pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else md_path.with_suffix('.pdf')

    # Enable logging to see font warnings from converter
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    try:
        convert_markdown_to_pdf(str(md_path), str(pdf_path))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
