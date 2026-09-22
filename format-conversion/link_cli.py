"""Shared CLI options for document-link preflight and explicit PDF choices."""

import argparse
import json
from pathlib import Path

from document_links import inspect_pdf_links


def add_link_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inspect-links", action="store_true", help="Print link preflight JSON without converting")
    parser.add_argument("--pdf-targets", type=Path, help="JSON object mapping source paths to PDF paths or null")
    parser.add_argument("--link-policy", choices=("prefer-pdf", "preserve"), default="prefer-pdf")


def read_pdf_targets(args: argparse.Namespace) -> dict | None:
    if not args.pdf_targets:
        return None
    data = json.loads(args.pdf_targets.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(not isinstance(k, str) or (v is not None and not isinstance(v, str)) for k, v in data.items()):
        raise ValueError("--pdf-targets must contain an object mapping source paths to PDF paths or null")
    return data


def print_link_inspection(source: Path, targets: dict | None) -> None:
    print(json.dumps(inspect_pdf_links(str(source), targets), ensure_ascii=False, indent=2))
