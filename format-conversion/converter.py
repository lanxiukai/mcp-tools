"""Document format conversion functions.

Provides four public functions for document and image format conversion:
- convert_markdown_to_pdf: Markdown → PDF (markdown-it-py + WeasyPrint)
- convert_html_to_pdf:     HTML → PDF (WeasyPrint or Chromium, selectable page theme)
- convert_pdf_to_text:     PDF → plain text (PyMuPDF, born-digital only)
- convert_svg_to_png:      SVG → PNG (CairoSVG, local resources disabled)
"""

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Iterator, Literal, Optional

import cairosvg
import fitz
from defusedxml import ElementTree as DefusedElementTree
from markdown_it import MarkdownIt
from PIL import Image
from weasyprint import HTML

logger = logging.getLogger(__name__)

# ── Playwright availability check (lazy, only for Chromium engine) ──

_PLAYWRIGHT_AVAILABLE: bool | None = None  # tri-state: None=unchecked


def _check_playwright() -> bool:
    """Check if Playwright + Chromium are installed.  Cached result."""
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is not None:
        return _PLAYWRIGHT_AVAILABLE
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        _PLAYWRIGHT_AVAILABLE = True
    except ImportError:
        _PLAYWRIGHT_AVAILABLE = False
    return _PLAYWRIGHT_AVAILABLE


# ── Module-level constants ──

# Match compound emoji sequences as single units so that ZWJ joining,
# skin-tone modifiers, and variation selectors stay in the same <span>.
# This prevents splitting 👨‍💻 or 👍🏻 across separate HTML elements.
_EMOJI_SEQUENCE_RE = re.compile(
    # (1) Compound emoji: base + (ZWJ base)* + optional skin tone + optional VS16
    '(?:[\U0001F300-\U0001FAFF\U00002600-\U000027BF]'
    '(?:\u200D[\U0001F300-\U0001FAFF\U00002600-\U000027BF])*'
    '[\U0001F3FB-\U0001F3FF]?'
    '\uFE0F?)'
    '|'
    # (2) Regional indicator flags (always pairs)
    '(?:[\U0001F1E6-\U0001F1FF]{2})'
    '|'
    # (3) Keycap sequences: digit/#/* + optional VS16 + U+20E3
    '(?:[0-9#*]\uFE0F?\u20E3)'
    '|'
    # (4) Lone modifiers/connectors (fallback for edge cases)
    '(?:\u200D|[\U0001F3FB-\U0001F3FF]|\uFE0F)'
)

_EMOJI_TEXT_MAP = {
    '📅': '[Calendar]', '🔔': '[Bell]', '☀️': '[Sun]', '🏃': '[Run]',
    '📚': '[Book]', '🍽️': '[Meal]', '💻': '[Laptop]', '🌙': '[Moon]',
    '📖': '[Book]', '📵': '[No Phone]', '🛏️': '[Bed]', '🟢': '[Green]',
    '🟡': '[Yellow]', '🔴': '[Red]', '🌜': '[Moon]',
    '⭐': '★', '✅': '✔', '❌': '✘',
    '💡': '●', '🎯': '◎', '👍': '☑',
    '🆓': 'free', '💰': '$',
}

# Regex to match LaTeX math: $$...$$ for display, $...$ for inline.
# Must protect code blocks BEFORE applying these.
_MATH_DISPLAY_RE = re.compile(r'\$\$\s*(.+?)\s*\$\$', re.DOTALL)
_MATH_INLINE_RE = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', re.DOTALL)

_SVG_LENGTH_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
    r"\s*(px|pt|pc|mm|cm|in|em|ex|ch|%)?\s*$",
    re.IGNORECASE,
)
_SVG_LENGTH_TO_PIXELS = {
    "": 1.0,
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "mm": 96.0 / 25.4,
    "cm": 96.0 / 2.54,
    "in": 96.0,
    "em": 16.0,
    "ex": 8.0,
    "ch": 8.0,
}

MAX_SVG_INPUT_BYTES = 16 * 1024 * 1024
MAX_PNG_DIMENSION = 8192
MAX_PNG_PIXELS = 32_000_000
MAX_SVG_SCALE = 16.0
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _is_likely_math(content: str, *, is_display: bool = False) -> bool:
    """Heuristic to reject $...$ / $$...$$ matches unlikely to be LaTeX math.

    Args:
        content:    The captured text between ``$...$`` or ``$$...$$``.
        is_display: True for display math (``$$...$$``), False for inline.

    For inline math (``$...$``):
      - Rejects content containing newlines — the bug pattern where currency
        ``$`` signs span table rows/sections across lines.
      - Rejects content exceeding 300 characters.

    For display math (``$$...$$``):
      - Allows newlines (multi-line formulas like matrices, cases are common).
      - Rejects content exceeding 2000 characters (guard against giant matches).
    """
    if '\n' in content and not is_display:
        return False
    max_len = 2000 if is_display else 300
    if len(content) > max_len:
        return False
    return True


# ── Font discovery ──

