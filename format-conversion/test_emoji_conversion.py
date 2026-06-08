#!/usr/bin/env python3
"""Test emoji conversion pipeline in markdown-to-pdf tool.

Tests the following scenarios:
  1. Emoji→text mapping (_EMOJI_TEXT_MAP) — replacement correctness
  2. _EMOJI_RE regex coverage — which emojis the regex captures
  3. _process_body() — star coloring + emoji wrapping with font
  4. _process_emoji() — emoji wrapping / fallback without font
  5. End-to-end: markdown → intermediate HTML inspection
  6. End-to-end: markdown → PDF → text extraction check
"""

import json
import os
import re
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

# Add parent for import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from converter import (
    _EMOJI_RE,
    _EMOJI_TEXT_MAP,
    _check_fonts,
    _process_body,
    _process_emoji,
    _process_checkboxes,
    _protect_code_blocks,
    _restore_code_blocks,
    _safe_emoji_replace,
    convert_markdown_to_pdf,
)

# ── Colour helpers ──
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {GREEN}PASS{RESET}  {name}")
    else:
        failed += 1
        print(f"  {RED}FAIL{RESET}  {name}")
        if detail:
            for line in detail.splitlines():
                print(f"         {line}")


# =====================================================================
# Test 1: _EMOJI_TEXT_MAP completeness & correctness
# =====================================================================
def test_text_map():
    print(f"\n{BOLD}=== Test 1: _EMOJI_TEXT_MAP 映射表检查 ==={RESET}")

    # 1a: All keys are single emoji characters (non-empty)
    for emoji, replacement in _EMOJI_TEXT_MAP.items():
        check(
            f"'{emoji}' → '{replacement}' is non-empty",
            len(emoji) > 0 and len(replacement) > 0,
        )

    # 1b: ⭐ → ★ (critical special case)
    check("⭐ → ★ mapping exists", _EMOJI_TEXT_MAP.get("⭐") == "★")

    # 1c: ✅ → ✔
    check("✅ → ✔ mapping exists", _EMOJI_TEXT_MAP.get("✅") == "✔")

    # 1d: ❌ → ✘
    check("❌ → ✘ mapping exists", _EMOJI_TEXT_MAP.get("❌") == "✘")

    # 1e: 💡 → ●
    check("💡 → ● mapping exists", _EMOJI_TEXT_MAP.get("💡") == "●")

    # 1f: 🎯 → ◎
    check("🎯 → ◎ mapping exists", _EMOJI_TEXT_MAP.get("🎯") == "◎")

    # 1g: 👍 → ☑
    check("👍 → ☑ mapping exists", _EMOJI_TEXT_MAP.get("👍") == "☑")

    # 1h: 📅 → [日历]
    check("📅 → [日历] mapping exists", _EMOJI_TEXT_MAP.get("📅") == "[日历]")

    # 1i: 🆓 → free
    check("🆓 → free mapping exists", _EMOJI_TEXT_MAP.get("🆓") == "free")

    # 1j: 💰 → $
    check("💰 → $ mapping exists", _EMOJI_TEXT_MAP.get("💰") == "$")

    # 1k: 🟢 → [绿]
    check("🟢 → [绿] mapping exists", _EMOJI_TEXT_MAP.get("🟢") == "[绿]")

    # 1l: 🟡 → [黄]
    check("🟡 → [黄] mapping exists", _EMOJI_TEXT_MAP.get("🟡") == "[黄]")

    # 1m: 🔴 → [红]
    check("🔴 → [红] mapping exists", _EMOJI_TEXT_MAP.get("🔴") == "[红]")

    print(f"  {CYAN}Total mapped emojis: {len(_EMOJI_TEXT_MAP)}{RESET}")


