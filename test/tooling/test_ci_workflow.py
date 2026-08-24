from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_cpu_ci_targets_both_supported_ubuntu_images() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    assert "runs-on: ${{ matrix.os }}" in workflow
    assert "ubuntu-22.04" in workflow
    assert "ubuntu-24.04" in workflow
    assert "fail-fast: false" in workflow
