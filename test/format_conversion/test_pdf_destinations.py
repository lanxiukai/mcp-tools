"""Actual PDF evidence, ambiguity, coordinates, and conversion integration."""

import json
import sys
from pathlib import Path
from urllib.parse import quote, unquote

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "format-conversion"))
import converter  # noqa: E402
from document_links import inspect_pdf_links  # noqa: E402
from pdf_destinations import resolve_pdf_destination  # noqa: E402


def make_pdf(path, titles=("Overview", "Methods"), *, outline=False, rotation=0):
    with fitz.open() as doc:
        for title in titles:
            page = doc.new_page(width=600, height=900)
            page.insert_text((60, 300), title)
            page.set_rotation(rotation)
        if outline:
            doc.set_toc([[1, title, i + 1, 300] for i, title in enumerate(titles)])
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}])
        doc.save(path)
    return path


def test_text_evidence_uses_physical_page_and_pdf_coordinates(tmp_path):
    path = make_pdf(tmp_path / "guide.pdf")
    result = resolve_pdf_destination(str(path), "methods")
    assert result["status"] == "resolved"
    assert result["basis"] == "text-line"
    assert result["page"] == 2
    assert result["page_label"] == "ii"
    dest = json.loads(unquote(result["fragment"]))
    with fitz.open(path) as doc:
        box = doc[1].search_for("Methods")[0]
        point = box.tl * ~doc[1].transformation_matrix
    assert dest[:2] == [1, {"name": "XYZ"}]
    assert dest[2:4] == pytest.approx([point.x, point.y], abs=0.001)
    assert resolve_pdf_destination(str(path), result["fragment"])["page"] == 2
    assert result["uri"].startswith(path.as_uri() + "#")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_outline_matches_slug_and_accounts_for_page_rotation(tmp_path, rotation):
    path = make_pdf(tmp_path / "guide.pdf", ("Introduction", "2. Getting Started"), outline=True, rotation=rotation)
    result = resolve_pdf_destination(str(path), "2-getting-started")
    assert result["basis"] == "outline"
    assert result["page"] == 2
    destination = json.loads(unquote(result["fragment"]))
    # set_toc(top=300) authors the same PDF point regardless of page rotation.
    assert destination[2:4] == pytest.approx([72, 600])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_coordinates_on_cropped_page(tmp_path, rotation):
    path = make_pdf(tmp_path / "cropped.pdf")
    with fitz.open(path) as doc:
        doc[1].set_cropbox(fitz.Rect(20, 50, 580, 850))
        doc[1].set_rotation(rotation)
        doc.saveIncr()
    dest = json.loads(unquote(resolve_pdf_destination(str(path), "Methods")["fragment"]))
    assert dest[2] == 60
    assert 600 < dest[3] < 615


def test_named_destination_preserves_reserved_characters(tmp_path):
    path = make_pdf(tmp_path / "named.pdf")
    name = "Section & 100% + notes"
    with fitz.open(path) as doc:
        doc.xref_set_key(doc.pdf_catalog(), "Names",
                         f"<< /Dests << /Names [{fitz.get_pdf_str(name)} [{doc.page_xref(1)} 0 R /XYZ 60 600 null]] >> >>")
        doc.saveIncr()
    result = resolve_pdf_destination(str(path), "nameddest=" + quote(name, safe=""))
    assert result["basis"] == "named-destination"
    assert result["page"] == 2
    assert result["fragment"] == "nameddest=" + quote(name, safe="")


@pytest.mark.parametrize("outline", [False, True])
def test_duplicate_titles_need_selection(tmp_path, outline):
    path = make_pdf(tmp_path / "guide.pdf", ("Summary", "Summary"), outline=outline)
    result = resolve_pdf_destination(str(path), "Summary")
    assert result["status"] == "ambiguous"
    assert "fragment" not in result
    assert [c["page"] for c in result["candidates"]] == [1, 2]
    assert resolve_pdf_destination(str(path), "Summary", page=2)["page"] == 2


@pytest.mark.parametrize("target", ["Meth", "absent", "page=0", "page=3", "page=1.5",
                                     "page=1&page=2", "nameddest=absent&page=2",
                                     '[2,{"name":"XYZ"},0,600,null]', '[0,{"name":"XYZ"}]'])
