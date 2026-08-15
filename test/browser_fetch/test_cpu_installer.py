from pathlib import Path


def test_shared_cpu_installer_uses_repository_uv_project() -> None:
    installer = Path(__file__).resolve().parents[2] / "install.sh"
    text = installer.read_text(encoding="utf-8")
    cpu_block = text.split(
        "# --------------- shared CPU runtime installation ---------------",
        maxsplit=1,
    )[1].split(
        "# --------------- configuration output ---------------",
        maxsplit=1,
    )[0]

    assert '"$UV_BIN" sync --project "$CPU_PROJECT_DIR" --locked' in cpu_block
    assert '"$CPU_PROJECT_DIR/.venv/bin/playwright" install chromium' in cpu_block
    assert 'echo "  Python: $CPU_PYTHON"' in cpu_block
    assert "ensure_environment" not in cpu_block
    assert "CONDA_CMD" not in cpu_block