# =====================================================================
# Test 2: _EMOJI_RE regex coverage
# =====================================================================
def test_emoji_regex():
    print(f"\n{BOLD}=== Test 2: _EMOJI_RE 正则覆盖面 ==={RESET}")

    # 2a: Mapped emojis that match the regex
    # Note: ⭐ (U+2B50) and 🆓 (U+1F193) are known NOT to be in _EMOJI_RE range.
    # This is fine — they are handled via _EMOJI_TEXT_MAP pre-parse replacement,
    # so the regex doesn't need to catch them.
    regex_mapped = [e for e in _EMOJI_TEXT_MAP if _EMOJI_RE.search(e)]
    not_in_regex = set(_EMOJI_TEXT_MAP) - set(regex_mapped)
    check(
        f"Mapped emojis matched by _EMOJI_RE: {len(regex_mapped)}/{len(_EMOJI_TEXT_MAP)}",
        len(not_in_regex) == 2 and not_in_regex == {'⭐', '🆓'},
        detail=f"Not matched: {not_in_regex} (expected: {{'⭐', '🆓'}})"
            if not_in_regex else "",
    )

    # 2b: Non-mapped emojis that match the regex (these would be wrapped in .emoji spans)
    test_emojis = ["😊", "😂", "❤️", "🤍", "😱", "😡", "🐕", "🐈", "🌸", "🌈", "🌟",
                   "🍎", "🍌", "🍇", "🎂", "☕", "🍺", "🚗", "✈️", "🚀",
                   "🏠", "🏫", "🏥", "🔥", "🎉", "🌍", "👋", "🤝"]
    matched_unmapped = [e for e in test_emojis if _EMOJI_RE.search(e)]
    check(
        f"Non-mapped emojis matched by _EMOJI_RE: {len(matched_unmapped)}/{len(test_emojis)}",
        len(matched_unmapped) == len(test_emojis),
        detail=f"Not matched: {set(test_emojis) - set(matched_unmapped)}" if len(matched_unmapped) < len(test_emojis) else "",
    )

    # 2c: ⭐ (U+2B50) is NOT in _EMOJI_RE range (U+2600-U+27BF covers U+2600-27BF,
    # but U+2B50 is in U+2B00-2BFF block). This is fine — ⭐ is handled by text map.
    check(
        "⭐ (U+2B50) NOT in _EMOJI_RE range (handled by text map instead)",
        _EMOJI_RE.search("⭐") is None,
    )

    # 2c2: 🆓 (U+1F193) is NOT in _EMOJI_RE range (U+1F300-U+1FAFF starts too high).
    # Also fine — handled by text map.
    check(
        "🆓 (U+1F193) NOT in _EMOJI_RE range (handled by text map instead)",
        _EMOJI_RE.search("🆓") is None,
    )

    # 2d: Plain text should NOT match
    check(
        "_EMOJI_RE does not match plain ASCII text",
        _EMOJI_RE.search("Hello World 123") is None,
    )

    # 2e: Chinese text should NOT match
    check(
        "_EMOJI_RE does not match Chinese text",
        _EMOJI_RE.search("你好世界") is None,
    )


# =====================================================================
# Test 3: _process_body (markdown→pdf, with font)
# =====================================================================
def test_process_body_with_font():
    print(f"\n{BOLD}=== Test 3: _process_body (有 emoji 字体) ==={RESET}")

    # 3a: ★ wrapped in .star span. _EMOJI_RE now skips ★, so no double-wrapping.
    body = "<p>Important ★ task ★★</p>"
    result = _process_body(body, has_emoji_font=True)
    check(
        "★ wrapped in .star span only (no double-wrapping)",
        '<span class="star">★</span>' in result,
        detail=f"Result: {result}",
    )
    check(
        "★★ wrapped in single .star span, not .emoji",
        '<span class="star">★★</span>' in result
        and 'class="emoji"' not in result,
        detail=f"Result: {result}",
    )

    # 3b: Remaining emoji (not in text map) should be wrapped in .emoji span
    body2 = '<p>Some fire 🔥 and party 🎉 here</p>'
    result2 = _process_body(body2, has_emoji_font=True)
    check(
        "🔥 wrapped in .emoji span",
        '<span class="emoji">🔥</span>' in result2,
        detail=f"Result: {result2}",
    )
    check(
        "🎉 wrapped in .emoji span",
        '<span class="emoji">🎉</span>' in result2,
        detail=f"Result: {result2}",
    )

    # 3c: ★ and emoji combined — ★ now correctly NOT inside .emoji (bug fixed)
    body3 = '<p>★ Star and 🎉 Party</p>'
    result3 = _process_body(body3, has_emoji_font=True)
    check(
        "★ in .star only, 🎉 in .emoji (no nesting, fixed)",
        '<span class="star">★</span>' in result3
        and '<span class="emoji">🎉</span>' in result3
        and '<span class="star"><span class="emoji">' not in result3,
        detail=f"Result: {result3}",
    )

    # 3d: Multiple emojis in sequence
    body4 = '<p>🔥🎉🌟</p>'
    result4 = _process_body(body4, has_emoji_font=True)
    check(
        "Multiple sequential emojis each wrapped",
        result4.count('<span class="emoji">') == 3,
        detail=f"Result: {result4}",
    )