def _fontconfig_path(family: str) -> Optional[str]:
    """Return a font file resolved by fontconfig for an exact family."""
    fc_match = shutil.which('fc-match')
    if fc_match is None:
        return None

    try:
        result = subprocess.run(
            [fc_match, family, '-f', '%{family}\n%{file}\n'],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    lines = result.stdout.splitlines()
    if len(lines) < 2 or family.casefold() not in lines[0].casefold():
        return None
    path = lines[1].strip()
    return path if os.path.isfile(path) else None


def _check_fonts() -> dict[str, Optional[str]]:
    """Check which fonts are available.

    Returns:
        dict mapping font name → file path (or None if missing).
        Keys: 'Noto Sans SC', 'Noto Emoji'.
    """
    home = Path.home()
    local_cjk = home / '.local/share/fonts/NotoSansSC-Regular.ttf'
    local_emoji = home / '.local/share/fonts/NotoEmoji-Regular.ttf'
    return {
        'Noto Sans SC': (
            str(local_cjk) if local_cjk.is_file()
            else _fontconfig_path('Noto Sans CJK SC')
        ),
        'Noto Emoji': (
            str(local_emoji) if local_emoji.is_file()
            else _fontconfig_path('Noto Color Emoji')
        ),
    }


def _font_face_rule(alias: str, path: str) -> str:
    """Build a font-face rule for standalone fonts, not font collections."""
    font_path = Path(path)
    if font_path.suffix.casefold() in {'.ttc', '.otc'}:
        return ''
    return f"""@font-face {{
    font-family: '{alias}';
    src: url('{font_path.resolve().as_uri()}');
}}"""


# ── CSS builders ──

MarkdownPdfTheme = Literal["print", "sepia", "one-dark-pro"]
HtmlPdfTheme = Literal["print", "sepia", "one-dark-pro"]

_MARKDOWN_THEME_PALETTES: dict[str, dict[str, str]] = {
    "print": {
        "page_bg": "#ffffff",
        "text": "#2d2d2d",
        "page_number": "#7a9eb1",
        "heading_1": "#1a4d60",
        "heading_2": "#1f5c72",
        "heading_3": "#2b6e89",
        "heading_4": "#3d7d96",
        "heading_5": "#4a8ba3",
        "heading_6": "#5b9ab5",
        "heading_border_1": "#2c6f8a",
        "heading_border_2": "#5b9ab5",
        "strong": "#1a3d4d",
        "link": "#2c6f8a",
        "quote_border": "#c47f2c",
        "quote_bg": "#fdf6ed",
        "quote_text": "#6b4e2a",
        "rule": "#c4d8e2",
        "code_bg": "#eaf1f5",
        "code_text": "#2c6f8a",
        "pre_bg": "#eef3f7",
        "pre_border": "#c4d8e2",
        "pre_text": "#333333",
        "table_border": "#b8cfdb",
        "table_header_bg": "#2c6f8a",
        "table_header_text": "#ffffff",
        "table_stripe": "#f2f7fa",
        "checkbox_unchecked": "#7a9eb1",
        "checkbox_checked": "#2c6f8a",
        "accent": "#c47f2c",
        "error_text": "#8b3a3a",
        "error_bg": "#fdf2f2",
        "error_border": "#e0b4b4",
    },
    "sepia": {
        "page_bg": "#f6f0df",
        "text": "#433d33",
        "page_number": "#9a8768",
        "heading_1": "#5e4930",
        "heading_2": "#6a5235",
        "heading_3": "#755b3b",
        "heading_4": "#806645",
        "heading_5": "#8a704e",
        "heading_6": "#947a58",
        "heading_border_1": "#8b6f47",
        "heading_border_2": "#b39a73",
        "strong": "#4e3b27",
        "link": "#7b633f",
        "quote_border": "#b47b35",
        "quote_bg": "#eee3cc",
        "quote_text": "#674b2c",
        "rule": "#cfc0a2",
        "code_bg": "#e9dfca",
        "code_text": "#725633",
        "pre_bg": "#ece3d1",
        "pre_border": "#cbb99a",
        "pre_text": "#443c31",
        "table_border": "#c7b594",
        "table_header_bg": "#806642",
        "table_header_text": "#fffaf0",
        "table_stripe": "#efe6d3",
        "checkbox_unchecked": "#9a8768",
        "checkbox_checked": "#806642",
        "accent": "#b47b35",
        "error_text": "#8f4438",
        "error_bg": "#f1ded4",
        "error_border": "#c99786",
    },
    "one-dark-pro": {
        "page_bg": "#16191d",
        "text": "#abb2bf",
        "page_number": "#667187",
        "heading_1": "#61afef",
        "heading_2": "#61afef",
        "heading_3": "#56b6c2",
        "heading_4": "#c678dd",
        "heading_5": "#e5c07b",
        "heading_6": "#98c379",
        "heading_border_1": "#61afef",
        "heading_border_2": "#3e4452",
        "strong": "#e5c07b",
        "link": "#61afef",
        "quote_border": "#4b5362",
        "quote_bg": "#2e3440",
        "quote_text": "#abb2bf",
        "rule": "#3e4452",
        "code_bg": "#1e2227",
        "code_text": "#d19a66",
        "pre_bg": "#1e2227",
        "pre_border": "#181a1f",
        "pre_text": "#abb2bf",
        "table_border": "#3e4452",
        "table_header_bg": "#23272e",
        "table_header_text": "#d7dae0",
        "table_stripe": "#1e2227",
        "checkbox_unchecked": "#667187",
        "checkbox_checked": "#98c379",
        "accent": "#e5c07b",
        "error_text": "#e06c75",
        "error_bg": "#23272e",
        "error_border": "#c24038",
    },
}

_HTML_THEME_PALETTES: dict[str, dict[str, str]] = {
    "print": {
        "scheme": "light",
        "page_bg": "#ffffff",
        "surface": "#ffffff",
        "soft": "#f4f7fb",
        "text": "#172033",
        "muted": "#526174",
        "meta": "#7b8796",
        "border": "#d8e2ee",
        "cell_border": "#e8eef5",
        "accent": "#2563eb",
        "accent_soft": "#eff6ff",
        "heading": "#102a43",
        "heading_secondary": "#16324f",
        "heading_tertiary": "#244967",
        "table_header": "#eaf1f8",
        "table_stripe": "#f8fafc",
        "chart_primary": "#2563eb",
        "chart_secondary": "#d97706",
        "chart_grid": "#e2e8f0",
        "page_number": "#94a3b8",
    },
    "sepia": {
        "scheme": "light",
        "page_bg": "#f6f0df",
        "surface": "#fbf6e8",
        "soft": "#eee3cc",
        "text": "#433d33",
        "muted": "#6f6250",
        "meta": "#806f58",
        "border": "#cbb99a",
        "cell_border": "#dccbad",
        "accent": "#9a672b",
        "accent_soft": "#efe3ca",
        "heading": "#4e3b27",
        "heading_secondary": "#5e4930",
        "heading_tertiary": "#755b3b",
        "table_header": "#e6d8bd",
        "table_stripe": "#f1e7d4",
        "chart_primary": "#8b5e34",
        "chart_secondary": "#b45309",
        "chart_grid": "#d8c8aa",
        "page_number": "#9a8768",
    },
    "one-dark-pro": {
        "scheme": "dark",
        "page_bg": "#16191d",
        "surface": "#1e2227",
        "soft": "#23272e",
        "text": "#d7dae0",
        "muted": "#9aa4b2",
        "meta": "#7f8a9a",
        "border": "#3e4452",
        "cell_border": "#343a45",
        "accent": "#61afef",
        "accent_soft": "#202d3a",
        "heading": "#e5c07b",
        "heading_secondary": "#61afef",
        "heading_tertiary": "#56b6c2",
        "table_header": "#2b3038",
        "table_stripe": "#1b1f24",
        "chart_primary": "#61afef",
        "chart_secondary": "#e5c07b",
        "chart_grid": "#3e4452",
        "page_number": "#667187",
    },
}


def _build_css(
    fonts_available: dict[str, Optional[str]],
    theme: MarkdownPdfTheme = "print",
) -> str:
    """Build CSS for Markdown→PDF conversion.

    Includes full styling: headers, tables, blockquotes, code blocks, etc.
    Emoji font isolated in .emoji spans; degrades gracefully if fonts missing.
    """
    if theme not in _MARKDOWN_THEME_PALETTES:
        choices = ", ".join(_MARKDOWN_THEME_PALETTES)
        raise ValueError(f"Unknown Markdown PDF theme: {theme!r}. Use one of: {choices}.")

    palette = _MARKDOWN_THEME_PALETTES[theme]
    font_rules = []
    body_stack: list[str] = []

    if fonts_available['Noto Sans SC']:
        rule = _font_face_rule('Noto Sans SC', fonts_available['Noto Sans SC'])
        if rule:
            font_rules.append(rule)
        body_stack.extend(["'Noto Sans SC'", "'Noto Sans CJK SC'"])

    body_stack.extend(["'DejaVu Sans'", 'sans-serif'])

    body_font = ', '.join(body_stack)

    emoji_stack = ["'Noto Sans SC'", 'sans-serif'] if fonts_available['Noto Sans SC'] else ['sans-serif']
    if fonts_available['Noto Emoji']:
        rule = _font_face_rule('Noto Emoji', fonts_available['Noto Emoji'])
        if rule:
            font_rules.append(rule)
        emoji_stack.insert(0, "'Noto Emoji'")
        emoji_stack.insert(1, "'Noto Color Emoji'")

    emoji_font = ', '.join(emoji_stack)

    return f"""
{"".join(font_rules)}

@page {{
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
    background: {palette['page_bg']};
    @bottom-center {{
        content: counter(page);
        font-family: {body_font};
        font-size: 9pt;
        color: {palette['page_number']};
    }}
}}

html {{ background: {palette['page_bg']}; }}

body {{
    font-family: {body_font};
    font-size: 10pt;
    line-height: 1.7;
    color: {palette['text']};
    background: {palette['page_bg']};
}}

.emoji {{
    font-family: {emoji_font};
}}

  /* ── Headers ── */
h1 {{
    font-size: 20pt; font-weight: 700;
    margin-top: 8mm; margin-bottom: 4mm;
    padding-bottom: 2mm;
    border-bottom: 2.5px solid {palette['heading_border_1']};
    color: {palette['heading_1']};
}}

h2 {{
    font-size: 16pt; font-weight: 700;
    margin-top: 6mm; margin-bottom: 3mm;
    padding-bottom: 1mm;
    border-bottom: 1.5px solid {palette['heading_border_2']};
    color: {palette['heading_2']};
    page-break-after: avoid;
}}

h3 {{
    font-size: 13pt; font-weight: 700;
    margin-top: 4mm; margin-bottom: 2mm;
    color: {palette['heading_3']};
    page-break-after: avoid;
}}

h4 {{
    font-size: 11.5pt; font-weight: 700;
    margin-top: 3mm; margin-bottom: 1.5mm;
    color: {palette['heading_4']};
    page-break-after: avoid;
}}

h5 {{
    font-size: 11pt; font-weight: 700;
    margin-top: 2mm; margin-bottom: 1mm;
    color: {palette['heading_5']};
    page-break-after: avoid;
}}

h6 {{
    font-size: 10.5pt; font-weight: 700;
    margin-top: 2mm; margin-bottom: 1mm;
    color: {palette['heading_6']};
    page-break-after: avoid;
}}

p {{ margin: 1.5mm 0; text-align: justify; }}
strong {{ color: {palette['strong']}; }}
a {{ color: {palette['link']}; text-decoration: none; }}

blockquote {{
    margin: 2mm 0 2mm 5mm; padding: 3mm 5mm;
    border-left: 3.5px solid {palette['quote_border']};
    background: {palette['quote_bg']};
    font-size: 10pt; color: {palette['quote_text']};
    page-break-inside: avoid;
}}

hr {{ border: none; border-top: 1px solid {palette['rule']}; margin: 4mm 0; }}

code {{
    font-family: 'DejaVu Sans Mono', monospace;
    font-size: 9.5pt; background: {palette['code_bg']};
    padding: 1px 3px; border-radius: 2px; color: {palette['code_text']};
}}
pre {{
    background: {palette['pre_bg']}; border: 1px solid {palette['pre_border']};
    border-radius: 3px; padding: 4mm;
    font-size: 9pt; line-height: 1.4;
    overflow-x: auto; page-break-inside: avoid;
}}
pre code {{ background: none; padding: 0; color: {palette['pre_text']}; }}

table {{
    width: 100%; border-collapse: collapse;
    margin: 3mm 0; font-size: 10pt;
}}
th, td {{
    border: 1px solid {palette['table_border']};
    padding: 2mm 3mm; text-align: left; vertical-align: top;
    overflow-wrap: anywhere;
}}
th {{ background: {palette['table_header_bg']}; color: {palette['table_header_text']}; font-weight: 700; }}
tr {{ page-break-inside: avoid; }}
tr:nth-child(even) td {{ background: {palette['table_stripe']}; }}

ul, ol {{ margin: 1.5mm 0; padding-left: 6mm; }}
li {{ margin: 1mm 0; }}
img {{ max-width: 100%; height: auto; }}

/* ── Task checkboxes ── */
.task-checkbox {{
    display: inline-block;
    margin-right: 0.35em;
    font-size: 1.05em;
    line-height: 1;
}}
.task-checkbox.unchecked {{
    color: {palette['checkbox_unchecked']};
}}
.task-checkbox.checked {{
    color: {palette['checkbox_checked']};
    font-weight: bold;
}}

.star {{ color: {palette['accent']}; font-weight: bold; }}

/* ── MathJax error fallback ── */
.math-error {{
    font-family: 'DejaVu Sans Mono', monospace;
    font-size: 9pt;
    color: {palette['error_text']};
}}
pre.math-error {{
    background: {palette['error_bg']}; border: 1px solid {palette['error_border']};
    border-radius: 3px; padding: 3mm 4mm;
    line-height: 1.4;
    overflow-x: auto; page-break-inside: avoid;
    margin: 2mm 0;
}}
"""


def _build_font_face_css(fonts_available: dict[str, Optional[str]]) -> str:
    """Build @font-face CSS rules for Noto Sans SC and Noto Emoji."""
    rules = []
    if fonts_available['Noto Sans SC']:
        rule = _font_face_rule('Noto Sans SC', fonts_available['Noto Sans SC'])
        if rule:
            rules.append(rule)
    if fonts_available['Noto Emoji']:
        rule = _font_face_rule('Noto Emoji', fonts_available['Noto Emoji'])
        if rule:
            rules.append(rule)
    return "\n".join(rules)


def _build_emoji_css(fonts_available: dict[str, Optional[str]]) -> str:
    """Build .emoji span font-family CSS."""
    stack: list[str] = []
    if fonts_available['Noto Emoji']:
        stack.append("'Noto Emoji'")
        stack.append("'Noto Color Emoji'")
    if fonts_available['Noto Sans SC']:
        stack.append("'Noto Sans SC'")
        stack.append("'Noto Sans CJK SC'")
    stack.append('sans-serif')
    return f".emoji {{ font-family: {', '.join(stack)}; }}"


def _page_font(fonts_available: dict[str, Optional[str]]) -> str:
    """Return page-number font-family string."""
    return (
        "'Noto Sans SC', 'Noto Sans CJK SC', sans-serif"
        if fonts_available['Noto Sans SC'] else 'sans-serif'
    )


def _build_page_number_css(page_font: str, color: str = "#94a3b8") -> str:
    """Build @page rule with @bottom-center page counter."""
    return f"""@page {{
    @bottom-center {{
        content: counter(page);
        font-family: {page_font};
        font-size: 8pt;
        color: {color};
    }}
}}"""


_PORTABLE_REPORT_MARKER = 'data-data-analytics-portable-artifact="true"'


def _html_theme_palette(theme: HtmlPdfTheme) -> dict[str, str]:
    """Return a validated HTML PDF theme palette."""
    if theme not in _HTML_THEME_PALETTES:
        choices = ", ".join(_HTML_THEME_PALETTES)
        raise ValueError(f"Unknown HTML PDF theme: {theme!r}. Use one of: {choices}.")
    return _HTML_THEME_PALETTES[theme]


def _build_html_theme_css(theme: HtmlPdfTheme = "print") -> str:
    """Build the page canvas and default foreground colors for HTML PDFs."""
    palette = _html_theme_palette(theme)
    return f"""
@page {{
    background: {palette['page_bg']};
}}
html {{
    color-scheme: {palette['scheme']} !important;
    background-color: {palette['page_bg']} !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}}
html body {{
    background-color: {palette['page_bg']} !important;
    color: {palette['text']};
}}
"""


def _build_portable_report_print_css(
    fonts_available: dict[str, Optional[str]],
    theme: HtmlPdfTheme = "print",
) -> str:
    """Build a polished print layer for portable Data Analytics reports.

    The report HTML is intentionally optimized for an interactive, wide screen.
    Printing it without a dedicated layer repeats tooltip provenance, shrinks
    charts, and forces wide tables into unreadably small columns. These rules
    target only the portable artifact marker and leave ordinary HTML untouched.
    """
    palette = _html_theme_palette(theme)
    body_font = (
        "'Noto Sans CJK SC', 'Noto Sans SC', system-ui, -apple-system, "
        "'Segoe UI', sans-serif"
        if fonts_available['Noto Sans SC']
        else "system-ui, -apple-system, 'Segoe UI', sans-serif"
    )
    chart_variant_css = (
        """
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-light {
        display: none !important;
    }
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-dark {
        display: block !important;
    }
"""
        if theme == "one-dark-pro"
        else """
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-light {
        display: block !important;
    }
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-dark {
        display: none !important;
    }
"""
    )
    return f"""
@page {{
    size: A4;
    margin: 13mm 13mm 16mm;
    background: {palette['page_bg']};
}}

@media print {{
    html[data-data-analytics-portable-artifact="true"] {{
        --mcp-print-page: {palette['page_bg']};
        --mcp-print-surface: {palette['surface']};
        --mcp-print-ink: {palette['text']};
        --mcp-print-muted: {palette['muted']};
        --mcp-print-meta: {palette['meta']};
        --mcp-print-soft: {palette['soft']};
        --mcp-print-border: {palette['border']};
        --mcp-print-cell-border: {palette['cell_border']};
        --mcp-print-accent: {palette['accent']};
        --mcp-print-accent-soft: {palette['accent_soft']};
        --mcp-print-heading: {palette['heading']};
        --mcp-print-heading-secondary: {palette['heading_secondary']};
        --mcp-print-heading-tertiary: {palette['heading_tertiary']};
        --mcp-print-table-header: {palette['table_header']};
        --mcp-print-table-stripe: {palette['table_stripe']};
        --mcp-print-chart-primary: {palette['chart_primary']};
        --mcp-print-chart-secondary: {palette['chart_secondary']};
        --mcp-print-chart-grid: {palette['chart_grid']};
        color-scheme: {palette['scheme']} !important;
        background: var(--mcp-print-page) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] body {{
        background: var(--mcp-print-page) !important;
        color: var(--mcp-print-ink) !important;
        font-family: {body_font} !important;
        font-size: 9.5pt !important;
        line-height: 1.5 !important;
        -webkit-font-smoothing: antialiased;
        text-rendering: optimizeLegibility;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-fallback {{
        width: 100% !important;
        max-width: none !important;
        padding: 0 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-page-header {{
        position: static !important;
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) auto !important;
        gap: 8mm !important;
        align-items: end !important;
        width: 100% !important;
        height: auto !important;
        min-height: 0 !important;
        margin: 0 0 8mm !important;
        padding: 0 0 5mm !important;
        border: 0 !important;
        border-bottom: 2px solid var(--mcp-print-accent) !important;
        background: var(--mcp-print-surface) !important;
        break-after: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-surface-label {{
        margin: 0 0 1.5mm !important;
        color: var(--mcp-print-accent) !important;
        font-size: 8pt !important;
        font-weight: 700 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-page-header h1 {{
        margin: 0 0 1.5mm !important;
        color: var(--mcp-print-heading) !important;
        font-size: 21pt !important;
        font-weight: 700 !important;
        line-height: 1.2 !important;
        letter-spacing: -0.02em !important;
        white-space: normal !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-description {{
        display: block !important;
        max-width: 145mm !important;
        margin: 0 !important;
        color: var(--mcp-print-muted) !important;
        font-size: 9pt !important;
        line-height: 1.45 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-page-meta {{
        display: block !important;
        max-width: 36mm !important;
        color: var(--mcp-print-meta) !important;
        font-size: 7.5pt !important;
        line-height: 1.4 !important;
        text-align: right !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-block-stack {{
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) !important;
        gap: 7mm !important;
        margin-top: 0 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-layout-half,
    html[data-data-analytics-portable-artifact="true"] .portable-layout-full {{
        grid-column: 1 !important;
    }}
    html[data-data-analytics-portable-artifact="true"]
    .portable-block[data-artifact-block-id="weekly_heading"] {{
        break-before: page;
        break-after: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-markdown {{
        max-width: none !important;
        color: var(--mcp-print-ink) !important;
        orphans: 3;
        widows: 3;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-markdown h2 {{
        margin: 0 0 3mm !important;
        color: var(--mcp-print-heading-secondary) !important;
        font-size: 15pt !important;
        font-weight: 700 !important;
        line-height: 1.25 !important;
        break-after: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-markdown h3 {{
        margin: 0 0 2mm !important;
        color: var(--mcp-print-heading-tertiary) !important;
        font-size: 11.5pt !important;
        font-weight: 700 !important;
        break-after: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-markdown p,
    html[data-data-analytics-portable-artifact="true"] .portable-markdown ul,
    html[data-data-analytics-portable-artifact="true"] .portable-markdown ol {{
        margin-top: 0 !important;
        margin-bottom: 2.5mm !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-markdown li + li {{
        margin-top: 1.2mm !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-metric-grid {{
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 3mm !important;
        align-items: stretch !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-metric-card {{
        min-height: 0 !important;
        padding: 4mm !important;
        border: 1px solid var(--mcp-print-border) !important;
        border-top: 2px solid var(--mcp-print-accent) !important;
        border-radius: 3mm !important;
        background: linear-gradient(
            180deg,
            var(--mcp-print-accent-soft),
            var(--mcp-print-surface) 45%
        ) !important;
        box-shadow: none !important;
        break-inside: avoid-page !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-metric-label {{
        color: var(--mcp-print-muted) !important;
        font-size: 8.5pt !important;
        font-weight: 600 !important;
        line-height: 1.35 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-metric-value {{
        margin: 1mm 0 0 !important;
        color: var(--mcp-print-heading) !important;
        font-size: 18pt !important;
        font-weight: 700 !important;
        line-height: 1.1 !important;
        font-variant-numeric: tabular-nums;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-card-description {{
        display: block !important;
        margin: 2mm 0 0 !important;
        color: var(--mcp-print-muted) !important;
        font-size: 7.8pt !important;
        line-height: 1.4 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-metric-badge {{
        padding: 0.5mm 2mm !important;
        border-color: var(--mcp-print-border) !important;
        background: var(--mcp-print-surface) !important;
        color: var(--mcp-print-muted) !important;
        font-size: 7.5pt !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-metric-badge * {{
        color: var(--mcp-print-ink) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-chart-summary {{
        margin: 0 !important;
        padding: 4mm 4mm 3mm !important;
        border: 1px solid var(--mcp-print-border) !important;
        border-radius: 3mm !important;
        background: var(--mcp-print-surface) !important;
        box-shadow: none !important;
        break-inside: avoid-page !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-visual-header {{
        margin: 0 0 2.5mm !important;
        break-after: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-visual-header > strong,
    html[data-data-analytics-portable-artifact="true"] .portable-visual-header h1,
    html[data-data-analytics-portable-artifact="true"] .portable-visual-header h2,
    html[data-data-analytics-portable-artifact="true"] .portable-visual-header h3 {{
        color: var(--mcp-print-heading-secondary) !important;
        font-size: 11pt !important;
        font-weight: 700 !important;
        line-height: 1.3 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart {{
        margin: 0 auto !important;
        overflow: visible !important;
        background: var(--mcp-print-surface) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-variant > svg {{
        display: block !important;
        width: 100% !important;
        max-height: 68mm !important;
        overflow: visible !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-legend-wrap,
    html[data-data-analytics-portable-artifact="true"] .portable-static-chart-legend {{
        color: var(--mcp-print-muted) !important;
        font-size: 7.5pt !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [stroke="rgb(0, 63, 122)"],
    html[data-data-analytics-portable-artifact="true"] svg [stroke="rgb(51, 156, 255)"] {{
        stroke: var(--mcp-print-chart-primary) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(0, 63, 122)"],
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(51, 156, 255)"] {{
        fill: var(--mcp-print-chart-primary) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [stroke="rgb(146, 59, 15)"],
    html[data-data-analytics-portable-artifact="true"] svg [stroke="rgb(251, 106, 34)"] {{
        stroke: var(--mcp-print-chart-secondary) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(146, 59, 15)"],
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(251, 106, 34)"] {{
        fill: var(--mcp-print-chart-secondary) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [stroke="rgba(26, 28, 31, 0.05)"],
    html[data-data-analytics-portable-artifact="true"] svg [stroke="rgba(255, 255, 255, 0.05)"] {{
        stroke: var(--mcp-print-chart-grid) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgba(26, 28, 31, 0.7)"],
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(205, 205, 205)"] {{
        fill: var(--mcp-print-muted) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(26, 28, 31)"],
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(223, 223, 223)"] {{
        fill: var(--mcp-print-ink) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] svg [fill="rgb(255, 255, 255)"] {{
        fill: var(--mcp-print-surface) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-card {{
        padding: 4mm !important;
        border: 1px solid var(--mcp-print-border) !important;
        border-radius: 3mm !important;
        background: var(--mcp-print-surface) !important;
        break-inside: auto !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll {{
        overflow: visible !important;
        border: 0 !important;
        border-radius: 0 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll table {{
        width: 100% !important;
        min-width: 0 !important;
        border: 1px solid var(--mcp-print-border) !important;
        border-collapse: collapse !important;
        table-layout: fixed !important;
        font-variant-numeric: tabular-nums;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll thead {{
        display: table-header-group;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll tr {{
        break-inside: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll th,
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll td {{
        display: table-cell !important;
        max-width: none !important;
        padding: 1.8mm 1.5mm !important;
        overflow: visible !important;
        border: 1px solid var(--mcp-print-border) !important;
        color: var(--mcp-print-ink) !important;
        font-size: 7.4pt !important;
        line-height: 1.35 !important;
        text-align: left !important;
        text-overflow: clip !important;
        overflow-wrap: anywhere !important;
        word-break: normal !important;
        white-space: normal !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll th {{
        position: static !important;
        background: var(--mcp-print-table-header) !important;
        color: var(--mcp-print-heading-secondary) !important;
        font-size: 7.2pt !important;
        font-weight: 700 !important;
        letter-spacing: 0 !important;
        text-transform: none !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-table-scroll tbody tr:nth-child(even) td {{
        background: var(--mcp-print-table-stripe) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] {{
        border: 0 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] thead {{
        display: none !important;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] tbody {{
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 3mm !important;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] tr {{
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        align-content: start !important;
        overflow: hidden !important;
        border: 1px solid var(--mcp-print-border) !important;
        border-radius: 2.5mm !important;
        background: var(--mcp-print-surface) !important;
        break-inside: avoid-page !important;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] td {{
        display: block !important;
        padding: 1.8mm 2mm !important;
        border: 0 !important;
        border-bottom: 1px solid var(--mcp-print-cell-border) !important;
        background: var(--mcp-print-surface) !important;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] td::before {{
        content: attr(data-mcp-print-label);
        display: block;
        margin-bottom: 0.5mm;
        color: var(--mcp-print-muted);
        font-size: 6.6pt;
        font-weight: 700;
        line-height: 1.2;
    }}
    html[data-data-analytics-portable-artifact="true"] table[data-mcp-print-layout="stacked"] td:first-child {{
        grid-column: 1 / -1;
        background: var(--mcp-print-accent-soft) !important;
        color: var(--mcp-print-heading-secondary) !important;
        font-size: 9pt !important;
        font-weight: 700 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-inline-source,
    html[data-data-analytics-portable-artifact="true"] .portable-source-tooltip-content,
    html[data-data-analytics-portable-artifact="true"] .portable-source-summary {{
        display: none !important;
    }}
    html[data-data-analytics-portable-artifact="true"]
    .portable-block-stack > .portable-block:first-child .portable-source-summary {{
        display: block !important;
        width: 100% !important;
        height: auto !important;
        margin: 4mm 0 0 !important;
        padding: 2.5mm 3mm !important;
        border: 0 !important;
        border-left: 2px solid var(--mcp-print-accent) !important;
        border-radius: 0 2mm 2mm 0 !important;
        background: var(--mcp-print-accent-soft) !important;
        color: var(--mcp-print-muted) !important;
        font-size: 7pt !important;
        line-height: 1.35 !important;
        break-inside: avoid-page;
    }}
    html[data-data-analytics-portable-artifact="true"]
    .portable-block-stack > .portable-block:first-child .portable-source-summary-content {{
        display: grid !important;
        grid-template-columns: auto minmax(0, 1fr) !important;
        gap: 1mm 2mm !important;
        align-items: baseline !important;
    }}
    html[data-data-analytics-portable-artifact="true"]
    .portable-block-stack > .portable-block:first-child .portable-source-summary-content * {{
        color: var(--mcp-print-muted) !important;
    }}
    html[data-data-analytics-portable-artifact="true"]
    .portable-block-stack > .portable-block:first-child .portable-source-summary-content > strong {{
        color: var(--mcp-print-heading-secondary) !important;
        font-weight: 700 !important;
    }}
    html[data-data-analytics-portable-artifact="true"] .portable-sources {{
        display: none !important;
    }}
{chart_variant_css}
}}
"""


def _build_html_print_css(
    html_text: str,
    fonts_available: dict[str, Optional[str]],
    theme: HtmlPdfTheme = "print",
) -> str:
    """Return a document-specific print profile when the HTML is recognized."""
    if _PORTABLE_REPORT_MARKER in html_text:
        return _build_portable_report_print_css(fonts_available, theme)
    return ""


_PREPARE_PORTABLE_REPORT_SCRIPT = """
() => {
    const root = document.documentElement;
    if (root.dataset.dataAnalyticsPortableArtifact !== "true") return;
    const report = document.getElementById("data-analytics-portable-fallback");
    if (!report) return;
    for (const table of report.querySelectorAll("table")) {
        const headers = Array.from(table.querySelectorAll("thead th"), (cell) =>
            (cell.textContent || "").trim()
        );
        if (headers.length < 10) continue;
        table.dataset.mcpPrintLayout = "stacked";
        for (const row of table.querySelectorAll("tbody tr")) {
            Array.from(row.children).forEach((cell, index) => {
                if (cell instanceof HTMLElement) {
                    cell.dataset.mcpPrintLabel = headers[index] || "";
                }
            });
        }
    }
}
"""


def _build_injected_css(
    fonts_available: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
    compat_css: str = "",
    theme: HtmlPdfTheme = "print",
) -> str:
    """Build CSS to inject into HTML→PDF conversion.

    Returns pure CSS (no ``<style>`` wrapper).  Caller wraps as needed.

    Args:
        fonts_available: Font availability dict from ``_check_fonts()``.
        page_numbers:    Whether to inject ``@page @bottom-center`` page footer.
        compat_css:      Additional CSS to inject (e.g. WeasyPrint compat rules).
        theme:           HTML PDF color theme.
    """
    palette = _html_theme_palette(theme)
    parts: list[str] = []
    parts.append(_build_font_face_css(fonts_available))
    parts.append(_build_emoji_css(fonts_available))
    parts.append(_build_html_theme_css(theme))
    if page_numbers:
        parts.append(_build_page_number_css(
            _page_font(fonts_available),
            palette['page_number'],
        ))
    if compat_css:
        parts.append(compat_css.strip())
    return "\n".join(p for p in parts if p)


def _inject_css_before_head_end(html_text: str, css: str) -> str:
    """Inject a ``<style>`` block just before ``</head>``."""
    style_tag = f"<style>\n{css}\n</style>\n</head>"
    if '</head>' in html_text:
        return html_text.replace('</head>', style_tag, 1)
    else:
        return style_tag + html_text


# ── Checkbox processing ──

def _process_checkboxes(body_html: str) -> str:
    """Convert Markdown checkbox syntax in <li> elements to styled checkboxes.

    Post-processes markdown-it-py HTML output (which treats ``[ ]`` / ``[x]``
    as literal text) into CSS-styled checkbox spans.

    Handles these patterns at the start of <li> content:
    - ``[ ]`` / ``[]`` → ☐ (unchecked)
    - ``[x]`` / ``[X]`` → ☑ (checked)
    """
    # Unchecked: [ ] or [] (with optional internal whitespace)
    body_html = re.sub(
        r'(<li[^>]*>)\s*\[\s*\]\s*',
        r'\1<span class="task-checkbox unchecked">☐</span> ',
        body_html,
    )
    # Checked: [x] or [X]
    body_html = re.sub(
        r'(<li[^>]*>)\s*\[[xX]\]\s*',
        r'\1<span class="task-checkbox checked">☑</span> ',
        body_html,
    )
    return body_html


# ── Emoji / body helpers ──

def _process_body(body_html: str, has_emoji_font: bool) -> str:
    """Post-process HTML body for Markdown→PDF.

    - Colorize ★ stars with .star CSS class.
    - Wrap emojis in .emoji spans if font available (★ excluded, already handled).
    """
    body_html = re.sub(r'★+', lambda m: f'<span class="star">{m.group()}</span>', body_html)

    if has_emoji_font:
        body_html = _EMOJI_SEQUENCE_RE.sub(
            lambda m: m.group() if m.group() == '★'
                      else f'<span class="emoji">{m.group()}</span>',
            body_html,
        )

    return body_html


def _process_emoji(html_text: str, has_emoji_font: bool) -> str:
    """Process emoji in raw HTML for HTML→PDF conversion.

    - Wrap emojis in .emoji spans if font available.
    - Replace with text equivalents if font missing.
    """
    if has_emoji_font:
        return _EMOJI_SEQUENCE_RE.sub(lambda m: f'<span class="emoji">{m.group()}</span>', html_text)
    else:
        for emoji, text in _EMOJI_TEXT_MAP.items():
            html_text = html_text.replace(emoji, text)
        return html_text


# ── Emoji-safe text replacement (ZWJ / skin-tone / code-block aware) ──


def _protect_code_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Temporarily replace code blocks and inline code with placeholders.

    Returns (modified_text, placeholder→original mapping) so
    ``_restore_code_blocks`` can undo the substitution.
    """
    placeholders: dict[str, str] = {}

    def _make_placeholder(match: re.Match) -> str:
        key = f"\x00CODE{len(placeholders)}\x00"
        placeholders[key] = match.group(0)
        return key

    # Order matters: fenced code blocks first (may contain backticks),
    # then inline code (single backtick spans).
    text = re.sub(r'```[\s\S]*?```', _make_placeholder, text)
    text = re.sub(r'`[^`\n]+`', _make_placeholder, text)
    return text, placeholders


def _restore_code_blocks(text: str, placeholders: dict[str, str]) -> str:
    """Reverse ``_protect_code_blocks`` — restore original code content."""
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


def _safe_emoji_replace(text: str, emoji_map: dict[str, str]) -> str:
    """Replace standalone emojis with their text equivalents.

    An emoji is considered *standalone* when it is NOT:
    - preceded by a ZWJ (U+200D) — part of a ZWJ sequence
    - followed by a ZWJ, skin-tone modifier, or VS16 — part of a larger glyph

    This preserves: ZWJ family/profession sequences (👩‍💻),
    skin-tone variants (👍🏻), and similar compound emojis.
    """
    for emoji, replacement in emoji_map.items():
        pattern = (
            r'(?<!\u200d)'                              # NOT preceded by ZWJ
            + re.escape(emoji)                          # the emoji itself
            + r'(?![\u200d\U0001F3FB-\U0001F3FF\ufe0f])'  # NOT followed by extender (ZWJ / skin-tone / VS16)
        )
        text = re.sub(pattern, replacement, text)
    return text


# ── Math processing (LaTeX → MathJax SVG) ──


def _discover_mathjax_node_path() -> Optional[str]:
    """Locate the directory containing the MathJax v4 Node component.

    Resolution order:

    1. Repository-local ``node_modules`` (installed from ``package-lock.json``).
    2. ``MATHJAX_NODE_PATH`` env var (explicit override).
    3. Every directory in ``NODE_PATH``.
    4. ``npm root -g`` output (legacy global fallback).

    Returns ``None`` if MathJax cannot be located.  Callers should fall back
    to plain-text rendering when discovery fails.
    """
    candidates = [str(Path(__file__).resolve().parent / 'node_modules')]
    explicit = os.environ.get('MATHJAX_NODE_PATH')
    if explicit:
        candidates.append(explicit)
    candidates.extend(
        item for item in os.environ.get('NODE_PATH', '').split(os.pathsep) if item
    )
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, 'mathjax', 'package.json')):
            return candidate

    npm = shutil.which('npm')
    if npm is not None:
        try:
            result = subprocess.run(
                [npm, 'root', '-g'],
                capture_output=True, text=True, timeout=5, check=True,
            )
            candidate = result.stdout.strip()
            if candidate and os.path.isfile(os.path.join(candidate, 'mathjax', 'package.json')):
                return candidate
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    return None


_MATHJAX_PRINT_WIDTH_EX = 88.0


def _make_mathjax_svg_responsive(markup: str) -> str:
    """Scale an over-wide standalone MathJax SVG to the A4 print column.

    MathJax 4 emits tagged or automatically broken display equations as a
    ``width="100%"`` SVG with an inline ``min-width``.  Chromium honors that
    minimum while printing and may shrink the entire document to fit one wide
    equation.  For equations wider than the 10pt A4 content column, remove the
    generated minimum and scale the SVG drawing itself.  Equations that already
    fit are returned unchanged.
    """
    original_markup = markup
    opening_tag = re.search(r'<svg\b[^>]*>', markup)
    if opening_tag is None:
        return markup

    tag = opening_tag.group(0)
    if not re.search(r'\sdata-mjx-viewBox="', tag):
        return markup

    style_match = re.search(r'style="([^"]*)"', tag)
    min_width = None if style_match is None else re.search(
        r'(?:^|\s)min-width\s*:\s*([^;\s"]+)', style_match.group(1),
    )
    if min_width is None:
        return markup

    width_match = re.fullmatch(r'([0-9.]+)ex', min_width.group(1))
    if width_match is None:
        return markup
    natural_width = float(width_match.group(1))
    if natural_width <= _MATHJAX_PRINT_WIDTH_EX:
        return markup

    scale = _MATHJAX_PRINT_WIDTH_EX / natural_width

    def remove_min_width(match: re.Match[str]) -> str:
        style = re.sub(r'\s*min-width\s*:\s*[^;"]+;?', '', match.group(1))
        style = re.sub(
            r'vertical-align\s*:\s*(-?[0-9.]+)ex',
            lambda value: f'vertical-align: {float(value.group(1)) * scale:.4f}ex',
            style,
        )
        return f'style="{style.strip()}"'

    tag = re.sub(r'style="([^"]*)"', remove_min_width, tag, count=1)
    tag = re.sub(
        r'height="([0-9.]+)ex"',
        lambda value: f'height="{float(value.group(1)) * scale:.4f}ex"',
        tag,
        count=1,
    )
    markup = markup[:opening_tag.start()] + tag + markup[opening_tag.end():]

    defs_end = markup.find('</defs>', opening_tag.start())
    svg_end = markup.rfind('</svg>')
    if defs_end < 0 or svg_end < 0:
        return original_markup
    content_start = defs_end + len('</defs>')
    return (
        markup[:content_start]
        + f'<g transform="scale({scale:.6f})">'
        + markup[content_start:svg_end]
        + '</g>'
        + markup[svg_end:]
    )


def _convert_math_to_mathjax_svg(text: str) -> str:
    """Convert LaTeX math ($...$ / $$...$$) to MathJax SVG for WeasyPrint.

    Uses MathJax via a single batch Node.js subprocess (JSON on stdin →
    JSON on stdout).  Falls back to plain text if unavailable.
    """
    import json
    import subprocess

    display_matches = [
        m for m in _MATH_DISPLAY_RE.finditer(text)
        if _is_likely_math(m.group(1), is_display=True)
    ]
    inline_matches = [
        m for m in _MATH_INLINE_RE.finditer(text)
        if _is_likely_math(m.group(1), is_display=False)
    ]

    # ── Filter inline matches that fall inside display math blocks ──
    # When a $$...$$ block contains nested $...$ (e.g. inside \text{}),
    # the inline regex incorrectly matches the inner $...$ as a separate
    # formula.  This would produce duplicated SVG + raw LaTeX source leak.
    display_spans = [(m.start(), m.end()) for m in display_matches]
    inline_matches = [
        m for m in inline_matches
        if not any(ds <= m.start() < de for ds, de in display_spans)
    ]

    all_matches = display_matches + inline_matches

    if not all_matches:
        return text

    batch = []
    for m in display_matches:
        batch.append({'latex': m.group(1), 'display': True})
    for m in inline_matches:
        batch.append({'latex': m.group(1), 'display': False})

    input_json = json.dumps(batch, ensure_ascii=False)

    node_script = r'''
        global.MathJax = {};
        const MJ = require("mathjax");
        var chunks = [];
        process.stdin.on("data", function(c){chunks.push(c)});
        process.stdin.on("end", function(){
            var formulas = JSON.parse(Buffer.concat(chunks).toString());
            MJ.init({
                loader: {load: ["input/tex", "output/svg"]},
                output: {linebreaks: {inline: false}}
            }).then(async function(){
                var results = [];
                for (const f of formulas) {
                    try {
                        var node = await MJ.tex2svgPromise(f.latex, {display: f.display});
                        var out = MJ.startup.adaptor.innerHTML(node);
                        results.push(f.display
                            ? '<div class="mathjax-block">' + out + '</div>'
                            : '<span class="mathjax-inline">' + out + '</span>');
                    } catch(e) { results.push(f.latex); }
                }
                process.stdout.write(JSON.stringify(results));
            }).catch(function(e){
                process.stderr.write(String(e));
                process.exitCode = 1;
            });
        });
    '''

    node_path = _discover_mathjax_node_path()
    if node_path is None:
        logger.warning(
            "MathJax not found in local node_modules, MATHJAX_NODE_PATH, "
            "NODE_PATH, or `npm root -g`. Math will render as plain text. "
            "Run `npm ci --ignore-scripts` in the format-conversion directory."
        )
        return text

    env = os.environ.copy()
    env['NODE_PATH'] = node_path

    try:
        result = subprocess.run(
            ['node', '-e', node_script],
            input=input_json, capture_output=True, text=True, env=env, timeout=60,
        )
        if result.returncode != 0:
            logger.warning("MathJax failed: %s", result.stderr.strip()[:200])
            return text
        rendered = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("MathJax unavailable (%s), math as plain text", e)
        return text
    except json.JSONDecodeError:
        logger.warning("MathJax returned invalid JSON")
        return text

    if len(rendered) != len(all_matches):
        logger.warning("MathJax mismatch: %d formulas, %d results",
                       len(all_matches), len(rendered))
        return text

    # ── Detect MathJax error SVGs ──
    # When MathJax encounters a LaTeX error (e.g. \tag inside aligned),
    # it produces a degraded SVG with a <title> error message and raw
    # LaTeX source in a fallback <text> element.  WeasyPrint renders
    # these <text> elements as visible text, leaking raw LaTeX into the
    # PDF.  Replace error SVGs with formatted plain-text fallbacks.
    _MJ_ERR_RE = re.compile(
        r'<title>(.+?)</title>|data-mjx-error="([^"]+)"|class="[^"]*mjx-merror',
        re.IGNORECASE,
    )

    paired = list(enumerate(all_matches))
    paired.sort(key=lambda x: x[1].start())

    result_parts = []
    last_end = 0
    for idx, m in paired:
        result_parts.append(text[last_end:m.start()])
        svg = rendered[idx]
        err_match = _MJ_ERR_RE.search(svg)
        if err_match:
            error_detail = next((group for group in err_match.groups() if group), 'rendering error')
            logger.warning(
                "MathJax error in formula %d: %s — %s",
                idx, m.group(1)[:80], error_detail,
            )
            # Replace error SVG with clean monospace rendering of the LaTeX source
            is_display = idx < len(display_matches)
            if is_display:
                result_parts.append(
                    f'<pre class="math-error">{escape(m.group(1))}</pre>'
                )
            else:
                result_parts.append(
                    f'<code class="math-error">{escape(m.group(1))}</code>'
                )
        else:
            result_parts.append(_make_mathjax_svg_responsive(svg))
        last_end = m.end()
    result_parts.append(text[last_end:])

    return ''.join(result_parts)


# ── Public API ──

# Re-export engine type for MCP server / external callers
HtmlPdfEngine = Literal["weasyprint", "chromium"]


class _PdfMarkdownIt(MarkdownIt):
    """Keep local file links in PDFs alongside ordinary Markdown links."""

    def validateLink(self, url: str) -> bool:
        # PDF documents may link to files anywhere on the reader's filesystem.
        # Keep the default validation for every other scheme.
        return url.strip().lower().startswith("file:") or super().validateLink(url)


@contextmanager
def _atomic_pdf_output(output_path: Path) -> Iterator[Path]:
    """Publish a generated PDF only after the backend returns successfully."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        yield temporary_path
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("PDF backend completed without producing a non-empty file")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def _atomic_png_output(
    output_path: Path,
    expected_dimensions: tuple[int, int],
) -> Iterator[Path]:
    """Validate and publish a generated PNG without exposing partial output."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp.png",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        yield temporary_path
        if not temporary_path.is_file():
            raise RuntimeError("SVG renderer completed without producing a PNG file")
        if temporary_path.stat().st_size <= len(_PNG_SIGNATURE):
            raise RuntimeError("SVG renderer produced an empty or truncated PNG file")
        with temporary_path.open("rb") as stream:
            if stream.read(len(_PNG_SIGNATURE)) != _PNG_SIGNATURE:
                raise RuntimeError("SVG renderer produced an invalid PNG signature")
        with Image.open(temporary_path) as image:
            actual_dimensions = image.size
            if image.format != "PNG":
                raise RuntimeError("SVG renderer output is not a PNG image")
            image.verify()
        if actual_dimensions != expected_dimensions:
            raise RuntimeError(
                "SVG renderer produced unexpected dimensions: "
                f"expected {expected_dimensions}, got {actual_dimensions}"
            )
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _svg_length_in_pixels(value: str | None, fallback: float | None) -> float:
    """Resolve a root SVG length using CairoSVG's 96-DPI defaults."""
    if value is None:
        if fallback is not None:
            return fallback
        raise ValueError("SVG width and height must be defined, directly or by viewBox")

    match = _SVG_LENGTH_RE.fullmatch(value)
    if match is None or match.group(2) == "%":
        if fallback is not None:
            return fallback
        raise ValueError(
            f"SVG root length {value!r} needs an absolute value or a viewBox"
        )

    number = float(match.group(1))
    unit = (match.group(2) or "").casefold()
    pixels = number * _SVG_LENGTH_TO_PIXELS[unit]
    if not math.isfinite(pixels) or pixels <= 0:
        raise ValueError(f"SVG root length must be positive and finite: {value!r}")
    return pixels


def _svg_intrinsic_dimensions(svg_bytes: bytes) -> tuple[float, float]:
    """Safely parse the root dimensions needed to bound raster allocation."""
    root = DefusedElementTree.fromstring(svg_bytes)
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise ValueError("Input XML root element must be <svg>")

    viewbox_width: float | None = None
    viewbox_height: float | None = None
    viewbox = root.get("viewBox")
    if viewbox:
        values = re.split(r"[\s,]+", viewbox.strip())
        if len(values) != 4:
            raise ValueError("SVG viewBox must contain exactly four numbers")
        try:
            parsed = tuple(float(value) for value in values)
        except ValueError as error:
            raise ValueError("SVG viewBox must contain only numbers") from error
        if not all(math.isfinite(value) for value in parsed):
            raise ValueError("SVG viewBox values must be finite")
        viewbox_width, viewbox_height = parsed[2], parsed[3]
        if viewbox_width <= 0 or viewbox_height <= 0:
            raise ValueError("SVG viewBox width and height must be positive")

    return (
        _svg_length_in_pixels(root.get("width"), viewbox_width),
        _svg_length_in_pixels(root.get("height"), viewbox_height),
    )


def _validated_png_dimensions(
    intrinsic: tuple[float, float],
    *,
    scale: float,
    output_width: int | None,
    output_height: int | None,
) -> tuple[int, int]:
    """Validate conversion controls and return CairoSVG's rounded PNG size."""
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise ValueError("scale must be a number")
    scale = float(scale)
    if not math.isfinite(scale) or not 0 < scale <= MAX_SVG_SCALE:
        raise ValueError(f"scale must be finite and in the range (0, {MAX_SVG_SCALE:g}]")

    for name, value in (
        ("output_width", output_width),
        ("output_height", output_height),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError(f"{name} must be a positive integer or null")

    if scale != 1.0 and (output_width is not None or output_height is not None):
        raise ValueError("scale cannot be combined with output_width or output_height")

    intrinsic_width, intrinsic_height = intrinsic
    if output_width is not None and output_height is not None:
        width, height = float(output_width), float(output_height)
    elif output_width is not None:
        width = float(output_width)
        height = intrinsic_height * width / intrinsic_width
    elif output_height is not None:
        height = float(output_height)
        width = intrinsic_width * height / intrinsic_height
    else:
        width = intrinsic_width * scale
        height = intrinsic_height * scale

    if not math.isfinite(width) or not math.isfinite(height):
        raise ValueError("Requested PNG dimensions must be finite")
    rounded = (round(width), round(height))
    if min(rounded) < 1:
        raise ValueError("Requested PNG dimensions round below one pixel")
    if max(rounded) > MAX_PNG_DIMENSION:
        raise ValueError(
            f"Requested PNG dimensions exceed the {MAX_PNG_DIMENSION}-pixel side limit: "
            f"{rounded[0]}x{rounded[1]}"
        )
    if rounded[0] * rounded[1] > MAX_PNG_PIXELS:
        raise ValueError(
            f"Requested PNG dimensions exceed the {MAX_PNG_PIXELS:,}-pixel limit: "
            f"{rounded[0]}x{rounded[1]}"
        )
    return rounded


def convert_svg_to_png(
    source_path: str,
    output_path: str,
    *,
    scale: float = 1.0,
    output_width: int | None = None,
    output_height: int | None = None,
    background_color: str = "",
) -> tuple[int, int]:
    """Rasterize a self-contained SVG file to a bounded PNG image.

    External file and network references are disabled. Data URLs embedded in
    the SVG remain available. Output is staged beside the destination, fully
    decoded and validated, then atomically published.

    Returns:
        The rendered ``(width, height)`` in pixels.
    """
    svg_path = Path(source_path)
    if not svg_path.is_file():
        raise FileNotFoundError(f"SVG file not found: {source_path}")
    if svg_path.suffix.casefold() != ".svg":
        raise ValueError(f"SVG input path must end with .svg: {source_path}")

    out_path = Path(output_path)
    if out_path.suffix.casefold() != ".png":
        raise ValueError(f"PNG output path must end with .png: {output_path}")
    if svg_path.resolve() == out_path.resolve():
        raise ValueError("SVG input and PNG output paths must be different")

    input_size = svg_path.stat().st_size
    if input_size > MAX_SVG_INPUT_BYTES:
        raise ValueError(
            f"SVG input exceeds the {MAX_SVG_INPUT_BYTES:,}-byte limit: {input_size:,}"
        )
    svg_bytes = svg_path.read_bytes()
    if len(svg_bytes) > MAX_SVG_INPUT_BYTES:
        raise ValueError(
            f"SVG input exceeds the {MAX_SVG_INPUT_BYTES:,}-byte limit: "
            f"{len(svg_bytes):,}"
        )

    intrinsic = _svg_intrinsic_dimensions(svg_bytes)
    dimensions = _validated_png_dimensions(
        intrinsic,
        scale=scale,
        output_width=output_width,
        output_height=output_height,
    )

    logger.info("Converting SVG: %s → %s", svg_path, out_path)
    with _atomic_png_output(out_path, dimensions) as temporary_output:
        cairosvg.svg2png(
            bytestring=svg_bytes,
            dpi=96,
            scale=scale,
            unsafe=False,
            background_color=background_color or None,
            write_to=str(temporary_output),
            output_width=output_width,
            output_height=output_height,
        )
    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)
    return dimensions


def convert_markdown_to_pdf(
    source_path: str,
    output_path: str,
    *,
    engine: HtmlPdfEngine = "weasyprint",
    theme: MarkdownPdfTheme = "print",
) -> None:
    """Convert a Markdown file to a styled PDF.

    Pipeline: markdown-it-py → HTML → (WeasyPrint or Chromium) → PDF.
    Includes Chinese fonts, table styling, code blocks, blockquotes,
    page numbers, emoji handling, and checkbox/task-list rendering.

    Args:
        source_path: Absolute path to the .md file.
        output_path: Absolute path for the output .pdf file.
        engine:      Rendering backend. ``"weasyprint"`` (default) or
                     ``"chromium"``.  Chromium renders MathJax SVG with
                     full Chrome fidelity (recommended for math-heavy docs).
        theme:       PDF color theme: ``"print"`` (white, default),
                     ``"sepia"`` (warm low-glare), or ``"one-dark-pro"``
                     (One Dark Pro Night Flat-inspired screen theme).

    Raises:
        FileNotFoundError: If source_path does not exist.
    """
    md_path = Path(source_path)
    if not md_path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {source_path}")
    if engine not in ('weasyprint', 'chromium'):
        raise ValueError(f"Unknown engine: {engine!r}. Use 'weasyprint' or 'chromium'.")
    if theme not in _MARKDOWN_THEME_PALETTES:
        choices = ", ".join(_MARKDOWN_THEME_PALETTES)
        raise ValueError(f"Unknown Markdown PDF theme: {theme!r}. Use one of: {choices}.")

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

    # Protect code blocks from emoji replacement (so code stays intact)
    text, code_placeholders = _protect_code_blocks(text)

    # Convert LaTeX math ($...$ / $$...$$) to MathJax SVG before markdown parsing
    text = _convert_math_to_mathjax_svg(text)

    # Replace standalone emojis with text equivalents only when emoji font is missing.
    # When Noto Emoji is available, emojis render natively via .emoji CSS spans.
    if fonts['Noto Emoji'] is None:
        text = _safe_emoji_replace(text, _EMOJI_TEXT_MAP)

    # Restore original code block content
    text = _restore_code_blocks(text, code_placeholders)

    # Parse markdown → HTML body
    md = _PdfMarkdownIt('commonmark', {'breaks': True, 'html': True})
    md.enable(['table', 'strikethrough'])
    body = md.render(text)

    # Post-process checkboxes (markdown-it doesn't support task lists natively)
    body = _process_checkboxes(body)

    # Post-process (star color + emoji wrapping)
    body = _process_body(body, fonts['Noto Emoji'] is not None)

    # Assemble full HTML + CSS → PDF
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<base href="{escape(md_path.parent.resolve().as_uri() + '/', quote=True)}">
<style>
{_build_css(fonts, theme)}
.mathjax-block {{ display: block; margin: 4mm auto; text-align: center; }}
.mathjax-inline {{ display: inline-block; }}
</style>
</head>
<body>
{body}
</body>
</html>"""

    if engine == "chromium":
        # Write HTML to temp file for Chromium rendering
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8',
        ) as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            convert_html_to_pdf(
                tmp_path, str(out_path),
                engine="chromium", page_numbers=False, theme=theme,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    elif engine == "weasyprint":
        logger.info("Converting (WeasyPrint): %s → %s", md_path, out_path)
        with _atomic_pdf_output(out_path) as temporary_output:
            HTML(string=html, base_url=str(md_path.parent)).write_pdf(
                str(temporary_output)
            )

    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)


# ── HTML→PDF backends ──

def _convert_html_to_pdf_weasyprint(
    html_path: Path,
    out_path: Path,
    fonts: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
    compat_css: str = "",
    theme: HtmlPdfTheme = "print",
) -> None:
    """HTML→PDF via WeasyPrint (default backend)."""
    html_text = html_path.read_text(encoding='utf-8')

    # Process emoji (wrap in .emoji spans or replace with text)
    html_text = _process_emoji(html_text, fonts['Noto Emoji'] is not None)

    css = _build_injected_css(
        fonts,
        page_numbers=page_numbers,
        compat_css=compat_css,
        theme=theme,
    )
    print_css = _build_html_print_css(html_text, fonts, theme)
    if print_css:
        css = f"{css}\n{print_css}"
    html_text = _inject_css_before_head_end(html_text, css)

    logger.info("Converting (WeasyPrint): %s → %s", html_path, out_path)
    HTML(string=html_text, base_url=str(html_path.parent)).write_pdf(str(out_path))
    logger.info("Done (WeasyPrint): %s (%s bytes)", out_path, out_path.stat().st_size)


def _convert_html_to_pdf_chromium(
    html_path: Path,
    out_path: Path,
    fonts: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
    theme: HtmlPdfTheme = "print",
) -> None:
    """HTML→PDF via Playwright/Chromium (sync wrapper for asyncio)."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — call async version directly via asyncio.run
        asyncio.run(_convert_html_to_pdf_chromium_async(
            html_path, out_path, fonts,
            page_numbers=page_numbers,
            theme=theme,
        ))
        return

    # Running inside an asyncio loop (MCP server) — use run_coroutine_threadsafe
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            asyncio.run,
            _convert_html_to_pdf_chromium_async(
                html_path, out_path, fonts,
                page_numbers=page_numbers,
                theme=theme,
            ),
        )
        future.result()


