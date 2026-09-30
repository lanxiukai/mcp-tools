"""Measure and draw mixed text/math with explicit shared baselines."""

from __future__ import annotations

from copy import deepcopy

from .models import Label
from .runtime import (
    DIRECTORY,
    clone_math,
    element,
    font_info,
    math_batch,
    math_fragment,
    missing_glyphs,
)


def measure(labels: list[Label], family: str, color: str, page) -> list[dict]:
    formulas = list(
        dict.fromkeys(
            run.content
            for label in labels
            for run in label.runs()
            if run.kind == "math"
        )
    )
    rendered = dict(zip(formulas, math_batch(formulas), strict=True))
    input_labels, fragments = [], []
    for index, label in enumerate(labels):
        font = font_info(family, label.weight)
        missing = missing_glyphs(
            "".join(run.content for run in label.runs() if run.kind == "text"), font
        )
        if missing:
            raise ValueError(
                f"Font {family} lacks glyphs for label {index}: {''.join(missing)[:80]}"
            )
        runs = []
        for run in label.runs():
            if run.kind == "text":
                runs.append({"kind": "text", "content": run.content})
            else:
                root, box = math_fragment(
                    rendered[run.content],
                    label.font_size,
                    label.color or color,
                    f"math-{len(fragments)}",
                )
                fragments.append(root)
                runs.append(
                    {
                        "kind": "math",
                        "content": run.content,
                        "math_index": len(fragments) - 1,
                        "width": box["width"],
                        "ascent": box["baseline"],
                        "descent": box["height"] - box["baseline"],
                    }
                )
        input_labels.append({**label.model_dump(), "runs": runs})
    measurements = page.evaluate(
        (DIRECTORY / "measure.js").read_text(),
        {"labels": input_labels, "family": family},
    )
    for label, result in zip(labels, measurements, strict=True):
        result["label"] = label
        result["family"] = family
        for line in result["lines"]:
            for run in line["runs"]:
                if run["kind"] == "math":
                    run["svg"] = fragments[run.pop("math_index")]
    return measurements


def draw_label(
    parent,
    measured: dict,
    x: float,
    y: float,
    width: float,
    color: str,
    label_id: str,
    owner: str = "",
):
    label = measured["label"]
    container = element(
        "g", {"id": label_id, "class": "diagram-label", "data-owner": owner}, parent
    )
    run_number = 0
    for line in measured["lines"]:
        cursor = x + (width - line["width"]) / 2 if label.align == "center" else x
        baseline = y + line["ascent"]
        for run in line["runs"]:
            if run["kind"] == "text":
                el = element(
                    "text",
                    {
                        "x": cursor - run["bearing"],
                        "y": baseline,
                        "font-size": label.font_size,
                        "font-family": measured["family"],
                        "font-weight": label.weight,
                        "fill": label.color or color,
                        "style": "white-space:pre",
                        "{http://www.w3.org/XML/1998/namespace}space": "preserve",
                    },
                    container,
                )
                el.text = run["content"]
            else:
                el = clone_math(run["svg"], f"{label_id}-r{run_number}")
                el.set("x", str(cursor))
                el.set("y", str(baseline - run["ascent"]))
                element("title", parent=el).text = run["content"]
                el.set("aria-label", run["content"])
                container.append(el)
            cursor += run["width"]
            run_number += 1
        y += line["height"] + measured["gap"]
    return container


def public_measurement(measured: dict) -> dict:
    data = deepcopy(
        {
            key: value
            for key, value in measured.items()
            if key not in {"label", "family"}
        }
    )
    for line in data["lines"]:
        for run in line["runs"]:
            run.pop("svg", None)
    return data