# =====================================================================
# Test 4: _process_body (markdown→pdf, WITHOUT font)
# =====================================================================
def test_process_body_without_font():
    print(f"\n{BOLD}=== Test 4: _process_body (无 emoji 字体) ==={RESET}")

    # In the no-font case, emoji→text mapping happened BEFORE markdown parsing.
    # So by the time _process_body runs, the text already has replacements.
    # But ★ still needs to be colored, and remaining emojis should NOT be wrapped.

    body = "<p>Important ★ task</p>"
    result = _process_body(body, has_emoji_font=False)
    check(
        "★ still wrapped in .star even without emoji font",
        '<span class="star">★</span>' in result,
        detail=f"Result: {result}",
    )

    body2 = "<p>Some fire 🔥 here</p>"
    result2 = _process_body(body2, has_emoji_font=False)
    check(
        "🔥 NOT wrapped in .emoji when font missing",
        '<span class="emoji">🔥</span>' not in result2,
        detail=f"Result: {result2}",
    )
    check(
        "🔥 preserved as-is when font missing",
        "🔥" in result2,
        detail=f"Result: {result2}",
    )


# =====================================================================
# Test 5: _process_emoji (html→pdf, with font)
# =====================================================================
def test_process_emoji_with_font():
    print(f"\n{BOLD}=== Test 5: _process_emoji (HTML→PDF, 有字体) ==={RESET}")

    html = "<p>Hello 🔥 World 🎉</p>"
    result = _process_emoji(html, has_emoji_font=True)
    check(
        "🔥 wrapped in .emoji in HTML",
        '<span class="emoji">🔥</span>' in result,
        detail=f"Result: {result}",
    )
    check(
        "🎉 wrapped in .emoji in HTML",
        '<span class="emoji">🎉</span>' in result,
        detail=f"Result: {result}",
    )
    check(
        "Non-emoji text preserved",
        "Hello" in result and "World" in result,
    )

    # emoji with ZWJ
    html2 = "<p>Family 👨‍👩‍👧‍👦</p>"
    result2 = _process_emoji(html2, has_emoji_font=True)
    check(
        "ZWJ family emoji wrapped in .emoji",
        '<span class="emoji">' in result2,
        detail=f"Result: {result2}",
    )


# =====================================================================
# Test 6: _process_emoji (html→pdf, WITHOUT font / fallback)
# =====================================================================
def test_process_emoji_without_font():
    print(f"\n{BOLD}=== Test 6: _process_emoji (HTML→PDF, 无字体 / 降级) ==={RESET}")

    # 6a: Mapped emoji should be replaced with text
    html = "<p>Task ⭐ is done ✅</p>"
    result = _process_emoji(html, has_emoji_font=False)
    check(
        "⭐ → ★ in fallback mode",
        "★" in result and "⭐" not in result,
        detail=f"Result: {result}",
    )
    check(
        "✅ → ✔ in fallback mode",
        "✔" in result and "✅" not in result,
        detail=f"Result: {result}",
    )

    # 6b: Non-mapped emoji should REMAIN (no .emoji span, no replacement)
    html2 = "<p>Party 🎉 and Fire 🔥</p>"
    result2 = _process_emoji(html2, has_emoji_font=False)
    check(
        "Non-mapped emoji preserved as-is when font missing",
        "🎉" in result2 and "🔥" in result2,
        detail=f"Result: {result2}",
    )
    check(
        "Non-mapped emoji NOT wrapped when font missing",
        '<span class="emoji">' not in result2,
        detail=f"Result: {result2}",
    )

    # 6c: Multiple mapped emojis
    html3 = "💡 Idea 🎯 Goal 👍 Recommend"
    result3 = _process_emoji(html3, has_emoji_font=False)
    check("💡 → ●", "●" in result3 and "💡" not in result3)
    check("🎯 → ◎", "◎" in result3 and "🎯" not in result3)
    check("👍 → ☑", "☑" in result3 and "👍" not in result3)


