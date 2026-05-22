#!/usr/bin/env python3
"""A/B comparison script — WeasyPrint HTML→PDF spacing diagnosis.

Generates 4 PDF variants from one HTML file, each testing a specific hypothesis
for the "extra blank line before 刺激控制" issue:

  A  baseline  — no page numbers (rules out @page injection)
  B  emoji     — emoji/font line-height fix (tests sec-title flex cross-size)
  C  flex      — flex gap + last-child margin fix (tests .two-col flex issues)
  D  outline   — red/blue/green borders to locate the blank space

Usage:
    python scripts/html2pdf_ab_test.py <source.html> [output_dir]
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "format-conversion"))

from converter import _check_fonts, _process_emoji  # noqa: E402
from weasyprint import HTML                         # noqa: E402

# ── compat CSS snippets ──

EMOJI_FIX_CSS = """
.emoji {
  line-height: 1 !important;
  display: inline-block !important;
  vertical-align: -0.1em !important;
}
.sec-title {
  line-height: 1.15 !important;
  row-gap: 0 !important;
}
"""

FLEX_FIX_CSS = """
.two-col {
  column-gap: 10px !important;
  row-gap: 0 !important;
  align-items: flex-start !important;
}
.two-col > .col > :last-child {
  margin-bottom: 0 !important;
}
"""

OUTLINE_CSS = """
.two-col {
  outline: 0.5px solid red !important;
}
.mech-box {
  outline: 0.5px solid blue !important;
}
.sec-title.mech {
  outline: 0.5px solid green !important;
}
"""

# ── variant definitions ──

VARIANTS = [
    {
        "suffix": "A_baseline",
        "label":  "A · 无页码（排除 @page 注入）",
        "page_numbers": False,
        "compat_css":   "",
    },
    {
        "suffix": "B_emoji",
        "label":  "B · emoji line-height 修正",
        "page_numbers": True,
        "compat_css":   EMOJI_FIX_CSS,
    },
    {
        "suffix": "C_flex",
        "label":  "C · flex gap + 末尾 margin 修正",
        "page_numbers": True,
        "compat_css":   FLEX_FIX_CSS,
    },
    {
        "suffix": "D_outline",
        "label":  "D · outline 定位（红=two-col 蓝=mech-box 绿=sec-title）",
        "page_numbers": True,
        "compat_css":   OUTLINE_CSS,
    },
]


# ── CSS builders (mirrors converter.py's _build_injected_css logic) ──

def _build_font_face_css(fonts: dict) -> str:
    rules = []
    if fonts.get("Noto Sans SC"):
        rules.append(f"""@font-face {{
    font-family: 'Noto Sans SC';
    src: url('file://{fonts["Noto Sans SC"]}') format('truetype');
}}""")
    if fonts.get("Noto Emoji"):
        rules.append(f"""@font-face {{
    font-family: 'Noto Emoji';
    src: url('file://{fonts["Noto Emoji"]}') format('truetype');
}}""")
    return "\n".join(rules)


def _build_emoji_css(fonts: dict) -> str:
    stack = []
    if fonts.get("Noto Emoji"):
        stack.append("'Noto Emoji'")
    if fonts.get("Noto Sans SC"):
        stack.append("'Noto Sans SC'")
    stack.append("sans-serif")
    return f".emoji {{ font-family: {', '.join(stack)}; }}"


def _page_font(fonts: dict) -> str:
    return "'Noto Sans SC', sans-serif" if fonts.get("Noto Sans SC") else "sans-serif"


def _build_page_number_css(page_font: str) -> str:
    return f"""@page {{
    @bottom-center {{
        content: counter(page);
        font-family: {page_font};
        font-size: 8pt;
        color: #94a3b8;
    }}
}}"""


def _compose_injected_css(fonts: dict, *, page_numbers: bool = True, compat_css: str = "") -> str:
    parts: list[str] = []
    parts.append(_build_font_face_css(fonts))
    parts.append(_build_emoji_css(fonts))
    if page_numbers:
        parts.append(_build_page_number_css(_page_font(fonts)))
    if compat_css:
        parts.append(compat_css.strip())
    return "\n".join(p for p in parts if p)


def _inject_style_before_head_end(html_text: str, css: str) -> str:
    """Inject <style> block just before </head>."""
    style_tag = f"<style>\n{css}\n</style>\n</head>"
    if "</head>" in html_text:
        return html_text.replace("</head>", style_tag, 1)
    else:
        return style_tag + html_text


# ── conversion ──

def convert_variant(html_path: Path, out_path: Path, fonts: dict, variant: dict) -> None:
    html_text = html_path.read_text(encoding="utf-8")
    html_text = _process_emoji(html_text, fonts["Noto Emoji"] is not None)

    css = _compose_injected_css(
        fonts,
        page_numbers=variant["page_numbers"],
        compat_css=variant["compat_css"],
    )
    html_text = _inject_style_before_head_end(html_text, css)

    HTML(string=html_text, base_url=str(html_path.parent)).write_pdf(str(out_path))


# ── CLI ──

def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <source.html> [output_dir]")
        sys.exit(1)

    html_path = Path(sys.argv[1]).resolve()
    if not html_path.is_file():
        print(f"Error: file not found: {html_path}")
        sys.exit(1)

    out_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else html_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    fonts = _check_fonts()
    missing = [n for n, p in fonts.items() if p is None]
    if missing:
        print(f"⚠  Missing font(s): {', '.join(missing)}. Using system fallback.")
    else:
        print("✓  All fonts found")

    stem = html_path.stem
    print(f"\nConverting '{html_path.name}' — {len(VARIANTS)} variants:\n")
    for variant in VARIANTS:
        out_name = f"{stem}_variant_{variant['suffix']}.pdf"
        out_path = out_dir / out_name
        print(f"  [{variant['suffix'][0]}] {variant['label']}")
        convert_variant(html_path, out_path, fonts, variant)
        kb = out_path.stat().st_size / 1024
        print(f"      → {out_path.name}  ({kb:.0f} KB)\n")

    print("Done. Open the 4 PDFs side by side to compare:")
    for variant in VARIANTS:
        out_name = f"{stem}_variant_{variant['suffix']}.pdf"
        print(f"  {out_dir / out_name}")


if __name__ == "__main__":
    main()
