"""Apply compact ID-based changes to the canonical spec, then render once."""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from .artifacts import Detail, Preview, read_diagram
from .models import Diagram, Identifier, Model
from .renderer import render


class Change(Model):
    op: Literal[
        "set_node",
        "add_node",
        "remove_node",
        "set_edge",
        "add_edge",
        "remove_edge",
        "set_group",
        "add_group",
        "remove_group",
        "set_label",
        "set_diagram",
    ]
    id: Identifier | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    label_index: int = Field(default=0, ge=0, le=7)

    @model_validator(mode="after")
    def operation_contract(self) -> Self:
        if self.op == "set_diagram":
            if self.id is not None:
                raise ValueError("set_diagram does not take an element ID")
            if set(self.values) - {
                "title",
                "description",
                "theme",
                "font_family",
                "layout",
            }:
                raise ValueError(
                    "set_diagram accepts title, description, theme, font_family, or layout"
                )
        elif self.id is None:
            raise ValueError("Element changes need an ID")
        if "id" in self.values:
            raise ValueError(
                "Use the change's id field; renaming element IDs is not supported"
            )
        if self.op.startswith("remove_") and self.values:
            raise ValueError("Remove operations do not accept values")
        if self.op != "set_label" and self.label_index:
            raise ValueError("label_index applies only to set_label")
        return self


def apply_changes(spec: Diagram, changes: list[Change]) -> Diagram:
    if not 1 <= len(changes) <= 100:
        raise ValueError("Supply 1-100 changes")
    if len(json.dumps([item.model_dump() for item in changes])) > 65536:
        raise ValueError("Change batches are limited to 64 KiB")
    data = spec.model_dump()
    for change in changes:
        if change.op == "set_diagram":
            values = dict(change.values)
            if "layout" in values:
                if not isinstance(values["layout"], dict):
                    raise ValueError("layout must be an object")
                values["layout"] = {**data["layout"], **values["layout"]}
            data.update(values)
            continue
        if change.op == "set_label":
            nodes = {node["id"]: node for node in data["nodes"]}
            if change.id not in nodes:
                raise ValueError(f"Unknown node ID: {change.id}")
            labels = nodes[change.id]["labels"]
            if change.label_index >= len(labels):
                raise ValueError(
                    f"Label {change.label_index} does not exist on {change.id}"
                )
            label = labels[change.label_index]
            if any(key in change.values for key in ("text", "latex", "spans")):
                label.update(text=None, latex=None, spans=None)
            label.update(change.values)
            continue
        verb, kind = change.op.split("_", 1)
        collection = data[kind + "s"]
        target = next((item for item in collection if item["id"] == change.id), None)
        if verb == "add":
            if target is not None:
                raise ValueError(f"{kind} already exists: {change.id}")
            collection.append({"id": change.id, **change.values})
        elif target is None:
            raise ValueError(f"Unknown {kind} ID: {change.id}")
        elif verb == "remove":
            collection.remove(target)
        else:
            target.update(change.values)
    # Validate the final graph once, allowing ordered add/remove operations in one batch.
    return Diagram.model_validate(data)


def update(
    file_path: str,
    changes: list[Change],
    output_path: str = "",
    expected_revision: str | None = None,
    preview: Preview = "inline",
    detail: Detail = "summary",
    overwrite: bool = False,
) -> dict:
    source, spec, source_revision = read_diagram(file_path)
    if expected_revision is not None and expected_revision != source_revision:
        raise ValueError(
            "Stale diagram revision; inspect the latest file before applying changes"
        )
    updated = apply_changes(spec, changes)
    destination = output_path or str(source)
    result = render(
        updated,
        destination,
        overwrite or destination == str(source),
        preview=preview,
        detail=detail,
        source_check=(source, source_revision),
    )
    result["changes_applied"] = len(changes)
    return result
