"""Sequential real-GPU validation for the bounded ASR and Vision profiles."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VISION_DIRECTORY = REPOSITORY_ROOT / "vision-local"
sys.path.insert(0, str(VISION_DIRECTORY))

import vision_runtime  # noqa: E402
from scripts.gpu_test_policy import require_8gb_profile_budget  # noqa: E402


ASR_PYTHON = REPOSITORY_ROOT / "environments/mcp-local-asr/.venv/bin/python"
ASR_SERVER = REPOSITORY_ROOT / "asr/qwen3_asr_server.py"
SMOKE_AUDIO = REPOSITORY_ROOT / "mcp-tool-test/smoke-test/asr_smoke_test.wav"
VISION_IMAGE = (
    REPOSITORY_ROOT
    / "mcp-tool-test/vision-local/samples/portrait-glasses-cary-grant.jpg"
)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_vram_mib() -> float:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--id=0",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(result.stdout.strip().splitlines()[0])


def _assert_no_compute_processes() -> None:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    active = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if active:
        raise RuntimeError(
            "GPU compute processes are already active; bounded profile "
            f"validation must run serially: {active}"
        )


def _signal_process_group(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _stop_process_group(process: subprocess.Popen[Any] | None) -> None:
    _signal_process_group(process)
    if process is None:
        return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


class _VramMonitor:
    def __init__(self, budget_mib: int, abort: Callable[[], None]) -> None:
        self.budget_mib = budget_mib
        self.abort_threshold_mib = max(1, budget_mib - 256)
        self.abort = abort
        self.samples: list[float] = []
        self.violation: str | None = None
        self._sampler: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        baseline = _read_vram_mib()
        self.samples.append(baseline)
        if baseline >= self.abort_threshold_mib:
            raise RuntimeError(
                f"GPU baseline {baseline:.0f} MiB leaves insufficient room below "
                f"the {self.budget_mib} MiB budget; stop other GPU workloads"
            )
        self._sampler = subprocess.Popen(
            [
                "nvidia-smi",
                "--id=0",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
                "--loop-ms=50",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._thread = threading.Thread(target=self._read_samples, daemon=True)
        self._thread.start()

    def _read_samples(self) -> None:
        assert self._sampler is not None and self._sampler.stdout is not None
        for line in self._sampler.stdout:
            try:
                value = float(line.strip())
            except ValueError:
                continue
            self.samples.append(value)
            if value >= self.abort_threshold_mib:
                self.violation = (
                    f"observed {value:.0f} MiB at or above the conservative abort "
                    f"threshold {self.abort_threshold_mib} MiB"
                )
                self.abort()
                return

    def stop(self) -> float:
        if self._sampler is not None and self._sampler.poll() is None:
            self._sampler.terminate()
            try:
                self._sampler.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._sampler.kill()
                self._sampler.wait(timeout=5)
        if self._thread is not None:
            self._thread.join(timeout=5)
        if not self.samples:
            raise AssertionError("VRAM monitor captured no samples")
        peak = max(self.samples)
        if self.violation is not None:
            raise AssertionError(self.violation)
        if peak > self.budget_mib:
            raise AssertionError(
                f"observed {peak:.0f} MiB above budget {self.budget_mib} MiB"
            )
        return peak


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _assert_heavyweight_ports_idle() -> None:
    active: list[int] = []
    for port in (8000, 8002, 8003, 8004, 8005):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(0.2)
            if client.connect_ex(("127.0.0.1", port)) == 0:
                active.append(port)
    if active:
        joined = ", ".join(str(port) for port in active)
        raise RuntimeError(
            f"heavyweight backend ports are active ({joined}); profile validation "
            "must run serially"
        )


def _wait_for_json(
    url: str,
    process: subprocess.Popen[Any],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"backend exited with status {process.returncode}")
        try:
            with DIRECT_OPENER.open(url, timeout=2) as response:
                return json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise TimeoutError(f"backend did not become ready: {last_error}")


def _post_audio(url: str, path: Path, timeout: float = 300) -> dict[str, Any]:
    boundary = f"----mcp-tools-8gb-{uuid.uuid4().hex}"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="language"\r\n\r\nEnglish',
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with DIRECT_OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def _wait_for_release(target_mib: float, timeout: float = 60) -> float:
    deadline = time.monotonic() + timeout
    current = _read_vram_mib()
    while current > target_mib and time.monotonic() < deadline:
        time.sleep(0.5)
        current = _read_vram_mib()
    if current > target_mib:
        raise RuntimeError(
            f"GPU memory did not settle below {target_mib:.0f} MiB; "
            f"last sample was {current:.0f} MiB"
        )
    return current


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_8GB_GPU_INTEGRATION") == "1",
    "set MCP_TOOLS_8GB_GPU_INTEGRATION=1 for real sequential GPU validation",
)
class EightGbProfileIntegrationTests(unittest.TestCase):
    def test_asr_then_vision_stay_below_budget(self) -> None:
        budget_mib = require_8gb_profile_budget()
        asr_model = Path(os.environ.get("ASR_8GB_MODEL_PATH", ""))
        self.assertTrue(
            (asr_model / "model.safetensors").is_file(),
            "ASR_8GB_MODEL_PATH must point to Qwen3-ASR-0.6B",
        )
        self.assertTrue(ASR_PYTHON.is_file())
        self.assertTrue(SMOKE_AUDIO.is_file())
        self.assertTrue(VISION_IMAGE.is_file())
        _assert_no_compute_processes()
        _assert_heavyweight_ports_idle()

        metrics: dict[str, dict[str, float | int | str]] = {}
        with tempfile.TemporaryDirectory(prefix="mcp-tools-8gb-profiles-") as directory:
            root = Path(directory)
            long_audio = root / "asr-61-seconds.flac"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-stream_loop",
                    "10",
                    "-i",
                    str(SMOKE_AUDIO),
                    "-t",
                    "61",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "flac",
                    str(long_audio),
                ],
                check=True,
            )

            baseline = _read_vram_mib()
            asr_process: subprocess.Popen[bytes] | None = None
            asr_monitor = _VramMonitor(
                budget_mib,
                lambda: _signal_process_group(asr_process),
            )
            asr_log = root / "asr.log"
            asr_port = _free_port()
            started = time.monotonic()
            failure: BaseException | None = None
            asr_monitor.start()
            try:
                with asr_log.open("wb") as log_file:
                    asr_process = subprocess.Popen(
                        [
                            str(ASR_PYTHON),
                            str(ASR_SERVER),
                            "--profile",
                            "8gb",
                            "--model",
                            str(asr_model),
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(asr_port),
                        ],
                        cwd=REPOSITORY_ROOT,
                        env={
                            **os.environ,
                            "PYTHONNOUSERSITE": "1",
                            "ASR_TEMP_ROOT": str(root / "asr-temp"),
                        },
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                health = _wait_for_json(
                    f"http://127.0.0.1:{asr_port}/health",
                    asr_process,
                    timeout=180,
                )
                self.assertEqual(health["profile"], "8gb")
                self.assertEqual(health["max_chunk_seconds"], 60)
                self.assertEqual(health["max_new_tokens"], 1024)
                self.assertEqual(health["cuda_memory_limit_mib"], 6144)
                self.assertEqual(
                    health["profile_tested_whole_device_vram_ceiling_mib"],
                    8000,
                )
                transcript = _post_audio(
                    f"http://127.0.0.1:{asr_port}/v1/audio/transcriptions",
                    long_audio,
                )
                self.assertTrue(transcript["text"].strip())
            except BaseException as exc:
                failure = exc
            finally:
                _stop_process_group(asr_process)
            try:
                asr_peak = asr_monitor.stop()
            except BaseException as exc:
                raise AssertionError(asr_log.read_text(errors="replace")[-4000:]) from exc
            if failure is not None:
                raise AssertionError(asr_log.read_text(errors="replace")[-4000:]) from failure
            metrics["asr"] = {
                "profile": "8gb",
                "peak_vram_mib": round(asr_peak, 1),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "chunk_seconds": 60,
            }

            _wait_for_release(baseline + 512)
            _assert_no_compute_processes()
            _assert_heavyweight_ports_idle()

            vision_monitor = _VramMonitor(
                budget_mib,
                lambda: _signal_process_group(vision_runtime._SERVER_PROCESS),
            )
            vision_port = _free_port()
            vision_model = (
                REPOSITORY_ROOT
                / "models/gguf/unsloth/Qwen3.5-4B-GGUF/"
                "Qwen3.5-4B-UD-Q4_K_XL.gguf"
            )
            vision_projector = (
                REPOSITORY_ROOT
                / "models/gguf/unsloth/Qwen3.5-4B-GGUF/mmproj-BF16.gguf"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "VISION_LOCAL_8GB_MODEL_PATH": str(vision_model),
                    "VISION_LOCAL_8GB_MMPROJ_PATH": str(vision_projector),
                },
                clear=False,
            ):
                settings = replace(
                    vision_runtime.load_settings("8gb"),
                    port=vision_port,
                    log_path=root / "vision.log",
                )
            started = time.monotonic()
            failure = None
            vision_monitor.start()
            try:
                vision_runtime.ensure_server(settings)
                result = vision_runtime.analyze_image(
                    VISION_IMAGE,
                    "Describe the visible subject in one sentence.",
                    max_tokens=64,
                    max_edge=512,
                    settings=settings,
                )
                self.assertTrue(result["text"].strip())
            except BaseException as exc:
                failure = exc
            finally:
                _stop_process_group(vision_runtime._SERVER_PROCESS)
                vision_runtime._SERVER_PROCESS = None
            try:
                vision_peak = vision_monitor.stop()
            except BaseException as exc:
                raise AssertionError(settings.log_path.read_text(errors="replace")[-4000:]) from exc
            if failure is not None:
                raise AssertionError(settings.log_path.read_text(errors="replace")[-4000:]) from failure
            metrics["vision"] = {
                "profile": "8gb",
                "peak_vram_mib": round(vision_peak, 1),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "gpu_layers": settings.gpu_layers,
                "context_size": settings.context_size,
                "parallel": settings.parallel,
            }

            _wait_for_release(baseline + 512)
            _assert_no_compute_processes()

        print("EIGHT_GB_PROFILE_METRICS=" + json.dumps(metrics, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
