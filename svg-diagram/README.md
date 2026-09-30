# SVG Diagram

Five local CPU MCP tools for editable model architecture diagrams, semantic
themes, measured text, vector mathematics, and geometry inspection. The server
uses the existing `mcp-local` Python profile, Playwright Chromium, fontconfig,
and the MathJax runtime locked in `../format-conversion/package-lock.json`.
No GPU, model download, hosted API, or credential is required.

## Install and connect

From the repository root:

```bash
bash install.sh --cpu-only
bin/mcp-tools svg-diagram
```

The second command starts an MCP stdio server; stdout is reserved for the
protocol. If the CPU profile already exists, the additional runtime checks are:

```bash
environments/mcp-local/.venv/bin/playwright install chromium
npm ci --prefix format-conversion --ignore-scripts --no-audit --no-fund
fc-match 'Noto Sans CJK SC'
```

Install `fontconfig` and `fonts-noto-cjk` if needed. A fontconfig fallback result
does not mean the requested family is installed: `diagram_catalog` checks exact
family matches. Use the current [client setup guide](../docs/client-configuration.md)
with launcher argument `svg-diagram` and a 120-second tool timeout.

## Authoring workflow

1. Call `diagram_catalog(example="stylegan2")` for a complete editable spec.
   `include_schema=true` adds the versioned JSON schema.
2. Edit the spec: nodes describe content, roles describe styling, edges describe
   dependencies, and layout constraints describe placement.
3. Call `render_diagram(spec=..., output_path="/absolute/path/model.svg")`.
4. Review `inspection.issues` and revise the spec if needed. The returned layout
   includes actual node bounds and edge waypoints for targeted adjustments.
5. Call `format_conversion.svg_to_png` and inspect the PNG visually. Automated
   checks do not establish model correctness or good information hierarchy.

The SVG embeds the complete input spec in `<metadata id="diagram-spec">`.
Regenerate from that spec after an edit. `inspect_diagram` reads actual SVG
geometry, so it also catches many problems introduced by manual SVG edits.

Run the three examples through real MCP sessions:

```bash
environments/mcp-local/.venv/bin/python examples/svg_diagram_demo.py \
  --output-dir /tmp/svg-diagram-demo --preview
```

Use `--theme print` for the light theme. Replacing existing outputs requires
`--overwrite`. Portable inputs are in [examples/](examples/):
[conditional VAE](examples/cvae.json), [StyleGAN2](examples/stylegan2.json), and
[VQ-VAE](examples/vq-vae.json).

## Tool surface

| Tool | Input | Result |
|---|---|---|
| `diagram_catalog` | Optional example name and `include_schema` | Themes, exact font availability, layout choices, example spec, optional schema |
| `render_math` | TeX expressions, absolute output directory, font size and color | Standalone SVG paths, width, height, and baseline for each formula |
| `measure_labels` | Text/math labels and font family | Actual line widths, ascent/descent, total bounds, wrapping and overflow |
| `render_diagram` | Typed version-1 spec, absolute SVG destination, optional overwrite | Editable SVG, node/edge layout, and inspection findings |
| `inspect_diagram` | Absolute SVG path | Geometry/font/reference issues with stable element IDs and inspection coverage |

Operations return structured errors with `status="error"`, error type, and an
actionable message. `render_diagram` can publish a valid SVG with layout issues:
always inspect its report. Invalid input, missing fonts, unroutable edges, or
invalid math fail before replacing an existing SVG. Formula batches validate
all formulas before publishing any files. Output creation is atomic per file;
a multi-file batch is not a filesystem transaction.

## Spec and labels

Minimal example:

```json
{
  "version": 1,
  "title": "Encoder and decoder",
  "theme": "dark",
  "layout": {"mode": "layered", "direction": "LR"},
  "nodes": [
    {"id": "encoder", "role": "feature", "labels": [
      {"text": "Encoder", "weight": 700},
      {"latex": "z=E_\\phi(x)"}
    ]},
    {"id": "decoder", "role": "feature", "labels": [
      {"text": "Decoder", "weight": 700},
      {"latex": "\\hat{x}=G_\\theta(z)"}
    ]}
  ],
  "edges": [{"id": "latent", "source": "encoder", "target": "decoder",
    "role": "state", "label": {"latex": "z"}}]
}
```

Each label has exactly one of `text`, `latex`, or `spans`. A mixed label is:

```json
{"spans": [{"kind": "text", "content": "Reconstruction "},
           {"kind": "math", "content": "\\hat{x}"}],
 "font_size": 20, "weight": 400, "max_width": 200, "align": "center"}
```

`max_width` wraps words and CJK text using Chromium's measured SVG text metrics.
An oversized word can break at grapheme boundaries. Math spans remain intact;
an over-wide expression reports overflow. Explicit newlines are supported in
plain text. Set a node's `width` to wrap its labels to the inner text area;
explicit dimensions are preserved and overflow is reported, not hidden.