async def _convert_html_to_pdf_chromium_async(
    html_path: Path,
    out_path: Path,
    fonts: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
    theme: HtmlPdfTheme = "print",
) -> None:
    """HTML→PDF via Playwright/Chromium (async implementation)."""
    if not _check_playwright():
        raise RuntimeError(
            "Chromium engine requires Playwright. "
            "Install with: pip install playwright && playwright install chromium"
        )

    from playwright.async_api import async_playwright

    # Build CSS injection (no emoji processing — Chrome handles emoji natively)
    html_text = html_path.read_text(encoding='utf-8')
    palette = _html_theme_palette(theme)
    color_scheme: Literal["light", "dark"] = (
        "dark" if theme == "one-dark-pro" else "light"
    )
    css_parts: list[str] = []
    css_parts.append(_build_font_face_css(fonts))
    css_parts.append(_build_html_theme_css(theme))
    if page_numbers:
        css_parts.append(_build_page_number_css(
            _page_font(fonts),
            palette['page_number'],
        ))
    css_parts.append(_build_html_print_css(html_text, fonts, theme))
    css_parts.append("""
html {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
""")
    injected_css = "\n".join(p for p in css_parts if p)

    logger.info("Converting (Chromium): %s → %s", html_path, out_path)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            color_scheme=color_scheme,
        )
        try:
            await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            await page.emulate_media(
                media="print",
                color_scheme=color_scheme,
                reduced_motion="reduce",
            )
            await page.add_style_tag(content=injected_css)
            await page.evaluate(_PREPARE_PORTABLE_REPORT_SCRIPT)
            await page.evaluate(
                "document.fonts ? document.fonts.ready : Promise.resolve()"
            )
            await page.evaluate("""
                () => Promise.all(
                    Array.from(document.images, (image) =>
                        image.decode ? image.decode().catch(() => undefined) : undefined
                    )
                )
            """)

            await page.pdf(
                path=str(out_path),
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=False,
            )
        finally:
            await browser.close()

    logger.info("Done (Chromium): %s (%s bytes)", out_path, out_path.stat().st_size)


