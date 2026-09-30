# Examples

This directory contains small, reproducible examples that exercise the real
repository servers through MCP rather than calling implementation functions
directly.

## PDF-to-text stdio demo

[`pdf_to_text_demo.py`](pdf_to_text_demo.py) creates a temporary born-digital
PDF, launches the Format Conversion MCP server through `bin/mcp-tools`, calls
`pdf_to_text`, validates the extracted text, and removes the temporary files.
It does not require CUDA, a browser, an API key, or committed binary fixtures.

From the repository root:

```bash
uv sync --project environments/mcp-local --locked
environments/mcp-local/.venv/bin/python examples/pdf_to_text_demo.py
```

Expected final line:

```text
MCP round trip: OK
```


## SVG model diagram stdio demo

[`svg_diagram_demo.py`](svg_diagram_demo.py) requests three reusable specs,
creates editable SVGs through the SVG Diagram server, and checks their geometry.
`--preview` also rasterizes them through Format Conversion. It requires the CPU
profile, Chromium, Noto CJK fonts, and the shared MathJax runtime.

```bash
environments/mcp-local/.venv/bin/python examples/svg_diagram_demo.py \
  --output-dir /tmp/svg-diagram-demo --preview
```

Use `--theme print` for a light theme and `--overwrite` to replace existing demo
artifacts. See the [SVG Diagram guide](../svg-diagram/README.md).


## SVG authoring efficiency comparison

[`svg_diagram_efficiency.py`](svg_diagram_efficiency.py) compares full-spec
rendering plus a PNG conversion against parameterized creation and ID-based
editing. It records real MCP JSON payloads and tool execution durations, and
checks identical SVG output for each pair.

```bash
environments/mcp-local/.venv/bin/python examples/svg_diagram_efficiency.py \
  --output-dir /tmp/svg-diagram-efficiency --language zh
```

It writes `measurements.json` and the compared SVG/PNG artifacts. This measures
request/result text and tool calls, not complete conversation usage or billing.
See [scope and results](../docs/svg-diagram-efficiency.md).
