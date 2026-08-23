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