# ── Public API ──

def convert_html_to_pdf(
    source_path: str,
    output_path: str,
    *,
    engine: HtmlPdfEngine = "chromium",
    page_numbers: bool = True,
    weasy_compat_css: str = "",
    theme: HtmlPdfTheme = "print",
) -> None:
    """Convert HTML to a themed PDF while preserving authored components.

    Supports two rendering backends:

    - ``engine="chromium"`` (default): Uses Playwright headless Chromium.  Pixel-identical
      to Chrome Print → Save as PDF.  Supports all modern CSS (flex, grid, etc.).
      Requires: ``pip install playwright && playwright install chromium``.
    - ``engine="weasyprint"``: Lightweight, good for simple documents.
      Replaces emoji with font-styled spans.  May not match Chrome pixel-perfectly
      for ``display:flex`` / ``display:grid`` layouts.

    Args:
        source_path:      Absolute path to the .html file.
        output_path:      Absolute path for the output .pdf file.
        engine:           Rendering backend (``"weasyprint"`` or ``"chromium"``).
        page_numbers:     Whether to add page-number footer (both engines).
        weasy_compat_css: Extra CSS injected when ``engine="weasyprint"``
                          (e.g. flex→table compatibility rules).  Ignored for
                          Chromium.
        theme:            PDF color theme: ``"print"`` (white, default),
                          ``"sepia"`` (warm low-glare), or ``"one-dark-pro"``
                          (One Dark Pro Night Flat-inspired screen theme).

    Raises:
        FileNotFoundError: If source_path does not exist.
        RuntimeError:      If ``engine="chromium"`` but Playwright not installed.
    """
    html_path = Path(source_path)
    if not html_path.is_file():
        raise FileNotFoundError(f"HTML file not found: {source_path}")
    if engine not in ('weasyprint', 'chromium'):
        raise ValueError(f"Unknown engine: {engine!r}. Use 'weasyprint' or 'chromium'.")
    _html_theme_palette(theme)

    out_path = Path(output_path)

    # Font check (warn via logger, not stdout)
    fonts = _check_fonts()
    missing = [n for n, p in fonts.items() if p is None]
    if missing:
        logger.warning("Missing font(s): %s. Using system fallback.", ', '.join(missing))
        if engine == "weasyprint" and 'Noto Emoji' in missing:
            logger.info("Emoji will be replaced with text equivalents.")
    else:
        logger.info("All fonts found (Noto Sans SC + Noto Emoji)")

    with _atomic_pdf_output(out_path) as temporary_output:
        if engine == "weasyprint":
            _convert_html_to_pdf_weasyprint(
                html_path, temporary_output, fonts,
                page_numbers=page_numbers,
                compat_css=weasy_compat_css,
                theme=theme,
            )
        elif engine == "chromium":
            _convert_html_to_pdf_chromium(
                html_path, temporary_output, fonts,
                page_numbers=page_numbers,
                theme=theme,
            )


def convert_pdf_to_text(source_path: str) -> str:
    """Extract plain text from a born-digital PDF using PyMuPDF.

    Only works with born-digital PDFs (text that can be selected/copied).
    Scanned-image PDFs will return an empty string; use ``ocr_document``
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
