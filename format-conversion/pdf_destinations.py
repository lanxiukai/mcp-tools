"""Resolve chapter links against the actual PDF, without modifying it."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

import fitz


def _normalized(text: str) -> str:
    """Compare complete titles and their slugs, never fuzzy substrings."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def _candidate(document, index: int, title: str, basis: str, point=None, *, rotated=False) -> dict:
    page = document[index]
    result = {"page": index + 1, "page_label": page.get_label(), "title": title, "basis": basis}
    if point is None:
        result["fragment"] = f"page={index + 1}"
    else:
        point = fitz.Point(point)
        if rotated:
            point *= page.derotation_matrix
        # PyMuPDF's matrix on a rotated, cropped page omits the crop offset.
        # Read the unrotated matrix in memory; the PDF file is never saved.
        rotation = page.rotation
        try:
            if rotation:
                page.set_rotation(0)
            point *= ~page.transformation_matrix
        finally:
            if rotation:
                page.set_rotation(rotation)
        destination = [index, {"name": "XYZ"}, round(point.x, 3), round(point.y, 3), None]
        result["fragment"] = quote(json.dumps(destination, separators=(",", ":")), safe="")
    return result


def _explicit_destination(value, page_count: int) -> bool:
    if not isinstance(value, list) or len(value) < 2 or type(value[0]) is not int:
        return False
    if not 0 <= value[0] < page_count or not isinstance(value[1], dict):
        return False
    sizes = {"XYZ": 5, "Fit": 2, "FitB": 2, "FitH": 3, "FitBH": 3,
             "FitV": 3, "FitBV": 3, "FitR": 6}
    mode = value[1].get("name")
    if not isinstance(mode, str) or sizes.get(mode) != len(value):
        return False
    return all((v is None and mode != "FitR") or (type(v) in (float, int) and math.isfinite(v)) for v in value[2:])


def resolve_pdf_destination(file_path: str, target: str, page: int | None = None) -> dict:
    """Find a named destination, bookmark title, or complete text line.

    ``page`` is an optional 1-based physical page filter, never a printed label.
    A unique match yields ``fragment`` and ``uri``; repeated titles yield all
    candidates for review. Text matching is an exact normalized line comparison,
    so callers can inspect candidates before choosing a chapter occurrence.
    """
    path = Path(file_path).expanduser().resolve()
    query = target.strip().removeprefix("#")
    if not query:
        raise ValueError("A PDF chapter title or destination is required")
    with fitz.open(path) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("Destination inspection requires an unencrypted PDF")
        if page is not None and (type(page) is not int or not 1 <= page <= len(document)):
            raise ValueError("page must be a 1-based physical page within the PDF")
        candidates = []
        decoded = unquote(query)
        params = parse_qs(query, keep_blank_values=True)
        names = document.resolve_names()
        name = params.get("nameddest", [decoded])[0]
        named_only = "nameddest" in params
        if any(len(params.get(key, [])) > 1 for key in ("page", "nameddest")):
            return {"file_path": str(path), "target": target, "page_count": len(document),
                    "status": "not-found", "candidates": []}
        if name in names:
            index = names[name].get("page", -1)
            if 0 <= index < len(document):
                candidate = _candidate(document, index, name, "named-destination")
                candidate["fragment"] = "nameddest=" + quote(name, safe="")
                candidates.append(candidate)
        elif "page" in params:
            numbers = params["page"]
            if len(numbers) == 1 and re.fullmatch(r"[1-9][0-9]*", numbers[0]):
                index = int(numbers[0]) - 1
                if index < len(document) and not named_only:
                    candidate = _candidate(document, index, target, "page")
                    candidate["fragment"] = query
                    candidates.append(candidate)
        elif decoded.startswith("["):
            try:
                value = json.loads(decoded)
            except ValueError:
                value = None
            if _explicit_destination(value, len(document)):
                candidate = _candidate(document, value[0], target, "explicit-destination")
                candidate["fragment"] = quote(json.dumps(value, separators=(",", ":")), safe="")
                candidates.append(candidate)
        elif not named_only:
            normalized = _normalized(decoded)
            if normalized:
                for _level, title, number, details in document.get_toc(simple=False):
                    if (_normalized(title) == normalized and 1 <= number <= len(document)
                            and details.get("kind") == fitz.LINK_GOTO):
                        candidates.append(_candidate(document, number - 1, title, "outline",
                                                     details.get("to"), rotated=True))
                if not candidates:
                    indices = [page - 1] if page is not None else range(len(document))
                    for index in indices:
                        for block in document[index].get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
                            for line in block.get("lines", []):
                                title = "".join(span["text"] for span in line["spans"]).strip()
                                if _normalized(title) == normalized:
                                    candidates.append(_candidate(document, index, title, "text-line", line["bbox"][:2]))
        if page is not None:
            candidates = [candidate for candidate in candidates if candidate["page"] == page]
        # Multiple bookmarks to the very same location are one choice.
        candidates = list({candidate["fragment"]: candidate for candidate in candidates}.values())
        result = {
            "file_path": str(path), "target": target, "page_count": len(document),
            "status": "resolved" if len(candidates) == 1 else "ambiguous" if candidates else "not-found",
            "candidates": candidates,
        }
        for candidate in candidates:
            candidate["uri"] = path.as_uri() + "#" + candidate["fragment"]
        if len(candidates) == 1:
            result.update(candidates[0])
        return result
