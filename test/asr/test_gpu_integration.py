"""Opt-in real-GPU ASR reliability and long-audio tests."""

from __future__ import annotations

import concurrent.futures
import glob
import json
import math
import os
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SMOKE_AUDIO = REPOSITORY_ROOT / "mcp-tool-test/smoke-test/asr_smoke_test.wav"
REAL_LONG_AUDIO = (
    REPOSITORY_ROOT
    / "mcp-tool-test/asr/podcast/en_single/greatinventors_02_steamboat.mp3"
)
SERVER_URL = os.environ.get("ASR_TEST_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _health(timeout: float = 3.0) -> dict[str, Any]:
    with DIRECT_OPENER.open(f"{SERVER_URL}/health", timeout=timeout) as response:
        return json.loads(response.read())


def _post(
    path: Path,
    *,
    response_format: str = "json",
    language: str = "English",
    timeout: float = 1800,
) -> tuple[int, bytes, str]:
    boundary = f"----asr-reliability-{uuid.uuid4().hex}"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="language"\r\n\r\n',
            language.encode(),
            f"\r\n--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="response_format"\r\n\r\n',
            response_format.encode(),
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    request = urllib.request.Request(
        f"{SERVER_URL}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with DIRECT_OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get_content_type()


def _write_patterned_flac(path: Path, duration_seconds: int) -> None:
    speech, sample_rate = sf.read(SMOKE_AUDIO, dtype="float32")
    if speech.ndim > 1:
        speech = speech.mean(axis=1)
    total_samples = duration_seconds * sample_rate
    speech_offsets = tuple(range(0, duration_seconds, 480))
    block_samples = sample_rate * 10
    with sf.SoundFile(
        path,
        mode="w",
        samplerate=sample_rate,
        channels=1,
        format="FLAC",
        subtype="PCM_16",
    ) as output:
        for block_start in range(0, total_samples, block_samples):
            block_end = min(block_start + block_samples, total_samples)
            block = np.zeros(block_end - block_start, dtype=np.float32)
            for offset_seconds in speech_offsets:
                speech_start = offset_seconds * sample_rate
                speech_end = min(speech_start + len(speech), total_samples)
                overlap_start = max(block_start, speech_start)
                overlap_end = min(block_end, speech_end)
                if overlap_start < overlap_end:
                    block[overlap_start - block_start : overlap_end - block_start] = speech[
                        overlap_start - speech_start : overlap_end - speech_start
                    ]
            output.write(block)


def _rss_mib(pid: int) -> float:
    status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def _vram_mib() -> float:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip().splitlines()[0])


def _temporary_chunk_bytes() -> int:
    total = 0
    root = os.environ.get("ASR_TEMP_ROOT", tempfile.gettempdir())
    for pattern in (
        f"{root}/mcp-tools-asr-*/chunks-*",
        f"{root}/mcp-tools-asr-*/decode-*",
    ):
        for directory in glob.glob(pattern):
            for candidate in Path(directory).rglob("*"):
                if candidate.is_file():
                    total += candidate.stat().st_size
    return total


def _temporary_work_directories() -> set[str]:
    root = os.environ.get("ASR_TEMP_ROOT", tempfile.gettempdir())
    directories: set[str] = set()
    for pattern in (
        f"{root}/mcp-tools-asr-*/chunks-*",
        f"{root}/mcp-tools-asr-*/decode-*",
    ):
        directories.update(glob.glob(pattern))
    return directories


def _measured_post(path: Path, *, timeout: float = 1800) -> tuple[int, bytes, dict[str, float]]:
    pid = int(os.environ["ASR_TEST_SERVER_PID"])
    started = time.monotonic()
    peak_rss = _rss_mib(pid)
    peak_vram = _vram_mib()
    peak_temp = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_post, path, timeout=timeout)
        while not future.done():
            peak_rss = max(peak_rss, _rss_mib(pid))
            peak_vram = max(peak_vram, _vram_mib())
            peak_temp = max(peak_temp, _temporary_chunk_bytes())
            time.sleep(0.5)
        code, body, _ = future.result()
    return code, body, {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "peak_rss_mib": round(peak_rss, 1),
        "peak_vram_mib": round(peak_vram, 1),
        "peak_temp_mib": round(peak_temp / 1024**2, 1),
        "input_mib": round(path.stat().st_size / 1024**2, 1),
    }


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_ASR_GPU_INTEGRATION") == "1",
    "set MCP_TOOLS_ASR_GPU_INTEGRATION=1 with a running ASR server",
)
class AsrGpuIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertEqual(_health()["device"], "cuda:0")

    def test_input_variants_output_modes_and_concurrency(self) -> None:
        source, sample_rate = sf.read(SMOKE_AUDIO, dtype="float32")
        if source.ndim > 1:
            source = source.mean(axis=1)
        with tempfile.TemporaryDirectory(prefix="asr reliability 音频 ") as directory:
            root = Path(directory)
            variants = {
                "unicode path with spaces 音频.wav": (source, sample_rate),
                "stereo.wav": (np.column_stack((source, source * 0.8)), sample_rate),
                "unusual-7350hz.wav": (source[::3], sample_rate // 3),
                "very-short.wav": (source[: max(1, sample_rate // 20)], sample_rate),
                "silence.wav": (np.zeros(sample_rate, dtype=np.float32), sample_rate),
                "clipped.wav": (np.sign(source), sample_rate),
            }
            for filename, (samples, rate) in variants.items():
                path = root / filename
                sf.write(path, samples, rate, subtype="PCM_16")
                with self.subTest(filename=filename):
                    code, body, content_type = _post(path, timeout=120)
                    self.assertEqual(code, 200, body[-1000:])
                    self.assertEqual(content_type, "application/json")
                    payload = json.loads(body)
                    self.assertIn("text", payload)

            ffmpeg_audio = root / "ffmpeg-fallback.m4a"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(SMOKE_AUDIO),
                    "-c:a",
                    "aac",
                    str(ffmpeg_audio),
                ],
                check=True,
            )
            code, body, _ = _post(ffmpeg_audio, timeout=120)
            self.assertEqual(code, 200, body[-1000:])

            for response_format, expected_type in (
                ("json", "application/json"),
                ("verbose_json", "application/json"),
                ("text", "text/plain"),
            ):
                with self.subTest(response_format=response_format):
                    code, body, content_type = _post(
                        SMOKE_AUDIO,
                        response_format=response_format,
                        timeout=120,
                    )
                    self.assertEqual(code, 200, body[-1000:])
                    self.assertEqual(content_type, expected_type)
                    self.assertTrue(body.strip())

            for filename, payload in (("empty.wav", b""), ("corrupt.wav", b"not audio")):
                path = root / filename
                path.write_bytes(payload)
                with self.subTest(invalid=filename):
                    code, body, content_type = _post(path, timeout=30)
                    self.assertEqual(code, 500)
                    self.assertEqual(content_type, "application/json")
                    error = json.loads(body)
                    self.assertTrue(error.get("detail"), error)
                    self.assertNotIn("Traceback", error["detail"])

            started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                replies = tuple(executor.map(lambda _: _post(SMOKE_AUDIO), range(4)))
            self.assertTrue(all(code == 200 for code, _, _ in replies), replies)
            print(
                "ASR_CONCURRENCY_METRICS="
                + json.dumps(
                    {"requests": 4, "elapsed_seconds": round(time.monotonic() - started, 3)}
                )
            )

    def test_health_remains_responsive_during_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "two-minutes.flac"
            _write_patterned_flac(source, 120)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_post, source, timeout=180)
                time.sleep(0.5)
                started = time.monotonic()
                health = _health(timeout=2.0)
                health_elapsed = time.monotonic() - started
                code, body, _ = future.result()
            self.assertEqual(code, 200, body[-1000:])
            self.assertEqual(health["status"], "ok")
            self.assertLess(health_elapsed, 2.0)

    def test_chunk_boundaries_on_real_backend(self) -> None:
        configured = os.environ.get("ASR_GPU_DURATIONS", "479,480,481,961")
        durations = tuple(int(value) for value in configured.split(","))
        metrics: dict[str, Any] = {}
        before = _temporary_work_directories()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for duration in durations:
                with self.subTest(duration=duration):
                    source = root / f"boundary-{duration}.flac"
                    _write_patterned_flac(source, duration)
                    code, body, measured = _measured_post(source)
                    self.assertEqual(code, 200, body[-1000:])
                    payload = json.loads(body)
                    self.assertTrue(payload["text"].strip())
                    measured["chunks"] = math.ceil(duration / 480)
                    metrics[str(duration)] = measured
                    self.assertEqual(_temporary_work_directories(), before)
        print("ASR_BOUNDARY_METRICS=" + json.dumps(metrics, sort_keys=True))


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_ASR_LONG_GPU") == "1",
    "set MCP_TOOLS_ASR_LONG_GPU=1 for 30-minute and 2-hour ASR stress",
)
class AsrLongGpuTests(unittest.TestCase):
    def test_thirty_minutes_and_two_hours(self) -> None:
        configured = os.environ.get("ASR_LONG_DURATIONS", "1800,7200")
        metrics: dict[str, Any] = {}
        before = _temporary_work_directories()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for duration in (int(value) for value in configured.split(",")):
                with self.subTest(duration=duration):
                    source = root / f"long-{duration}.flac"
                    _write_patterned_flac(source, duration)
                    code, body, measured = _measured_post(source, timeout=3600)
                    self.assertEqual(code, 200, body[-1000:])
                    payload = json.loads(body)
                    self.assertTrue(payload["text"].strip())
                    measured["chunks"] = math.ceil(duration / 480)
                    metrics[str(duration)] = measured
                    self.assertEqual(_temporary_work_directories(), before)
        print("ASR_LONG_METRICS=" + json.dumps(metrics, sort_keys=True))


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_ASR_REAL_LONG_GPU") == "1",
    "set MCP_TOOLS_ASR_REAL_LONG_GPU for continuous-speech ASR stress",
)
class AsrRealLongGpuTests(unittest.TestCase):
    def test_real_thirty_one_minute_speech(self) -> None:
        code, body, measured = _measured_post(REAL_LONG_AUDIO, timeout=1800)
        self.assertEqual(code, 200, body[-1000:])
        payload = json.loads(body)
        self.assertTrue(payload["text"].strip())
        measured["chunks"] = 4
        measured["text_characters"] = len(payload["text"])
        self.assertEqual(_temporary_work_directories(), set())
        print("ASR_REAL_31_MIN_METRICS=" + json.dumps(measured, sort_keys=True))

    def test_repeated_real_speech_two_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "repeated-real-speech-2h.flac"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-stream_loop",
                    "3",
                    "-i",
                    str(REAL_LONG_AUDIO),
                    "-t",
                    "7200",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "flac",
                    str(source),
                ],
                check=True,
            )
            code, body, measured = _measured_post(source, timeout=3600)
            self.assertEqual(code, 200, body[-1000:])
            payload = json.loads(body)
            self.assertTrue(payload["text"].strip())
            measured["chunks"] = 15
            measured["text_characters"] = len(payload["text"])
            self.assertEqual(_temporary_work_directories(), set())
        print("ASR_REAL_2H_METRICS=" + json.dumps(measured, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
