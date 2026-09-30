"""Exercise compact authoring, safe edits, previews, and real ELK geometry."""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image
import pytest
from pydantic import ValidationError

from svg_diagram.artifacts import image_bytes, read_diagram
from svg_diagram.editing import Change, apply_changes, update
from svg_diagram.inspection import inspect_markup
from svg_diagram.models import Diagram
from svg_diagram.renderer import compile_diagram, render
from svg_diagram.runtime import NS, svg_bytes
from svg_diagram.templates import PARAMETERS, build, create


@pytest.fixture
def elk_ready():
    root = Path(__file__).resolve().parents[2]
    if not (root / "svg-diagram/node_modules/elkjs/package.json").is_file():
        pytest.skip("Restore SVG Diagram's locked ELK runtime")


@pytest.mark.parametrize("name", list(PARAMETERS))
@pytest.mark.parametrize("layout", ["grid", "elk"])
def test_templates_have_clean_geometry(name, layout, svg_page, elk_ready):
    spec = build(name, {"language": "zh"}, layout=layout)
    root, geometry = compile_diagram(spec, svg_page)
    report = inspect_markup(svg_bytes(root).decode(), svg_page)
    assert report["issues"] == []
    assert len(geometry["nodes"]) == len(spec.nodes)
    if name == "tensor-stack":
        tensor = root.find(f"{{{NS}}}g[@id='node-tensor-1']")
        assert len(tensor.findall(f"{{{NS}}}rect")) == 3


@pytest.mark.parametrize(
    "name,parameters",
    [
        ("residual", {"layers": 100}),
        ("attention", {"model_dim": 513, "heads": 8}),
        ("attention", {"head": 8}),
        ("encoder-decoder", {"channels": [64, -1]}),
        ("tensor-stack", {"shapes": [[3, 32, 32], [64, 16, 16]], "operations": []}),
        ("tensor-stack", {"shapes": [[0, 3]]}),
        ("loss-branches", {"terms": [{"label": "Only one", "symbol": "L"}]}),
    ],
)
def test_template_parameters_are_validated(name, parameters):
    with pytest.raises(ValidationError):
        build(name, parameters)


def test_label_patch_preserves_typography_and_switches_content_type():
    spec = build("residual")
    original = spec.model_dump_json()
    changed = apply_changes(
        spec,
        [
            Change(op="set_label", id="input", values={"latex": "X"}),
            Change(op="set_diagram", values={"layout": {"gap_x": 100}}),
        ],
    )
    assert changed.nodes[0].labels[0].text is None
    assert changed.nodes[0].labels[0].latex == "X"
    assert changed.nodes[0].labels[0].weight == 700
    assert changed.layout.mode == "grid" and changed.layout.gap_x == 100
    assert spec.model_dump_json() == original


def test_changes_validate_the_final_graph_and_do_not_cascade():
    spec = build("residual")
    with pytest.raises(ValidationError, match="missing node"):
        apply_changes(spec, [Change(op="remove_node", id="output")])
    changed = apply_changes(
        spec,
        [
            Change(op="remove_node", id="output"),
            Change(op="remove_edge", id="sum-to-output"),
            Change(
                op="add_edge",
                id="new-output",
                values={"source": "sum", "target": "new"},
            ),
            Change(
                op="add_node",
                id="new",
                values={"row": 0, "column": 4, "labels": [{"text": "New output"}]},
            ),
        ],
    )
    assert "output" not in {node.id for node in changed.nodes}
    assert changed.edges[-1].target == "new"


@pytest.mark.parametrize(
    "change",
    [
        {"op": "set_node", "id": "missing", "values": {"role": "loss"}},
        {"op": "add_node", "id": "input", "values": {}},
        {"op": "set_node", "id": "input", "values": {"unknown": True}},
        {
            "op": "set_label",
            "id": "input",
            "label_index": 7,
            "values": {"text": "No label"},
        },
    ],
)
def test_invalid_edits_fail(change):
    with pytest.raises(ValueError):
        apply_changes(build("residual"), [Change.model_validate(change)])


def test_elk_rejects_constraints_it_cannot_honor():
    data = build("residual").model_dump()
    data["layout"]["mode"] = "elk"
    with pytest.raises(ValidationError, match="pins"):
        Diagram.model_validate(data)
    data = build("residual", layout="elk").model_dump()
    data["edges"][0]["via"] = [{"x": 20, "y": 20}]
    with pytest.raises(ValidationError, match="waypoints"):
        Diagram.model_validate(data)


def test_elk_preserves_named_ports_labels_and_direction(svg_page, elk_ready):
    spec = Diagram.model_validate(
        {
            "layout": {"mode": "elk", "direction": "TB"},
            "nodes": [
                {
                    "id": "a",
                    "labels": [{"text": "Source"}],
                    "ports": [{"id": "tap", "side": "bottom", "offset": 0.25}],
                },
                {"id": "b", "labels": [{"text": "Target"}]},
            ],
            "edges": [
                {
                    "id": "ab",
                    "source": "a",
                    "target": "b",
                    "source_port": "tap",
                    "target_port": "top",
                    "label": {"latex": "z"},
                }
            ],
        }
    )
    root, geometry = compile_diagram(spec, svg_page)
    source = geometry["nodes"]["a"]
    x, y = geometry["edges"]["ab"][0]
    assert x == pytest.approx(source["x"] + source["width"] * 0.25)
    assert y == pytest.approx(source["y"] + source["height"])
    assert geometry["nodes"]["b"]["y"] > source["y"]
    assert inspect_markup(svg_bytes(root).decode(), svg_page)["issues"] == []


