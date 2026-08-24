#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CPU_PYTHON="${REPO_DIR}/environments/mcp-local/.venv/bin/python"
RUFF="${REPO_DIR}/environments/mcp-local/.venv/bin/ruff"

if [[ ! -x "$CPU_PYTHON" || ! -x "$RUFF" ]]; then
    printf '%s\n' \
        'The locked CPU development profile is missing.' \
        'Run: uv sync --project environments/mcp-local --locked' >&2
    exit 1
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

cd "$REPO_DIR"

printf '\n== Ruff ==\n'
"$RUFF" check \
    asr asr-pipeline browser-fetch format-conversion ocr vision-local test examples scripts

printf '\n== Shell syntax ==\n'
while IFS= read -r -d '' shell_file; do
    bash -n "$shell_file"
done < <(
    git ls-files --cached --others --exclude-standard -z -- '*.sh'
)
printf 'Shell syntax: OK\n'

printf '\n== CPU-safe tests ==\n'
"$CPU_PYTHON" -m pytest -q \
    test/format_conversion \
    test/browser_fetch/test_cpu_installer.py \
    test/browser_fetch/test_reliability.py \
    test/brave_websearch/test_launcher.py \
    test/vision_local/test_vision_runtime.py \
    test/ocr/test_model_paths.py \
    test/tooling/test_doctor.py

printf '\n== MCP discovery ==\n'
"$CPU_PYTHON" scripts/mcp_discovery.py --python "$CPU_PYTHON"

printf '\n== PDF-to-text example ==\n'
"$CPU_PYTHON" examples/pdf_to_text_demo.py

printf '\n== Doctor ==\n'
bin/mcp-tools doctor

printf '\n== Repository metadata ==\n'
"$CPU_PYTHON" scripts/validate_repository.py

printf '\n== Whitespace ==\n'
git diff --check

printf '\nAll normal CPU-safe checks passed.\n'
