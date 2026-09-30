"""Inspect final SVG geometry, without trusting embedded layout metadata."""

from __future__ import annotations

from collections import Counter
import re

from defusedxml import ElementTree as SafeET

from .geometry import Rect, intersects
from .runtime import MAX_SVG_BYTES, NS, font_info, missing_glyphs

ALLOWED_TAGS = {
    "svg",
    "g",
    "defs",
    "style",
    "title",
    "desc",
    "metadata",
    "marker",
    "path",
    "rect",
    "circle",
    "ellipse",
    "text",
    "tspan",
    "use",
    "line",
    "polyline",
    "polygon",
    "clipPath",
    "linearGradient",
    "radialGradient",
    "stop",
}


def safe_svg(markup: str):
    if len(markup.encode()) > MAX_SVG_BYTES:
        raise ValueError("SVG input exceeds 16 MiB")
    root = SafeET.fromstring(markup)
    if root.tag != f"{{{NS}}}svg":
        raise ValueError("Expected a namespaced SVG root")
    if sum(1 for _ in root.iter()) > 50000:
        raise ValueError("SVG input exceeds 50,000 elements")
    for item in root.iter():
        local = item.tag.removeprefix(f"{{{NS}}}")
        if local not in ALLOWED_TAGS:
            raise ValueError(f"Unsupported SVG element: {local}")
        for key, value in item.attrib.items():
            attr = key.rsplit("}", 1)[-1].lower()
            if attr.startswith("on"):
                raise ValueError("SVG event handlers are not allowed")
            if attr == "href" and not value.startswith("#"):
                raise ValueError("Only local fragment references are allowed")
            if attr in {
                "style",
                "fill",
                "stroke",
                "filter",
                "clip-path",
                "marker-start",
                "marker-mid",
                "marker-end",
            }:
                check_css(value)
        if local == "style":
            check_css(item.text or "")
    return root


def check_css(css: str):
    if "\\" in css or "@" in css or re.search(r"expression\s*\(", css, re.I):
        raise ValueError("CSS escapes, at-rules, and expressions are not supported")
    for reference in re.findall(r"url\s*\(([^)]*)\)", css, re.I):
        if not reference.strip().strip("\"'").startswith("#"):
            raise ValueError("External SVG/CSS resources are not allowed")


GEOMETRY_JS = r"""markup => {
  document.body.replaceChildren();
  const xml = new DOMParser().parseFromString(markup, 'image/svg+xml');
  const root = document.importNode(xml.documentElement, true);
  document.body.appendChild(root);
  const inverse = root.getCTM().inverse();
  function box(el) {
    const b = el.getBBox(), m = inverse.multiply(el.getCTM());
    const corners = [[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]]
      .map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));
    const x=Math.min(...corners.map(p=>p.x)), y=Math.min(...corners.map(p=>p.y));
    return {x,y,w:Math.max(...corners.map(p=>p.x))-x,h:Math.max(...corners.map(p=>p.y))-y};
  }
  function record(el, index) {return {id:el.id || `element-${index}`, ...box(el)};}
  const visible = el=>!el.closest('defs,marker,clipPath') && getComputedStyle(el).display !== 'none';
  const nodes = [...root.querySelectorAll('.diagram-node')].map(record);
  const labels = [...root.querySelectorAll('.diagram-label')].map((el,i)=>({...record(el,i),owner:el.dataset.owner||''}));
  const groups = [...root.querySelectorAll('.diagram-group')].map(el=>({...record(el,0),members:(el.dataset.members||'').split(' ')}));
  const edges = [...root.querySelectorAll('.diagram-edge')].map(el=>{
    const m = inverse.multiply(el.getCTM());
    return {id:el.id,source:el.dataset.source,target:el.dataset.target,d:el.getAttribute('d'),matrix:[m.a,m.b,m.c,m.d,m.e,m.f]};
  });
  const texts = [...root.querySelectorAll('text')].filter(visible).map((el,i)=>{
    const style=getComputedStyle(el);
    return {...record(el,i),text:el.textContent,family:style.fontFamily,weight:style.fontWeight};
  });
  const view = root.viewBox.baseVal;
  const canvas = view.width ? {x:view.x,y:view.y,w:view.width,h:view.height} : {x:0,y:0,w:root.width.baseVal.value,h:root.height.baseVal.value};
  const shapes = [...root.querySelectorAll('rect,circle,ellipse,path,line,polyline,polygon,text,use')]
    .filter(visible).filter(el=>!el.closest('.diagram-label')).map(record);
  const viewport = root.getBoundingClientRect();
  return {canvas,pixels:{width:viewport.width,height:viewport.height},nodes,labels,groups,edges,texts,shapes};
}"""


