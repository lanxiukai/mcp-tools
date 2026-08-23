## What changed

Describe the user problem and the focused solution.

## Verification

List exact tests, lint commands, MCP smoke checks, and relevant hardware or
external services. Explain any checks you could not run.

## Checklist

- [ ] I used the owning locked uv profile and did not rely on distribution Python.
- [ ] I ran `scripts/check.sh`, or explained why a normal CPU-safe check could not run.
- [ ] I added or updated focused tests for behavior changes.
- [ ] I ran Ruff on changed Python files and avoided unrelated reformatting.
- [ ] I updated the relevant component and entry documentation.
- [ ] I preserved stable MCP tool names and return fields, or documented the breaking change.
- [ ] I removed credentials, cookies, personal data, machine-specific paths, and generated artifacts.
- [ ] I identified model, license, hardware, network, or external-service implications.