# =====================================================================
# Test 7: Pre-parse emoji→text replacement simulation
# (replicating lines 419-421 of converter.py)
# =====================================================================
def test_pre_parse_replacement():
    print(f"\n{BOLD}=== Test 7: Markdown 解析前 emoji→text 替换 ==={RESET}")

    md_text = "# Task ⭐\n\n- Done ✅\n- Failed ❌\n- Note 💡\n- Target 🎯\n- Recommend 👍"

    for emoji_char, replacement in _EMOJI_TEXT_MAP.items():
        md_text = md_text.replace(emoji_char, replacement)

    check("⭐ replaced before markdown parse", "⭐" not in md_text)
    check("★ present after replacement", "★" in md_text)
    check("✅ replaced before markdown parse", "✅" not in md_text)
    check("✔ present after replacement", "✔" in md_text)
    check("❌ replaced before markdown parse", "❌" not in md_text)
    check("✘ present after replacement", "✘" in md_text)
    check("💡 replaced before markdown parse", "💡" not in md_text)
    check("● present after replacement", "●" in md_text)
    check("🎯 replaced before markdown parse", "🎯" not in md_text)
    check("◎ present after replacement", "◎" in md_text)
    check("👍 replaced before markdown parse", "👍" not in md_text)
    check("☑ present after replacement", "☑" in md_text)


# =====================================================================
# Test 8: Code blocks preserve emoji (uses new safe replacement)
# =====================================================================
def test_code_block_preservation():
    print(f"\n{BOLD}=== Test 8: 代码块中 emoji 保留 ==={RESET}")

    md_text = textwrap.dedent("""\
    # Title

    ```python
    status = "✅ done"
    print("⭐ star")
    ```

    Regular text ✅ ⭐ here.

    Inline `code ✅` too.

    End.
    """)

    # Apply the new safe pipeline
    text, placeholders = _protect_code_blocks(md_text)
    text = _safe_emoji_replace(text, _EMOJI_TEXT_MAP)
    text = _restore_code_blocks(text, placeholders)

    # Code block emojis should be preserved
    check(
        "✅ preserved in code block (fix verified)",
        '✅' in text,
        detail=f"Code block content: {text}",
    )
    check(
        "⭐ preserved in code block (fix verified)",
        '⭐' in text,
        detail=f"Code block content: {text}",
    )

    # Regular text emojis should be replaced
    check(
        "Standalone ✅ in regular text replaced → ✔",
        '✔' in text,
    )
    check(
        "Standalone ⭐ in regular text replaced → ★",
        '★' in text,
    )

    # Inline code emojis should also be preserved
    check(
        "✅ in inline code `code ✅` preserved",
        '`code ✅`' in text,
    )

    # Verify the text still has valid code block markers
    check("Fenced code block markers preserved", '```python' in text and '```' in text.split('```python')[1] if '```python' in text else False)

    # Standalone emojis outside code blocks were replaced
    check(
        "✔ and ★ appear from non-code standalone replacement",
        "✔" in text and "★" in text,
    )


