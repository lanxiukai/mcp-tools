"""Opt-in ASR backend lifecycle tests that deliberately run on CPU."""

from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVER = REPOSITORY_ROOT / "asr/qwen3_asr_server.py"
SMOKE_AUDIO = REPOSITORY_ROOT / "mcp-tool-test/smoke-test/asr_smoke_test.wav"
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_server(
    port: int,
    root: Path,
    *,
    idle_timeout: int,
    model: str | None = None,
) -> tuple[subprocess.Popen[bytes], Path]:
    temp_root = root / "temp"
    temp_root.mkdir(exist_ok=True)
    log_path = root / f"server-{time.monotonic_ns()}.log"
    environment = os.environ.copy()
    environment.update(
        {
            "ASR_IDLE_TIMEOUT": str(idle_timeout),
            "TMPDIR": str(temp_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    command = [
        sys.executable,
        str(SERVER),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--device",
        "cpu",
    ]
    if model is not None:
        command.extend(("--model", model))
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return process, log_path


def _wait_health(port: int, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with DIRECT_OPENER.open(
                f"http://127.0.0.1:{port}/health",
                timeout=0.5,
            ) as response:
                return json.loads(response.read())
        except Exception as exc:  # Startup can refuse connections until Uvicorn binds.
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"ASR CPU server did not become ready: {last_error}")


def _post(port: int, path: Path, timeout: float = 120.0) -> tuple[int, bytes]:
    boundary = f"----asr-lifecycle-{uuid.uuid4().hex}"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with DIRECT_OPENER.open(request, timeout=timeout) as response:
        return response.status, response.read()


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_ASR_CPU_LIFECYCLE") == "1",
    "set MCP_TOOLS_ASR_CPU_LIFECYCLE=1 for real CPU backend lifecycle tests",
)
class AsrCpuLifecycleIntegrationTests(unittest.TestCase):
    def test_cold_start_request_during_startup_and_idle_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = _free_port()
            process, log_path = _start_server(port, root, idle_timeout=5)
            try:
                started = time.monotonic()
                try:
                    _post(port, SMOKE_AUDIO, timeout=0.2)
                except (OSError, TimeoutError, urllib.error.URLError):
                    pass
                self.assertLess(time.monotonic() - started, 2.0)
                self.assertEqual(_wait_health(port)["device"], "cpu")
                self.assertIn(process.wait(timeout=15), (0, -signal.SIGTERM))
                log = log_path.read_text(encoding="utf-8", errors="replace")
                self.assertIn("Idle timeout reached", log)
                self.assertIn("Server shutdown complete", log)
            finally:
                _stop(process)

    def test_force_kill_during_request_restart_cleans_stale_temp_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = _free_port()
            process, _log = _start_server(port, root, idle_timeout=120)
            restarted: subprocess.Popen[bytes] | None = None
            try:
                _wait_health(port)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    request = executor.submit(_post, port, SMOKE_AUDIO)
                    time.sleep(0.5)
                    process.kill()
                    process.wait(timeout=10)
                    with self.assertRaises(Exception):
                        request.result(timeout=10)

                stale_uploads = tuple((root / "temp").rglob("*.wav"))
                self.assertTrue(
                    stale_uploads,
                    "force-kill fixture did not leave an interrupted upload",
                )

                restarted, _restart_log = _start_server(
                    port,
                    root,
                    idle_timeout=120,
                )
                _wait_health(port)
                self.assertTrue(
                    all(not stale.exists() for stale in stale_uploads),
                    stale_uploads,
                )
                code, body = _post(port, SMOKE_AUDIO)
                self.assertEqual(code, 200)
                self.assertTrue(json.loads(body)["text"].strip())
            finally:
                _stop(process)
                if restarted is not None:
                    _stop(restarted)

    def test_invalid_model_path_fails_fast_with_actionable_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-model"
            process, log_path = _start_server(
                _free_port(),
                root,
                idle_timeout=30,
                model=str(missing),
            )
            try:
                self.assertNotEqual(process.wait(timeout=30), 0)
            finally:
                _stop(process)
            log = log_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("Failed to load model", log)
            self.assertIn(str(missing), log)


if __name__ == "__main__":
    unittest.main()
