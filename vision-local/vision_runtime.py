#!/usr/bin/env python3
"""Shared local vision runtime backed by a persistent llama.cpp server."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent
DEFAULT_MODEL_DIR = PROJECT_ROOT / "hf-models/models/gguf/unsloth/Qwen3.5-9B-GGUF"
BATCH_MODEL_DIR = PROJECT_ROOT / "hf-models/models/gguf/unsloth/Qwen3.5-4B-GGUF"
SUPPORTED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_START_LOCK = threading.Lock()
_SERVER_PROCESS: subprocess.Popen[bytes] | None = None


@dataclass(frozen=True)
class VisionSettings:
    profile: str
    server_binary: Path
    model_path: Path
    mmproj_path: Path
    host: str
    port: int
    context_size: int
    parallel: int
    image_max_tokens: int
    sleep_idle_seconds: int
    startup_timeout: int
    request_timeout: int
    log_path: Path

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _profile_env(
    profile: str,
    name: str,
    default: str,
    *,
    inherit_default: bool = False,
) -> str:
    if profile == "default":
        return os.environ.get(f"VISION_LOCAL_{name}", default)
    batch_name = f"VISION_LOCAL_BATCH_{name}"
    if batch_name in os.environ:
        return os.environ[batch_name]
    if inherit_default:
        return os.environ.get(f"VISION_LOCAL_{name}", default)
    return default


def _profile_env_int(
    profile: str,
    name: str,
    default: int,
    *,
    minimum: int = 1,
    inherit_default: bool = False,
) -> int:
    value = int(
        _profile_env(
            profile,
            name,
            str(default),
            inherit_default=inherit_default,
        )
    )
    if value < minimum:
        prefix = "VISION_LOCAL" if profile == "default" else "VISION_LOCAL_BATCH"
        raise ValueError(f"{prefix}_{name} must be >= {minimum}, got {value}")
    return value


def load_settings(profile: str = "default") -> VisionSettings:
    """Load the default 9B or batch-oriented 4B runtime profile."""
    if profile not in {"default", "batch"}:
        raise ValueError(f"Unknown vision profile: {profile!r}")

    is_batch = profile == "batch"
    model_dir = BATCH_MODEL_DIR if is_batch else DEFAULT_MODEL_DIR
    model_filename = (
        "Qwen3.5-4B-UD-Q4_K_XL.gguf" if is_batch else "Qwen3.5-9B-UD-Q4_K_XL.gguf"
    )
    return VisionSettings(
        profile=profile,
        server_binary=Path(
            _profile_env(
                profile,
                "SERVER_BINARY",
                str(ROOT.parent / ".runtime/llama.cpp-build/bin/llama-server"),
                inherit_default=True,
            )
        ).expanduser(),
        model_path=Path(
            _profile_env(
                profile,
                "MODEL_PATH",
                str(model_dir / model_filename),
            )
        ).expanduser(),
        mmproj_path=Path(
            _profile_env(
                profile,
                "MMPROJ_PATH",
                str(model_dir / "mmproj-BF16.gguf"),
            )
        ).expanduser(),
        host=_profile_env(profile, "HOST", "127.0.0.1"),
        port=_profile_env_int(profile, "PORT", 8004 if is_batch else 8003),
        context_size=_profile_env_int(
            profile, "CONTEXT_SIZE", 4096 if is_batch else 8192
        ),
        parallel=_profile_env_int(profile, "PARALLEL", 4),
        image_max_tokens=_profile_env_int(
            profile, "IMAGE_MAX_TOKENS", 512 if is_batch else 1024
        ),
        sleep_idle_seconds=_profile_env_int(profile, "SLEEP_IDLE_SECONDS", 300),
        startup_timeout=_profile_env_int(profile, "STARTUP_TIMEOUT", 180),
        request_timeout=_profile_env_int(profile, "REQUEST_TIMEOUT", 180),
        log_path=Path(
            _profile_env(
                profile,
                "LOG_PATH",
                (
                    "/tmp/vision_local_batch_llama_server.log"
                    if is_batch
                    else "/tmp/vision_local_llama_server.log"
                ),
            )
        ).expanduser(),
    )


def validate_image_path(file_path: str | Path) -> Path:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    if path.suffix.lower() not in SUPPORTED_IMAGE_TYPES:
        supported = ", ".join(sorted(SUPPORTED_IMAGE_TYPES))
        raise ValueError(f"Unsupported image type {path.suffix!r}; supported: {supported}")
    return path


def image_data_url(
    file_path: str | Path,
    *,
    max_edge: int = 1024,
    jpeg_quality: int = 92,
) -> tuple[str, dict[str, int]]:
    """Normalize an image in memory and return a compact JPEG data URL."""
    path = validate_image_path(file_path)
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        if getattr(image, "is_animated", False):
            image.seek(0)
        image = image.convert("RGB")
        original_width, original_height = image.size
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        width, height = image.size
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        f"data:image/jpeg;base64,{encoded}",
        {
            "original_width": original_width,
            "original_height": original_height,
            "input_width": width,
            "input_height": height,
        },
    )


def build_server_command(settings: VisionSettings) -> list[str]:
    """Build the reproducible llama-server command for this workstation."""
    return [
        str(settings.server_binary),
        "--model",
        str(settings.model_path),
        "--mmproj",
        str(settings.mmproj_path),
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--ctx-size",
        str(settings.context_size),
        "--parallel",
        str(settings.parallel),
        "--n-gpu-layers",
        "99",
        "--flash-attn",
        "on",
        "--batch-size",
        "512",
        "--ubatch-size",
        "256",
        "--threads",
        str(min(16, os.cpu_count() or 8)),
        "--image-max-tokens",
        str(settings.image_max_tokens),
        "--sleep-idle-seconds",
        str(settings.sleep_idle_seconds),
        "--reasoning",
        "off",
        "--jinja",
        "--no-webui",
    ]


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def server_health(settings: VisionSettings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    try:
        health = _get_json(f"{settings.base_url}/health")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ready": False, "error": str(exc), "base_url": settings.base_url}
    ready = health.get("status") in {"ok", "ready"}
    return {"ready": ready, "base_url": settings.base_url, "health": health}


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def ensure_server(settings: VisionSettings | None = None) -> VisionSettings:
    """Start llama-server once and wait until its health endpoint is ready."""
    global _SERVER_PROCESS
    settings = settings or load_settings()
    if server_health(settings).get("ready"):
        return settings

    with _START_LOCK:
        if server_health(settings).get("ready"):
            return settings
        for path, label in (
            (settings.server_binary, "llama-server binary"),
            (settings.model_path, "model"),
            (settings.mmproj_path, "multimodal projector"),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"Missing {label}: {path}")

        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        with settings.log_path.open("ab", buffering=0) as log_file:
            log_file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting vision_local\n".encode()
            )
            _SERVER_PROCESS = subprocess.Popen(
                build_server_command(settings),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        deadline = time.monotonic() + settings.startup_timeout
        while time.monotonic() < deadline:
            if server_health(settings).get("ready"):
                return settings
            if _SERVER_PROCESS.poll() is not None:
                raise RuntimeError(
                    f"vision_local backend exited with {_SERVER_PROCESS.returncode}:\n"
                    f"{_tail(settings.log_path)}"
                )
            time.sleep(1)

        raise TimeoutError(
            f"vision_local backend did not become ready in {settings.startup_timeout}s:\n"
            f"{_tail(settings.log_path)}"
        )


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"vision backend HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"vision backend connection failed: {exc.reason}") from exc


def _chat(
    image_url: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    response_format: dict[str, Any] | None = None,
    settings: VisionSettings | None = None,
) -> tuple[str, dict[str, Any]]:
    settings = ensure_server(settings)
    payload: dict[str, Any] = {
        "model": "vision-local",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if response_format is not None:
        payload["response_format"] = response_format
    result = _post_json(
        f"{settings.base_url}/v1/chat/completions",
        payload,
        timeout=settings.request_timeout,
    )
    try:
        text = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected vision backend response: {result}") from exc
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    return text.strip(), result


def analyze_image(
    file_path: str | Path,
    prompt: str,
    *,
    max_tokens: int = 512,
    max_edge: int = 1024,
    settings: VisionSettings | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    image_url, image_meta = image_data_url(file_path, max_edge=max_edge)
    text, raw = _chat(
        image_url,
        prompt,
        max_tokens=max_tokens,
        temperature=0.1,
        settings=settings,
    )
    return {
        "text": text,
        "backend": "local",
        "image": image_meta,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": raw.get("usage", {}),
    }


EYEWEAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "wearing_glasses": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["wearing_glasses", "confidence"],
    "additionalProperties": False,
}

EYEWEAR_PROMPT = """Inspect the person's eyes and face. Decide whether the person is currently wearing eyeglasses or sunglasses over the eyes. Count clear-lens, thin, rimless, transparent-frame, reading, safety, and sun glasses. Look for subtle temples, nose bridges, lens edges, and lens reflections. Do not count glasses held in a hand, resting only on top of the head, printed on clothing, or background objects. Return only the required JSON object."""


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"No JSON object in model output: {text!r}")
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got: {parsed!r}")
    return parsed


