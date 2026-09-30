# SVG Diagram Workflow Efficiency

This local check compares two supported ways to generate the same diagram and
its PNG: a complete spec plus a separate conversion, and parameterized creation
or an ID-based edit with an integrated preview. It does not measure total agent
usage or billing.

## Method

- Date: 2026-09-30. Three existing templates: cVAE, StyleGAN2, and VQ-VAE.
- Native grid layout, Chinese labels, and dark theme; one creation and one label edit per model.
- Both routes run through real MCP stdio sessions. Each corresponding SVG is byte-identical.
- The full-spec route sends a minimized spec (defaults and nulls omitted) to `render_diagram`, then calls `svg_to_png`.
- The compact route calls `create_diagram` or `update_diagram` with `preview="file"`.
- Count only the JSON request and result payloads, serialized with `ensure_ascii=False` and compact separators. Count each request/result separately, then sum.
- Text encoding: `o200k_base` via tiktoken. This is an encoding-based estimate, not a model-specific usage report.
- Exclude tool definitions, system/user conversation, reasoning, native image tokens, and client overhead. Inline previews add image input and are deliberately excluded from this text comparison.

## Observed text payloads

| Diagram | Operation | Full-spec tokens | Compact tokens | Reduction |
|---|---|---:|---:|---:|
| cvae | create | 2,450 | 207 | 91.6% |
| cvae | edit | 2,452 | 235 | 90.4% |
| stylegan2 | create | 1,717 | 208 | 87.9% |
| stylegan2 | edit | 1,666 | 238 | 85.7% |
| vq-vae | create | 2,095 | 214 | 89.8% |
| vq-vae | edit | 2,096 | 241 | 88.5% |

Across these six cases, request/result text decreased from **12,476** to **1,343** tokens (**89.2%**). SVG+PNG tool calls decreased from **12** to **6**.

Measured tool execution times were about 0.70-0.95 seconds per case. Some
compact calls were faster and some were slower. The demonstrated benefit is
smaller text payloads and fewer tool round trips, not an established reduction
in local rendering time. Timings are single sequential observations, exclude
server initialization and visual review, and are not a general performance claim.

ELK is evaluated separately for geometry and previews. It is not used in this
payload comparison, so these numbers do not imply that ELK is faster than native layout.

## Reproduce

From the repository root after restoring the CPU, browser, and Node runtimes:

```bash
environments/mcp-local/.venv/bin/python examples/svg_diagram_efficiency.py \
  --output-dir /tmp/svg-diagram-efficiency --language zh
```

The script writes `measurements.json` with actual request/result objects,
character counts, call counts, elapsed times, and the compared SVG/PNG files.
Tokenize each recorded request and response separately using the serialization
and encoding above. File paths affect exact counts; they may differ slightly
on another checkout. The script itself adds no token-counting dependency to MCP.

Use `--overwrite` only to replace existing benchmark artifacts. The regular
[SVG Diagram guide](../svg-diagram/README.md) documents API options and limits.