def path_segments(d: str, matrix: list[float]):
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d)
    if any(
        token.isalpha() and token.upper() not in {"M", "L", "H", "V", "Z"}
        for token in tokens
    ):
        return None
    points, segments = [], []
    index, command, cursor, first = 0, "", (0.0, 0.0), (0.0, 0.0)
    a, b, c, dd, e, f = matrix

    def transform(point):
        return a * point[0] + c * point[1] + e, b * point[0] + dd * point[1] + f

    try:
        while index < len(tokens):
            if tokens[index].isalpha():
                command = tokens[index]
                index += 1
            kind = command.upper()
            relative = command.islower()
            if kind == "Z":
                point = first
                command = ""
            elif kind in {"M", "L"}:
                point = (float(tokens[index]), float(tokens[index + 1]))
                index += 2
                if relative:
                    point = point[0] + cursor[0], point[1] + cursor[1]
            elif kind == "H":
                point = (
                    float(tokens[index]) + (cursor[0] if relative else 0),
                    cursor[1],
                )
                index += 1
            elif kind == "V":
                point = (
                    cursor[0],
                    float(tokens[index]) + (cursor[1] if relative else 0),
                )
                index += 1
            else:
                return None
            if kind != "M":
                segments.append((transform(cursor), transform(point)))
            else:
                first = point
                command = "l" if relative else "L"
            cursor = point
            points.append(point)
    except (ValueError, IndexError):
        return None
    return segments


def crosses_box(a, b, box: Rect) -> bool:
    """Open-interior segment/AABB intersection, including transformed edges."""
    box = box.inflate(-0.5)
    lower, upper = 0.0, 1.0
    for start, delta, minimum, maximum in (
        (a[0], b[0] - a[0], box.x, box.right),
        (a[1], b[1] - a[1], box.y, box.bottom),
    ):
        if abs(delta) < 1e-9:
            if not minimum < start < maximum:
                return False
        else:
            lo, hi = sorted(((minimum - start) / delta, (maximum - start) / delta))
            lower, upper = max(lower, lo), min(upper, hi)
            if lower >= upper:
                return False
    return lower < upper