def test_unavailable_destinations_are_not_claimed(tmp_path, target):
    path = make_pdf(tmp_path / "guide.pdf")
    result = resolve_pdf_destination(str(path), target)
    assert result["status"] == "not-found"
    assert "fragment" not in result


def test_scan_requires_ocr_and_explicit_page_validation(tmp_path):
    path = make_pdf(tmp_path / "scan.pdf", ("", ""))
    assert resolve_pdf_destination(str(path), "Methods")["status"] == "not-found"
    assert resolve_pdf_destination(str(path), "page=2")["page"] == 2
    with pytest.raises(ValueError, match="physical page"):
        resolve_pdf_destination(str(path), "Methods", page=0)


def test_chinese_title_and_percent_encoded_anchor(tmp_path):
    path = tmp_path / "unicode.pdf"
    title = "\u7b2c\u4e8c\u7ae0 \u65b9\u6cd5"
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 100), title, fontname="china-s")
        doc.save(path)
    result = resolve_pdf_destination(str(path), quote(title))
    assert result["status"] == "resolved"
    assert result["title"].replace(" ", "") == title.replace(" ", "")


@pytest.mark.parametrize("extension", ["md", "html"])
def test_reviewed_mapping_converts_arbitrary_source_anchor_without_editing_source(tmp_path, extension):
    target = make_pdf(tmp_path / "renamed.pdf", ("Introduction", "Methods"))
    (tmp_path / "guide.html").write_text('<h2 id="custom-id">Methods</h2>')
    href = "guide.html#custom-id"
    source = tmp_path / ("index." + extension)
    content = f"[Methods]({href})" if extension == "md" else f'<a href="{href}">Methods</a>'
    source.write_text(content)
    targets = {"guide.html": "renamed.pdf"}
    report = inspect_pdf_links(str(source), targets)
    assert report["requires_selection"]
    assert report["links"][0]["destination"]["status"] == "not-found"
    output = source.with_suffix(".pdf")
    output.write_bytes(b"previous PDF")
    convert = converter.convert_markdown_to_pdf if extension == "md" else converter.convert_html_to_pdf
    with pytest.raises(ValueError, match="Unresolved PDF chapters"):
        convert(str(source), str(output), pdf_targets=targets)
    assert output.read_bytes() == b"previous PDF"
    resolved = resolve_pdf_destination(str(target), "Methods")
    report = convert(str(source), str(output), engine="weasyprint", pdf_targets=targets,
                     pdf_destinations={href: resolved["fragment"]})
    assert report["links"][0]["destination"]["page"] == 2
    with fitz.open(output) as doc:
        link = doc[0].get_links()[0]
        assert doc.xref_get_key(link["xref"], "A/URI")[1] == resolved["uri"]
    assert source.read_text() == content


def test_non_pdf_anchors_and_view_only_parameters_keep_their_meaning(tmp_path):
    make_pdf(tmp_path / "guide.pdf")
    (tmp_path / "guide.html").write_text("<h1>Guide</h1>")
    source = tmp_path / "index.md"
    source.write_text("[Source](guide.html#custom)\n\n[View](guide.pdf#zoom=150)")
    report = inspect_pdf_links(str(source), {"guide.html": None},
                               {"guide.html#custom": "page=2"})
    assert report["links"][0]["target_uri"].endswith("guide.html#custom")
    assert report["links"][1]["target_uri"].endswith("guide.pdf#zoom=150")
    assert not report["requires_selection"]


def test_direct_pdf_fragment_is_resolved_and_missing_pdf_chapter_stops_conversion(tmp_path):
    make_pdf(tmp_path / "guide.pdf")
    source = tmp_path / "index.md"
    source.write_text("[Methods](guide.pdf#methods)")
    report = inspect_pdf_links(str(source))
    assert report["links"][0]["destination"]["page"] == 2
    assert not report["requires_selection"]
    source.write_text("[Missing](missing.pdf#page=2)")
    assert inspect_pdf_links(str(source))["links"][0]["destination"]["status"] == "missing-file"
    with pytest.raises(ValueError, match="Unresolved PDF chapters"):
        converter.convert_markdown_to_pdf(str(source), str(source.with_suffix(".pdf")))
