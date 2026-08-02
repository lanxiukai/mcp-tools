#!/usr/bin/env python3
"""End-to-end stdio MCP smoke test over the public vision-local fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPO_ROOT = Path(__file__).resolve().parents[2]


def unpack_result(result: Any) -> dict[str, Any]:
    structured = result.structuredContent
    if isinstance(structured, dict):
        if set(structured) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {"error": "MCP result had no structured JSON", "raw": str(result)}


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    sample_root = args.sample_root.resolve()
    manifest = json.loads((sample_root / "samples.json").read_text(encoding="utf-8"))
    server = StdioServerParameters(
        command=str(args.python),
        args=[str(args.server)],
    )
    report: dict[str, Any] = {
        "started_at_epoch": time.time(),
        "sample_root": str(sample_root),
        "server": str(args.server),
        "cases": [],
    }
    failures: list[str] = []

    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            report["tools"] = [tool.name for tool in tools.tools]

            status_result = await session.call_tool(
                "vision_status",
                {},
                read_timeout_seconds=timedelta(seconds=30),
            )
            report["initial_status"] = unpack_result(status_result)

            for sample in manifest["samples"]:
                file_path = sample_root / sample["file"]
                kind = sample["kind"]
                if kind == "portrait":
                    tool = "classify_eyewear"
                    arguments = {"file_path": str(file_path)}
                elif kind == "general":
                    tool = "analyze_image"
                    arguments = {
                        "file_path": str(file_path),
                        "prompt": sample["prompt"],
                        "max_tokens": 96,
                    }
                elif kind == "chart":
                    tool = "analyze_chart"
                    arguments = {
                        "file_path": str(file_path),
                        "question": sample["question"],
                    }
                elif kind == "text":
                    tool = "extract_text_from_image"
                    arguments = {"file_path": str(file_path)}
                else:
                    continue

                started = time.perf_counter()
                call_result = await session.call_tool(
                    tool,
                    arguments,
                    read_timeout_seconds=timedelta(seconds=args.timeout),
                )
                payload = unpack_result(call_result)
                case = {
                    "file": sample["file"],
                    "kind": kind,
                    "tool": tool,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "result": payload,
                }
                if payload.get("error"):
                    failures.append(f"{sample['file']}: {payload['error']}")
                elif kind == "portrait":
                    expected = sample["expected_wearing_glasses"]
                    actual = payload.get("wearing_glasses")
                    case["expected_wearing_glasses"] = expected
                    case["passed"] = actual is expected
                    if actual is not expected:
                        failures.append(
                            f"{sample['file']}: expected wearing_glasses={expected}, got {actual}"
                        )
                else:
                    text = payload.get("text")
                    case["passed"] = isinstance(text, str) and bool(text.strip())
                    if not case["passed"]:
                        failures.append(f"{sample['file']}: empty text result")
                report["cases"].append(case)

            verification_sample = next(
                sample for sample in manifest["samples"] if sample["kind"] == "portrait"
            )
            verification_path = sample_root / verification_sample["file"]
            started = time.perf_counter()
            verification_result = await session.call_tool(
                "verify_eyewear",
                {"file_path": str(verification_path)},
                read_timeout_seconds=timedelta(seconds=args.timeout),
            )
            verification_payload = unpack_result(verification_result)
            verification_expected = verification_sample["expected_wearing_glasses"]
            verification_passed = (
                not verification_payload.get("error")
                and verification_payload.get("wearing_glasses") is verification_expected
                and bool(verification_payload.get("visual_cues"))
            )
            report["cases"].append(
                {
                    "file": verification_sample["file"],
                    "kind": "high_resolution_verification",
                    "tool": "verify_eyewear",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "expected_wearing_glasses": verification_expected,
                    "result": verification_payload,
                    "passed": verification_passed,
                }
            )
            if not verification_passed:
                failures.append(
                    f"{verification_sample['file']}: high-resolution verification failed"
                )

            final_status = await session.call_tool(
                "vision_status",
                {},
                read_timeout_seconds=timedelta(seconds=30),
            )
            report["final_status"] = unpack_result(final_status)

    report["completed_at_epoch"] = time.time()
    report["elapsed_seconds"] = round(
        report["completed_at_epoch"] - report["started_at_epoch"], 3
    )
    report["passed"] = not failures
    report["failures"] = failures
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-root",
        type=Path,
        default=REPO_ROOT / "mcp-tool-test/vision-local",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python used to launch the MCP server (default: current interpreter)",
    )
    parser.add_argument(
        "--server",
        type=Path,
        default=REPO_ROOT / "vision-local/vision_local_mcp_server.py",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    report = asyncio.run(run_smoke(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
