from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from svg_diagram.geometry import Rect, layers, route, segment_hits
from svg_diagram.inspection import inspect_markup, safe_svg
from svg_diagram.labels import measure
from svg_diagram.models import Diagram, Label, Span
from svg_diagram.renderer import compile_diagram, render
from svg_diagram.runtime import NS, font_info, publish, svg_bytes
from svg_diagram.service import catalog, render_math_files


def simple_spec(**options):
    return Diagram.model_validate(
        {"nodes": [{"id": "encoder", "labels": [{"text": "Encoder"}]}], **options}
    )


@pytest.mark.parametrize(
    "change",
    [
        {"edges": [{"id": "bad", "source": "encoder", "target": "missing"}]},
        {"nodes": [{"id": "duplicate", "labels": [{"text": "A"}]}] * 2},
        {"layout": {"mode": "manual"}},
        {"layout": {"mode": "grid"}},
        {"theme": "unknown"},
        {"font_family": "Font; url(https://example.com)"},
        {
            "nodes": [
                {"id": "bad", "x": float("nan"), "y": 30, "labels": [{"text": "X"}]}
            ]
        },
    ],
)
def test_invalid_specs_fail_before_render(change):
    with pytest.raises(ValidationError):
        simple_spec(**change)


def test_label_requires_exactly_one_content():
    with pytest.raises(ValidationError):
        Label(text="A", latex="x")
    with pytest.raises(ValidationError):
        Label()


def test_cycle_requires_explicit_feedback():
    spec = simple_spec(
        nodes=[{"id": key, "labels": [{"text": key}]} for key in ("a", "b")],
        edges=[
            {"id": "ab", "source": "a", "target": "b"},
            {"id": "ba", "source": "b", "target": "a"},
        ],
    )
    with pytest.raises(ValueError, match="DAG"):
        layers(spec)
    spec.edges[1].feedback = True
    assert layers(spec) == {"a": 0, "b": 1}


def test_route_avoids_multiple_obstacles_and_has_no_diagonals():
    obstacles = [Rect(40, -10, 40, 70), Rect(110, -60, 40, 70)]
    points = route((0, 0), (200, 0), obstacles)
    assert points[0] == (0, 0) and points[-1] == (200, 0)
    assert len(points) >= 4
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))
    assert not any(
        segment_hits(a, b, box) for a, b in zip(points, points[1:]) for box in obstacles
    )
    with pytest.raises(ValueError, match="inside"):
        route((50, 0), (200, 0), obstacles)


@pytest.mark.parametrize(
    "content",
    [
        "<script>alert(1)</script>",
        '<rect onload="alert(1)"/>',
        '<use href="file:///etc/passwd"/>',
        '<style>@import "https://example.com";</style>',
        '<rect fill="url(https://example.com/a.svg)"/>',
        "<foreignObject/>",
        '<animate attributeName="href"/>',
        "<style>rect{fill:u\\72l(https://example.com)}</style>",
    ],
)
def test_active_or_external_svg_rejected(content):
    with pytest.raises(ValueError):
        safe_svg(f'<svg xmlns="{NS}">{content}</svg>')


def test_xml_entities_rejected():
    with pytest.raises(Exception):
        safe_svg(
            f'<!DOCTYPE svg [<!ENTITY secret SYSTEM "file:///etc/passwd">]><svg xmlns="{NS}"><text>&secret;</text></svg>'
        )


def test_publish_preserves_existing_file_and_checks_create_race(tmp_path):
    path = tmp_path / "existing.svg"
    path.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        publish(path, b"replacement", False)
    assert path.read_bytes() == b"original"
    publish(path, b"replacement", True)
    assert path.read_bytes() == b"replacement"
    assert list(tmp_path.iterdir()) == [path]


def test_missing_font_is_explicit():
    with pytest.raises(ValueError, match="unavailable"):
        font_info("Definitely Absent Diagram Font")


