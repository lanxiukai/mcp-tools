"""Document format conversion functions.

Provides three public functions for document format conversion:
- convert_markdown_to_pdf: Markdown → PDF (markdown-it-py + WeasyPrint)
- convert_html_to_pdf:     HTML → PDF (WeasyPrint or Chromium, preserves original styles)
- convert_pdf_to_text:     PDF → plain text (PyMuPDF, born-digital only)
"""

import logging
import os
import re
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

# Match emoji + optional variation selector (FE0F) as a single unit,
# plus ZWJ as a separate token.  This prevents splitting ✈️ into two spans.
_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF]\uFE0F?'
    '|\u200D'
    '|\uFE0F'
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
    font-size: 10pt;
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
}}
h1:first-of-type {{ }}

h2 {{
    font-size: 16pt; font-weight: 700;
    margin-top: 6mm; margin-bottom: 3mm;
    padding-bottom: 1mm;
    border-bottom: 1.5px solid #5b9ab5;
    color: #1f5c72;
    page-break-after: avoid;
}}

h3 {{
    font-size: 13pt; font-weight: 700;
    margin-top: 4mm; margin-bottom: 2mm;
    color: #2b6e89;
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
    color: #5b9ab5;
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
}}
th, td {{
    border: 1px solid #b8cfdb;
    padding: 2mm 3mm; text-align: left; vertical-align: top;
}}
th {{ background: #2c6f8a; color: #fff; font-weight: 700; }}
tr {{ page-break-inside: avoid; }}
tr:nth-child(even) td {{ background: #f2f7fa; }}

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
    color: #7a9eb1;
}}
.task-checkbox.checked {{
    color: #2c6f8a;
    font-weight: bold;
}}

.star {{ color: #c47f2c; font-weight: bold; }}
"""


def _build_font_face_css(fonts_available: dict[str, Optional[str]]) -> str:
    """Build @font-face CSS rules for Noto Sans SC and Noto Emoji."""
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
    return "\n".join(rules)


def _build_emoji_css(fonts_available: dict[str, Optional[str]]) -> str:
    """Build .emoji span font-family CSS."""
    stack: list[str] = []
    if fonts_available['Noto Emoji']:
        stack.append("'Noto Emoji'")
    if fonts_available['Noto Sans SC']:
        stack.append("'Noto Sans SC'")
    stack.append('sans-serif')
    return f".emoji {{ font-family: {', '.join(stack)}; }}"


def _page_font(fonts_available: dict[str, Optional[str]]) -> str:
    """Return page-number font-family string."""
    return "'Noto Sans SC', sans-serif" if fonts_available['Noto Sans SC'] else 'sans-serif'


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
        body_html = _EMOJI_RE.sub(
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
        return _EMOJI_RE.sub(lambda m: f'<span class="emoji">{m.group()}</span>', html_text)
    else:
        for emoji, text in _EMOJI_TEXT_MAP.items():
            html_text = html_text.replace(emoji, text)
        return html_text


# ── Emoji-safe text replacement (ZWJ / skin-tone / code-block aware) ──

# Regex to match characters that indicate an emoji is part of a larger sequence.
# ZWJ (U+200D), skin-tone modifiers (U+1F3FB–U+1F3FF), variation selector-16 (U+FE0F).
_EMOJI_EXTENDER_RE = re.compile('[\u200d\U0001F3FB-\U0001F3FF\ufeff]')


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
            + r'(?![\u200d\U0001F3FB-\U0001F3FF\ufeff])'  # NOT followed by extender
        )
        text = re.sub(pattern, replacement, text)
    return text


# ── Math processing (LaTeX → MathJax SVG) ──

_MATHJAX_NODE_PATH = '/home/lanxiukai/.nvm/versions/node/v24.15.0/lib/node_modules'


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
        var _mj = require("mathjax-full/js/mathjax.js");
        var _tex = require("mathjax-full/js/input/tex.js");
        var _svg = require("mathjax-full/js/output/svg.js");
        var _adp = require("mathjax-full/js/adaptors/liteAdaptor.js");
        var _reg = require("mathjax-full/js/handlers/html.js");
        var _all = require("mathjax-full/js/input/tex/AllPackages.js");
        var adaptor = new _adp.liteAdaptor();
        _reg.RegisterHTMLHandler(adaptor);
        var pkgs = _all.AllPackages.filter(function(p){return p!=="physics"});
        var tex = new _tex.TeX({packages: pkgs, processEscapes: true});
        var svg = new _svg.SVG({fontCache: "local"});
        var doc = _mj.mathjax.document("", {InputJax: tex, OutputJax: svg});
        var chunks = [];
        process.stdin.on("data", function(c){chunks.push(c)});
        process.stdin.on("end", function(){
            var formulas = JSON.parse(Buffer.concat(chunks).toString());
            var results = formulas.map(function(f){
                try {
                    var node = doc.convert(f.latex, {display: f.display});
                    var out = adaptor.innerHTML(node);
                    if (f.display) out = out.replace("<svg", '<svg class="mathjax-block"');
                    else out = out.replace("<svg", '<svg class="mathjax-inline"');
                    return out;
                } catch(e) { return f.latex; }
            });
            process.stdout.write(JSON.stringify(results));
        });
    '''

    env = os.environ.copy()
    env['NODE_PATH'] = _MATHJAX_NODE_PATH

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

    paired = list(enumerate(all_matches))
    paired.sort(key=lambda x: x[1].start())

    result_parts = []
    last_end = 0
    for idx, m in paired:
        result_parts.append(text[last_end:m.start()])
        result_parts.append(rendered[idx])
        last_end = m.end()
    result_parts.append(text[last_end:])

    return ''.join(result_parts)


# ── Public API ──

def convert_markdown_to_pdf(source_path: str, output_path: str) -> None:
    """Convert a Markdown file to a styled PDF.

    Pipeline: markdown-it-py → HTML → WeasyPrint → PDF.
    Includes Chinese fonts, table styling, code blocks, blockquotes,
    page numbers, emoji handling, and checkbox/task-list rendering.

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
<style>
{_build_css(fonts)}
.mathjax-block {{ display: block; margin: 4mm auto; text-align: center; }}
.mathjax-inline {{ display: inline-block; }}
</style>
</head>
<body>
{body}
</body>
</html>"""

    logger.info("Converting: %s → %s", md_path, out_path)
    HTML(string=html).write_pdf(str(out_path))
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

# Re-export engine type for MCP server / external callers
HtmlPdfEngine = Literal["weasyprint", "chromium"]


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

    - ``engine="weasyprint"`` (default): Lightweight, good for simple documents.
      Replaces emoji with font-styled spans.  May not match Chrome pixel-perfectly
      for ``display:flex`` / ``display:grid`` layouts.
    - ``engine="chromium"``: Uses Playwright headless Chromium.  Pixel-identical
      to Chrome Print → Save as PDF.  Supports all modern CSS (flex, grid, etc.).
      Requires: ``pip install playwright && playwright install chromium``.

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