# =====================================================================
# Test 9: End-to-end — markdown→PDF with actual file
# =====================================================================
def test_end_to_end():
    print(f"\n{BOLD}=== Test 9: 端到端 markdown→PDF 转换 ==={RESET}")

    test_md = Path(__file__).resolve().parent / "test_emoji_markdown.md"
    test_pdf = Path(__file__).resolve().parent / "test_emoji_output.pdf"

    if not test_md.exists():
        check("Test markdown file exists", False, f"Missing: {test_md}")
        return

    try:
        convert_markdown_to_pdf(str(test_md), str(test_pdf))
        check("PDF generated successfully", test_pdf.exists())
        if test_pdf.exists():
            size_kb = test_pdf.stat().st_size / 1024
            print(f"  {CYAN}PDF size: {size_kb:.1f} KB{RESET}")

            # Extract text to verify key replacements
            import fitz
            doc = fitz.open(str(test_pdf))
            pdf_text = ""
            for page in doc:
                pdf_text += page.get_text()
            doc.close()

            check("PDF text contains ★ (star replacement)", "★" in pdf_text)
            check("PDF text contains ✔ (check replacement)", "✔" in pdf_text)
            check("PDF text contains ✘ (cross replacement)", "✘" in pdf_text)
            check("PDF text contains ● (bulb replacement)", "●" in pdf_text)
            check("PDF text contains ◎ (target replacement)", "◎" in pdf_text)
            check("PDF text contains ☑ (thumbsup replacement)", "☑" in pdf_text)
            check("PDF text contains [日历] (calendar replacement)", "[日历]" in pdf_text)
            check("PDF text contains [铃] (bell replacement)", "[铃]" in pdf_text or "[铃]" in pdf_text)
            check("PDF text contains free (free replacement)", "free" in pdf_text)
            check("PDF text contains $ (money replacement)", "$" in pdf_text)
            check("PDF text contains [绿] (green replacement)", "[绿]" in pdf_text)
            check("PDF text contains [黄] (yellow replacement)", "[黄]" in pdf_text)
            check("PDF text contains [红] (red replacement)", "[红]" in pdf_text)
            check("PDF text contains [书] (books replacement)", "[书]" in pdf_text)
            check("PDF text contains [电脑] (computer replacement)", "[电脑]" in pdf_text)

            # Non-mapped emojis (with font, should render as emoji chars)
            check("PDF text contains 😊 (non-mapped emoji rendered)", "😊" in pdf_text)
            check("PDF text contains 🔥 (non-mapped emoji rendered)", "🔥" in pdf_text)
            check("PDF text contains 🚀 (non-mapped emoji rendered)", "🚀" in pdf_text)

            # Code block emojis should NOW be preserved (fix applied)
            if "✅" in pdf_text:
                print(f"  {GREEN}FIX VERIFIED{RESET}: ✅ preserved in PDF (code block protection works)")
            if "❌" in pdf_text:
                print(f"  {GREEN}FIX VERIFIED{RESET}: ❌ preserved in PDF (code block protection works)")

            # ⭐/✅/❌ in code blocks are now preserved, so they should appear.
            # Check that standalone (non-code) instances are still replaced:
            check("⭐ preserved in PDF (from code block, expected after fix)", "⭐" in pdf_text)
            check("✅ preserved in PDF (from code block, expected after fix)", "✅" in pdf_text)
            check("❌ preserved in PDF (from code block, expected after fix)", "❌" in pdf_text)

            # But ★ (the replacement of ⭐ in regular text) should also appear
            check("★ (star replacement) still present in regular text", "★" in pdf_text)

    except Exception as e:
        check("PDF conversion", False, f"Exception: {e}")


# =====================================================================
# Test 10: Font availability detection
# =====================================================================
def test_font_detection():
    print(f"\n{BOLD}=== Test 10: 字体检测 ==={RESET}")

    fonts = _check_fonts()
    check("Noto Sans SC detected", fonts["Noto Sans SC"] is not None)
    check("Noto Emoji detected", fonts["Noto Emoji"] is not None)
    if fonts["Noto Emoji"]:
        print(f"  {CYAN}Emoji font path: {fonts['Noto Emoji']}{RESET}")


# =====================================================================
# Test 11: Checkbox processing
# =====================================================================
def test_checkbox():
    print(f"\n{BOLD}=== Test 11: 复选框处理 {RESET}")

    # Unchecked
    result = _process_checkboxes("<ul>\n<li>[ ] Unchecked task</li>\n</ul>")
    check("Unchecked: ☐ span inserted", 'task-checkbox unchecked">☐</span>' in result)
    check("Unchecked: [ ] removed", "[ ]" not in result)

    # Checked
    result = _process_checkboxes("<ul>\n<li>[x] Checked task</li>\n</ul>")
    check("Checked: ☑ span inserted", 'task-checkbox checked">☑</span>' in result)
    check("Checked: [x] removed", "[x]" not in result)

    # Mixed
    result = _process_checkboxes("<ul>\n<li>[ ] A</li>\n<li>[X] B</li>\n</ul>")
    check("Mixed: both checkboxes processed",
          'unchecked">☐</span>' in result and 'checked">☑</span>' in result)


