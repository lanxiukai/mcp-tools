"""Document format conversion functions.

Provides three public functions for document format conversion:
- convert_markdown_to_pdf: Markdown → PDF (markdown-it-py + WeasyPrint)
- convert_html_to_pdf:     HTML → PDF (WeasyPrint, preserves original styles)
- convert_pdf_to_text:     PDF → plain text (PyMuPDF, born-digital only)
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

import fitz
from markdown_it import MarkdownIt
from weasyprint import HTML

logger = logging.getLogger(__name__)


# ── Module-level constants ──

_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF'
    '\U00002600-\U000027BF'
    '\U0000FE0F'
    '\U0000200D'
    ']'
)

_EMOJI_TEXT_MAP = {
    '📅': '[日历]', '🔔': '[铃]', '☀️': '[太阳]', '🏃': '[跑]',
    '📚': '[书]', '🍽️': '[餐]', '💻': '[电脑]', '🌙': '[月亮]',
    '📖': '[书]', '📵': '[关机]', '🛏️': '[床]', '🟢': '[绿]',
    '🟡': '[黄]', '🔴': '[红]', '🌜': '[月亮]',
    '⭐': '★', '✅': '✔', '❌': '✘',
    '💡': '●', '🎯': '◎', '👍': '☑',
    '🆓': 'free', '💰': '$',
}


# ── Font discovery ──

def _check_fonts() -> dict[str, Optional[str]]:
    """Check which fonts are available.

    Returns:
        dict mapping font name → file path (or None if missing).
        Keys: 'Noto Sans SC', 'Noto Emoji'.
    """
    home = os.path.expanduser('~')
    fonts = {
        'Noto Sans SC': os.path.join(home, '.local/share/fonts/NotoSansSC-Regular.ttf'),
        'Noto Emoji':   os.path.join(home, '.local/share/fonts/NotoEmoji-Regular.ttf'),
    }
    available: dict[str, Optional[str]] = {}
    for name, path in fonts.items():
        available[name] = path if os.path.isfile(path) else None
    return available


# ── CSS builders ──

def _build_css(fonts_available: dict[str, Optional[str]]) -> str:
    """Build CSS for Markdown→PDF conversion.

    Includes full styling: headers, tables, blockquotes, code blocks, etc.
    Emoji font isolated in .emoji spans; degrades gracefully if fonts missing.
    """
    font_rules = []
    body_stack = ['sans-serif']

    if fonts_available['Noto Sans SC']:
        font_rules.append(f"""@font-face {{
    font-family: 'Noto Sans SC';
    src: url('file://{fonts_available["Noto Sans SC"]}') format('truetype');
}}""")
        body_stack.insert(0, "'Noto Sans SC'")

    body_stack.insert(0, "'DejaVu Sans'")

    body_font = ', '.join(body_stack)

    emoji_stack = ["'Noto Sans SC'", 'sans-serif'] if fonts_available['Noto Sans SC'] else ['sans-serif']
    if fonts_available['Noto Emoji']:
        font_rules.append(f"""@font-face {{
    font-family: 'Noto Emoji';
    src: url('file://{fonts_available["Noto Emoji"]}') format('truetype');
}}""")
        emoji_stack.insert(0, "'Noto Emoji'")

    emoji_font = ', '.join(emoji_stack)

    return f"""
{"".join(font_rules)}

@page {{
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
    @bottom-center {{
        content: counter(page);
        font-family: {body_font};
        font-size: 9pt;
        color: #7a9eb1;
    }}
}}

body {{
    font-family: {body_font};
    font-size: 11pt;
    line-height: 1.7;
    color: #2d2d2d;
}}

.emoji {{
    font-family: {emoji_font};
}}

/* ── Headers ── */
h1 {{
    font-size: 20pt; font-weight: 700;
    margin-top: 8mm; margin-bottom: 4mm;
    padding-bottom: 2mm;
    border-bottom: 2.5px solid #2c6f8a;
    color: #1a4d60;
    page-break-before: always;
}}
h1:first-of-type {{ page-break-before: avoid; }}

h2 {{
    font-size: 16pt; font-weight: 700;
    margin-top: 6mm; margin-bottom: 3mm;
    padding-bottom: 1mm;
    border-bottom: 1.5px solid #5c9bb5;
    color: #1f5c72;
    page-break-after: avoid;
}}

h3 {{
    font-size: 13pt; font-weight: 700;
    margin-top: 4mm; margin-bottom: 2mm;
    color: #2c6f8a;
    page-break-after: avoid;
}}

