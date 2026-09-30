"""Compile a typed diagram into editable SVG with measured labels."""

from __future__ import annotations

from pathlib import Path

from .artifacts import Detail, Preview, preview_png, revision, summarize
from .geometry import Rect, intersects, place, port, route, segment_hits, simplify
from .inspection import inspect_markup
from .labels import draw_label, measure
from .models import Diagram, Label
from .runtime import absolute_path, browser_page, element, publish, svg_bytes
from .themes import THEMES


def render(
    spec: Diagram,
    output_path: str,
    overwrite: bool = False,
    *,
    preview: Preview = "none",
    detail: Detail = "full",
    source_check: tuple[Path, str] | None = None,
) -> dict:
    if preview not in {"none", "file", "inline"} or detail not in {"summary", "full"}:
        raise ValueError("Unsupported preview or detail mode")
    destination = absolute_path(output_path, ".svg")
    png_path = destination.with_suffix(".preview.png")
    for path in [destination] + ([png_path] if preview != "none" else []):
        if path.exists() and not path.is_file():
            raise ValueError(f"Output destination is not a regular file: {path}")
    if preview != "none":
        absolute_path(str(png_path), ".png")
        if png_path.exists() and not overwrite:
            raise FileExistsError(f"Preview exists; set overwrite=true: {png_path}")
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"Output exists; set overwrite=true to replace it: {destination}"
        )
    with browser_page() as page:
        root, layout = compile_diagram(spec, page)
        markup = svg_bytes(root)
        report = inspect_markup(markup.decode(), page)
    png = preview_png(markup) if preview != "none" else None
    if (
        source_check is not None
        and revision(source_check[0].read_bytes()) != source_check[1]
    ):
        raise ValueError("Diagram changed while rendering; no outputs were replaced")
    publish(destination, markup, overwrite)
    if png is not None:
        publish(png_path, png, overwrite)
    result = {
        "status": "success",
        "output_path": str(destination),
        "size_bytes": len(markup),
        "theme": spec.theme,
        "revision": revision(markup),
        "layout": layout,
        "inspection": report,
        "next_step": "Call format_conversion.svg_to_png on output_path and visually review the PNG.",
    }
    if png is not None:
        result["preview_path"] = str(png_path)
        result["next_step"] = (
            "Review the preview and any inspection findings; update_diagram accepts ID-based changes."
        )
    return summarize(result, spec, detail)


