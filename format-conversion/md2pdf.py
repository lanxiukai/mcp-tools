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

import os
import sys
import re
from pathlib import Path
from markdown_it import MarkdownIt
from weasyprint import HTML


# ── Font discovery ──

def _check_fonts():
    """Check which fonts are available. Returns dict of font_name → file_path or None."""
    home = os.path.expanduser('~')
    fonts = {
        'Noto Sans SC':  os.path.join(home, '.local/share/fonts/NotoSansSC-Regular.ttf'),
        'Noto Emoji':    os.path.join(home, '.local/share/fonts/NotoEmoji-Regular.ttf'),
    }
    available = {}
    for name, path in fonts.items():
        if os.path.isfile(path):
            available[name] = path
        else:
            available[name] = None
    return available


# ── CSS builder ──

def build_css(fonts_available):
    """Build CSS. Emoji font isolated in .emoji spans; degrades gracefully if fonts missing."""

    font_rules = []
    body_stack = ['sans-serif']

    # Chinese font
    if fonts_available['Noto Sans SC']:
        font_rules.append(f"""@font-face {{
    font-family: 'Noto Sans SC';
    src: url('file://{fonts_available["Noto Sans SC"]}') format('truetype');
}}""")
        body_stack.insert(0, "'Noto Sans SC'")

    body_stack.insert(0, "'DejaVu Sans'" if fonts_available['Noto Sans SC'] else "'DejaVu Sans'")

    body_font = ', '.join(body_stack)

    # Emoji font (only used in .emoji spans)
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


# ── Emoji handling ──

_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF'
    '\U00002600-\U000027BF'
    '\U0000FE0F'
    '\U0000200D'
    ']'
)

# Fallback table for emoji → text (used when emoji font is unavailable)
_EMOJI_TEXT_MAP = {
    '📅': '[日历]', '🔔': '[铃]', '☀️': '[太阳]', '🏃': '[跑]',
    '📚': '[书]', '🍽️': '[餐]', '💻': '[电脑]', '🌙': '[月亮]',
    '📖': '[书]', '📵': '[关机]', '🛏️': '[床]', '🟢': '[绿]',
    '🟡': '[黄]', '🔴': '[红]', '🌜': '[月亮]',
    '⭐': '★', '✅': '✔', '❌': '✘',
    '💡': '●', '🎯': '◎', '👍': '☑',
    '🆓': 'free', '💰': '$',
}


def _process_body(body, has_emoji_font):
    """Post-process HTML body: colorize stars, wrap emoji if font available."""
    body = re.sub(r'★+', lambda m: f'<span class="star">{m.group()}</span>', body)

    if has_emoji_font:
        body = _EMOJI_RE.sub(lambda m: f'<span class="emoji">{m.group()}</span>', body)
    else:
        for emoji, text in _EMOJI_TEXT_MAP.items():
            body = body.replace(emoji, text)

    return body


# ── Main ──

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(f"Usage: {sys.argv[0]} <input.md> [output.pdf]")
        print(__doc__)
        sys.exit(0)

    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print(f"Error: file not found: {md_path}")
        sys.exit(1)

    pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else md_path.with_suffix('.pdf')

    # ── Font check ──
    fonts = _check_fonts()
    missing = [n for n, p in fonts.items() if p is None]
    if missing:
        print(f"⚠  Missing font(s): {', '.join(missing)}")
        print(f"   Expected in ~/.local/share/fonts/ — using system fallback.")
        if 'Noto Emoji' in missing:
            print("   Emoji will be replaced with text equivalents.")
    else:
        print("✓  All fonts found (Noto Sans SC + Noto Emoji)")

    # ── Read & preprocess markdown ──
    text = md_path.read_text(encoding='utf-8')

    # Apply emoji→text fallback early (used regardless of font availability)
    for emoji, replacement in _EMOJI_TEXT_MAP.items():
        text = text.replace(emoji, replacement)

    # Parse markdown → HTML
    md = MarkdownIt('commonmark', {'breaks': True, 'html': True})
    md.enable(['table', 'strikethrough'])
    body = md.render(text)

    # Post-process (star color, emoji wrapping)
    body = _process_body(body, fonts['Noto Emoji'] is not None)

    # ── Render ──
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{build_css(fonts)}</style>
</head>
<body>
{body}
</body>
</html>"""

    print(f"Converting: {md_path} → {pdf_path}")
    HTML(string=html).write_pdf(str(pdf_path))
    print(f"Done: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
