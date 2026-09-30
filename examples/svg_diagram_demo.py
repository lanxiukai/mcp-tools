#!/usr/bin/env python3
"""Render three reusable model diagrams through real MCP stdio sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin/mcp-tools"


async def call(session, name, args):
    response = await session.call_tool(name, args)
    if response.isError:
        raise RuntimeError(f"{name}: {response.content}")
    payload = response.structuredContent
    if not isinstance(payload, dict):
        payload = json.loads(response.content[0].text)
    if payload.get("status") == "error":
        raise RuntimeError(f"{name}: {payload['error']}")
    return payload


async def run(output_dir: Path, theme="dark", overwrite=False, preview=False):
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    params = StdioServerParameters(command=str(LAUNCHER), args=["svg-diagram"])
    async with asyncio.timeout(180):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                for name in ("cvae", "stylegan2", "vq-vae"):
                    catalog = await call(session, "diagram_catalog", {"example": name})
                    spec = catalog["spec"]
                    spec["theme"] = theme
                    result = await call(
                        session,
                        "render_diagram",
                        {
                            "spec": spec,
                            "output_path": str(output_dir / f"{name}.svg"),
                            "overwrite": overwrite,
                        },
                    )
                    if result["inspection"]["status"] != "ok":
                        raise RuntimeError(f"{name}: {result['inspection']['issues']}")
                    results.append(result)
                    print(f"{name}: SVG ready, geometry clean")
    if preview:
        params = StdioServerParameters(
            command=str(LAUNCHER), args=["format-conversion"]
        )
        async with asyncio.timeout(90):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    for result in results:
                        path = Path(result["output_path"])
                        png = path.with_suffix(".png")
                        if png.exists() and not overwrite:
                            raise FileExistsError(
                                f"Preview exists; pass --overwrite: {png}"
                            )
                        await call(
                            session,
                            "svg_to_png",
                            {"file_path": str(path), "output_path": str(png)},
                        )
    print(f"SVG MCP round trip: OK ({output_dir})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--theme", choices=["dark", "print"], default="dark")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.output_dir, args.theme, args.overwrite, args.preview))
