"""Versioned, bounded diagram input; unknown options are errors, not guesses."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Size = Annotated[float, Field(gt=0, le=4096, allow_inf_nan=False)]
Coordinate = Annotated[float, Field(ge=0, le=8000, allow_inf_nan=False)]
Role = Literal["default", "feature", "state", "condition", "loss", "frozen"]
Side = Literal["left", "right", "top", "bottom"]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True
    )


class Span(Model):
    kind: Literal["text", "math"] = "text"
    content: str = Field(min_length=1, max_length=4096)


class Label(Model):
    """Exactly one of text, latex, or spans. Math spans are indivisible."""

    text: str | None = Field(default=None, min_length=1, max_length=4096)
    latex: str | None = Field(default=None, min_length=1, max_length=4096)
    spans: list[Span] | None = Field(default=None, min_length=1, max_length=32)
    font_size: float = Field(default=20, ge=10, le=80)
    weight: Literal[400, 700] = 400
    color: Color | None = None
    max_width: Size | None = None
    align: Literal["center", "left"] = "center"

    @model_validator(mode="after")
    def one_content(self) -> Self:
        if sum(value is not None for value in (self.text, self.latex, self.spans)) != 1:
            raise ValueError("A label needs exactly one of text, latex, or spans")
        return self

    def runs(self) -> list[Span]:
        if self.spans is not None:
            return self.spans
        if self.latex is not None:
            return [Span(kind="math", content=self.latex)]
        return [Span(content=self.text or " ")]


class Port(Model):
    id: Identifier
    side: Side
    offset: float = Field(default=0.5, ge=0.1, le=0.9)


class Node(Model):
    id: Identifier
    labels: list[Label] = Field(min_length=1, max_length=8)
    role: Role = "default"
    shape: Literal["box", "circle", "tensor"] = "box"
    width: Size | None = None
    height: Size | None = None
    row: int | None = Field(default=None, ge=0, le=40)
    column: int | None = Field(default=None, ge=0, le=40)
    x: Coordinate | None = None
    y: Coordinate | None = None
    ports: list[Port] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def coordinates_and_ports(self) -> Self:
        if self.shape == "tensor" and any(
            value is not None and value <= 12 for value in (self.width, self.height)
        ):
            raise ValueError(
                "Tensor width and height must exceed the 12-pixel stack depth"
            )
        if (self.x is None) != (self.y is None):
            raise ValueError("Set both x and y, or neither")
        if (self.row is None) != (self.column is None):
            raise ValueError("Set both row and column, or neither")
        names = [port.id for port in self.ports]
        if len(set(names)) != len(names) or set(names) & {
            "left",
            "right",
            "top",
            "bottom",
        }:
            raise ValueError(
                "Port IDs must be unique and cannot use built-in side names"
            )
        return self


class Point(Model):
    x: Coordinate
    y: Coordinate


class Edge(Model):
    id: Identifier
    source: Identifier
    target: Identifier
    source_port: str | None = Field(default=None, max_length=64)
    target_port: str | None = Field(default=None, max_length=64)
    role: Role = "default"
    dashed: bool = False
    feedback: bool = False
    label: Label | None = None
    via: list[Point] = Field(default_factory=list, max_length=12)


class Group(Model):
    id: Identifier
    title: str = Field(min_length=1, max_length=160)
    members: list[Identifier] = Field(min_length=1, max_length=40)
    role: Role = "default"


class Layout(Model):
    mode: Literal["layered", "grid", "manual", "elk"] = "layered"
    direction: Literal["LR", "TB"] = "LR"
    gap_x: float = Field(default=90, ge=48, le=500)
    gap_y: float = Field(default=84, ge=48, le=500)
    padding: float = Field(default=48, ge=32, le=160)


class Diagram(Model):
    version: Literal[1] = 1
    title: str = Field(default="", max_length=240)
    description: str = Field(default="", max_length=4096)
    theme: Literal["dark", "print"] = "dark"
    font_family: str = Field(
        default="Noto Sans CJK SC", min_length=1, max_length=100, pattern=r"^[\w .-]+$"
    )
    layout: Layout = Field(default_factory=Layout)
    nodes: list[Node] = Field(min_length=1, max_length=40)
    edges: list[Edge] = Field(default_factory=list, max_length=80)
    groups: list[Group] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def graph_contract(self) -> Self:
        all_ids = [
            item.id for items in (self.nodes, self.edges, self.groups) for item in items
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Node, edge, and group IDs must be globally unique")
        nodes = {node.id: node for node in self.nodes}
        for edge in self.edges:
            for node_id, port in (
                (edge.source, edge.source_port),
                (edge.target, edge.target_port),
            ):
                if node_id not in nodes:
                    raise ValueError(
                        f"Edge {edge.id} references missing node {node_id}"
                    )
                names = {item.id for item in nodes[node_id].ports}
                if port is not None and port not in names | {
                    "left",
                    "right",
                    "top",
                    "bottom",
                }:
                    raise ValueError(
                        f"Edge {edge.id} references missing port {node_id}.{port}"
                    )
            if edge.source == edge.target:
                raise ValueError(
                    "Self-edges are not supported; use a separate feedback node"
                )
        grouped: set[str] = set()
        for group in self.groups:
            if not set(group.members) <= nodes.keys():
                raise ValueError(f"Group {group.id} references missing nodes")
            if len(group.members) != len(set(group.members)) or grouped & set(
                group.members
            ):
                raise ValueError("Each node can belong to only one group")
            grouped.update(group.members)
        for node in self.nodes:
            if self.layout.mode == "elk" and (
                node.x is not None or node.row is not None
            ):
                raise ValueError(
                    "ELK does not accept x/y or row/column pins; clear them or use grid/manual layout"
                )
            if self.layout.mode == "manual" and node.x is None:
                raise ValueError(f"Manual layout requires x and y for {node.id}")
            if self.layout.mode == "grid" and node.row is None and node.x is None:
                raise ValueError(
                    f"Grid layout requires row/column or x/y for {node.id}"
                )
        if self.layout.mode == "elk" and any(edge.via for edge in self.edges):
            raise ValueError(
                "ELK does not accept explicit edge waypoints; use a native layout for via constraints"
            )
        if len(self.model_dump_json()) > 262144:
            raise ValueError("Diagram specifications are limited to 256 KiB")
        return self