def classify_eyewear(
    file_path: str | Path,
    *,
    max_edge: int = 512,
    settings: VisionSettings | None = None,
) -> dict[str, Any]:
    """Classify eyewear with schema-constrained, short structured output."""
    started = time.perf_counter()
    path = validate_image_path(file_path)
    image_url, image_meta = image_data_url(path, max_edge=max_edge, jpeg_quality=90)
    text, raw = _chat(
        image_url,
        EYEWEAR_PROMPT,
        max_tokens=32,
        temperature=0.0,
        response_format={"type": "json_object", "schema": EYEWEAR_SCHEMA},
        settings=settings,
    )
    parsed = _parse_json_object(text)
    wearing = parsed.get("wearing_glasses")
    confidence = parsed.get("confidence")
    if not isinstance(wearing, bool):
        raise ValueError(f"Invalid wearing_glasses value: {wearing!r}")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError(f"Invalid confidence value: {confidence!r}")
    return {
        "file": str(path),
        "wearing_glasses": wearing,
        "confidence": confidence,
        "backend": "local",
        "image": image_meta,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": raw.get("usage", {}),
    }


EYEWEAR_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "wearing_glasses": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "visual_cues": {"type": "string", "maxLength": 240},
    },
    "required": ["wearing_glasses", "confidence", "visual_cues"],
    "additionalProperties": False,
}