h4 {{
    font-size: 11.5pt; font-weight: 700;
    margin-top: 3mm; margin-bottom: 1.5mm;
    color: #3d7d96;
    page-break-after: avoid;
}}

h5 {{
    font-size: 11pt; font-weight: 700;
    margin-top: 2mm; margin-bottom: 1mm;
    color: #4a8ba3;
    page-break-after: avoid;
}}

h6 {{
    font-size: 10.5pt; font-weight: 700;
    margin-top: 2mm; margin-bottom: 1mm;
    color: #5c9bb5;
    page-break-after: avoid;
}}

p {{ margin: 1.5mm 0; text-align: justify; }}
strong {{ color: #1a3d4d; }}
a {{ color: #2c6f8a; text-decoration: none; }}

blockquote {{
    margin: 2mm 0 2mm 5mm; padding: 3mm 5mm;
    border-left: 3.5px solid #c47f2c;
    background: #fdf6ed;
    font-size: 10pt; color: #6b4e2a;
    page-break-inside: avoid;
}}

hr {{ border: none; border-top: 1px solid #c4d8e2; margin: 4mm 0; }}

code {{
    font-family: 'DejaVu Sans Mono', monospace;
    font-size: 9.5pt; background: #eaf1f5;
    padding: 1px 3px; border-radius: 2px; color: #2c6f8a;
}}
pre {{
    background: #eef3f7; border: 1px solid #c4d8e2;
    border-radius: 3px; padding: 4mm;
    font-size: 9pt; line-height: 1.4;
    overflow-x: auto; page-break-inside: avoid;
}}
pre code {{ background: none; padding: 0; color: #333; }}

table {{
    width: 100%; border-collapse: collapse;
    margin: 3mm 0; font-size: 10pt;
    page-break-inside: avoid;
}}
th, td {{
    border: 1px solid #b8cfdb;
    padding: 2mm 3mm; text-align: left; vertical-align: top;
}}
th {{ background: #2c6f8a; color: #fff; font-weight: 700; }}
tr:nth-child(even) td {{ background: #f2f7fa; }}

ul, ol {{ margin: 1.5mm 0; padding-left: 6mm; }}
li {{ margin: 1mm 0; }}
img {{ max-width: 100%; height: auto; }}

.star {{ color: #c47f2c; font-weight: bold; }}
"""


def _build_injected_css(fonts_available: dict[str, Optional[str]]) -> str:
    """Build CSS to inject into HTML→PDF conversion.

    Adds font @font-face rules, .emoji span styling, and page-number footer.
    Returns HTML snippet intended to replace '</head>'.
    """
    rules = []

    if fonts_available['Noto Sans SC']:
        rules.append(f"""@font-face {{
    font-family: 'Noto Sans SC';
    src: url('file://{fonts_available["Noto Sans SC"]}') format('truetype');
}}""")

    if fonts_available['Noto Emoji']:
        rules.append(f"""@font-face {{
    font-family: 'Noto Emoji';
    src: url('file://{fonts_available["Noto Emoji"]}') format('truetype');
}}""")

    emoji_stack: list[str] = []
    if fonts_available['Noto Emoji']:
        emoji_stack.append("'Noto Emoji'")
    if fonts_available['Noto Sans SC']:
        emoji_stack.append("'Noto Sans SC'")
    emoji_stack.append('sans-serif')

    page_font = "'Noto Sans SC', sans-serif" if fonts_available['Noto Sans SC'] else 'sans-serif'

    return f"""<style>
{"".join(rules)}

.emoji {{
    font-family: {', '.join(emoji_stack)};
}}

@page {{
    @bottom-center {{
        content: counter(page);
        font-family: {page_font};
        font-size: 8pt;
        color: #94a3b8;
    }}
}}
</style>
</head>"""


# ── Emoji / body helpers ──

def _process_body(body_html: str, has_emoji_font: bool) -> str:
    """Post-process HTML body for Markdown→PDF.

    - Colorize ★ stars with .star CSS class.
    - Wrap remaining emojis in .emoji spans if font available.
    """
    body_html = re.sub(r'★+', lambda m: f'<span class="star">{m.group()}</span>', body_html)

    if has_emoji_font:
        body_html = _EMOJI_RE.sub(lambda m: f'<span class="emoji">{m.group()}</span>', body_html)
    # else: emoji→text already applied before markdown parsing

    return body_html


def _process_emoji(html_text: str, has_emoji_font: bool) -> str:
    """Process emoji in raw HTML for HTML→PDF conversion.

    - Wrap emojis in .emoji spans if font available.
    - Replace with text equivalents if font missing.
    """
    if has_emoji_font:
        return _EMOJI_RE.sub(lambda m: f'<span class="emoji">{m.group()}</span>', html_text)
    else:
        for emoji, text in _EMOJI_TEXT_MAP.items():
            html_text = html_text.replace(emoji, text)
        return html_text


# ── Public API ──

def convert_markdown_to_pdf(source_path: str, output_path: str) -> None:
    """Convert a Markdown file to a styled PDF.

    Pipeline: markdown-it-py → HTML → WeasyPrint → PDF.
    Includes Chinese fonts, table styling, code blocks, blockquotes,
    page numbers, and emoji handling.

    Args:
        source_path: Absolute path to the .md file.
        output_path: Absolute path for the output .pdf file.

    Raises:
        FileNotFoundError: If source_path does not exist.
    """
    md_path = Path(source_path)
    if not md_path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {source_path}")

    out_path = Path(output_path)

    # Font check (warn via logger, not stdout)
    fonts = _check_fonts()
    missing = [n for n, p in fonts.items() if p is None]
    if missing:
        logger.warning("Missing font(s): %s. Using system fallback.", ', '.join(missing))
        if 'Noto Emoji' in missing:
            logger.info("Emoji will be replaced with text equivalents.")
    else:
        logger.info("All fonts found (Noto Sans SC + Noto Emoji)")

    # Read & preprocess markdown
    text = md_path.read_text(encoding='utf-8')

    # Emoji→text fallback applied early (before markdown parsing)
    # to avoid emoji being escaped by markdown-it.
    for emoji_char, replacement in _EMOJI_TEXT_MAP.items():
        text = text.replace(emoji_char, replacement)

    # Parse markdown → HTML body
    md = MarkdownIt('commonmark', {'breaks': True, 'html': True})
    md.enable(['table', 'strikethrough'])
    body = md.render(text)

    # Post-process (star color + emoji wrapping)
    body = _process_body(body, fonts['Noto Emoji'] is not None)

    # Assemble full HTML + CSS → PDF
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{_build_css(fonts)}</style>
</head>
<body>
{body}
</body>
</html>"""

    logger.info("Converting: %s → %s", md_path, out_path)
    HTML(string=html).write_pdf(str(out_path))
    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)


def convert_html_to_pdf(source_path: str, output_path: str) -> None:
    """Convert an HTML file to PDF, preserving original styles.

    Only injects: emoji font and page-number footer.  All existing
    styles (colors, gradients, cards, @page rules) are preserved.

    Args:
        source_path: Absolute path to the .html file.
        output_path: Absolute path for the output .pdf file.

    Raises:
        FileNotFoundError: If source_path does not exist.
    """
    html_path = Path(source_path)
    if not html_path.is_file():
        raise FileNotFoundError(f"HTML file not found: {source_path}")

    out_path = Path(output_path)

    # Font check
    fonts = _check_fonts()
    missing = [n for n, p in fonts.items() if p is None]
    if missing:
        logger.warning("Missing font(s): %s. Using system fallback.", ', '.join(missing))
        if 'Noto Emoji' in missing:
            logger.info("Emoji will be replaced with text equivalents.")
    else:
        logger.info("All fonts found (Noto Sans SC + Noto Emoji)")

    html_text = html_path.read_text(encoding='utf-8')

    # Process emoji (wrap or replace)
    html_text = _process_emoji(html_text, fonts['Noto Emoji'] is not None)

    # Inject font CSS + page numbers before </head>
    inject = _build_injected_css(fonts)
    if '</head>' in html_text:
        html_text = html_text.replace('</head>', inject, 1)
    else:
        html_text = inject + html_text

    logger.info("Converting: %s → %s", html_path, out_path)
    HTML(string=html_text, base_url=str(html_path.parent)).write_pdf(str(out_path))
    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)


def convert_pdf_to_text(source_path: str) -> str:
    """Extract plain text from a born-digital PDF using PyMuPDF.

    Only works with born-digital PDFs (text that can be selected/copied).
    Scanned-image PDFs will return an empty string; use the ``glm_ocr``
    tool for those.

    Args:
        source_path: Absolute path to the .pdf file.

    Returns:
        Extracted text as a single string (pages joined with newlines).

    Raises:
        FileNotFoundError: If source_path does not exist.
    """
    pdf_path = Path(source_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {source_path}")

    logger.info("Extracting text from: %s", source_path)
    doc = fitz.open(source_path)
    try:
        pages_text: list[str] = []
        for page in doc:
            text = page.get_text()
            pages_text.append(text)
    finally:
        doc.close()

    result = '\n'.join(pages_text)
    logger.info("Extracted %d chars from %d pages", len(result), len(pages_text))
    return result
