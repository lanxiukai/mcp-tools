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
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parent
LEGACY_SIBLING_ROOT = REPOSITORY_ROOT.parent / "hf-models" / "models" / "gguf" / "unsloth"
PROFILE_DIRECTORIES = {
    "default": "Qwen3.5-9B-GGUF",
    "batch": "Qwen3.5-4B-GGUF",
    "8gb": "Qwen3.5-4B-GGUF",
}
PROFILE_MODEL_FILENAMES = {
    "default": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
    "batch": "Qwen3.5-4B-UD-Q4_K_XL.gguf",
    "8gb": "Qwen3.5-4B-UD-Q4_K_XL.gguf",
}
SUPPORTED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_START_LOCK = threading.Lock()
_SERVER_PROCESS: subprocess.Popen[bytes] | None = None
_WARNED_LEGACY_MODEL_DIRS: set[Path] = set()
_EIGHT_GB_MAXIMA = {
    "context_size": 2048,
    "parallel": 1,
    "image_max_tokens": 512,
    "max_output_tokens": 512,
    "gpu_layers": 20,
    "batch_size": 256,
    "ubatch_size": 128,
}


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
    max_output_tokens: int
    gpu_layers: int
    batch_size: int
    ubatch_size: int
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
    prefixes = {
        "default": "VISION_LOCAL",
        "batch": "VISION_LOCAL_BATCH",
        "8gb": "VISION_LOCAL_8GB",
    }
    variable = f"{prefixes[profile]}_{name}"
    if variable in os.environ:
        return os.environ[variable]
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
        prefix = {
            "default": "VISION_LOCAL",
            "batch": "VISION_LOCAL_BATCH",
            "8gb": "VISION_LOCAL_8GB",
        }[profile]
        raise ValueError(f"{prefix}_{name} must be >= {minimum}, got {value}")
    return value


