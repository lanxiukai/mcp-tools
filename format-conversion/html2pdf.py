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

import os
import re
import sys
from pathlib import Path
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
        available[name] = path if os.path.isfile(path) else None
    return available


# ── CSS injection ──

def _build_injected_css(fonts_available):
    """Build CSS to inject: explicit font loading, emoji span, page numbers."""

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

    emoji_stack = []
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


# ── Emoji handling ──

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


def _process_emoji(html_text, has_emoji_font):
    """Wrap emoji in .emoji spans, or replace with text if font missing."""
    if has_emoji_font:
        return _EMOJI_RE.sub(lambda m: f'<span class="emoji">{m.group()}</span>', html_text)
    else:
        for emoji, text in _EMOJI_TEXT_MAP.items():
            html_text = html_text.replace(emoji, text)
        return html_text


# ── Main ──

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(f"Usage: {sys.argv[0]} <input.html> [output.pdf]")
        print(__doc__)
        sys.exit(0)

    html_path = Path(sys.argv[1])
    if not html_path.exists():
        print(f"Error: file not found: {html_path}")
        sys.exit(1)

    pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else html_path.with_suffix('.pdf')

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

    html_text = html_path.read_text(encoding='utf-8')

    # Process emoji (wrap or replace)
    html_text = _process_emoji(html_text, fonts['Noto Emoji'] is not None)

    # Inject font CSS + page numbers before </head>
    inject = _build_injected_css(fonts)
    if '</head>' in html_text:
        html_text = html_text.replace('</head>', inject, 1)
    else:
        html_text = inject + html_text

    print(f"Converting: {html_path} → {pdf_path}")
    HTML(string=html_text, base_url=str(html_path.parent)).write_pdf(str(pdf_path))
    print(f"Done: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
