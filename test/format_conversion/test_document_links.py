"""Conversion policy and actual PDF annotation regression coverage."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "format-conversion"))
import converter  # noqa: E402
from document_links import inspect_pdf_links, prepare_links, record_pdf_source  # noqa: E402


def pdf(path):
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), path.stem)
        document.save(path)
    return path


def entry(report):
    return report["links"][0]


@pytest.mark.parametrize("edition", ["guide-dark.pdf", "guide (print).pdf"])
def test_multiple_editions_require_selection_and_preserve_existing_output(tmp_path, edition):
    source = tmp_path / "index.md"
    source.write_text("[Guide](guide.md)")
    (tmp_path / "guide.md").write_text("# Guide")
    pdf(tmp_path / "guide.pdf")
    pdf(tmp_path / edition)
    assert entry(inspect_pdf_links(str(source)))["status"] == "ambiguous"
    output = tmp_path / "index.pdf"
    output.write_bytes(b"previous-output")
    with pytest.raises(ValueError, match="Ambiguous PDF targets"):
        converter.convert_markdown_to_pdf(str(source), str(output))
    assert output.read_bytes() == b"previous-output"
    report = inspect_pdf_links(str(source), {"guide.md": edition})
    assert entry(report)["pdf_path"] == str(tmp_path / edition)


def test_renamed_pdf_provenance_and_multiple_recorded_outputs(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("[Guide](guide.html)")
    guide = tmp_path / "guide.html"
    guide.write_text("<p>Guide</p>")
    first = pdf(tmp_path / "custom-edition.pdf")
    record_pdf_source(first, guide, first)
    assert entry(inspect_pdf_links(str(source)))["pdf_path"] == str(first)
    second = pdf(tmp_path / "another-edition.pdf")
    record_pdf_source(second, guide, second)
    assert entry(inspect_pdf_links(str(source)))["status"] == "ambiguous"


def test_persistent_mapping_supports_renamed_pdf_and_explicit_source(tmp_path):
    source = tmp_path / "index.html"
    source.write_text('<a href="guide.md">Guide</a>')
    (tmp_path / "guide.md").write_text("# Guide")
    custom = pdf(tmp_path / "edition.pdf")
    (tmp_path / ".pdf-links.json").write_text(json.dumps({"guide.md": custom.name}))
    assert entry(inspect_pdf_links(str(source)))["pdf_path"] == str(custom)
    report = inspect_pdf_links(str(source), {"guide.md": None})
    assert entry(report)["status"] == "source-selected"
    assert entry(report)["target_uri"].endswith("guide.md")


def test_same_stem_markdown_and_html_require_review(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("[Guide](guide.md)")
    (tmp_path / "guide.md").write_text("# Guide")
    (tmp_path / "guide.html").write_text("<p>Different guide</p>")
    pdf(tmp_path / "guide.pdf")
    assert entry(inspect_pdf_links(str(source)))["status"] == "ambiguous"


def test_html_base_and_quoted_attributes_do_not_corrupt_markup(tmp_path):
    nested = tmp_path / "other"
    nested.mkdir()
    (nested / "guide.md").write_text("# Guide")
    pdf(nested / "guide.pdf")
    html = '''<base href="other/"><a title=" href='fake'" href="guide.md?q=1&amp;b=2#section">Guide</a><script>const x = '<a href="guide.md">';</script>'''
    rewritten, report = prepare_links(html, tmp_path / "index.html")
    assert '''title=" href='fake'"''' in rewritten
    assert "const x = '<a href=\"guide.md\">';" in rewritten
    assert entry(report)["target_uri"] == (nested / "guide.pdf").as_uri() + "?q=1&b=2#section"
    external = '<base href="https://example.com/"><a href="guide.md">Guide</a>'
    assert prepare_links(external, tmp_path / "index.html") == (external, {
        "source_path": str(tmp_path / "index.html"), "link_policy": "prefer-pdf", "requires_selection": False, "links": [],
    })


@pytest.mark.parametrize("extension", ["md", "html"])
@pytest.mark.parametrize("engine", ["weasyprint", "chromium"])
def test_rendered_pdf_targets_and_source_preservation(tmp_path, extension, engine):
    if engine == "chromium" and os.environ.get("MCP_TOOLS_CHROMIUM_TESTS") != "1":
        pytest.skip("Chromium integration is opt-in")
    repo = tmp_path / "repo-a"
    sibling = tmp_path / "repo-b"
    repo.mkdir()
    sibling.mkdir()
    guide = sibling / "guide notes.md"
    guide.write_text("# Guide")
    target = pdf(guide.with_suffix(".pdf"))
    (sibling / "README.md").write_text("# No PDF")
    script = sibling / "example.py"
    script.write_text("raise RuntimeError('This file must never be executed')")
    urls = ["../repo-b/guide%20notes.md#part", "../repo-b/README.md", "../repo-b/example.py", "https://example.com/guide.md"]
    text = "\n\n".join(f"[Link {i}](<{url}>)" for i, url in enumerate(urls)) if extension == "md" else "".join(f'<p><a href="{url}">Link {i}</a></p>' for i, url in enumerate(urls))
    source = repo / f"index.{extension}"
    source.write_text(text)
    output = tmp_path / f"output-{extension}.pdf"
    convert = converter.convert_markdown_to_pdf if extension == "md" else converter.convert_html_to_pdf
    convert(str(source), str(output), engine=engine)
    with fitz.open(output) as document:
        destinations = {
            unquote(document.xref_get_key(link["xref"], "A/URI")[1])
            for page in document for link in page.get_links()
        }
        assert "mcp-tools-links:resolved" in document.metadata["keywords"]
    assert destinations == {
        unquote(target.as_uri()) + "#part", (sibling / "README.md").as_uri(),
        script.as_uri(), "https://example.com/guide.md",
    }
    assert source.read_text() == text


def test_invalid_explicit_mapping_fails_before_conversion(tmp_path):
    (tmp_path / "guide.md").write_text("# Guide")
    source = tmp_path / "index.md"
    source.write_text("[Guide](guide.md)")
    with pytest.raises(ValueError, match="Selected PDF"):
        inspect_pdf_links(str(source), {"guide.md": "missing.pdf"})


def test_reference_links_and_autolinks_are_inspected(tmp_path):
    guide = tmp_path / "guide.md"
    guide.write_text("# Guide")
    pdf(guide.with_suffix(".pdf"))
    source = tmp_path / "index.md"
    source.write_text(f"[Guide][g]\n\n[g]: guide.md\n\n<{guide.as_uri()}>")
    report = inspect_pdf_links(str(source))
    assert len(report["links"]) == 2
    assert all(item["status"] == "selected" for item in report["links"])
