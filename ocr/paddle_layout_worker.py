"""Isolated PP-DocLayoutV3 worker used by the generic OCR adapter.

PaddlePaddle and PyTorch intentionally live in different environments.  This
short-lived process writes its result to a JSON file so Paddle's own stdout
logging cannot corrupt the protocol.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _result_payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        raise TypeError(f"Unexpected layout result type: {type(payload).__name__}")
    nested = payload.get("res", payload)
    if not isinstance(nested, dict):
        raise TypeError("Layout result 'res' must be an object")
    return nested


def _normalise_boxes(result: Any, threshold: float) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    for index, raw in enumerate(_result_payload(result).get("boxes", []), start=1):
        if not isinstance(raw, dict):
            continue
        score = float(raw.get("score", 0.0))
        coordinate = raw.get("coordinate")
        if score < threshold or not isinstance(coordinate, (list, tuple)) or len(coordinate) != 4:
            continue
        raw_order = raw.get("order")
        boxes.append(
            {
                "label": str(raw.get("label", "text")),
                "score": score,
                "coordinate": [int(round(float(value))) for value in coordinate],
                "order": int(raw_order) if raw_order is not None else index,
            }
        )
    boxes.sort(key=lambda box: (box["order"], box["coordinate"][1], box["coordinate"][0]))
    return boxes


def run(request_path: Path, output_path: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    images = request.get("images", [])
    if not isinstance(images, list) or not images:
        raise ValueError("Layout request must contain at least one image path")

    from paddlex import create_predictor

    predictor = create_predictor(
        "PP-DocLayoutV3",
        model_dir=request["model_dir"],
        device=request.get("device", "gpu:0"),
        batch_size=int(request.get("batch_size", 1)),
    )
    threshold = float(request.get("threshold", 0.5))
    pages: list[dict[str, Any]] = []
    for page_index, image_path in enumerate(images):
        results = list(predictor.predict(str(image_path)))
        if len(results) != 1:
            raise RuntimeError(
                f"Expected one layout result for page {page_index}, got {len(results)}"
            )
        pages.append(
            {
                "page_index": page_index,
                "boxes": _normalise_boxes(results[0], threshold),
            }
        )

    output_path.write_text(
        json.dumps({"pages": pages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated PP-DocLayoutV3 inference")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.request, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
