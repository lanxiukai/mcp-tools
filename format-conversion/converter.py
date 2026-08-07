"""Document format conversion functions.

Provides three public functions for document format conversion:
- convert_markdown_to_pdf: Markdown → PDF (markdown-it-py + WeasyPrint)
- convert_html_to_pdf:     HTML → PDF (WeasyPrint or Chromium, preserves original styles)
- convert_pdf_to_text:     PDF → plain text (PyMuPDF, born-digital only)
"""

import logging
import os
import re
import shutil
import subprocess
from html import escape
from pathlib import Path
from typing import Literal, Optional

import fitz
from markdown_it import MarkdownIt
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
        "page_bg": "#282c34",
        "text": "#abb2bf",
        "page_number": "#5c6370",
        "heading_1": "#61afef",
        "heading_2": "#61afef",
        "heading_3": "#56b6c2",
        "heading_4": "#c678dd",
        "heading_5": "#e5c07b",
        "heading_6": "#98c379",
        "heading_border_1": "#61afef",
        "heading_border_2": "#3e4451",
        "strong": "#e5c07b",
        "link": "#56b6c2",
        "quote_border": "#d19a66",
        "quote_bg": "#2c313c",
        "quote_text": "#d7ba7d",
        "rule": "#3e4451",
        "code_bg": "#21252b",
        "code_text": "#e06c75",
        "pre_bg": "#21252b",
        "pre_border": "#3e4451",
        "pre_text": "#abb2bf",
        "table_border": "#4b5263",
        "table_header_bg": "#3b5268",
        "table_header_text": "#d7dae0",
        "table_stripe": "#2c313a",
        "checkbox_unchecked": "#5c6370",
        "checkbox_checked": "#98c379",
        "accent": "#e5c07b",
        "error_text": "#e06c75",
        "error_bg": "#34262b",
        "error_border": "#7f3f49",
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


def _build_page_number_css(page_font: str) -> str:
    """Build @page rule with @bottom-center page counter."""
    return f"""@page {{
    @bottom-center {{
        content: counter(page);
        font-family: {page_font};
        font-size: 8pt;
        color: #94a3b8;
    }}
}}"""


def _build_injected_css(
    fonts_available: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
    compat_css: str = "",
) -> str:
    """Build CSS to inject into HTML→PDF conversion.

    Returns pure CSS (no ``<style>`` wrapper).  Caller wraps as needed.

    Args:
        fonts_available: Font availability dict from ``_check_fonts()``.
        page_numbers:    Whether to inject ``@page @bottom-center`` page footer.
        compat_css:      Additional CSS to inject (e.g. WeasyPrint compat rules).
    """
    parts: list[str] = []
    parts.append(_build_font_face_css(fonts_available))
    parts.append(_build_emoji_css(fonts_available))
    if page_numbers:
        parts.append(_build_page_number_css(_page_font(fonts_available)))
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
            result_parts.append(svg)
        last_end = m.end()
    result_parts.append(text[last_end:])

    return ''.join(result_parts)


# ── Public API ──

# Re-export engine type for MCP server / external callers
HtmlPdfEngine = Literal["weasyprint", "chromium"]


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
                     (dark screen-reading theme).

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
    md = MarkdownIt('commonmark', {'breaks': True, 'html': True})
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
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8',
        ) as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            convert_html_to_pdf(
                tmp_path, str(out_path),
                engine="chromium", page_numbers=False,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    elif engine == "weasyprint":
        logger.info("Converting (WeasyPrint): %s → %s", md_path, out_path)
        HTML(string=html, base_url=str(md_path.parent)).write_pdf(str(out_path))

    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)


# ── HTML→PDF backends ──

def _convert_html_to_pdf_weasyprint(
    html_path: Path,
    out_path: Path,
    fonts: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
    compat_css: str = "",
) -> None:
    """HTML→PDF via WeasyPrint (default backend)."""
    html_text = html_path.read_text(encoding='utf-8')

    # Process emoji (wrap in .emoji spans or replace with text)
    html_text = _process_emoji(html_text, fonts['Noto Emoji'] is not None)

    css = _build_injected_css(fonts, page_numbers=page_numbers, compat_css=compat_css)
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
) -> None:
    """HTML→PDF via Playwright/Chromium (sync wrapper for asyncio)."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — call async version directly via asyncio.run
        asyncio.run(_convert_html_to_pdf_chromium_async(
            html_path, out_path, fonts, page_numbers=page_numbers,
        ))
        return

    # Running inside an asyncio loop (MCP server) — use run_coroutine_threadsafe
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            asyncio.run,
            _convert_html_to_pdf_chromium_async(
                html_path, out_path, fonts, page_numbers=page_numbers,
            ),
        )
        future.result()


async def _convert_html_to_pdf_chromium_async(
    html_path: Path,
    out_path: Path,
    fonts: dict[str, Optional[str]],
    *,
    page_numbers: bool = True,
) -> None:
    """HTML→PDF via Playwright/Chromium (async implementation)."""
    if not _check_playwright():
        raise RuntimeError(
            "Chromium engine requires Playwright. "
            "Install with: pip install playwright && playwright install chromium"
        )

    from playwright.async_api import async_playwright

    # Build CSS injection (no emoji processing — Chrome handles emoji natively)
    css_parts: list[str] = []
    css_parts.append(_build_font_face_css(fonts))
    if page_numbers:
        css_parts.append(_build_page_number_css(_page_font(fonts)))
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
        page = await browser.new_page()
        try:
            await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            await page.emulate_media(media="print")
            await page.add_style_tag(content=injected_css)

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
) -> None:
    """Convert an HTML file to PDF, preserving original styles.

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

    Raises:
        FileNotFoundError: If source_path does not exist.
        RuntimeError:      If ``engine="chromium"`` but Playwright not installed.
    """
    html_path = Path(source_path)
    if not html_path.is_file():
        raise FileNotFoundError(f"HTML file not found: {source_path}")

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

    if engine == "weasyprint":
        _convert_html_to_pdf_weasyprint(
            html_path, out_path, fonts,
            page_numbers=page_numbers,
            compat_css=weasy_compat_css,
        )
    elif engine == "chromium":
        _convert_html_to_pdf_chromium(
            html_path, out_path, fonts,
            page_numbers=page_numbers,
        )
    else:
        raise ValueError(f"Unknown engine: {engine!r}. Use 'weasyprint' or 'chromium'.")


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