def test_compact_create_and_edit_preserve_artifacts_on_errors(
    tmp_path, svg_browser_ready
):
    path = tmp_path / "model.svg"
    created = create("residual", str(path), preview="file")
    assert "layout" not in created
    assert created["inspection"]["issues"] == []
    png = Path(created["preview_path"])
    before_svg, before_png = path.read_bytes(), png.read_bytes()
    with pytest.raises(ValueError, match="Stale"):
        update(
            str(path),
            [Change(op="set_diagram", values={"theme": "print"})],
            expected_revision="old",
        )
    with pytest.raises(ValueError, match="MathJax"):
        update(
            str(path),
            [Change(op="set_label", id="input", values={"latex": r"\unknownCommand"})],
        )
    assert path.read_bytes() == before_svg and png.read_bytes() == before_png
    updated = update(
        str(path),
        [
            Change(op="set_label", id="input", values={"text": "New input"}),
            Change(op="set_diagram", values={"theme": "print"}),
        ],
        expected_revision=created["revision"],
        preview="file",
    )
    assert updated["revision"] != created["revision"]
    assert read_diagram(str(path))[1].theme == "print"
    assert read_diagram(str(path))[1].nodes[0].labels[0].text == "New input"


def test_preview_failure_does_not_replace_svg(tmp_path, svg_browser_ready, monkeypatch):
    from svg_diagram import renderer

    path = tmp_path / "model.svg"
    create("residual", str(path), preview="file")
    original = path.read_bytes()

    def fail(_):
        raise RuntimeError("Synthetic rasterizer failure")

    monkeypatch.setattr(renderer, "preview_png", fail)
    with pytest.raises(RuntimeError, match="rasterizer"):
        update(str(path), [Change(op="set_diagram", values={"theme": "print"})])
    assert path.read_bytes() == original


def test_concurrent_source_change_aborts_edit(tmp_path, svg_browser_ready, monkeypatch):
    from svg_diagram import renderer

    path = tmp_path / "model.svg"
    created = create("residual", str(path), preview="file")
    old_png = Path(created["preview_path"]).read_bytes()
    external = path.read_bytes() + b"\n<!-- External edit -->\n"
    rasterizer = renderer.preview_png

    def concurrent_edit(markup):
        result = rasterizer(markup)
        path.write_bytes(external)
        return result

    monkeypatch.setattr(renderer, "preview_png", concurrent_edit)
    with pytest.raises(ValueError, match="changed while rendering"):
        update(str(path), [Change(op="set_diagram", values={"theme": "print"})])
    assert path.read_bytes() == external
    assert Path(created["preview_path"]).read_bytes() == old_png


def test_copy_edit_and_inline_image_bounds(tmp_path, svg_browser_ready):
    path, copy = tmp_path / "model.svg", tmp_path / "copy.svg"
    old = create("encoder-decoder", str(path), preview="file")
    original = path.read_bytes()
    result = update(
        str(path),
        [Change(op="set_diagram", values={"title": "Copy"})],
        output_path=str(copy),
        preview="file",
    )
    assert path.read_bytes() == original
    assert old["revision"] != result["revision"]
    with Image.open(io.BytesIO(image_bytes(result["preview_path"]))) as image:
        assert max(image.size) <= 1200


def test_full_response_remains_available(tmp_path, svg_browser_ready):
    spec = build("residual")
    path = tmp_path / "model.svg"
    full = render(spec, str(path))
    assert "layout" in full and "preview_path" not in full
    compact = render(spec, str(path), True, detail="summary")
    assert len(json.dumps(compact)) < len(json.dumps(full))


def test_invalid_preview_destination_preserves_existing_svg(
    tmp_path, svg_browser_ready
):
    path = tmp_path / "model.svg"
    create("residual", str(path), preview="none")
    original = path.read_bytes()
    path.with_suffix(".preview.png").mkdir()
    with pytest.raises(ValueError, match="regular file"):
        update(str(path), [Change(op="set_diagram", values={"theme": "print"})])
    assert path.read_bytes() == original


def test_tensor_fixed_size_cannot_be_smaller_than_stack_depth():
    spec = build("tensor-stack").model_dump()
    spec["nodes"][0]["width"] = 10
    with pytest.raises(ValidationError, match="stack depth"):
        Diagram.model_validate(spec)


def test_round_trip_spec_renders_identical_svg(svg_page):
    spec = build("cvae")
    original, _ = compile_diagram(spec, svg_page)
    restored = Diagram.model_validate_json(spec.model_dump_json())
    rerendered, _ = compile_diagram(restored, svg_page)
    assert svg_bytes(original) == svg_bytes(rerendered)
