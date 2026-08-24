# Contributing to mcp-tools

Thanks for helping improve the toolkit. Contributions should keep the public MCP
surface small, factual, and reproducible on a fresh Linux or WSL2 checkout.

## Before you start

- Search existing issues before opening a duplicate.
- For a user-visible behavior change or new dependency, open a feature request
  first so the runtime and compatibility trade-offs can be discussed.
- Never commit credentials, exported cookies, model weights, test recordings,
  generated OCR artifacts, local logs, or machine-specific paths.
- Keep new and changed repository content in English.

## Development setup

The repository owns three independent uv projects. Restore only the profile you
need from the repository root; do not install project packages into the Linux
distribution Python.

```bash
# CPU-side servers, shared lint tool, and CPU tests
uv sync --project environments/mcp-local --locked

# ASR and diarization
uv sync --project environments/mcp-local-asr --locked

# OCR
uv sync --project environments/mcp-local-ocr --locked
```

Profile-specific system requirements are documented in
[`README.md`](README.md#installation-profiles). The quick MCP smoke test needs
only the CPU project:

```bash
environments/mcp-local/.venv/bin/python examples/pdf_to_text_demo.py
```

## Run tests

Run commands from the repository root so imports and fixtures resolve
consistently.

For the normal CPU-safe contributor gate, run:

```bash
scripts/check.sh
```

This is the same entry point used by CI. It runs Ruff, shell syntax checks,
CPU-compatible tests, MCP discovery for all five repository-owned frontends,
the PDF-to-text example, `bin/mcp-tools doctor`, Markdown link and GitHub YAML
validation, and `git diff --check`.

To run suites individually:

```bash
# CPU-side unit tests
environments/mcp-local/.venv/bin/python -m pytest -q \
  test/format_conversion \
  test/browser_fetch/test_cpu_installer.py \
  test/vision_local/test_vision_runtime.py \
  test/ocr/test_model_paths.py \
  test/tooling/test_doctor.py

# ASR and pipeline tests
environments/mcp-local-asr/.venv/bin/python -m pytest -q \
  test/asr test/asr_pipeline

# OCR tests
environments/mcp-local-ocr/.venv/bin/python -m pytest -q test/ocr
```

Run only the MCP registration contract check with:

```bash
environments/mcp-local/.venv/bin/python scripts/mcp_discovery.py
```

The discovery check initializes each frontend with the shared CPU interpreter
and validates all 24 stable tool names. It does not load models, launch a
browser, call an external API, or perform inference.

Some OCR integration tests bind loopback sockets. If a restricted sandbox
rejects local socket creation, rerun the unchanged command in the host terminal
before treating the result as a product failure.

Real GPU or browser smoke tests are opt-in because they require large model
artifacts, hardware, or network access. Follow
[`docs/mcp-tools-testing.md`](docs/mcp-tools-testing.md) and record the exact
hardware, model revision, command, and fixture scope in any reported result.

## Lint and format

The shared CPU profile pins Ruff. Check the full tracked Python surface:

```bash
environments/mcp-local/.venv/bin/ruff check \
  asr asr-pipeline browser-fetch format-conversion ocr vision-local test examples
```

Format only Python files you changed; avoid an unrelated repository-wide
reformat in a focused pull request.

```bash
environments/mcp-local/.venv/bin/ruff format --check path/to/changed.py
environments/mcp-local/.venv/bin/ruff format path/to/changed.py
```

For shell changes, at minimum run the affected script's `--help` or no-op check
and `bash -n path/to/script.sh`.

Use `bin/mcp-tools doctor` when changing installers, model paths, launchers, or
runtime requirements. It reports credential presence without printing secret
values.

## Add or change an MCP tool

1. Choose the existing server and uv profile that own the dependencies. Avoid a
   new runtime unless dependency isolation genuinely requires one.
2. Keep the stdio frontend lightweight. Heavy models belong behind the existing
   lazy local backend pattern.
3. Add a typed `@mcp.tool()` function with an actionable docstring, explicit
   path validation, bounded timeouts, and structured error output.
4. Write logs to stderr. Stdout is reserved for MCP protocol messages.
5. Preserve stable tool names and return fields unless the change is explicitly
   designed and documented as a breaking change.
6. Add focused tests. Include an end-to-end stdio check when transport behavior
   changes.
7. Update the component README and
   [`docs/tools-reference.md`](docs/tools-reference.md). Update the root README
   when the capability, install profile, environment variables, or routing
   guidance changes.

For long work, prefer submit/status/wait APIs over one unbounded MCP call. For
local GPU services, account for the repository's ASR/OCR shared-GPU exclusion
and idle release behavior.

## Documentation changes

- Describe only behavior supported by the current implementation.
- Use repository-relative links and portable placeholders such as
  `/absolute/path/to/mcp-tools`.
- Mark hardware measurements with the date, device, fixture, and method.
- Do not generalize a workstation-specific result into a universal benchmark.
- Keep secrets and private infrastructure out of examples.

## Pull requests

Open the pull request against the repository's default branch unless a
maintainer asks for another target. Keep each pull request focused and explain:

- the user problem and chosen approach;
- which profile or public tool surface changes;
- tests, lint, and manual checks run;
- hardware or external services required for unrun checks;
- compatibility or migration considerations.

The pull-request template contains the final checklist. A change does not need
to exercise every GPU profile, but its untested boundaries must be explicit.
