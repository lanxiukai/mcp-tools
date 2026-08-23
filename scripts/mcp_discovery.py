"""Verify every repository-owned MCP frontend and its stable tool registry."""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class ServerContract:
    name: str
    script: Path
    tools: frozenset[str]


SERVER_CONTRACTS = (
    ServerContract(
        "format-conversion",
        REPOSITORY_ROOT / "format-conversion" / "format_mcp_server.py",
        frozenset({"markdown_to_pdf", "html_to_pdf", "pdf_to_text"}),
    ),
    ServerContract(
        "browser-fetch",
        REPOSITORY_ROOT / "browser-fetch" / "browser_fetch_mcp_server.py",
        frozenset(
            {"fetch_page", "fetch_page_with_engine", "screenshot", "browser_status"}
        ),
    ),
    ServerContract(
        "asr",
        REPOSITORY_ROOT / "asr" / "asr_mcp_server.py",
        frozenset(
            {
                "transcribe_audio",
                "asr_status",
                "transcribe_diarized",
                "transcribe_podcast",
            }
        ),
    ),
    ServerContract(
        "ocr",
        REPOSITORY_ROOT / "ocr" / "ocr_mcp_server.py",
        frozenset({"ocr_document", "ocr_submit", "ocr_wait", "ocr_status"}),
    ),
    ServerContract(
        "vision-local",
        REPOSITORY_ROOT / "vision-local" / "vision_local_mcp_server.py",
        frozenset(
            {
                "vision_status",
                "analyze_image",
                "extract_text_from_image",
                "analyze_chart",
                "classify_eyewear",
                "verify_eyewear",
                "classify_eyewear_batch",
                "eyewear_batch_status",
            }
        ),
    ),
)


async def discover(contract: ServerContract, python: Path, timeout: float) -> int:
    if not contract.script.is_file():
        raise FileNotFoundError(
            f"{contract.name}: entrypoint missing: {contract.script}"
        )

    environment = os.environ.copy()
    environment.pop("BRAVE_API_KEY", None)
    environment.pop("HF_TOKEN", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    parameters = StdioServerParameters(
        command=str(python),
        args=[str(contract.script)],
        env=environment,
    )

    async with asyncio.timeout(timeout):
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                response = await session.list_tools()

    discovered = frozenset(tool.name for tool in response.tools)
    missing = sorted(contract.tools - discovered)
    unexpected = sorted(discovered - contract.tools)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise RuntimeError(
            f"{contract.name}: tool contract changed ({'; '.join(details)})"
        )

    print(f"{contract.name}: {len(discovered)} tools OK")
    return len(discovered)


async def run(python: Path, timeout: float) -> None:
    total = 0
    for contract in SERVER_CONTRACTS:
        try:
            total += await discover(contract, python, timeout)
        except Exception as error:
            raise RuntimeError(
                f"MCP discovery failed for {contract.name}: {error}"
            ) from error
    print(f"MCP discovery: {total} tools across {len(SERVER_CONTRACTS)} frontends OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=REPOSITORY_ROOT
        / "environments"
        / "mcp-local"
        / ".venv"
        / "bin"
        / "python",
        help="Python interpreter used to start lightweight frontends",
    )
    parser.add_argument(
        "--timeout", type=float, default=20.0, help="Seconds allowed per server"
    )
    args = parser.parse_args()

    python = args.python.expanduser()
    if not python.is_absolute():
        python = (Path.cwd() / python).absolute()
    if not python.is_file():
        parser.error(f"Python interpreter not found: {python}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    asyncio.run(run(python, args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
