from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY_ROOT / "bin" / "mcp-tools"


def test_doctor_reports_credentials_without_printing_values() -> None:
    with TemporaryDirectory() as directory:
        result = subprocess.run(
            [str(LAUNCHER), "doctor"],
            cwd=REPOSITORY_ROOT,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(Path(directory) / "home"),
                "MCP_TOOLS_MODEL_DIR": str(Path(directory) / "models"),
                "HF_TOKEN": "hf-secret-value-for-test",
                "BRAVE_API_KEY": "brave-secret-value-for-test",
            },
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "HF_TOKEN: configured" in result.stdout
    assert "BRAVE_API_KEY: configured" in result.stdout
    assert "hf-secret-value-for-test" not in result.stdout
    assert "brave-secret-value-for-test" not in result.stdout


def test_doctor_rejects_unknown_arguments() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "doctor", "unexpected"],
        cwd=REPOSITORY_ROOT,
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "")},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Usage: bin/mcp-tools doctor" in result.stderr


def test_doctor_fails_when_core_repository_runtime_is_missing() -> None:
    with TemporaryDirectory() as directory:
        repository = Path(directory) / "repository"
        (repository / "bin").mkdir(parents=True)
        (repository / "scripts").mkdir()
        launcher = shutil.copy2(LAUNCHER, repository / "bin" / "mcp-tools")
        shutil.copy2(
            REPOSITORY_ROOT / "scripts" / "doctor.sh",
            repository / "scripts" / "doctor.sh",
        )
        result = subprocess.run(
            [str(launcher), "doctor"],
            cwd=repository,
            env={"PATH": os.environ["PATH"], "HOME": str(Path(directory) / "home")},
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 1
    assert "blocking failure(s)" in result.stdout


def test_doctor_reports_relative_model_root_as_an_absolute_path() -> None:
    with TemporaryDirectory() as directory:
        result = subprocess.run(
            [str(LAUNCHER), "doctor"],
            cwd=REPOSITORY_ROOT,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(Path(directory) / "home"),
                "MCP_TOOLS_MODEL_DIR": "relative-model-root",
            },
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(REPOSITORY_ROOT / "relative-model-root") in result.stdout


def test_doctor_exact_vision_pair_suppresses_legacy_fallback() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        repository = root / "repository"
        (repository / "scripts").mkdir(parents=True)
        doctor = shutil.copy2(
            REPOSITORY_ROOT / "scripts" / "doctor.sh",
            repository / "scripts" / "doctor.sh",
        )
        legacy = root / "hf-models" / "models" / "gguf" / "unsloth"
        profile = legacy / "Qwen3.5-9B-GGUF"
        profile.mkdir(parents=True)
        (profile / "Qwen3.5-9B-UD-Q4_K_XL.gguf").touch()
        (profile / "mmproj-BF16.gguf").touch()
        exact_model = root / "exact-model.gguf"
        exact_projector = root / "exact-mmproj.gguf"

        result = subprocess.run(
            [str(doctor)],
            cwd=repository,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(root / "home"),
                "VISION_LOCAL_MODEL_PATH": str(exact_model),
                "VISION_LOCAL_MMPROJ_PATH": str(exact_projector),
            },
            check=False,
            capture_output=True,
            text=True,
        )

    assert str(exact_model) in result.stdout
    assert str(exact_projector) in result.stdout
    assert "deprecated sibling hf-models compatibility fallback" not in result.stdout
