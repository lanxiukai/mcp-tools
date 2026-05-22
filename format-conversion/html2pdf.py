#!/usr/bin/env python3
"""Convert an HTML file to PDF using WeasyPrint.

Usage:
    html2pdf.py <input.html> [output.pdf]

Dependencies (conda):
    conda install -c conda-forge weasyprint

Fonts (optional but recommended):
    ~/.local/share/fonts/NotoSansSC-Regular.ttf   — Chinese text
    ~/.local/share/fonts/NotoEmoji-Regular.ttf    — emoji rendering
    Missing → auto-fallback to system sans-serif with degraded emoji support.

Preserves the HTML's own styles. Emoji are wrapped in .emoji spans and
rendered with an isolated emoji font — never touching the text font stack.
"""

import logging
import sys
from pathlib import Path

from converter import convert_html_to_pdf


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(f"Usage: {sys.argv[0]} <input.html> [output.pdf]")
        print(__doc__)
        sys.exit(0)

    html_path = Path(sys.argv[1])
    if not html_path.exists():
        print(f"Error: file not found: {html_path}")
        sys.exit(1)

    pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else html_path.with_suffix('.pdf')

    # Enable logging to see font warnings from converter
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    try:
        convert_html_to_pdf(str(html_path), str(pdf_path))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