def compile_diagram(spec: Diagram, page):
    palette = THEMES[spec.theme]
    labels, node_slices, edge_indexes, group_indexes = [], {}, {}, {}
    title_index = None
    if spec.title:
        title_index = len(labels)
        labels.append(
            Label(
                text=spec.title, font_size=28, weight=700, align="left", max_width=1400
            )
        )
    for node in spec.nodes:
        start = len(labels)
        for label in node.labels:
            data = label.model_dump()
            if node.width is not None:
                data["max_width"] = min(
                    label.max_width or 4096, max(10, node.width - 36)
                )
            labels.append(Label(**data))
        node_slices[node.id] = slice(start, len(labels))
    for edge in spec.edges:
        if edge.label is not None:
            edge_indexes[edge.id] = len(labels)
            labels.append(edge.label)
    for group in spec.groups:
        group_indexes[group.id] = len(labels)
        labels.append(Label(text=group.title, font_size=18, weight=700, align="left"))
    measured = measure(labels, spec.font_family, palette["text"], page)
    sizes = {}
    for node in spec.nodes:
        blocks = measured[node_slices[node.id]]
        width = node.width or max(112, max(item["width"] for item in blocks) + 36)
        height = node.height or max(
            64, sum(item["height"] for item in blocks) + (len(blocks) - 1) * 8 + 32
        )
        if node.shape == "circle":
            width = height = max(width, height)
        elif node.shape == "tensor":
            width += 12 if node.width is None else 0
            height += 12 if node.height is None else 0
        sizes[node.id] = width, height
    title_height = (
        measured[title_index]["height"] + 28 if title_index is not None else 0
    )
    if spec.layout.mode == "elk":
        from .elk import layout as elk_layout

        group_sizes = {
            key: (measured[i]["width"], measured[i]["height"])
            for key, i in group_indexes.items()
        }
        label_sizes = {
            key: (measured[i]["width"], measured[i]["height"])
            for key, i in edge_indexes.items()
        }
        boxes, groups, routes, edge_labels = elk_layout(
            spec, sizes, group_sizes, label_sizes, title_height
        )
        header_boxes = []
        if title_index is not None:
            heading = measured[title_index]
            header_boxes.append(
                Rect(
                    spec.layout.padding,
                    spec.layout.padding - 10,
                    heading["width"],
                    heading["height"],
                )
            )
        for key, box in groups.items():
            heading = measured[group_indexes[key]]
            header_boxes.append(
                Rect(box.x + 16, box.y + 10, heading["width"], heading["height"])
            )
    else:
        boxes, groups, header_boxes, routes, edge_labels = native_layout(
            spec,
            sizes,
            measured,
            group_indexes,
            edge_indexes,
            title_index,
            title_height,
        )
    bounds = (
        list(boxes.values())
        + list(groups.values())
        + header_boxes
        + list(edge_labels.values())
    )
    for points in routes.values():
        bounds.extend(Rect(x, y, 0, 0) for x, y in points)
    min_x = min(0, min(box.x for box in bounds) - 16)
    min_y = min(0, min(box.y for box in bounds) - 16)
    width = max(box.right for box in bounds) + spec.layout.padding - min_x
    height = max(box.bottom for box in bounds) + spec.layout.padding - min_y
    if width > 8192 or height > 8192 or width * height > 32000000:
        raise ValueError(
            "Layout exceeds 8192 pixels per side or 32 million pixels; split the diagram"
        )
    root = element(
        "svg",
        {
            "width": round(width, 3),
            "height": round(height, 3),
            "viewBox": f"{min_x} {min_y} {width} {height}",
            "role": "img",
            "aria-labelledby": "diagram-title diagram-desc",
        },
    )
    element("title", {"id": "diagram-title"}, root).text = spec.title or "Model diagram"
    element("desc", {"id": "diagram-desc"}, root).text = spec.description
    element("metadata", {"id": "diagram-spec"}, root).text = spec.model_dump_json()
    defs = element("defs", parent=root)
    for role, (_, stroke) in palette["roles"].items():
        marker = element(
            "marker",
            {
                "id": f"arrow-{role}",
                "viewBox": "0 0 10 10",
                "refX": 10,
                "refY": 5,
                "markerWidth": 9,
                "markerHeight": 9,
                "orient": "auto",
                "markerUnits": "userSpaceOnUse",
            },
            defs,
        )
        element("path", {"d": "M0 0L10 5L0 10Z", "fill": stroke}, marker)
    element(
        "rect",
        {
            "x": min_x,
            "y": min_y,
            "width": width,
            "height": height,
            "rx": 18,
            "fill": palette["background"],
        },
        root,
    )
    for group in spec.groups:
        box = groups[group.id]
        element(
            "rect",
            {
                "id": f"group-{group.id}",
                "class": "diagram-group",
                "data-members": " ".join(f"node-{name}" for name in group.members),
                "x": box.x,
                "y": box.y,
                "width": box.w,
                "height": box.h,
                "rx": 16,
                "fill": "none",
                "stroke": palette["roles"][group.role][1],
                "stroke-width": 1.3,
                "stroke-dasharray": "7 5",
            },
            root,
        )
        draw_label(
            root,
            measured[group_indexes[group.id]],
            box.x + 16,
            box.y + 10,
            box.w - 32,
            palette["muted"],
            f"group-label-{group.id}",
        )
    for edge in spec.edges:
        attrs = {
            "id": f"edge-{edge.id}",
            "class": "diagram-edge",
            "data-source": f"node-{edge.source}",
            "data-target": f"node-{edge.target}",
            "d": "M" + " L".join(f"{x:.4f},{y:.4f}" for x, y in routes[edge.id]),
            "fill": "none",
            "stroke": palette["roles"][edge.role][1],
            "stroke-width": 2.3,
            "stroke-linejoin": "round",
            "marker-end": f"url(#arrow-{edge.role})",
        }
        if edge.dashed or edge.role == "frozen":
            attrs["stroke-dasharray"] = "7 5"
        element("path", attrs, root)
    for node in spec.nodes:
        box = boxes[node.id]
        fill, stroke = palette["roles"][node.role]
        attrs = {
            "id": f"node-{node.id}",
            "class": "diagram-node",
            "fill": fill,
            "stroke": stroke,
            "stroke-width": 1.5,
        }
        if node.role == "frozen":
            attrs["stroke-dasharray"] = "7 5"
        if node.shape == "circle":
            element(
                "circle",
                {
                    **attrs,
                    "cx": box.x + box.w / 2,
                    "cy": box.y + box.h / 2,
                    "r": box.w / 2,
                },
                root,
            )
        elif node.shape == "tensor":
            container = element("g", attrs, root)
            for offset in (12, 6, 0):
                element(
                    "rect",
                    {
                        "x": box.x + offset,
                        "y": box.y + 12 - offset,
                        "width": box.w - 12,
                        "height": box.h - 12,
                        "rx": 3,
                        "opacity": 1 - offset / 30,
                    },
                    container,
                )
            box = Rect(box.x, box.y + 12, box.w - 12, box.h - 12)
        else:
            element(
                "rect",
                {
                    **attrs,
                    "x": box.x,
                    "y": box.y,
                    "width": box.w,
                    "height": box.h,
                    "rx": 12,
                },
                root,
            )
        blocks = measured[node_slices[node.id]]
        total = sum(block["height"] for block in blocks) + 8 * (len(blocks) - 1)
        y = box.y + (box.h - total) / 2
        for index, block in enumerate(blocks):
            draw_label(
                root,
                block,
                box.x + 18,
                y,
                box.w - 36,
                palette["text"],
                f"label-{node.id}-{index}",
                f"node-{node.id}",
            )
            y += block["height"] + 8
    for edge in spec.edges:
        if edge.id in edge_labels:
            box = edge_labels[edge.id]
            element(
                "rect",
                {
                    "x": box.x - 4,
                    "y": box.y - 2,
                    "width": box.w + 8,
                    "height": box.h + 4,
                    "fill": palette["background"],
                    "rx": 3,
                },
                root,
            )
            draw_label(
                root,
                measured[edge_indexes[edge.id]],
                box.x,
                box.y,
                box.w,
                palette["text"],
                f"edge-label-{edge.id}",
                f"edge-{edge.id}",
            )
    if title_index is not None:
        draw_label(
            root,
            measured[title_index],
            spec.layout.padding,
            spec.layout.padding - 10,
            width - 2 * spec.layout.padding,
            palette["text"],
            "heading",
        )
    layout = {
        "nodes": {
            key: {"x": box.x, "y": box.y, "width": box.w, "height": box.h}
            for key, box in boxes.items()
        },
        "edges": {
            key: [list(point) for point in points] for key, points in routes.items()
        },
    }
    return root, layout