def inspect_markup(markup: str, page) -> dict:
    root = safe_svg(markup)
    issues = []

    def issue(code, ids, message):
        issues.append({"code": code, "elements": ids, "message": message})

    identifiers = Counter(item.get("id") for item in root.iter() if item.get("id"))
    for identifier, count in identifiers.items():
        if count > 1:
            issue("duplicate_id", [identifier], f"ID occurs {count} times")
    for item in root.iter():
        refs = []
        for key, value in item.attrib.items():
            if key.rsplit("}", 1)[-1] == "href" and value.startswith("#"):
                refs.append(value[1:])
            refs.extend(re.findall(r"url\(\s*['\"]?#([^)'\"\s]+)", value))
        for target in refs:
            if target not in identifiers:
                issue(
                    "missing_reference",
                    [item.get("id", "anonymous")],
                    f"Missing SVG reference #{target}",
                )
    geometry = page.evaluate(GEOMETRY_JS, markup)
    canvas = Rect(**geometry["canvas"])
    pixel_width, pixel_height = geometry["pixels"].values()
    if not (
        0 < pixel_width <= 8192
        and 0 < pixel_height <= 8192
        and pixel_width * pixel_height <= 32000000
        and canvas.w > 0
        and canvas.h > 0
    ):
        issue(
            "canvas_size",
            [],
            "Canvas must be positive, at most 8192 per side and 32 million pixels",
        )

    def rect(record):
        return Rect(*(record[key] for key in ("x", "y", "w", "h")))

    def inside(inner, outer, tolerance=1):
        return (
            inner.x >= outer.x - tolerance
            and inner.y >= outer.y - tolerance
            and inner.right <= outer.right + tolerance
            and inner.bottom <= outer.bottom + tolerance
        )

    nodes = {record["id"]: rect(record) for record in geometry["nodes"]}
    for index, (name, box) in enumerate(nodes.items()):
        for other, other_box in list(nodes.items())[index + 1 :]:
            if intersects(box, other_box):
                issue("node_overlap", [name, other], "Node bounds overlap")
    for record in geometry["labels"]:
        owner = record["owner"]
        if owner in nodes and not inside(rect(record), nodes[owner].inflate(-10)):
            issue(
                "label_overflow",
                [record["id"], owner],
                "Label exceeds the node's text area",
            )
        for name, box in nodes.items():
            if name != owner and intersects(rect(record), box):
                issue(
                    "label_node_overlap",
                    [record["id"], name],
                    "Label overlaps another node",
                )
    for index, record in enumerate(geometry["labels"]):
        for other in geometry["labels"][index + 1 :]:
            if intersects(rect(record), rect(other)):
                issue(
                    "label_overlap",
                    [record["id"], other["id"]],
                    "Visible label bounds overlap",
                )
    for record in geometry["shapes"] + geometry["labels"]:
        if not inside(rect(record), canvas, 2):
            issue(
                "out_of_bounds",
                [record["id"]],
                "Visible geometry extends outside the viewBox",
            )
    for edge in geometry["edges"]:
        segments = path_segments(edge["d"], edge["matrix"])
        if segments is None:
            issue(
                "unsupported_edge_path",
                [edge["id"]],
                "Only M/L/H/V/Z paths receive collision checks",
            )
            continue
        for name, box in nodes.items():
            if name not in {edge["source"], edge["target"]} and any(
                crosses_box(a, b, box) for a, b in segments
            ):
                issue("edge_node_overlap", [edge["id"], name], "Edge crosses a node")
        for label in geometry["labels"]:
            if label["owner"] != edge["id"] and any(
                crosses_box(a, b, rect(label)) for a, b in segments
            ):
                issue(
                    "edge_label_overlap",
                    [edge["id"], label["id"]],
                    "Edge crosses a label",
                )
    for group in geometry["groups"]:
        for name, box in nodes.items():
            if name not in group["members"] and intersects(rect(group), box):
                issue(
                    "group_node_overlap",
                    [group["id"], name],
                    "Group contains or overlaps a non-member node",
                )
    font_checks = {}
    for record in geometry["texts"]:
        family = record["family"].split(",")[0].strip(" \"'")
        key = (family, 700 if int(record["weight"]) >= 600 else 400)
        if key not in font_checks:
            try:
                font_checks[key] = font_info(*key)
            except (ValueError, RuntimeError) as error:
                font_checks[key] = None
                issue("font_unavailable", [record["id"]], str(error))
        font = font_checks[key]
        if font and (missing := missing_glyphs(record["text"], font)):
            issue(
                "missing_glyphs",
                [record["id"]],
                "Missing glyphs: " + "".join(missing)[:80],
            )
    return {
        "status": "ok" if not issues else "issues_found",
        "coverage": "diagram_geometry"
        if nodes
        else "generic_svg_bounds_fonts_references",
        "canvas": geometry["canvas"],
        "counts": {
            "nodes": len(nodes),
            "edges": len(geometry["edges"]),
            "labels": len(geometry["labels"]),
            "issues": len(issues),
        },
        "issues": issues[:200],
        "issues_omitted": max(0, len(issues) - 200),
        "limitations": "Checks geometry and selected-font coverage, not model semantics, color perception, or overall readability. Generic SVGs need diagram classes/IDs for node and edge checks.",
    }