@pytest.mark.parametrize("name", ["cvae", "stylegan2", "vq-vae"])
@pytest.mark.parametrize("theme", ["dark", "print"])
def test_example_geometry_is_clean(name, theme, svg_page):
    spec = Diagram.model_validate(catalog(name)["spec"])
    spec.theme = theme
    root, layout = compile_diagram(spec, svg_page)
    report = inspect_markup(svg_bytes(root).decode(), svg_page)
    assert report["issues"] == []
    assert report["counts"]["nodes"] == len(spec.nodes)
    assert set(layout["edges"]) == {edge.id for edge in spec.edges}
    embedded = root.find(f"{{{NS}}}metadata")
    assert json.loads(embedded.text)["theme"] == theme
    ids = [item.get("id") for item in root.iter() if item.get("id")]
    assert len(ids) == len(set(ids))


def test_mixed_cjk_math_wrapping_and_baselines(svg_page):
    labels = [
        Label(
            spans=[
                Span(content="\u91cd\u5efa "),
                Span(kind="math", content=r"\hat{x}=G_\theta(z_q)"),
            ],
            max_width=115,
        )
    ]
    result = measure(labels, "Noto Sans CJK SC", "#E7EDF5", svg_page)[0]
    assert len(result["lines"]) == 2
    assert result["overflow"] is False
    math = result["lines"][1]["runs"][0]
    assert math["kind"] == "math"
    assert math["ascent"] > 0 and math["descent"] > 0
    assert result["width"] <= 115


def test_inspection_detects_post_render_changes(svg_page):
    root, _ = compile_diagram(simple_spec(), svg_page)
    label = root.find(f".//{{{NS}}}g[@id='label-encoder-0']")
    label.set("transform", "translate(300,0)")
    report = inspect_markup(svg_bytes(root).decode(), svg_page)
    codes = {item["code"] for item in report["issues"]}
    assert {"label_overflow", "out_of_bounds"} <= codes


def test_inspection_detects_edge_through_node(svg_page):
    spec = simple_spec(
        nodes=[
            {"id": name, "x": x, "y": 80, "labels": [{"text": name}]}
            for name, x in [("a", 40), ("b", 260), ("c", 480)]
        ],
        layout={"mode": "manual"},
        edges=[{"id": "ac", "source": "a", "target": "c"}],
    )
    root, _ = compile_diagram(spec, svg_page)
    edge = root.find(f".//{{{NS}}}path[@id='edge-ac']")
    edge.set("d", "M152 112H480")
    report = inspect_markup(svg_bytes(root).decode(), svg_page)
    assert any(
        item["code"] == "edge_node_overlap" and "node-b" in item["elements"]
        for item in report["issues"]
    )


def test_math_errors_do_not_publish_partial_batch(tmp_path, svg_browser_ready):
    with pytest.raises(ValueError, match="MathJax failed"):
        render_math_files(["x", r"\notARealCommand{x}"], str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_math_output_is_self_contained_and_has_baseline(tmp_path, svg_browser_ready):
    result = render_math_files(
        [r"\frac{a}{b}", r"A=\begin{bmatrix}1&2\\3&4\end{bmatrix}"], str(tmp_path)
    )
    for record in result["formulas"]:
        root = safe_svg(Path(record["output_path"]).read_text())
        assert record["width_px"] > 0
        assert 0 < record["baseline_px"] <= record["height_px"]
        ids = {item.get("id") for item in root.iter() if item.get("id")}
        for item in root.iter():
            if ref := item.get("{http://www.w3.org/1999/xlink}href"):
                assert ref[1:] in ids


def test_failed_rerender_preserves_original(tmp_path, svg_browser_ready):
    path = tmp_path / "diagram.svg"
    path.write_text("original")
    spec = simple_spec(
        nodes=[{"id": "math", "labels": [{"latex": r"\notARealCommand"}]}]
    )
    with pytest.raises(ValueError):
        render(spec, str(path), overwrite=True)
    assert path.read_text() == "original"


def test_repeated_math_has_unique_glyph_ids(svg_page):
    spec = simple_spec(
        nodes=[{"id": name, "labels": [{"latex": "x^2"}]} for name in ["a", "b"]]
    )
    root, _ = compile_diagram(spec, svg_page)
    report = inspect_markup(ET.tostring(root, encoding="unicode"), svg_page)
    assert report["issues"] == []


def test_math_prose_without_vector_glyphs_requires_text_span(
    tmp_path, svg_browser_ready
):
    with pytest.raises(ValueError, match="text span"):
        render_math_files(["\\text{" + "\u4e2d\u6587" + "}"], str(tmp_path))
    assert list(tmp_path.iterdir()) == []
