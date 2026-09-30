"""ELK layout adapter; all rendering, fonts, and geometry inspection stay local."""

from __future__ import annotations

import json
import math
import subprocess

from .geometry import Rect, port, simplify
from .models import Diagram
from .runtime import DIRECTORY


def layout(
    spec: Diagram,
    sizes: dict,
    group_sizes: dict,
    label_sizes: dict,
    title_height: float,
):
    horizontal = spec.layout.direction == "LR"
    root = {
        "id": "__root",
        "children": [],
        "edges": [],
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT" if horizontal else "DOWN",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.hierarchyHandling": "INCLUDE_CHILDREN",
            "elk.padding": "[top=0,left=0,bottom=0,right=0]",
            "elk.randomSeed": "1",
            "elk.spacing.nodeNode": str(
                spec.layout.gap_y if horizontal else spec.layout.gap_x
            ),
            "elk.layered.spacing.nodeNodeBetweenLayers": str(
                spec.layout.gap_x if horizontal else spec.layout.gap_y
            ),
            "elk.spacing.edgeNode": "18",
            "elk.spacing.edgeEdge": "14",
        },
    }
    parents, group_graphs = {}, {}
    for group in spec.groups:
        title = group_sizes[group.id]
        graph = {
            "id": group.id,
            "children": [],
            "edges": [],
            "labels": [
                {
                    "id": group.id + "-title",
                    "text": group.title,
                    "width": title[0],
                    "height": title[1],
                }
            ],
            "layoutOptions": {
                "elk.padding": "[top=52,left=24,bottom=24,right=24]",
                "elk.nodeLabels.placement": "INSIDE H_LEFT V_TOP",
                "elk.nodeSize.constraints": "NODE_LABELS MINIMUM_SIZE",
            },
        }
        group_graphs[group.id] = graph
        root["children"].append(graph)
        parents.update({name: group.id for name in group.members})
    ports = {}
    for node in spec.nodes:
        width, height = sizes[node.id]
        graph = {
            "id": node.id,
            "width": width,
            "height": height,
            "ports": [],
            "layoutOptions": {"elk.portConstraints": "FIXED_POS"},
        }
        names = ["left", "right", "top", "bottom"] + [item.id for item in node.ports]
        for name in names:
            side = next((p.side for p in node.ports if p.id == name), name)
            position, _ = port(node, Rect(0, 0, width, height), name)
            identifier = f"{node.id}::{name}"
            ports[node.id, name] = identifier
            graph["ports"].append(
                {
                    "id": identifier,
                    "x": position[0],
                    "y": position[1],
                    "width": 0,
                    "height": 0,
                    "layoutOptions": {
                        "elk.port.side": {
                            "left": "WEST",
                            "right": "EAST",
                            "top": "NORTH",
                            "bottom": "SOUTH",
                        }[side]
                    },
                }
            )
        parent = group_graphs[parents[node.id]] if node.id in parents else root
        parent["children"].append(graph)
    for edge in spec.edges:
        defaults = ("right", "left") if horizontal else ("bottom", "top")
        if edge.feedback:
            defaults = defaults[::-1]
        graph = {
            "id": edge.id,
            "sources": [ports[edge.source, edge.source_port or defaults[0]]],
            "targets": [ports[edge.target, edge.target_port or defaults[1]]],
        }
        if edge.id in label_sizes:
            width, height = label_sizes[edge.id]
            graph["labels"] = [
                {
                    "id": edge.id + "-label",
                    "text": "label",
                    "width": width,
                    "height": height,
                }
            ]
        group = parents.get(edge.source)
        parent = (
            group_graphs[group] if group and group == parents.get(edge.target) else root
        )
        parent["edges"].append(graph)
    try:
        result = subprocess.run(
            ["node", str(DIRECTORY / "elk.cjs")],
            input=json.dumps(root),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=DIRECTORY,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Node.js is required for ELK") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("ELK exceeded its 30-second layout deadline") from error
    if result.returncode:
        raise RuntimeError(
            "ELK failed: "
            + result.stderr[:1000]
            + ". Restore with npm ci --prefix svg-diagram --ignore-scripts."
        )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("ELK output exceeds 4 MiB")
    graph = json.loads(result.stdout)
    offset = (spec.layout.padding, spec.layout.padding + title_height)
    origins, boxes, groups, edges = {}, {}, {}, []
    node_ids = {node.id for node in spec.nodes}

    def visit(item, x, y):
        x += item.get("x", 0)
        y += item.get("y", 0)
        if not all(
            math.isfinite(v)
            for v in (x, y, item.get("width", 0), item.get("height", 0))
        ):
            raise ValueError("ELK returned non-finite geometry")
        origins[item["id"]] = (x, y)
        if item["id"] in node_ids:
            boxes[item["id"]] = Rect(x, y, item["width"], item["height"])
        elif item["id"] != "__root":
            groups[item["id"]] = Rect(x, y, item["width"], item["height"])
        edges.extend((edge, item["id"]) for edge in item.get("edges", []))
        for child in item.get("children", []):
            visit(child, x, y)

    visit(graph, *offset)
    routes, edge_labels = {}, {}
    for edge, parent in edges:
        ox, oy = origins[edge.get("container", parent)]
        sections = edge.get("sections", [])
        if len(sections) != 1:
            raise ValueError(
                f"ELK returned {len(sections)} sections for {edge['id']}; expected a single directed edge"
            )
        section = sections[0]
        points = (
            [section["startPoint"]]
            + section.get("bendPoints", [])
            + [section["endPoint"]]
        )
        routes[edge["id"]] = simplify(
            [(point["x"] + ox, point["y"] + oy) for point in points]
        )
        for label in edge.get("labels", []):
            edge_labels[edge["id"]] = Rect(
                label["x"] + ox, label["y"] + oy, label["width"], label["height"]
            )
    if boxes.keys() != node_ids or routes.keys() != {edge.id for edge in spec.edges}:
        raise ValueError("ELK returned an incomplete diagram")
    return boxes, groups, routes, edge_labels
