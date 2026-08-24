"""Repository-owned contract tests for the upstream Brave MCP launcher."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY_ROOT / "brave-websearch" / "run.sh"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _install_shell_helpers(path: Path) -> None:
    for name in ("cut", "sed"):
        executable = shutil.which(name)
        assert executable is not None
        (path / name).symlink_to(executable)


def _run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_missing_node_has_an_actionable_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        environment = {"PATH": directory}

        result = _run(environment)

    assert result.returncode != 0
    assert "Node.js is not installed or not in PATH" in result.stderr


def test_missing_npx_has_an_actionable_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        binary_directory = Path(directory)
        _install_shell_helpers(binary_directory)
        _write_executable(
            binary_directory / "node",
            "#!/bin/bash\nprintf '%s\\n' 'v24.0.0'\n",
        )
        environment = {
            "PATH": str(binary_directory),
            "BRAVE_API_KEY": "test-key",
        }

        result = _run(environment)

    assert result.returncode != 0
    assert "npx is not in PATH" in result.stderr


def test_missing_api_key_fails_before_upstream_launch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        binary_directory = Path(directory)
        _install_shell_helpers(binary_directory)
        marker = binary_directory / "npx-called"
        _write_executable(
            binary_directory / "node",
            "#!/bin/bash\nprintf '%s\\n' 'v24.0.0'\n",
        )
        _write_executable(
            binary_directory / "npx",
            f"#!/bin/bash\n: > '{marker}'\n",
        )
        environment = {"PATH": str(binary_directory)}

        result = _run(environment)
        marker_exists = marker.exists()

    assert result.returncode != 0
    assert "BRAVE_API_KEY is not set" in result.stderr
    assert not marker_exists


def test_launcher_propagates_credentials_proxy_environment_and_arguments() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        binary_directory = root / "bin"
        binary_directory.mkdir()
        _install_shell_helpers(binary_directory)
        capture_path = root / "capture.json"
        _write_executable(
            binary_directory / "node",
            "#!/bin/bash\nprintf '%s\\n' 'v24.0.0'\n",
        )
        _write_executable(
            binary_directory / "npx",
            "#!/bin/bash\n"
            "printf '{\"api_key\":\"%s\",\"https_proxy\":\"%s\","
            "\"node_proxy\":\"%s\",\"args\":\"%s\"}\\n' "
            '"$BRAVE_API_KEY" "$HTTPS_PROXY" "$NODE_USE_ENV_PROXY" "$*" '
            ' > "$CAPTURE_PATH"\n',
        )
        environment = {
            "PATH": str(binary_directory),
            "BRAVE_API_KEY": "test-key",
            "HTTPS_PROXY": "http://proxy.test:8080",
            "CAPTURE_PATH": str(capture_path),
            "BRAVE_MCP_LOG_LEVEL": "warn",
            "BRAVE_MCP_ENABLED_TOOLS": "brave_web_search brave_news_search",
        }

        result = _run(environment)
        captured = json.loads(capture_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert captured["api_key"] == "test-key"
    assert captured["https_proxy"] == "http://proxy.test:8080"
    assert captured["node_proxy"] == "1"
    assert "@brave/brave-search-mcp-server" in captured["args"]
    assert "--transport stdio" in captured["args"]
    assert "--logging-level warn" in captured["args"]
    assert "--enabled-tools brave_web_search brave_news_search" in captured["args"]


def test_upstream_exit_code_and_stderr_remain_visible() -> None:
    with tempfile.TemporaryDirectory() as directory:
        binary_directory = Path(directory)
        _install_shell_helpers(binary_directory)
        _write_executable(
            binary_directory / "node",
            "#!/bin/bash\nprintf '%s\\n' 'v24.0.0'\n",
        )
        _write_executable(
            binary_directory / "npx",
            "#!/bin/bash\nprintf '%s\\n' 'simulated upstream exit' >&2\nexit 17\n",
        )
        environment = {
            "PATH": str(binary_directory),
            "BRAVE_API_KEY": "test-key",
        }

        result = _run(environment)

    assert result.returncode == 17
    assert "simulated upstream exit" in result.stderr
