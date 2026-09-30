"""Verify all seven tools through the actual launcher and stdio transport."""

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]


def test_real_mcp_round_trip(tmp_path, svg_browser_ready):
    async def exercise():
        parameters = StdioServerParameters(
            command=str(ROOT / "bin/mcp-tools"), args=["svg-diagram"]
        )
        async with asyncio.timeout(60):
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        "diagram_catalog",
                        "render_math",
                        "measure_labels",
                        "render_diagram",
                        "create_diagram",
                        "update_diagram",
                        "inspect_diagram",
                    }

                    async def call(name, arguments):
                        response = await session.call_tool(name, arguments)
                        assert not response.isError
                        payload = response.structuredContent or json.loads(
                            response.content[0].text
                        )
                        assert payload.get("status") != "error", payload
                        return payload

                    catalog = await call(
                        "diagram_catalog",
                        {"example": "stylegan2", "include_schema": True},
                    )
                    assert catalog["schema"]["properties"]["version"]["const"] == 1
                    measured = await call(
                        "measure_labels",
                        {"labels": [{"text": "Encoder"}, {"latex": r"z_q"}]},
                    )
                    assert measured["labels"][1]["width"] > 0
                    math = await call(
                        "render_math",
                        {
                            "expressions": [r"\hat x"],
                            "output_dir": str(tmp_path / "math"),
                        },
                    )
                    assert Path(math["formulas"][0]["output_path"]).is_file()
                    rendered = await call(
                        "render_diagram",
                        {
                            "spec": catalog["spec"],
                            "output_path": str(tmp_path / "model.svg"),
                        },
                    )
                    assert rendered["inspection"]["issues"] == []
                    report = await call(
                        "inspect_diagram", {"file_path": rendered["output_path"]}
                    )
                    assert report["status"] == "ok"
                    invalid = await session.call_tool(
                        "render_math",
                        {
                            "expressions": [r"\unknownMacro"],
                            "output_dir": str(tmp_path / "bad"),
                        },
                    )
                    failure = invalid.structuredContent or json.loads(
                        invalid.content[0].text
                    )
                    assert failure["status"] == "error"
                    assert not (tmp_path / "bad").exists()
                    created = await session.call_tool(
                        "create_diagram",
                        {
                            "template": "residual",
                            "parameters": {"channels": 128},
                            "output_path": str(tmp_path / "template.svg"),
                        },
                    )
                    assert not created.isError
                    assert any(block.type == "image" for block in created.content)
                    summary = created.structuredContent
                    assert "layout" not in summary and "input" in summary["node_ids"]
                    updated = await session.call_tool(
                        "update_diagram",
                        {
                            "file_path": summary["output_path"],
                            "expected_revision": summary["revision"],
                            "changes": [
                                {
                                    "op": "set_label",
                                    "id": "input",
                                    "values": {"text": "Updated input"},
                                }
                            ],
                        },
                    )
                    assert not updated.isError
                    assert any(block.type == "image" for block in updated.content)
                    assert updated.structuredContent["changes_applied"] == 1

    asyncio.run(exercise())
