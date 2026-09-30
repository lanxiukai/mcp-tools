"""Deterministic layered/grid placement and rectilinear obstacle routing."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import heapq
import math

from .models import Diagram, Node

EPS = 1e-6


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    def inflate(self, distance: float):
        return Rect(
            self.x - distance,
            self.y - distance,
            self.w + 2 * distance,
            self.h + 2 * distance,
        )

    def contains(self, point):
        x, y = point
        return (
            self.x + EPS < x < self.right - EPS and self.y + EPS < y < self.bottom - EPS
        )


def intersects(a: Rect, b: Rect) -> bool:
    return (
        min(a.right, b.right) - max(a.x, b.x) > EPS
        and min(a.bottom, b.bottom) - max(a.y, b.y) > EPS
    )


def segment_hits(a, b, box: Rect) -> bool:
    if abs(a[0] - b[0]) < EPS:
        return (
            box.x + EPS < a[0] < box.right - EPS
            and min(max(a[1], b[1]), box.bottom) - max(min(a[1], b[1]), box.y) > EPS
        )
    if abs(a[1] - b[1]) < EPS:
        return (
            box.y + EPS < a[1] < box.bottom - EPS
            and min(max(a[0], b[0]), box.right) - max(min(a[0], b[0]), box.x) > EPS
        )
    raise ValueError("Only orthogonal segments are supported")


def layers(spec: Diagram) -> dict[str, int]:
    degree = {node.id: 0 for node in spec.nodes}
    children = defaultdict(list)
    rank = dict.fromkeys(degree, 0)
    for edge in spec.edges:
        if not edge.feedback:
            degree[edge.target] += 1
            children[edge.source].append(edge.target)
    queue = deque(key for key in degree if degree[key] == 0)
    visited = 0
    while queue:
        source = queue.popleft()
        visited += 1
        for target in children[source]:
            rank[target] = max(rank[target], rank[source] + 1)
            degree[target] -= 1
            if degree[target] == 0:
                queue.append(target)
    if visited != len(degree):
        raise ValueError(
            "Layered layout needs a DAG; mark feedback edges with feedback=true or use grid/manual layout"
        )
    return rank


def place(
    spec: Diagram, sizes: dict[str, tuple[float, float]], top: float
) -> dict[str, Rect]:
    ranks = layers(spec) if spec.layout.mode == "layered" else {}
    count = defaultdict(int)
    cells = {}
    for node in spec.nodes:
        if node.x is not None:
            continue
        if node.row is not None:
            cells[node.id] = (node.row, node.column)
        else:
            column = ranks[node.id]
            row = count[column]
            count[column] += 1
            cells[node.id] = (
                (row, column) if spec.layout.direction == "LR" else (column, row)
            )
    widths, heights = defaultdict(float), defaultdict(float)
    for key, (row, column) in cells.items():
        widths[column] = max(widths[column], sizes[key][0])
        heights[row] = max(heights[row], sizes[key][1])
    xs, ys = {}, {}
    x, y = spec.layout.padding + (28 if spec.groups else 0), top
    for column in range(max(widths, default=-1) + 1):
        xs[column] = x
        x += widths[column] + spec.layout.gap_x
    for row in range(max(heights, default=-1) + 1):
        ys[row] = y
        y += heights[row] + spec.layout.gap_y
    result = {}
    for node in spec.nodes:
        w, h = sizes[node.id]
        if node.x is not None:
            nx, ny = node.x, node.y
        else:
            row, column = cells[node.id]
            nx, ny = (
                xs[column] + (widths[column] - w) / 2,
                ys[row] + (heights[row] - h) / 2,
            )
        result[node.id] = Rect(nx, ny, w, h)
    return result


def port(node: Node, box: Rect, name: str):
    side, offset = name, 0.5
    for item in node.ports:
        if item.id == name:
            side, offset = item.side, item.offset
            break
    if side in {"left", "right"}:
        x, y = (box.x if side == "left" else box.right), box.y + offset * box.h
        if node.shape == "circle":
            dx = math.sqrt(max(0, (box.w / 2) ** 2 - (y - box.y - box.h / 2) ** 2))
            x = box.x + box.w / 2 + (-dx if side == "left" else dx)
        stub = (box.x - 16 if side == "left" else box.right + 16, y)
    else:
        x, y = box.x + offset * box.w, (box.y if side == "top" else box.bottom)
        if node.shape == "circle":
            dy = math.sqrt(max(0, (box.h / 2) ** 2 - (x - box.x - box.w / 2) ** 2))
            y = box.y + box.h / 2 + (-dy if side == "top" else dy)
        stub = (x, box.y - 16 if side == "top" else box.bottom + 16)
    return (x, y), stub


def simplify(points):
    result = []
    for point in points:
        if result and point == result[-1]:
            continue
        if len(result) > 1:
            a, b = result[-2:]
            # Do not remove a turnaround: it may be an explicit waypoint.
            if (
                a[0] == b[0] == point[0] and (b[1] - a[1]) * (point[1] - b[1]) >= 0
            ) or (a[1] == b[1] == point[1] and (b[0] - a[0]) * (point[0] - b[0]) >= 0):
                result.pop()
        result.append(point)
    return result


def route(start, end, obstacles: list[Rect]):
    """A* on obstacle-boundary tracks; cost includes an explicit bend penalty."""
    if any(box.contains(start) or box.contains(end) for box in obstacles):
        raise ValueError(
            "A port exit or waypoint is inside another node; increase spacing or change ports"
        )
    xs = {start[0], end[0]}
    ys = {start[1], end[1]}
    for box in obstacles:
        xs.update((box.x, box.right))
        ys.update((box.y, box.bottom))
    xs.update((min(xs) - 24, max(xs) + 24))
    ys.update((min(ys) - 24, max(ys) + 24))
    xs, ys = sorted(xs), sorted(ys)
    initial = (xs.index(start[0]), ys.index(start[1]), -1)
    target = (xs.index(end[0]), ys.index(end[1]))
    queue = [(0.0, initial)]
    costs = {initial: 0.0}
    previous = {}
    blocked = {}
    while queue:
        priority, state = heapq.heappop(queue)
        ix, iy, direction = state
        here = (xs[ix], ys[iy])
        if state[:2] == target:
            points = [end]
            while state in previous:
                state = previous[state]
                points.append((xs[state[0]], ys[state[1]]))
            return simplify(list(reversed(points)))
        heuristic = abs(here[0] - end[0]) + abs(here[1] - end[1])
        if priority > costs[state] + heuristic + EPS:
            continue
        for dx, dy, axis in ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1)):
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < len(xs) and 0 <= jy < len(ys)):
                continue
            there = (xs[jx], ys[jy])
            key = tuple(sorted((here, there)))
            if key not in blocked:
                blocked[key] = any(segment_hits(here, there, box) for box in obstacles)
            if blocked[key]:
                continue
            cost = costs[state] + abs(there[0] - here[0]) + abs(there[1] - here[1])
            if direction not in (-1, axis):
                cost += 24
            neighbor = (jx, jy, axis)
            if cost < costs.get(neighbor, float("inf")) - EPS:
                costs[neighbor] = cost
                previous[neighbor] = state
                estimate = abs(there[0] - end[0]) + abs(there[1] - end[1])
                heapq.heappush(queue, (cost + estimate, neighbor))
    raise ValueError(
        "No orthogonal route found; adjust node spacing, ports, or waypoints"
    )