def native_layout(
    spec, sizes, measured, group_indexes, edge_indexes, title_index, title_height
):
    top = spec.layout.padding + title_height + (60 if spec.groups else 0)
    boxes = place(spec, sizes, top)
    groups, header_boxes = {}, []
    if title_index is not None:
        header_boxes.append(
            Rect(
                spec.layout.padding,
                spec.layout.padding - 10,
                measured[title_index]["width"],
                measured[title_index]["height"],
            )
        )
    for group in spec.groups:
        members = [boxes[name] for name in group.members]
        left, top = (
            min(box.x for box in members) - 24,
            min(box.y for box in members) - 52,
        )
        right, bottom = (
            max(box.right for box in members) + 24,
            max(box.bottom for box in members) + 24,
        )
        groups[group.id] = Rect(left, top, right - left, bottom - top)
        heading = measured[group_indexes[group.id]]
        header_boxes.append(
            Rect(left + 16, top + 10, heading["width"], heading["height"])
        )
    obstacle_boxes = [box.inflate(14) for box in boxes.values()] + [
        box.inflate(6) for box in header_boxes
    ]
    node_map = {node.id: node for node in spec.nodes}
    routes, edge_labels = {}, {}
    for edge in spec.edges:
        source, target = boxes[edge.source], boxes[edge.target]
        horizontal = spec.layout.direction == "LR"
        forward = (target.x >= source.x) if horizontal else (target.y >= source.y)
        defaults = ("right", "left") if horizontal else ("bottom", "top")
        if not forward:
            defaults = defaults[::-1]
        a, exit_a = port(node_map[edge.source], source, edge.source_port or defaults[0])
        b, exit_b = port(node_map[edge.target], target, edge.target_port or defaults[1])
        for name, box in boxes.items():
            if name != edge.source and segment_hits(a, exit_a, box):
                raise ValueError(
                    f"Edge {edge.id} source stub crosses {name}; adjust ports or spacing"
                )
            if name != edge.target and segment_hits(exit_b, b, box):
                raise ValueError(
                    f"Edge {edge.id} target stub crosses {name}; adjust ports or spacing"
                )
        waypoints = [exit_a] + [(point.x, point.y) for point in edge.via] + [exit_b]
        points = [a]
        for start, end in zip(waypoints, waypoints[1:]):
            points.extend(route(start, end, obstacle_boxes))
        points.append(b)
        routes[edge.id] = simplify(points)
        if edge.id in edge_indexes:
            label = measured[edge_indexes[edge.id]]
            candidates = []
            for p, q in zip(points, points[1:]):
                length = abs(p[0] - q[0]) + abs(p[1] - q[1])
                for fraction in (0.5, 0.25, 0.75):
                    mx, my = (
                        p[0] + (q[0] - p[0]) * fraction,
                        p[1] + (q[1] - p[1]) * fraction,
                    )
                    if p[1] == q[1]:
                        positions = [
                            (mx - label["width"] / 2, my - label["height"] - 9),
                            (mx - label["width"] / 2, my + 9),
                        ]
                    else:
                        positions = [
                            (mx + 9, my - label["height"] / 2),
                            (mx - label["width"] - 9, my - label["height"] / 2),
                        ]
                    for x, y in positions:
                        box = Rect(x, y, label["width"], label["height"])
                        overlaps = sum(
                            intersects(box.inflate(4), other)
                            for other in list(boxes.values())
                            + header_boxes
                            + list(edge_labels.values())
                        )
                        candidates.append((overlaps, -length, x, y, box))
            if not candidates:
                raise ValueError(f"Edge {edge.id} has no room for its label")
            edge_labels[edge.id] = min(candidates, key=lambda entry: entry[:4])[-1]
            obstacle_boxes.append(edge_labels[edge.id].inflate(5))
    return boxes, groups, header_boxes, routes, edge_labels
