#!/usr/bin/env python3
"""Validate explicit VRAM budgets before local GPU test groups run."""

from __future__ import annotations

import argparse
import os


PROFILE_8GB_CEILING_MIB = 8000
CONFLICT_MINIMUM_MIB = PROFILE_8GB_CEILING_MIB + 1


def parse_budget(value: str | int | None, *, environment_name: str) -> int:
    """Parse one positive integer VRAM budget without guessing a default."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{environment_name} must be set to an integer MiB budget")
    try:
        budget = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{environment_name} must be an integer MiB budget") from exc
    if budget < 1:
        raise ValueError(f"{environment_name} must be positive")
    return budget


def require_8gb_profile_budget(value: str | int | None = None) -> int:
    """Accept only a whole-device budget at or below the tested 8 GB ceiling."""
    environment_name = "MCP_TOOLS_GPU_BUDGET_MIB"
    budget = parse_budget(
        os.environ.get(environment_name) if value is None else value,
        environment_name=environment_name,
    )
    if budget > PROFILE_8GB_CEILING_MIB:
        raise ValueError(
            f"{environment_name}={budget} exceeds the 8 GB profile ceiling of "
            f"{PROFILE_8GB_CEILING_MIB} MiB"
        )
    return budget


def require_conflict_budget(value: str | int | None = None) -> int:
    """Reject deliberate overlap unless a higher explicit budget is provided."""
    environment_name = "MCP_TOOLS_GPU_CONFLICT_BUDGET_MIB"
    budget = parse_budget(
        os.environ.get(environment_name) if value is None else value,
        environment_name=environment_name,
    )
    if budget < CONFLICT_MINIMUM_MIB:
        raise ValueError(
            f"{environment_name}={budget} does not authorize deliberate GPU "
            f"overlap; provide more than {PROFILE_8GB_CEILING_MIB} MiB"
        )
    return budget


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("8gb-profile", "conflict"))
    parser.add_argument(
        "--budget-mib",
        help="Explicit MiB budget; otherwise read the mode-specific environment variable",
    )
    arguments = parser.parse_args(argv)
    validator = (
        require_8gb_profile_budget
        if arguments.mode == "8gb-profile"
        else require_conflict_budget
    )
    try:
        budget = validator(arguments.budget_mib)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"GPU test budget accepted: mode={arguments.mode}, budget_mib={budget}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