# =====================================================================
# Test 12: ZWJ sequences & skin-tone variants preserved
# =====================================================================
def test_zwj_skin_tone_preservation():
    print(f"\n{BOLD}=== Test 12: ZWJ 序列 / 肤色变体保护 ==={RESET}")

    # 12a: Thumbs-up with skin tone should remain intact
    text = "👍 👍🏻"
    result = _safe_emoji_replace(text, _EMOJI_TEXT_MAP)
    check(
        "Standalone 👍 → ☑",
        "☑" in result,
        detail=f"Result: {result!r}",
    )
    check(
        "👍🏻 (thumbs up + skin tone) preserved, NOT broken to ☑🏻",
        "👍🏻" in result and "☑🏻" not in result,
        detail=f"Result: {result!r}",
    )

    # 12b: Woman technologist ZWJ sequence preserved
    text2 = "💻 👩‍💻"
    result2 = _safe_emoji_replace(text2, _EMOJI_TEXT_MAP)
    check(
        "Standalone 💻 → [电脑]",
        "[电脑]" in result2,
        detail=f"Result: {result2!r}",
    )
    check(
        "👩‍💻 (woman + ZWJ + computer) preserved, NOT 👩‍[电脑]",
        "👩\u200d💻" in result2 and "👩‍[电脑]" not in result2 and "👩[电脑]" not in result2,
        detail=f"Result: {result2!r}",
    )

    # 12c: Multiple skin tones
    text3 = "👋 👋🏻 👋🏼 👋🏽 👋🏾 👋🏿"
    result3 = _safe_emoji_replace(text3, _EMOJI_TEXT_MAP)
    check(
        "Skin-tone variants all preserved",
        all(emoji in result3 for emoji in ["👋🏻", "👋🏼", "👋🏽", "👋🏾", "👋🏿"]),
        detail=f"Result: {result3!r}",
    )

    # 12d: Family ZWJ sequence
    text4 = "👨‍👩‍👧‍👦"
    result4 = _safe_emoji_replace(text4, _EMOJI_TEXT_MAP)
    check(
        "Family ZWJ sequence preserved untouched (no mapped emojis inside)",
        result4 == "👨\u200d👩\u200d👧\u200d👦",
        detail=f"Result: {result4!r}",
    )

    # 12e: Man scientist ZWJ sequence
    text5 = "👨‍🔬"
    result5 = _safe_emoji_replace(text5, _EMOJI_TEXT_MAP)
    check(
        "Man scientist preserved (🔬 not in map, ZWJ untouched)",
        "👨\u200d🔬" in result5,
        detail=f"Result: {result5!r}",
    )

    # 12f: Emoji with VS16 variation selector
    text6 = "☀️ ☀"
    result6 = _safe_emoji_replace(text6, _EMOJI_TEXT_MAP)
    # ☀️ (with VS16) is in the map → should be replaced
    # ☀ (without VS16) is NOT in the map → should stay (not matched by regex)
    check(
        "☀️ (with VS16, in map) → [太阳]",
        "[太阳]" in result6 and "☀️" not in result6,
        detail=f"Result: {result6!r}",
    )
    check(
        "☀ (without VS16, not in map) preserved",
        "☀" in result6,
        detail=f"Result: {result6!r}",
    )


# =====================================================================
# Main
# =====================================================================
def main():
    global passed, failed
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Markdown→PDF Emoji 转换测试套件{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    # Check fonts first
    fonts = _check_fonts()
    print(f"\n{CYAN}字体状态:{RESET}")
    for name, path in fonts.items():
        status = f"{GREEN}✓{RESET}" if path else f"{RED}✗{RESET}"
        print(f"  {status} {name}")

    test_text_map()
    test_emoji_regex()
    test_process_body_with_font()
    test_process_body_without_font()
    test_process_emoji_with_font()
    test_process_emoji_without_font()
    test_pre_parse_replacement()
    test_code_block_preservation()
    test_zwj_skin_tone_preservation()
    test_font_detection()
    test_checkbox()
    test_end_to_end()

    # ── Summary ──
    print(f"\n{BOLD}{'='*60}{RESET}")
    total = passed + failed
    if failed == 0:
        print(f"{BOLD}{GREEN}  ALL {total} TESTS PASSED{RESET}")
    else:
        print(f"{BOLD}{RED}  {failed}/{total} TESTS FAILED{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
