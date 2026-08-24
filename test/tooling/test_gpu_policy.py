from __future__ import annotations

import pytest

from scripts.gpu_test_policy import (
    main,
    require_8gb_profile_budget,
    require_conflict_budget,
)


@pytest.mark.parametrize("budget", (1, 4096, 8000))
def test_8gb_profile_accepts_only_budgets_at_or_below_ceiling(budget: int) -> None:
    assert require_8gb_profile_budget(budget) == budget


@pytest.mark.parametrize("budget", (8001, 12288, 16384))
def test_conflict_mode_requires_an_explicit_higher_budget(budget: int) -> None:
    assert require_conflict_budget(budget) == budget


def test_profile_and_conflict_budgets_cannot_share_the_8gb_boundary() -> None:
    with pytest.raises(ValueError, match="exceeds the 8 GB profile ceiling"):
        require_8gb_profile_budget(8001)
    with pytest.raises(ValueError, match="does not authorize deliberate GPU overlap"):
        require_conflict_budget(8000)


@pytest.mark.parametrize("budget", (None, "", "not-a-number", 0, -1))
def test_invalid_budgets_fail_closed(budget: str | int | None) -> None:
    with pytest.raises(ValueError):
        require_8gb_profile_budget(budget)


def test_cli_reads_the_explicit_conflict_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MCP_TOOLS_GPU_CONFLICT_BUDGET_MIB", "8001")

    assert main(["conflict"]) == 0

    assert "mode=conflict, budget_mib=8001" in capsys.readouterr().out
