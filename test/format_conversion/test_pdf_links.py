"""Check clickable PDF annotations using both document rendering backends."""

from __future__ import annotations

import os
import sys
from html import escape
from pathlib import Path
from urllib.parse import unquote

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "format-conversion"))

import converter  # noqa: E402


@pytest.fixture(params=["weasyprint", "chromium"])
def engine(request):
    if request.param == "chromium" and os.environ.get("MCP_TOOLS_CHROMIUM_TESTS") != "1":
        pytest.skip("set MCP_TOOLS_CHROMIUM_TESTS=1 to test installed Chromium")
    return request.param


@pytest.mark.parametrize("source_type", ["md", "html"])
def test_pdf_links_keep_their_text_and_destinations(tmp_path, engine, source_type):
    source_dir = tmp_path / "repo-a"
    sibling_dir = tmp_path / "repo-b"
    output_dir = tmp_path / "exports"
    for directory in (source_dir, sibling_dir, output_dir):
        directory.mkdir()
    target = sibling_dir / "guide \u4e2d\u6587.md"
    target.write_text("# Destination\n", encoding="utf-8")
    same_repo_target = source_dir / "guide.md"
    same_repo_target.write_text("# Same repository\n", encoding="utf-8")
    same_repo_pdf = source_dir / "appendix.pdf"
    with fitz.open() as appendix:
        appendix.new_page()
        appendix.save(same_repo_pdf)
    links = {
        "Web": "https://example.com/docs?q=one&lang=en#section",
        "Mail": "mailto:reader@example.com",
        "Relative": "../repo-b/guide%20%E4%B8%AD%E6%96%87.md#section",
        "Absolute": str(target),
        "File": target.as_uri(),
        "SameRepo": "./guide.md",
        "SameRepoPdf": "./appendix.pdf",
        "Missing": "../repo-b/not-created.pdf#page=2",
    }
    expected = {
        **links,
        "Relative": unquote(target.as_uri()) + "#section",
        "Absolute": unquote(target.as_uri()),
        "File": unquote(target.as_uri()),
        "SameRepo": same_repo_target.as_uri(),
        "SameRepoPdf": same_repo_pdf.as_uri(),
        "Missing": (sibling_dir / "not-created.pdf").as_uri() + "#page=2",
    }
    source = source_dir / f"links.{source_type}"
    if source_type == "md":
        content = "\n\n".join(f"[{label}](<{url}>)" for label, url in links.items())
        content += (
            f"\n\n[Reference][local]\n\n[local]: <{target.as_uri()}>\n\n"
            f"<{target.as_uri()}>\n\n"
            "[Internal](#target)\n\n"
            '<h2 id="target" style="break-before: page">Target</h2>\n'
        )
        expected["Reference"] = unquote(target.as_uri())
        expected[unquote(target.as_uri())] = unquote(target.as_uri())
        convert = converter.convert_markdown_to_pdf
    else:
        content = "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        content += "".join(
            f'<p><a href="{escape(url, quote=True)}">{label}</a></p>'
            for label, url in links.items()
        )
        content += (
            '<p><a href="#target">Internal</a></p>'
            '<h2 id="target" style="break-before: page">Target</h2></body></html>'
        )
        convert = converter.convert_html_to_pdf
    source.write_text(content, encoding="utf-8")
    output = output_dir / "links.pdf"

    convert(str(source), str(output), engine=engine)

    # Inspect PDF actions themselves: file URIs may be reported as Launch
    # links by PyMuPDF even when their actual PDF action is /URI.
    actual = {}
    with fitz.open(output) as document:
        for page in document:
            for link in page.get_links():
                label = page.get_textbox(link["from"] + (-1, -1, 1, 1)).strip()
                action_type, uri = document.xref_get_key(link["xref"], "A/URI")
                if action_type == "string":
                    actual[unquote(label)] = unquote(uri)
                else:
                    assert label == "Internal"
                    assert link["page"] == 1
                    actual[label] = "page 2"
    assert actual == {**expected, "Internal": "page 2"}
    assert source.read_text(encoding="utf-8") == content


def test_html_links_respect_authored_base_url(tmp_path, engine):
    source = tmp_path / "links.html"
    source.write_text(
        '<html><head><base href="https://example.com/manual/"></head><body>'
        '<a href="../guide?q=1&amp;lang=en#section">Guide</a></body></html>',
        encoding="utf-8",
    )
    output = tmp_path / "links.pdf"
    converter.convert_html_to_pdf(str(source), str(output), engine=engine)
    with fitz.open(output) as document:
        links = document[0].get_links()
        assert len(links) == 1
        assert links[0]["uri"] == "https://example.com/guide?q=1&lang=en#section"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "vbscript:msgbox(1)", "data:text/html,example",
])
def test_file_link_support_keeps_other_scheme_restrictions(url):
    parser = converter._PdfMarkdownIt("commonmark")
    assert "<a " not in parser.render(f"[Blocked](<{url}>)")


def test_uppercase_file_scheme_is_clickable():
    parser = converter._PdfMarkdownIt("commonmark")
    assert '<a href="FILE:///tmp/guide.md">File</a>' in parser.render(
        "[File](FILE:///tmp/guide.md)"
    )