def model_cache_root() -> Path:
    """Return the configurable cross-component model root."""
    configured = os.environ.get("MCP_TOOLS_MODEL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return (base / "mcp-tools" / "models").resolve(strict=False)


def vision_model_root() -> Path:
    """Return the directory that contains the two Vision profile directories."""
    configured = os.environ.get("VISION_LOCAL_MODEL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return model_cache_root() / "vision"


def _profile_model_dir(profile: str) -> Path:
    directory_name = PROFILE_DIRECTORIES[profile]
    configured_dir = vision_model_root() / directory_name
    explicitly_configured = bool(
        os.environ.get("VISION_LOCAL_MODEL_DIR", "").strip()
        or os.environ.get("MCP_TOOLS_MODEL_DIR", "").strip()
    )
    prefix = {
        "default": "VISION_LOCAL",
        "batch": "VISION_LOCAL_BATCH",
        "8gb": "VISION_LOCAL_8GB",
    }[profile]
    exact_paths_configured = all(
        os.environ.get(f"{prefix}_{name}", "").strip()
        for name in ("MODEL_PATH", "MMPROJ_PATH")
    )
    if explicitly_configured or exact_paths_configured or configured_dir.exists():
        return configured_dir

    legacy_dir = LEGACY_SIBLING_ROOT / directory_name
    model_file = legacy_dir / PROFILE_MODEL_FILENAMES[profile]
    if model_file.is_file() and (legacy_dir / "mmproj-BF16.gguf").is_file():
        if legacy_dir not in _WARNED_LEGACY_MODEL_DIRS:
            _WARNED_LEGACY_MODEL_DIRS.add(legacy_dir)
            warnings.warn(
                f"Using deprecated sibling model directory {legacy_dir}. Move it below "
                "MCP_TOOLS_MODEL_DIR/vision or set VISION_LOCAL_MODEL_DIR; the legacy "
                "fallback will be removed in a future release.",
                FutureWarning,
                stacklevel=3,
            )
        return legacy_dir
    return configured_dir


def load_settings(profile: str = "default") -> VisionSettings:
    """Load the default 9B, batch 4B, or bounded 8 GB profile."""
    if profile not in {"default", "batch", "8gb"}:
        raise ValueError(f"Unknown vision profile: {profile!r}")

    is_batch = profile == "batch"
    is_8gb = profile == "8gb"
    is_compact = is_batch or is_8gb
    model_dir = _profile_model_dir(profile)
    model_filename = PROFILE_MODEL_FILENAMES[profile]
    settings = VisionSettings(
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
        port=_profile_env_int(
            profile,
            "PORT",
            8005 if is_8gb else (8004 if is_batch else 8003),
        ),
        context_size=_profile_env_int(
            profile,
            "CONTEXT_SIZE",
            2048 if is_8gb else (4096 if is_batch else 8192),
        ),
        parallel=_profile_env_int(profile, "PARALLEL", 1 if is_8gb else 4),
        image_max_tokens=_profile_env_int(
            profile, "IMAGE_MAX_TOKENS", 512 if is_compact else 1024
        ),
        max_output_tokens=_profile_env_int(
            profile, "MAX_OUTPUT_TOKENS", 512 if is_compact else 4096
        ),
        gpu_layers=_profile_env_int(profile, "GPU_LAYERS", 20 if is_8gb else 99),
        batch_size=_profile_env_int(profile, "BATCH_SIZE", 256 if is_8gb else 512),
        ubatch_size=_profile_env_int(profile, "UBATCH_SIZE", 128 if is_8gb else 256),
        sleep_idle_seconds=_profile_env_int(profile, "SLEEP_IDLE_SECONDS", 300),
        startup_timeout=_profile_env_int(profile, "STARTUP_TIMEOUT", 180),
        request_timeout=_profile_env_int(profile, "REQUEST_TIMEOUT", 180),
        log_path=Path(
            _profile_env(
                profile,
                "LOG_PATH",
                (
                    "/tmp/vision_local_8gb_llama_server.log"
                    if is_8gb
                    else (
                        "/tmp/vision_local_batch_llama_server.log"
                        if is_batch
                        else "/tmp/vision_local_llama_server.log"
                    )
                ),
            )
        ).expanduser(),
    )
    if is_8gb:
        for attribute, maximum in _EIGHT_GB_MAXIMA.items():
            value = getattr(settings, attribute)
            if value > maximum:
                environment_name = f"VISION_LOCAL_8GB_{attribute.upper()}"
                raise ValueError(
                    f"{environment_name}={value} exceeds the validated 8gb "
                    f"maximum of {maximum}"
                )
    return settings


def load_interactive_settings() -> VisionSettings:
    """Load the explicitly selected interactive profile."""
    profile = os.environ.get("VISION_LOCAL_PROFILE", "default").strip()
    if profile not in {"default", "8gb"}:
        raise ValueError(
            "VISION_LOCAL_PROFILE must be 'default' or '8gb'; "
            f"got {profile!r}"
        )
    return load_settings(profile)


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
        str(settings.gpu_layers),
        "--flash-attn",
        "on",
        "--batch-size",
        str(settings.batch_size),
        "--ubatch-size",
        str(settings.ubatch_size),
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


def _default_cuda_library_dirs() -> list[Path]:
    """Find CUDA 13 runtime libraries without changing the parent process."""
    candidates = [
        *sorted(
            (ROOT.parent / "environments/mcp-local-asr/.venv/lib").glob(
                "python*/site-packages/nvidia/cu13/lib"
            )
        ),
        *sorted(Path("/usr/local").glob("cuda*/targets/x86_64-linux/lib")),
        *sorted(Path("/usr/local").glob("cuda*/lib64")),
        Path("/usr/lib/wsl/lib"),
    ]
    result: list[Path] = []
    for path in candidates:
        if path.is_dir() and path not in result:
            result.append(path)
    return result


def build_server_environment() -> dict[str, str]:
    """Build the child environment with a usable CUDA library search path."""
    environment = os.environ.copy()
    configured = environment.get("VISION_LOCAL_CUDA_LIBRARY_PATH")
    if configured is None:
        library_dirs = [str(path) for path in _default_cuda_library_dirs()]
    else:
        library_dirs = [path for path in configured.split(os.pathsep) if path]

    inherited = environment.get("LD_LIBRARY_PATH")
    if inherited:
        library_dirs.extend(path for path in inherited.split(os.pathsep) if path)
    if library_dirs:
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(dict.fromkeys(library_dirs))
    return environment


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def server_health(settings: VisionSettings | None = None) -> dict[str, Any]:
    settings = settings or load_interactive_settings()
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
    settings = settings or load_interactive_settings()
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
                raise FileNotFoundError(
                    f"Missing {label}: {path}. Run 'bin/mcp-tools doctor' from the "
                    "repository root and follow vision-local/README.md."
                )

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
                env=build_server_environment(),
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
    settings = settings or load_interactive_settings()
    max_tokens = min(max_tokens, settings.max_output_tokens)
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
