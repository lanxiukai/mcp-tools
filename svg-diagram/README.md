# SVG Diagram

Seven local CPU MCP tools for editable model architecture diagrams, semantic
themes, measured text, vector mathematics, and geometry inspection. The server
uses the existing `mcp-local` Python profile, Playwright Chromium, fontconfig,
the MathJax runtime locked in `../format-conversion/package-lock.json`, and
ELKjs 0.12.0 locked in this component's [package-lock.json](package-lock.json).
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
npm ci --prefix svg-diagram --ignore-scripts --no-audit --no-fund
fc-match 'Noto Sans CJK SC'
```

Install `fontconfig` and `fonts-noto-cjk` if needed. A fontconfig fallback result
does not mean the requested family is installed: `diagram_catalog` checks exact
family matches. Use the current [client setup guide](../docs/client-configuration.md)
with launcher argument `svg-diagram` and a 120-second tool timeout.

## Authoring workflow

1. Call `create_diagram` with a template and a few parameters. It renders SVG,
   checks geometry, writes a full-resolution PNG, and returns an inline preview.
2. Review the preview and `inspection.issues`. The default response contains
   paths, a revision, logical node IDs, and a compact inspection summary.
3. Call `update_diagram` with a batch of ID-based changes and the returned
   `expected_revision`. It validates the complete changed spec and renders once.
4. Use `diagram_catalog(template="attention", include_schema=true)` for one
   template's parameters. `section="templates"` lists only the available templates.
5. For a fully custom graph, use `render_diagram` with a spec. Its existing
   defaults remain `detail="full", preview="none"`. Compact output and preview
   are opt-in through the same arguments as the new tools.

```python
create_diagram(
    template="residual",
    parameters={"channels": 64, "layers": 2, "language": "en"},
    layout="elk",
    output_path="/absolute/path/residual.svg",
)

update_diagram(
    file_path="/absolute/path/residual.svg",
    expected_revision="<revision returned by the previous call>",
    changes=[
        {"op": "set_label", "id": "input", "values": {"text": "Feature input"}},
        {"op": "set_diagram", "values": {"theme": "print"}},
    ],
)
```

`preview="none"` skips PNG generation; `"file"` saves `<stem>.preview.png`;
`"inline"` also returns a native MCP image limited to 1200 pixels on its longest
side. The saved PNG retains full resolution. `detail="summary"` omits coordinates
and caps returned issues at 12, with an omitted count. `"full"` retains the full
layout and inspection report. Read the report and review the image: neither
layout engine verifies model semantics.

The SVG embeds its canonical spec in `<metadata id="diagram-spec">`.
`inspect_diagram(include_spec=true)` retrieves it together with the current
revision. Changes address logical IDs such as `input`, not DOM IDs such as
`node-input`. `set_label` supports `label_index` and preserves typography while
switching text/math content. `add`, `set`, and `remove` operations exist for
nodes, edges, and groups; `set_diagram` changes title, description, theme, font,
or layout. Removals do not cascade: remove dangling references in the same batch.

Updates replace the source and its generated preview by default. Supply
`output_path` to write a copy; existing copy destinations require `overwrite`.
A supplied revision is checked before editing, and a changed source during
rendering aborts publication. These are optimistic checks; serialize writers to
the same path. Edits regenerate from the embedded spec and do not import manual
SVG-only changes. Automatic layouts may move other nodes after content changes.

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
| `diagram_catalog` | Optional template/example, section, and `include_schema` | Selected template defaults/schema or themes, fonts, layouts, and examples |
| `create_diagram` | Template, parameters, theme/layout, output path | Editable SVG, compact report, revision, and preview |
| `update_diagram` | Source, revision, and ID-based change batch | Validated revised SVG, compact report, revision, and preview |
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

## Parameterized templates

| Template | Parameters beyond optional `language=en|zh` and `title` |
|---|---|
| `residual` | `channels`, `layers` (1-8 convolutions), `kernel_size` |
| `encoder-decoder` | `channels` (1-6 levels), `latent_dim`, `skip_connections` |
| `attention` | `model_dim`, `heads`, `tokens`; model width must divide evenly into heads |
| `loss-branches` | `terms`: 2-6 objects with `label`, `symbol`, and optional `formula` |
| `tensor-stack` | `shapes`: 1-6 dimension lists; optional `operations` between adjacent tensors |
| `cvae`, `stylegan2`, `vq-vae` | The existing complete examples with optional localization/title |

Templates expand into ordinary specs; every resulting node remains editable.
They are schematic building blocks, not a model-code tracer. `grid` preserves
the authored arrangement; `layered` and `elk` generate automatic placement and
omit the template's grid coordinates.

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
  coordinates also pin a node in native layered/grid modes. Leave room for the title.
- `elk`: uses the locked ELK Layered engine with orthogonal routing, fixed
  named-port positions, edge-label space, and compound groups. Directions are
  `LR` and `TB`. It rejects explicit x/y or row/column pins and `via` waypoints
  instead of silently ignoring them. The worker has a 30-second deadline.
  ELK and native grid layouts emphasize different arrangements; choose the one
  that makes the model's explanation clearest.
- `gap_x`, `gap_y`, and `padding` control spacing. `width` and `height` override
  node sizes. Shapes are `box`, `circle`, and `tensor`. Circles use the larger
  dimension. Tensor nodes draw three stacked planes inside their bounds; fixed
  dimensions must exceed the 12-pixel stack depth.
- Built-in ports are `left`, `right`, `top`, and `bottom`. Named ports use
  `{"id":"skip","side":"bottom","offset":0.75}` on a node. An edge's
  `source_port` and `target_port` select them. Offsets range from 0.1 to 0.9.
- `via: [{"x": ..., "y": ...}]` adds ordered waypoints in native layouts. Routing uses orthogonal
  obstacle-boundary tracks with a bend penalty; it avoids node interiors and
  known title/label boxes. It does not optimize all edge crossings globally.
- Groups use `id`, `title`, `members`, and `role`. Native layouts enclose
  already-placed members; keep unrelated nodes outside that region. ELK lays
  out members as compound nodes. The current spec allows one group level.
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

The MathJax Node runtime is shared with Format Conversion. ELK has its own
component-local npm manifest/lock and uses its published
`EPL-2.0 OR GPL-3.0-or-later` license; see the installed package license.
[ELKjs](https://github.com/kieler/elkjs) supplies coordinates, not SVG rendering. `MATHJAX_NODE_PATH` can point
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
detail diagrams. Existing outputs require explicit overwrite or an
`update_diagram` call. Change batches are limited to 100 operations and 64 KiB.

## Verification

```bash
environments/mcp-local/.venv/bin/python -m pytest -q test/svg_diagram
environments/mcp-local/.venv/bin/python scripts/mcp_discovery.py
scripts/check.sh
```

The focused suite exercises both themes and all examples, mixed CJK/math
measurement, actual post-render geometry edits, obstacle routing, malformed
math, output preservation, hostile SVG rejection, and all seven MCP operations
over stdio. It also covers all templates with native/ELK layout, stale and
concurrent edits, preview failures, and spec round-trip reproducibility.
Browser/ELK-dependent cases skip if their optional runtime is missing; CI
provisions Chromium, MathJax, and ELK. No GPU tests are required.

The [efficiency comparison](../examples/svg_diagram_efficiency.py) records real
MCP requests/results for the full-spec and compact workflows, checking that
they generate identical SVGs. See [measurement scope](../docs/svg-diagram-efficiency.md).
