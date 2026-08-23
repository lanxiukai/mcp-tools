#!/usr/bin/env python3
"""Exercise the real Format Conversion server through an MCP stdio session."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import fitz
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPO_DIR = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_DIR / "bin" / "mcp-tools"
EXPECTED_TEXT = "Hello from mcp-tools over MCP stdio."
MCP_ROUND_TRIP_TIMEOUT_SECONDS = 30


def create_demo_pdf(path: Path) -> None:
    """Create a one-page born-digital PDF without adding fixture files."""
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 96), EXPECTED_TEXT, fontsize=14)
        document.save(path)
    finally:
        document.close()


def result_payload(result: Any) -> dict[str, Any]:
    """Read structured output, with a text-content fallback for older clients."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    for block in result.content:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    raise RuntimeError("The MCP result did not contain a JSON object")


async def run_demo() -> None:
    with tempfile.TemporaryDirectory(prefix="mcp-tools-demo-") as temp_dir:
        pdf_path = Path(temp_dir) / "hello.pdf"
        create_demo_pdf(pdf_path)

        parameters = StdioServerParameters(
            command=str(LAUNCHER),
            args=["format-conversion"],
        )
        async with asyncio.timeout(MCP_ROUND_TRIP_TIMEOUT_SECONDS):
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "pdf_to_text",
                        {"file_path": str(pdf_path), "save_text": False},
                    )

        if result.isError:
            raise RuntimeError(f"pdf_to_text failed: {result.content!r}")
        payload = result_payload(result)
        extracted = str(payload.get("text", "")).strip()
        if EXPECTED_TEXT not in extracted:
            raise RuntimeError(f"Unexpected extracted text: {extracted!r}")

        names = ", ".join(tool.name for tool in tools.tools)
        print(f"Connected tools: {names}")
        print(f"Extracted text: {extracted}")
        print("MCP round trip: OK")


if __name__ == "__main__":
    try:
        asyncio.run(run_demo())
    except Exception as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
