#!/usr/bin/env python3
"""Generic local vision MCP server with resumable eyewear batch jobs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from vision_runtime import (
    VisionSettings,
    analyze_image as runtime_analyze_image,
    classify_eyewear as runtime_classify_eyewear,
    load_settings,
    server_health,
    verify_eyewear as runtime_verify_eyewear,
)


ROOT = Path(__file__).resolve().parent
mcp = FastMCP(
    name="Vision Local",
    json_response=True,
    instructions=(
        "Local image understanding with a persistent GPU backend. Use analyze_image for "
        "general visual questions, extract_text_from_image for visible text, analyze_chart "
        "for plots, classify_eyewear for a fast portrait pass, verify_eyewear for "
        "high-resolution cues, and classify_eyewear_batch for resumable audits. Interactive "
        "tools use the default 9B profile; batch audits automatically use the 4B profile."
    ),
)


def _error(exc: Exception) -> dict[str, str]:
    return {"error": str(exc), "error_type": type(exc).__name__}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _profile_status(settings: VisionSettings) -> dict[str, Any]:
    return {
        **server_health(settings),
        "profile": settings.profile,
        "server_binary_present": settings.server_binary.is_file(),
        "model_present": settings.model_path.is_file(),
        "mmproj_present": settings.mmproj_path.is_file(),
        "model_path": str(settings.model_path),
        "mmproj_path": str(settings.mmproj_path),
        "parallel": settings.parallel,
        "context_size": settings.context_size,
        "image_max_tokens": settings.image_max_tokens,
        "sleep_idle_seconds": settings.sleep_idle_seconds,
    }


@mcp.tool()
def vision_status() -> dict[str, Any]:
    """Report both model profiles and required artifacts without starting either backend."""
    try:
        default_status = _profile_status(load_settings())
        batch_status = _profile_status(load_settings("batch"))
        return {
            **default_status,
            "profiles": {
                "default": default_status,
                "batch": batch_status,
            },
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def analyze_image(
    file_path: str,
    prompt: str = "Describe the image accurately and concisely.",
    max_tokens: int = 512,
    max_edge: int = 1024,
) -> dict[str, Any]:
    """Analyze a local image using a custom natural-language prompt."""
    try:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 1 and 4096")
        if not 128 <= max_edge <= 2048:
            raise ValueError("max_edge must be between 128 and 2048")
        return runtime_analyze_image(
            file_path,
            prompt,
            max_tokens=max_tokens,
            max_edge=max_edge,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def extract_text_from_image(file_path: str) -> dict[str, Any]:
    """Extract all visible text from a local image while preserving reading order."""
    try:
        return runtime_analyze_image(
            file_path,
            "Transcribe all visible text exactly. Preserve reading order, line breaks, labels, and table structure. Do not summarize or invent missing text.",
            max_tokens=2048,
            max_edge=1536,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def analyze_chart(file_path: str, question: str = "") -> dict[str, Any]:
    """Analyze a chart, including axes, series, values, trends, and caveats."""
    try:
        prompt = (
            "Analyze this chart. Identify the chart type, title, axes, legend, units, key values, trends, comparisons, and any visual uncertainty. "
        )
        if question.strip():
            prompt += f"Also answer this question: {question.strip()}"
        return runtime_analyze_image(file_path, prompt, max_tokens=1024, max_edge=1536)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def classify_eyewear(file_path: str, max_edge: int = 512) -> dict[str, Any]:
    """Return structured evidence on whether the portrait subject is wearing glasses."""
    try:
        if not 256 <= max_edge <= 1024:
            raise ValueError("max_edge must be between 256 and 1024")
        return runtime_classify_eyewear(file_path, max_edge=max_edge)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def verify_eyewear(file_path: str) -> dict[str, Any]:
    """Carefully recheck a portrait at high resolution and return visible eyewear cues."""
    try:
        return runtime_verify_eyewear(file_path)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def classify_eyewear_batch(
    g_dir: str,
    ng_dir: str,
    output_dir: str,
    concurrency: int = 4,
    max_edge: int = 512,
    resume: bool = False,
) -> dict[str, Any]:
    """Launch a detached, resumable audit of G and NG portrait directories."""
    try:
        if not 1 <= concurrency <= 8:
            raise ValueError("concurrency must be between 1 and 8")
        if not 256 <= max_edge <= 1024:
            raise ValueError("max_edge must be between 256 and 1024")
        g_path = Path(g_dir).expanduser().resolve()
        ng_path = Path(ng_dir).expanduser().resolve()
        if not g_path.is_dir():
            raise FileNotFoundError(f"G directory not found: {g_path}")
        if not ng_path.is_dir():
            raise FileNotFoundError(f"NG directory not found: {ng_path}")

        output_path = Path(output_dir).expanduser().resolve()
        if output_path.exists() and not resume:
            raise FileExistsError(
                f"Output directory already exists: {output_path}; choose a new path or set resume=true"
            )
        output_path.mkdir(parents=True, exist_ok=resume)
        command = [
            sys.executable,
            str(ROOT / "batch_classify.py"),
            "--g-dir",
            str(g_path),
            "--ng-dir",
            str(ng_path),
            "--output-dir",
            str(output_path),
            "--concurrency",
            str(concurrency),
            "--max-edge",
            str(max_edge),
        ]
        command.append("--resume" if resume else "--no-resume")
        log_path = output_path / "batch.log"
        with log_path.open("ab", buffering=0) as log_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return {
            "status": "submitted",
            "pid": process.pid,
            "model_profile": "batch",
            "output_dir": str(output_path),
            "progress_file": str(output_path / "progress.json"),
            "log_file": str(log_path),
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def eyewear_batch_status(output_dir: str) -> dict[str, Any]:
    """Read progress and final artifacts for a submitted eyewear batch job."""
    try:
        output_path = Path(output_dir).expanduser().resolve()
        if not output_path.is_dir():
            raise FileNotFoundError(f"Batch output directory not found: {output_path}")
        progress = _read_json(output_path / "progress.json")
        summary = _read_json(output_path / "summary.json")
        manifest = _read_json(output_path / "manifest.json")
        pid = manifest.get("pid") if manifest else None
        process_alive = False
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
                process_alive = True
            except OSError:
                process_alive = False
        return {
            "output_dir": str(output_path),
            "process_alive": process_alive,
            "progress": progress,
            "summary": summary,
            "results_jsonl": str(output_path / "results.jsonl"),
            "misclassified_csv": str(output_path / "misclassified.csv"),
        }
    except Exception as exc:
        return _error(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
