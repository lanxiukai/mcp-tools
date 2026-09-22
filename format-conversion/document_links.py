"""Inspect local document references and select existing derived PDFs.

Never infer an arbitrary PDF filename from its contents or silently choose one
of several editions. Callers can supply a reviewed mapping; .pdf-links.json
stores the same mapping relative to its own directory.
"""

from __future__ import annotations

import json
import os
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import fitz
from markdown_it import MarkdownIt

LinkPolicy = Literal["prefer-pdf", "preserve"]
PdfTargets = dict[str, str | None]
SOURCE_SUFFIXES = {".md", ".markdown", ".html", ".htm"}
SOURCE_MARKER = "mcp-tools-source:"


class PdfMarkdownIt(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        return url.strip().lower().startswith("file:") or super().validateLink(url)


class _Links(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=False)
        self.base: str | None = None
        self.links: list[tuple[str, int, int, str]] = []
        self.offsets = [0]
        for line in html.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        href = attributes.get("href")
        if href is None:
            return
        if tag == "base" and self.base is None:
            self.base = href
        if tag not in {"a", "area"}:
            return
        raw = self.get_starttag_text()
        # Consume complete attributes so text inside a quoted title cannot be
        # mistaken for the href attribute being replaced.
        attributes_start = re.match(r"<\s*[^\s/>]+", raw).end()
        match = next((
            match for match in re.finditer(
                r'''([^\s=<>/'"]+)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?''',
                raw[attributes_start:],
            ) if match.group(1).lower() == "href"
        ), None)
        if match:
            line, column = self.getpos()
            offset = self.offsets[line - 1] + column
            self.links.append((href, offset + attributes_start + match.start(), offset + attributes_start + match.end(), tag))

    handle_startendtag = handle_starttag


def _pdf_exists(path: Path) -> bool:
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            return b"%PDF-" in stream.read(1024)
    except OSError:
        return False


def _absolute(path: str, directory: Path) -> Path:
    candidate = Path(path).expanduser()
    return (directory / candidate).resolve()


def _manifest_choice(source: Path) -> tuple[bool, Path | None]:
    for directory in (source.parent, *source.parent.parents):
        manifest = directory / ".pdf-links.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"PDF link mapping must be an object: {manifest}")
            for key, value in data.items():
                if _absolute(key, directory) == source:
                    if value is not None and not isinstance(value, str):
                        raise ValueError(f"Choose one PDF filename or null in {manifest}: {key}")
                    return True, _absolute(value, directory) if value is not None else None
        if (directory / ".git").exists():
            break
    return False, None


def _recorded_source(pdf: Path) -> Path | None:
    try:
        with fitz.open(pdf) as document:
            keywords = (document.metadata or {}).get("keywords", "") or ""
        for item in keywords.split(";"):
            if item.strip().startswith(SOURCE_MARKER):
                return (pdf.parent / unquote(item.strip()[len(SOURCE_MARKER):])).resolve()
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def select_pdf(source: Path, overrides: dict[Path, Path | None], catalog: dict) -> dict:
    """Return evidence, including candidates that require human/agent review."""
    explicit = source in overrides
    chosen = overrides.get(source)
    origin = "argument"
    if not explicit:
        explicit, chosen = _manifest_choice(source)
        origin = "manifest"
    if explicit:
        if chosen is not None and not _pdf_exists(chosen):
            raise ValueError(f"Selected PDF does not exist or is not a PDF: {chosen}")
        return {
            "status": "selected" if chosen else "source-selected",
            "pdf_path": str(chosen) if chosen else None,
            "basis": origin,
            "candidates": [str(chosen)] if chosen else [],
        }
    # Stay local: no recursive scan of repositories or the user's home.
    if source.parent not in catalog:
        paths = sorted(p.resolve() for p in source.parent.glob("*.[pP][dD][fF]") if _pdf_exists(p))
        catalog[source.parent] = [(pdf, _recorded_source(pdf)) for pdf in paths]
    nearby = [pdf for pdf, _ in catalog[source.parent]]
    exact: list[Path] = []
    recorded: list[Path] = []
    variants: list[Path] = []
    for pdf, provenance in catalog[source.parent]:
        if provenance == source:
            recorded.append(pdf)
        elif provenance is not None:
            continue
        elif pdf.stem == source.stem:
            exact.append(pdf)
        elif any(pdf.stem.startswith(source.stem + separator) for separator in ("-", "_", ".", " ", "(", "（")):
            variants.append(pdf)
    candidates = sorted(set(exact + recorded + variants))
    competing_sources = [
        p for p in source.parent.iterdir()
        if p != source and p.is_file() and p.stem == source.stem
        and p.suffix.lower() in SOURCE_SUFFIXES
    ]
    selected = (
        len(candidates) == 1 and not variants
        and (bool(recorded) or (bool(exact) and not competing_sources))
    )
    return {
        "status": "selected" if selected else "ambiguous" if candidates else "no-pdf",
        "pdf_path": str(candidates[0]) if selected else None,
        "basis": "recorded-source" if selected and recorded else "same-stem" if selected else "inspection-required",
        "candidates": [str(p) for p in candidates],
        "nearby_pdfs": [str(p) for p in nearby] if not selected else [],
        "competing_sources": [str(p) for p in competing_sources],
    }


def prepare_links(
    html: str,
    source: Path,
    *,
    pdf_targets: PdfTargets | None = None,
    link_policy: LinkPolicy = "prefer-pdf",
    require_selection: bool = False,
) -> tuple[str, dict]:
    if link_policy not in {"prefer-pdf", "preserve"}:
        raise ValueError("link_policy must be 'prefer-pdf' or 'preserve'")
    source = source.resolve()
    overrides = {
        _absolute(key, source.parent): _absolute(value, source.parent) if value is not None else None
        for key, value in (pdf_targets or {}).items()
    }
    parser = _Links(html)
    base = urljoin(source.as_uri(), parser.base) if parser.base is not None else source.as_uri()
    entries = []
    decisions: dict[Path, dict] = {}
    catalog: dict = {}
    replacements = []
    seen: set[str] = set()
    for href, start, end, _tag in parser.links:
        stripped = href.strip()
        if not stripped or stripped.startswith("#"):
            continue
        resolved = urlsplit(urljoin(base, stripped))
        if resolved.scheme != "file" or resolved.netloc not in {"", "localhost"}:
            continue
        target = Path(unquote(resolved.path)).resolve()
        entry = {
            "href": href, "source_path": str(target), "source_exists": target.is_file(),
            "status": "original", "pdf_path": None, "candidates": [],
        }
        if link_policy == "prefer-pdf" and target.suffix.lower() in SOURCE_SUFFIXES:
            if target not in decisions:
                decisions[target] = select_pdf(target, overrides, catalog) if target.is_file() else {
                    "status": "missing-source", "pdf_path": None, "candidates": [],
                }
            entry.update(decisions[target])
        destination = Path(entry["pdf_path"]) if entry["pdf_path"] else target
        new = urlsplit(destination.as_uri())
        entry["target_uri"] = urlunsplit((new.scheme, new.netloc, new.path, resolved.query, resolved.fragment))
        if resolved.fragment and entry["pdf_path"]:
            entry["fragment_note"] = "Opening the PDF is supported; a source HTML/Markdown anchor needs a matching PDF named destination."
        replacements.append((start, end, f'href="{escape(entry["target_uri"], quote=True)}"'))
        if href not in seen:
            entries.append(entry)
            seen.add(href)
    ambiguous = [entry for entry in entries if entry["status"] == "ambiguous"]
    if ambiguous and require_selection:
        paths = ", ".join(entry["source_path"] for entry in ambiguous)
        raise ValueError(
            f"Ambiguous PDF targets: {paths}. Run inspect_pdf_links and supply pdf_targets "
            "with the chosen PDF path, or null to keep the source file."
        )
    for start, end, replacement in reversed(replacements):
        html = html[:start] + replacement + html[end:]
    return html, {
        "source_path": str(source), "link_policy": link_policy,
        "requires_selection": bool(ambiguous), "links": entries,
    }


def inspect_pdf_links(file_path: str, pdf_targets: PdfTargets | None = None) -> dict:
    source = Path(file_path).resolve()
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() in {".md", ".markdown"}:
        parser = PdfMarkdownIt("commonmark", {"html": True})
        parser.enable(["table", "strikethrough"])
        text = parser.render(text)
    elif source.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("Link inspection requires a Markdown or HTML source file")
    return prepare_links(text, source, pdf_targets=pdf_targets)[1]


def record_pdf_source(pdf: Path, source: Path, output: Path) -> None:
    """Store portable provenance in the generated PDF, without sidecar writes."""
    relative = os.path.relpath(source.resolve(), output.resolve().parent)
    with fitz.open(pdf) as document:
        metadata = document.metadata
        items = [
            item.strip() for item in (metadata.get("keywords") or "").split(";")
            if item.strip() and not item.strip().startswith(SOURCE_MARKER)
        ]
        items.append(SOURCE_MARKER + quote(relative, safe="/"))
        if "mcp-tools-links:resolved" not in items:
            items.append("mcp-tools-links:resolved")
        metadata["keywords"] = "; ".join(items)
        document.set_metadata(metadata)
        document.saveIncr()