EYEWEAR_VERIFICATION_PROMPT = """Perform a careful high-resolution eyewear inspection. Look closely around both eyes, temples, ears, and the nose. Thin or rimless glasses may show only a tiny metal temple, nose bridge, nose pads, lens edge, refraction, or reflection; these still count as wearing glasses. Transparent frames and sunglasses also count. Glasses only on the head, in a hand, on clothing, or in the background do not count. Return the required JSON and briefly name the visible cues that support the decision."""


def verify_eyewear(
    file_path: str | Path,
    *,
    max_edge: int = 1024,
    settings: VisionSettings | None = None,
) -> dict[str, Any]:
    """High-resolution second-pass verification for coarse-pass disagreements."""
    started = time.perf_counter()
    path = validate_image_path(file_path)
    image_url, image_meta = image_data_url(path, max_edge=max_edge, jpeg_quality=94)
    text, raw = _chat(
        image_url,
        EYEWEAR_VERIFICATION_PROMPT,
        max_tokens=128,
        temperature=0.0,
        response_format={"type": "json_object", "schema": EYEWEAR_VERIFICATION_SCHEMA},
        settings=settings,
    )
    parsed = _parse_json_object(text)
    wearing = parsed.get("wearing_glasses")
    confidence = parsed.get("confidence")
    visual_cues = parsed.get("visual_cues")
    if not isinstance(wearing, bool):
        raise ValueError(f"Invalid wearing_glasses value: {wearing!r}")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError(f"Invalid confidence value: {confidence!r}")
    if not isinstance(visual_cues, str) or not visual_cues.strip():
        raise ValueError(f"Invalid visual_cues value: {visual_cues!r}")
    return {
        "file": str(path),
        "wearing_glasses": wearing,
        "confidence": confidence,
        "visual_cues": visual_cues.strip(),
        "backend": "local",
        "image": image_meta,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": raw.get("usage", {}),
    }
