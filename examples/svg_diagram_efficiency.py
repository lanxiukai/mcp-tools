#!/usr/bin/env python3
"""Compare full-spec and compact SVG workflows through real MCP calls."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "svg-diagram"))
from svg_diagram.editing import Change, apply_changes  # noqa: E402
from svg_diagram.templates import build  # noqa: E402


async def benchmark(output_dir: Path, language: str = "en", overwrite: bool = False):
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "measurements.json"
    if report_path.exists() and not overwrite:
        raise FileExistsError(f"Use --overwrite to replace {report_path}")
    records = []
    async with AsyncExitStack() as stack:
        sessions = {}
        for name in ("svg-diagram", "format-conversion"):
            streams = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=str(ROOT / "bin/mcp-tools"), args=[name]
                    )
                )
            )
            session = await stack.enter_async_context(ClientSession(*streams))
            await session.initialize()
            sessions[name] = session

        async def workflow(name, phase, variant, requests):
            exchanges = []
            started = time.perf_counter()
            for server, tool, arguments in requests:
                response = await sessions[server].call_tool(tool, arguments)
                if response.isError:
                    raise RuntimeError(str(response.content))
                payload = response.structuredContent or json.loads(
                    response.content[0].text
                )
                if payload.get("status") == "error":
                    raise RuntimeError(str(payload["error"]))
                if payload.get("inspection", {}).get("status", "ok") != "ok":
                    raise RuntimeError(str(payload["inspection"]))
                exchanges.append(
                    {
                        "request": {"tool": tool, "arguments": arguments},
                        "response": payload,
                    }
                )
            record = {
                "example": name,
                "phase": phase,
                "variant": variant,
                "calls": len(exchanges),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "exchanges": exchanges,
            }
            for side in ("request", "response"):
                record[side + "_characters"] = sum(
                    len(
                        json.dumps(
                            item[side], ensure_ascii=False, separators=(",", ":")
                        )
                    )
                    for item in exchanges
                )
            records.append(record)
            print(
                json.dumps(
                    {key: value for key, value in record.items() if key != "exchanges"}
                ),
                flush=True,
            )
            return exchanges[0]["response"]

        for name in ("cvae", "stylegan2", "vq-vae"):
            spec = build(name, {"language": language})
            full, compact = (
                output_dir / f"{name}-full.svg",
                output_dir / f"{name}-compact.svg",
            )
            if not overwrite and any(
                path.exists()
                for path in (
                    full,
                    compact,
                    full.with_suffix(".preview.png"),
                    compact.with_suffix(".preview.png"),
                )
            ):
                raise FileExistsError(
                    f"Example artifacts already exist for {name}; use --overwrite"
                )

            def full_calls(model, editing=False):
                return [
                    (
                        "svg-diagram",
                        "render_diagram",
                        {
                            "spec": model.model_dump(
                                exclude_defaults=True, exclude_none=True
                            ),
                            "output_path": str(full),
                            "overwrite": overwrite or editing,
                        },
                    ),
                    (
                        "format-conversion",
                        "svg_to_png",
                        {
                            "file_path": str(full),
                            "output_path": str(full.with_suffix(".preview.png")),
                        },
                    ),
                ]

            await workflow(name, "create", "full", full_calls(spec))
            created = await workflow(
                name,
                "create",
                "compact",
                [
                    (
                        "svg-diagram",
                        "create_diagram",
                        {
                            "template": name,
                            "parameters": {"language": language},
                            "output_path": str(compact),
                            "preview": "file",
                            "overwrite": overwrite,
                        },
                    )
                ],
            )
            assert full.read_bytes() == compact.read_bytes(), (
                "Creation paths produced different SVGs"
            )
            first = spec.nodes[0]
            changes = [
                Change(
                    op="set_label",
                    id=first.id,
                    values={"text": first.labels[0].text + " v2"},
                )
            ]
            revised = apply_changes(spec, changes)
            await workflow(name, "edit", "full", full_calls(revised, editing=True))
            await workflow(
                name,
                "edit",
                "compact",
                [
                    (
                        "svg-diagram",
                        "update_diagram",
                        {
                            "file_path": str(compact),
                            "expected_revision": created["revision"],
                            "preview": "file",
                            "changes": [
                                change.model_dump(
                                    exclude_defaults=True, exclude_none=True
                                )
                                for change in changes
                            ],
                        },
                    )
                ],
            )
            assert full.read_bytes() == compact.read_bytes(), (
                "Edit paths produced different SVGs"
            )
    report = {
        "scope": "JSON request and result payloads for SVG+PNG creation and one label edit; tool schemas, messages, image tokens, and billing are excluded.",
        "serialization": "UTF-8 JSON with ensure_ascii=False and compact separators; each exchange counted separately.",
        "timing": "One local sequential observation per case; includes tool execution, excludes server initialization and visual review. Not a general speed benchmark.",
        "equivalent_svg": True,
        "records": records,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language", choices=["en", "zh"], default="en")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    asyncio.run(benchmark(args.output_dir, args.language, args.overwrite))
