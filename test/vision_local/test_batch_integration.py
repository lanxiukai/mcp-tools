"""Opt-in real-GPU stress and interruption tests for Vision batch jobs."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VISION_DIRECTORY = REPOSITORY_ROOT / "vision-local"
sys.path.insert(0, str(VISION_DIRECTORY))

import batch_classify  # noqa: E402


G_SOURCE = (
    REPOSITORY_ROOT
    / "mcp-tool-test/vision-local/samples/portrait-glasses-cary-grant.jpg"
)
NG_SOURCE = (
    REPOSITORY_ROOT
    / "mcp-tool-test/vision-local/samples/portrait-no-glasses-barack-obama.jpg"
)


def _populate_inputs(root: Path, *, total: int, corrupt: bool = False) -> tuple[Path, Path]:
    g_dir = root / "G"
    ng_dir = root / "NG"
    g_dir.mkdir(parents=True)
    ng_dir.mkdir(parents=True)
    g_count = (total + 1) // 2
    for index in range(g_count):
        shutil.copyfile(G_SOURCE, g_dir / f"portrait-{index:04d}.jpg")
    for index in range(total - g_count):
        shutil.copyfile(NG_SOURCE, ng_dir / f"portrait-{index:04d}.jpg")
    (g_dir / "unsupported.txt").write_text("ignored", encoding="utf-8")
    if corrupt:
        (ng_dir / "corrupt.png").write_bytes(b"not an image")
    return g_dir, ng_dir


def _arguments(
    g_dir: Path,
    ng_dir: Path,
    output_dir: Path,
    *,
    concurrency: int,
    resume: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        g_dir=g_dir,
        ng_dir=ng_dir,
        output_dir=output_dir,
        concurrency=concurrency,
        max_edge=512,
        retries=0,
        progress_every=25,
        resume=resume,
    )


def _run_batch(args: argparse.Namespace) -> tuple[int, float]:
    started = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        return_code = batch_classify.run(args)
    return return_code, time.perf_counter() - started


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_VISION_BATCH_INTEGRATION") == "1",
    "set MCP_TOOLS_VISION_BATCH_INTEGRATION=1 for real batch GPU tests",
)
class VisionBatchIntegrationTests(unittest.TestCase):
    def test_sizes_concurrency_and_failed_record_resume(self) -> None:
        metrics: dict[str, object] = {"sizes": {}, "concurrency": {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            for total in (1, 10, 104):
                case_root = root / f"size-{total}"
                g_dir, ng_dir = _populate_inputs(case_root, total=total)
                output_dir = case_root / "output"
                return_code, elapsed = _run_batch(
                    _arguments(
                        g_dir,
                        ng_dir,
                        output_dir,
                        concurrency=8,
                        resume=False,
                    )
                )
                latest = batch_classify.load_latest_records(
                    output_dir / "results.jsonl"
                )
                summary = _read_json(output_dir / "summary.json")

                self.assertEqual(return_code, 0)
                self.assertEqual(len(latest), total)
                self.assertEqual(summary["total_images"], total)
                self.assertEqual(summary["error_images"], 0)
                self.assertEqual(len({record["file"] for record in latest.values()}), total)
                metrics["sizes"][str(total)] = round(elapsed, 3)

            for concurrency in (1, 4, 8):
                case_root = root / f"concurrency-{concurrency}"
                g_dir, ng_dir = _populate_inputs(case_root, total=16)
                output_dir = case_root / "output"
                return_code, elapsed = _run_batch(
                    _arguments(
                        g_dir,
                        ng_dir,
                        output_dir,
                        concurrency=concurrency,
                        resume=False,
                    )
                )
                self.assertEqual(return_code, 0)
                self.assertEqual(
                    _read_json(output_dir / "summary.json")["successful_images"],
                    16,
                )
                metrics["concurrency"][str(concurrency)] = round(elapsed, 3)

            retry_root = root / "failed-resume"
            g_dir, ng_dir = _populate_inputs(retry_root, total=8, corrupt=True)
            output_dir = retry_root / "output"
            first_code, _ = _run_batch(
                _arguments(
                    g_dir,
                    ng_dir,
                    output_dir,
                    concurrency=4,
                    resume=False,
                )
            )
            first_summary = _read_json(output_dir / "summary.json")
            self.assertEqual(first_code, 2)
            self.assertEqual(first_summary["error_images"], 1)

            shutil.copyfile(NG_SOURCE, ng_dir / "corrupt.png")
            resumed_code, _ = _run_batch(
                _arguments(
                    g_dir,
                    ng_dir,
                    output_dir,
                    concurrency=4,
                    resume=True,
                )
            )
            resumed_summary = _read_json(output_dir / "summary.json")
            latest = batch_classify.load_latest_records(output_dir / "results.jsonl")
            corrupt_record = latest[str((ng_dir / "corrupt.png").resolve())]

            self.assertEqual(resumed_code, 0)
            self.assertEqual(resumed_summary["total_images"], 9)
            self.assertEqual(resumed_summary["error_images"], 0)
            self.assertIsNone(corrupt_record["error"])
            self.assertEqual(_line_count(output_dir / "results.jsonl"), 10)

        print("VISION_BATCH_METRICS=" + json.dumps(metrics, sort_keys=True))

    def test_signal_interruptions_resume_without_losing_completed_records(self) -> None:
        script = VISION_DIRECTORY / "batch_classify.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for signal_value, label in (
                (signal.SIGINT, "sigint"),
                (signal.SIGTERM, "sigterm"),
                (signal.SIGKILL, "sigkill"),
            ):
                with self.subTest(signal=label):
                    case_root = root / label
                    g_dir, ng_dir = _populate_inputs(case_root, total=20)
                    output_dir = case_root / "output"
                    command = [
                        sys.executable,
                        str(script),
                        "--g-dir",
                        str(g_dir),
                        "--ng-dir",
                        str(ng_dir),
                        "--output-dir",
                        str(output_dir),
                        "--concurrency",
                        "1",
                        "--retries",
                        "0",
                        "--progress-every",
                        "1",
                        "--no-resume",
                    ]
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    results_path = output_dir / "results.jsonl"
                    deadline = time.monotonic() + 60
                    while _line_count(results_path) < 1 and time.monotonic() < deadline:
                        if process.poll() is not None:
                            self.fail(
                                f"batch exited before {label} injection: {process.returncode}"
                            )
                        time.sleep(0.05)
                    self.assertGreaterEqual(_line_count(results_path), 1)

                    process.send_signal(signal_value)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        self.fail(f"batch did not stop promptly after {label}")

                    completed_before_resume = _line_count(results_path)
                    self.assertLess(completed_before_resume, 20)

                    resume_command = command[:-1] + ["--resume"]
                    resumed = subprocess.run(
                        resume_command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    self.assertEqual(resumed.returncode, 0, resumed.stderr)
                    latest = batch_classify.load_latest_records(results_path)
                    summary = _read_json(output_dir / "summary.json")
                    self.assertEqual(len(latest), 20)
                    self.assertEqual(summary["successful_images"], 20)
                    self.assertEqual(summary["error_images"], 0)


if __name__ == "__main__":
    unittest.main()
