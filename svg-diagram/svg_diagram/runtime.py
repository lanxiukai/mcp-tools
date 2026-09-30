"""Local fonts, Chromium geometry, MathJax, and atomic SVG publication."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET

NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
DIRECTORY = Path(__file__).resolve().parent
MAX_SVG_BYTES = 16 * 1024 * 1024
ET.register_namespace("", NS)
ET.register_namespace("xlink", XLINK)


def element(name: str, attrs: dict | None = None, parent=None):
    attributes = {key: str(value) for key, value in (attrs or {}).items()}
    if parent is None:
        return ET.Element(f"{{{NS}}}{name}", attributes)
    return ET.SubElement(parent, f"{{{NS}}}{name}", attributes)


def svg_bytes(root) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def absolute_path(value: str, suffix: str | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute file or directory path")
    if suffix is not None and path.suffix.lower() != suffix:
        raise ValueError(f"Expected a {suffix} file")
    if path.is_symlink():
        raise ValueError("Symbolic-link destinations are not supported")
    return path


def publish(path: Path, data: bytes, overwrite: bool) -> None:
    """Publish complete bytes atomically; create mode never clobbers a race winner."""
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"Output exists; set overwrite=true to replace it: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@lru_cache(maxsize=64)
def font_info(family: str, weight: int = 400) -> dict:
    if not re.fullmatch(r"[\w .-]{1,100}", family):
        raise ValueError("Use a single font family name, not a CSS font stack")
    try:
        result = subprocess.run(
            [
                "fc-match",
                "-f",
                "%{family}\n%{file}\n%{charset}\n",
                f"{family}:weight={'bold' if weight == 700 else 'regular'}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "Fontconfig is required; install fontconfig and fonts-noto-cjk"
        ) from error
    lines = result.stdout.splitlines()
    if len(lines) < 3 or family.casefold() not in {
        name.strip().casefold() for name in lines[0].split(",")
    }:
        resolved = lines[0] if lines else "unknown"
        raise ValueError(
            f"Font '{family}' is unavailable (fontconfig selected '{resolved}')"
        )
    ranges = []
    for token in lines[2].split():
        endpoints = token.split("-")
        ranges.append((int(endpoints[0], 16), int(endpoints[-1], 16)))
    return {"family": family, "file": lines[1], "weight": weight, "ranges": ranges}


def missing_glyphs(text: str, font: dict) -> list[str]:
    return sorted(
        {
            character
            for character in text
            if not character.isspace()
            and not any(start <= ord(character) <= end for start, end in font["ranges"])
        }
    )


@contextmanager
def browser_page():
    """The only browser context used for measurement and inspection is offline."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch(timeout=20000)
        try:
            context = browser.new_context(java_script_enabled=False)
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_content(
                '<!doctype html><meta http-equiv="Content-Security-Policy" '
                "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
                "<style>body{margin:0}svg{overflow:visible}</style><body></body>"
            )
            yield page
        finally:
            browser.close()


def math_batch(expressions: list[str]) -> list[str]:
    if not expressions:
        return []
    if len(expressions) > 128 or any(
        not expr or len(expr) > 4096 for expr in expressions
    ):
        raise ValueError("Use 1-128 formulas, each with 1-4096 characters")
    try:
        result = subprocess.run(
            ["node", str(DIRECTORY / "mathjax.cjs")],
            input=json.dumps(expressions),
            text=True,
            capture_output=True,
            timeout=45,
            cwd=DIRECTORY,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Node.js is required for math; restore the CPU installation profile"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "MathJax exceeded the 45-second formula batch limit"
        ) from error
    if result.returncode:
        raise ValueError(
            "MathJax failed: "
            + result.stderr.strip()[:1000]
            + ". For a missing runtime, run npm ci --prefix format-conversion --ignore-scripts."
        )
    if len(result.stdout) > MAX_SVG_BYTES:
        raise ValueError("Rendered math exceeds the 16 MiB batch limit")
    outputs = json.loads(result.stdout)
    if not isinstance(outputs, list) or len(outputs) != len(expressions):
        raise RuntimeError("MathJax returned an incomplete batch")
    return outputs


def math_fragment(markup: str, font_size: float, color: str, prefix: str) -> tuple:
    root = SafeET.fromstring(markup)
    if any(item.tag == f"{{{NS}}}text" for item in root.iter()):
        raise ValueError(
            "MathJax has no vector glyph for part of this formula; "
            "put prose/CJK characters in a text span beside the math span"
        )
    x, y, width, height = map(float, root.attrib["viewBox"].split())
    scale = font_size / 1000
    dimensions = {
        "width": width * scale,
        "height": height * scale,
        "baseline": -y * scale,
    }
    if width <= 0 or height <= 0 or max(width, height) * scale > 8192:
        raise ValueError("Formula has empty or oversized bounds")
    root.attrib.pop("style", None)
    root.attrib.update(
        {
            "width": str(dimensions["width"]),
            "height": str(dimensions["height"]),
            "color": color,
            "role": "img",
        }
    )
    ids = {
        item.attrib["id"]: prefix + "-" + item.attrib["id"]
        for item in root.iter()
        if "id" in item.attrib
    }
    for item in root.iter():
        if "id" in item.attrib:
            item.set("id", ids[item.attrib["id"]])
        for attr, value in list(item.attrib.items()):
            if value == "currentColor":
                item.set(attr, color)
            elif attr in ("href", f"{{{XLINK}}}href") and value.startswith("#"):
                item.set(attr, "#" + ids[value[1:]])
    # The standalone SVG cannot rely on a surrounding HTML MathJax stylesheet.
    defs = element("defs", parent=root)
    element("style", parent=defs).text = (
        "[data-frame],[data-line]{stroke-width:70px;fill:none}"
        ".mjx-dashed{stroke-dasharray:140}"
        ".mjx-dotted{stroke-linecap:round;stroke-dasharray:0,140}"
        "use[data-c]{stroke-width:3px}"
    )
    return root, dimensions


def clone_math(root, prefix: str):
    clone = deepcopy(root)
    ids = {e.get("id"): prefix + "-" + e.get("id") for e in clone.iter() if e.get("id")}
    for item in clone.iter():
        if item.get("id"):
            item.set("id", ids[item.get("id")])
        for attr in ("href", f"{{{XLINK}}}href"):
            value = item.get(attr, "")
            if value.startswith("#"):
                item.set(attr, "#" + ids[value[1:]])
    return clone
