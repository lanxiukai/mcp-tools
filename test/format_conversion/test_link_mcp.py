"""Verify link preflight and explicit choices through a fresh MCP server."""

import asyncio
import json
import os
import sys
from pathlib import Path

import fitz
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_link_preflight_and_conversion(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("[Guide](guide.html)\n\n[Script](example.py)")
    (tmp_path / "guide.html").write_text("<h1>Guide</h1>")
    (tmp_path / "example.py").write_text("# Link target")
    with fitz.open() as document:
        document.new_page().insert_text((40, 300), "Methods")
        document.save(tmp_path / "custom.pdf")

    async def exercise():
        server = Path(__file__).resolve().parents[2] / "format-conversion/format_mcp_server.py"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(server)], env=env)) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {"inspect_pdf_links", "resolve_pdf_destination"} <= {tool.name for tool in tools.tools}
                resolved = await session.call_tool("resolve_pdf_destination", {"file_path": str(tmp_path / "custom.pdf"), "target": "Methods"})
                assert not resolved.isError
                destination = json.loads(resolved.content[0].text)
                assert destination["page"] == 1
                source.write_text("[Guide](guide.html#custom-id)\n\n[Script](example.py)")
                arguments = {"file_path": str(source), "pdf_targets": {"guide.html": "custom.pdf"},
                             "pdf_destinations": {"guide.html#custom-id": destination["fragment"]}}
                inspection = await session.call_tool("inspect_pdf_links", arguments)
                assert not inspection.isError
                report = json.loads(inspection.content[0].text)
                assert report["links"][0]["pdf_path"] == str(tmp_path / "custom.pdf")
                converted = await session.call_tool("markdown_to_pdf", {**arguments, "engine": "weasyprint"})
                assert not converted.isError
                result = json.loads(converted.content[0].text)
                assert result["link_report"]["links"][1]["target_uri"].endswith("example.py")
                assert Path(result["output_path"]).is_file()
    async def bounded():
        async with asyncio.timeout(30):
            await exercise()
    asyncio.run(bounded())