Text is editable and uses one installed font family, defaulting to
`Noto Sans CJK SC`. The tool checks glyph coverage instead of silently switching
fonts. Receivers need the same text font for identical appearance; math glyphs
are already paths. MathJax's mathematical alphabet is independent of the text
font. Set label colors explicitly only when overriding semantic theme colors is
intentional. Both normal and bold text are supported.

## Layout and styling

- `layered`: ranks a DAG left-to-right (`LR`) or top-to-bottom (`TB`). Mark
  backward dependencies `feedback=true` to omit them from ranking. They remain
  visible and routed. This is deterministic rank placement, not global layout
  optimization.
- `grid`: specify zero-based `row` and `column` on every unpinned node. Rows and
  columns grow to accommodate measured content. This suits parallel branches,
  losses, and two-stream architectures.
- `manual`: supply absolute `x` and `y` coordinates for every node. Explicit
  coordinates also pin a node in the other modes. Leave room for the title.
- `gap_x`, `gap_y`, and `padding` control spacing. `width` and `height` override
  node sizes. Shapes are `box` and `circle`; a circle uses the larger dimension.
- Built-in ports are `left`, `right`, `top`, and `bottom`. Named ports use
  `{"id":"skip","side":"bottom","offset":0.75}` on a node. An edge's
  `source_port` and `target_port` select them. Offsets range from 0.1 to 0.9.
- `via: [{"x": ..., "y": ...}]` adds ordered waypoints. Routing uses orthogonal
  obstacle-boundary tracks with a bend penalty; it avoids node interiors and
  known title/label boxes. It does not optimize all edge crossings globally.
- Groups use `id`, `title`, `members`, and `role`. They enclose already-placed
  members. They are not nested and do not rearrange the graph. Keep unrelated
  nodes outside the enclosed region.
- Semantic roles are `default`, `feature`, `state`, `condition`, `loss`, and
  `frozen`; `frozen` also uses dashed outlines. Edges can set `dashed=true`.
  Themes are `dark` and `print`; their role colors are returned by the catalog.

## Formula rendering

`render_math` accepts 1-128 expressions without `$...$` delimiters. It writes
`formula-001.svg`, etc., and returns `width_px`, `height_px`, and `baseline_px`
at the requested font size. The baseline is measured from the SVG's top edge.
Use those metrics when embedding formulas in custom SVGs.

The local worker supports MathJax base/AMS mathematics, including subscripts,
fractions, accents, matrices, sums, and norms. It deliberately omits MathJax's
undefined-command fallback: a typo is an error, not a rendered command name.
It is not a full LaTeX/TikZ compiler. Glyph IDs are namespaced per placement;
characters without MathJax vector glyphs are rejected. Put CJK prose in a text
span next to a math span instead of inside a TeX `\text{...}` expression.
Glyph definitions and required CSS travel with each SVG. No browser-side
MathJax script or network font fetch is required to view the result.

The Node runtime is shared with Format Conversion. `MATHJAX_NODE_PATH` can point
to an alternate local Node module search root. It does not change the lock.
The worker has a 45-second batch deadline.

## Inspection and boundaries

Generated diagrams receive checks for node/label overlap, text-area overflow,
edges crossing nodes/labels, group overlap with non-members, canvas bounds,
font coverage, duplicate IDs, and broken internal references. The checks use
Chromium's final SVG geometry, not positions saved in metadata. Existing generic
SVGs receive bounds/font/reference checks; they need the generated classes and
IDs for node/edge semantics. Inspection returns its coverage explicitly.

Only static, self-contained SVG is accepted for inspection. Scripts, event
handlers, external references, CSS imports, foreign objects, and unsupported
elements are rejected. Browser requests are blocked. XML parsing rejects entity
expansion. Bounds and collisions are geometric checks; stroke/marker extents,
arbitrary curved-edge intersections, accessibility, color perception, and
model semantics still require review.

Limits: 40 nodes, 80 edges, 12 groups, 128 formulas per render batch, 4,096
characters per label/span, 256 KiB diagram specs, and 16 MiB SVG input. Generated
canvases are limited to 8,192 pixels per side and 32 million pixels, matching the
existing PNG converter. Large architectures should be split into overview and
detail diagrams. No existing source diagram is modified automatically.

## Verification

```bash
environments/mcp-local/.venv/bin/python -m pytest -q test/svg_diagram
environments/mcp-local/.venv/bin/python scripts/mcp_discovery.py
scripts/check.sh
```

The focused suite exercises both themes and all examples, mixed CJK/math
measurement, actual post-render geometry edits, obstacle routing, malformed
math, output preservation, hostile SVG rejection, and all five MCP operations
over stdio. Browser-dependent cases skip only if the optional Chromium or
MathJax runtime is missing; CI provisions both. No GPU tests are required.
