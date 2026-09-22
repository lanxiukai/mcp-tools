# Format Conversion — Document and Image Conversion MCP Service

Provides 5 local CPU tools: link preflight, Markdown/HTML → PDF, PDF → plain text,
and SVG → PNG. HTML→PDF supports dual engines (Chromium / WeasyPrint), while
SVG rasterization uses a bounded, safe-by-default CairoSVG path.

---

## MCP Tools

| Tool | Input | Output | Engine |
|---|---|---|---|
| `inspect_pdf_links` | `.md` / `.html` | Local references, actual PDF candidates, selection evidence, missing targets | Read-only local inspection |
| `markdown_to_pdf` | `.md` | `.pdf` (A4 layout, selectable print/sepia/One Dark Pro Night Flat theme, CJK/tables/code blocks/MathJax SVG math) | markdown-it-py + Chromium (default) / WeasyPrint |
| `html_to_pdf` | `.html` | `.pdf` (selectable print/sepia/One Dark Pro background; auto-polishes recognized portable analytics reports) | Chromium (default) / WeasyPrint |
| `pdf_to_text` | `.pdf` (born-digital) | Plain text string + auto-saved `.txt` | PyMuPDF (fitz) |
| `svg_to_png` | `.svg` | Validated `.png` with optional scaling, dimensions, and background | CairoSVG 2.9.0 |

> `markdown_to_pdf` defaults to `engine="chromium"` and `theme="print"` in the MCP tool. MathJax SVG preprocessing works with both engines; Chromium is recommended for math-heavy documents because its SVG/CSS rendering matches Chrome. The underlying `converter.py` function keeps its lightweight `engine="weasyprint"` default.
>
> `html_to_pdf` defaults to the Chromium backend (Playwright) and `theme="print"`. It supports the same `print`, `sepia`, and `one-dark-pro` choices as Markdown conversion. Portable Data Analytics artifacts are recognized from their root semantic marker and receive a scoped A4 report profile: repeated inline provenance is consolidated into one summary near the front, charts and metric cards gain theme-aware print styling, and Chromium turns tables with 10 or more columns into labeled record cards. For simple documents, use `engine="weasyprint"` to switch to the lightweight backend. `pdf_to_text` auto-saves a `.txt` file in the same directory by default; set `save_text=False` to disable.

> `pdf_to_text` only handles born-digital PDFs (text selectable/copyable). For scanned PDFs, use `ocr_document`.
>
> `svg_to_png` accepts self-contained SVG. External file and network references
> are blocked; embedded `data:` resources remain available. The tool limits
> input to 16 MiB, each output side to 8192 pixels, and the canvas to 32 million
> pixels.

MCP Server entry point: `format_mcp_server.py` (FastMCP, stdio protocol).

### Clickable PDF links

Both PDF engines preserve Markdown links and HTML `<a href="...">` links as
clickable text. Supported destinations include web URLs, local absolute paths,
`file://` URLs, and relative paths such as `../../other-repo/docs/guide.md`.
Markdown reference links and explicit autolinks (`<file:///path/to/guide.md>`)
are supported too.

Relative file links resolve from the source document's directory, including
paths outside its repository, even when the PDF is saved elsewhere. An authored
HTML `<base href="...">` continues to control relative URL resolution. Local
destinations are stored as absolute file URLs, retaining query strings and
fragments. Before conversion, call `inspect_pdf_links` and review its result.
By default, local Markdown/HTML references prefer an existing, unambiguous PDF.
References without a PDF (often `README.md`) and other file types such as `.py`
keep their original targets. Linked documents are never converted or embedded
automatically, and the original source content is not edited.
Use angle brackets around Markdown destinations containing spaces, for example
`[Guide](<../other-repo/docs/guide notes.md>)`.

Links to existing HTML anchors such as `[Details](#details)` with
`<h2 id="details">Details</h2>` jump within the PDF. Markdown headings do not
automatically receive anchor IDs.

The bundled [PDF Local Links extension](../vscode-pdf/README.md) opens those
targets in VS Code and retains the current WSL connection. Upstream
`tomoki1207.pdf` does not render `file:` annotations as clickable links.
Moving or sharing a PDF does not move its linked files.

### Select the actual PDF filename

```python
inspect_pdf_links("/absolute/path/index.md")
markdown_to_pdf(
    "/absolute/path/index.md",
    pdf_targets={"../other-repo/guide.html": "../other-repo/exports/guide-print.pdf",
                 "README.md": None},
)
```

The same `pdf_targets` and `link_policy` arguments apply to `html_to_pdf` and
the Python conversion functions. Relative mapping keys and values resolve from
the source directory, independently of the PDF output directory. An explicit
`null`/`None` keeps the source even when a PDF exists. `link_policy="preserve"`
retains all original targets.

A unique same-stem PDF or a PDF's recorded source establishes an automatic
selection. Multiple editions, filename variants, or competing same-stem
Markdown and HTML sources require an explicit choice; conversion stops before
replacing an existing output. The inspection includes nearby PDFs so the agent
can inspect legacy files with unrelated names and project output conventions.
Filename similarity alone does not establish correspondence. Files in other
directories need an explicit mapping when their correspondence is not known;
inspection never recursively scans a home directory.

For a persistent choice, put `.pdf-links.json` in the referenced source's
directory or an ancestor within its repository. Paths are relative to that
manifest, for example:

```json
{
  "guide.md": "exports/guide-print.pdf",
  "README.md": null
}
```

Per-call choices override the manifest. New PDFs record their source path
relative to their output directory in PDF keywords, allowing differently named
outputs beside a source to be recognized later. No sidecar is written
automatically. Static Markdown links, reference links, autolinks, and authored
HTML `a`/`area` links are inspected; script-generated HTML links need an authored
equivalent or explicit review. A source fragment only locates a PDF chapter if
the target PDF contains a matching destination.

Both CLIs accept `--inspect-links`, `--pdf-targets choices.json`, and
`--link-policy prefer-pdf|preserve`. MCP conversion results include `link_report`.
Restart an existing MCP server/client session to discover the new preflight
tool and updated argument schemas.

---

## Module API

Core conversion logic lives in `converter.py`, importable by MCP server, CLI scripts, or external code:

```python
from converter import (
    convert_markdown_to_pdf,  # (..., engine="weasyprint" | "chromium", pdf_targets=None, link_policy="prefer-pdf") -> dict
    convert_html_to_pdf,      # (..., engine="chromium", page_numbers=True, pdf_targets=None, link_policy="prefer-pdf") -> dict
    convert_pdf_to_text,      # (source_path: str) -> str
    convert_svg_to_png,       # (..., scale=1.0, output_width=None, output_height=None) -> (width, height)
)
```

The PDF renderers share fontconfig-aware discovery (user fonts first, then
system Noto CJK/Emoji fonts) and the same emoji fallback strategy. SVG
rasterization uses the fonts referenced by the SVG and available through the
host's Cairo/fontconfig stack.

---

## CLI Scripts

| Script | Input | Purpose |
|---|---|---|
| `md2pdf.py` | `.md` | Markdown → PDF (full styling: tables/blockquotes/code blocks) |
| `html2pdf.py` | `.html` | HTML → PDF (selectable theme; adds page numbers, fonts, and recognized-report print polish) |

Both are thin wrappers around converter (`from converter import ...`), keeping
the original CLI usage. Chromium and WeasyPrint share the repository-local
`mcp-local` uv project.

---

## md2pdf.py — Markdown → PDF

### Overview

**Pipeline**: `Markdown` → `markdown-it-py` → `HTML` → Chromium (default via MCP) / WeasyPrint → `PDF`

**Features**:
- A4 paper, 18-20mm margins, 10pt body and table text, 9.5pt inline code, and auto-centered page numbers
- Long table-cell content wraps instead of triggering Chromium whole-page shrink-to-fit
- CJK font (Noto Sans SC/CJK SC) + emoji font (Noto Emoji or Noto Color Emoji)
- Tables with borders/zebra striping/dark blue header with white text
- Blockquotes with warm amber gray background and left bar, code block highlighting, teal-colored headings
- Three complete PDF color themes: `print` (white), `sepia` (warm low-glare), and `one-dark-pro` (One Dark Pro Night Flat-inspired screen reading)
- ⭐→★ gold mapping (does not modify source file), other emoji covered by font
- Pinned local MathJax runtime for offline LaTeX-to-SVG rendering; over-wide
  display equations scale individually instead of shrinking the whole PDF

---

## Shared CPU Runtime Setup (One-Time)

```bash
# Preferred: provision the shared CPU runtime from the repository root
bash install.sh --cpu-only

# Manual equivalent: restore the locked shared Python project
uv sync --project environments/mcp-local --locked
environments/mcp-local/.venv/bin/playwright install chromium

# Install the pinned local MathJax runtime (no lifecycle scripts)
npm ci --prefix format-conversion --ignore-scripts --no-audit --no-fund

# Install CJK and emoji fonts through Ubuntu/fontconfig
sudo apt install fonts-noto-cjk fonts-noto-color-emoji
fc-cache -f
fc-list :lang=zh | grep Noto
fc-list | grep Emoji
```

**System Requirements**:
- `cairosvg` 2.9.0, `defusedxml` 0.7.1, `weasyprint` 68+, `markdown-it-py` 4+, `pymupdf` 1.27+, `playwright` 1.60+
- System must have cairo / pango / gdk-pixbuf installed (Ubuntu includes them by default)
- Chromium backend requires additional system libraries (`libnss3`, `libatk-bridge2.0-0`, `libxkbcommon0`, etc.; `playwright install --with-deps chromium` handles this automatically)
- Node.js + npm for the repository-local, lockfile-pinned MathJax v4 runtime
- CJK font: system `fonts-noto-cjk`, or a user-installed Noto Sans SC font discoverable by fontconfig

**Behavior When Fonts Are Missing**:
- Noto Sans SC/CJK missing → falls back to system sans-serif (DejaVu Sans), CJK characters may appear as tofu boxes
- Noto Emoji missing → emoji auto-replaced with text labels (e.g., 📅→[Calendar], ⭐→★), PDF is readable but contains no emoji
- Missing font warnings are printed at startup, no error exit

---

## Usage

```bash
# Basic usage (output PDF with same name and directory as .md)
uv run --project environments/mcp-local python format-conversion/md2pdf.py \
  "notebooks/health-daily/bedtime-reading-list.md"

# Specify output path
uv run --project environments/mcp-local python format-conversion/md2pdf.py input.md output.pdf

# Warm, low-glare PDF for screen reading
uv run --project environments/mcp-local python format-conversion/md2pdf.py \
  input.md output-sepia.pdf --theme sepia

# Dark PDF inspired by VS Code One Dark Pro Night Flat
uv run --project environments/mcp-local python format-conversion/md2pdf.py \
  input.md output-dark.pdf --theme one-dark-pro
```

> **Note**: Run these commands from the repository root. Dependencies live in
> `environments/mcp-local/.venv`, not in system Python.

### Color Themes

For Markdown, the `theme` option applies to the entire PDF, including page
margins, headings, tables, blockquotes, code, and page numbers. Both rendering
engines preserve the selected background color.

| Theme | Background | Intended use |
|---|---|---|
| `print` (default) | White | Printing and general-purpose documents |
| `sepia` | Warm light beige | Lower-glare daytime or evening screen reading |
| `one-dark-pro` | One Dark Pro Night Flat-inspired near-black | Dark-room screen reading |

MCP example:

```python
markdown_to_pdf(
    "/absolute/path/notes.md",
    "/absolute/path/notes-dark.pdf",
    theme="one-dark-pro",
)

html_to_pdf(
    "/absolute/path/report.html",
    "/absolute/path/report-sepia.pdf",
    theme="sepia",
)
```

Dark and sepia backgrounds are embedded in the PDF. Use `theme="print"` before
physical printing to avoid unnecessary ink or toner use.

---

## Format Reference

| Markdown Syntax | PDF Rendering |
|---|---|
| `# Heading 1` | 20pt bold, 2px black bottom border, auto page break |
| `## Heading 2` | 16pt bold, 1px gray bottom border |
| `### / #### / #####` | 13pt / 11.5pt / 11pt decreasing |
| `**Bold**` | Bold font weight |
| `> Blockquote` | Gray background + 3px gray left bar, 10pt font |
| `---` | 1px gray horizontal rule |
| Tables | Borders + gray header background + zebra striping |
| Code blocks | Gray background border, monospace font (DejaVu Sans Mono) |
| `⭐` / emoji | Auto-replaced with `★` / compatible characters |

---

## Verifying Output

After generating a PDF, verify with the following MCP tools:

### 1. OCR Verification (Content Completeness)

Call `ocr_document(<pdf_path>)` → returns artifact metadata with a `.md` file path. Read the Markdown at the artifact path, then check that headings, tables, and paragraphs are all present. The current PaddleOCR-VL backend cold-starts in several seconds on the reference GPU.

### 2. Layout Verification

After generating a PDF, use `ocr_document` to verify content completeness and layout quality.

---

## Known Issues & Solutions

| Issue | Cause | Solution |
|---|---|---|
| Emoji not displayed | No emoji font on system | ① Install `fonts-noto-color-emoji` or a user Noto Emoji font ② ⭐→★ compatibility replacement ③ CSS/fontconfig registration |
| Some emoji not rendered | WeasyPrint has limited color emoji support | Use monochrome Noto Emoji Regular (not Noto Color Emoji), most common emoji render correctly |
| Tables not rendered (shows raw `\|` characters) | `MarkdownIt('commonmark')` lacks table extension | Add `.enable(['table', 'strikethrough'])` |
| Code blocks have no syntax highlighting | markdown-it does not output language class by default | For highlighting, switch to `pandoc` approach |


---

## Alternative Approaches Comparison

| Approach | Pros | Cons |
|---|---|---|
| **Chromium + WeasyPrint** (current) | Chromium pixel-level Chrome compatibility, WeasyPrint as lightweight fallback | Chromium requires Playwright (~300 MB) |
| `pandoc + wkhtmltopdf` | Mature ecosystem, supports more formats | Requires apt install (sudo restricted on this machine) |
| `pandoc + xelatex` | Best typography, academic publishing grade | texlive install 2 GB+, too heavy |
| VS Code Markdown PDF extension | GUI, one-click export | Not scriptable, not batch-capable |

---

## html2pdf.py — HTML → PDF

### Overview

Renders HTML files to PDF while preserving authored component styles. Defaults to the Chromium backend (Playwright), the white `print` theme, and a theme-aware page canvas. WeasyPrint can be selected with `--engine weasyprint` or `engine="weasyprint"` in code.

**Ideal for**: HTML with inline styles (e.g., calendars, weekly planners, cheat sheets, invoices), no markdown parsing needed.

### Usage

```bash
uv run --project environments/mcp-local python format-conversion/html2pdf.py input.html [output.pdf]

# Warm, low-glare background
uv run --project environments/mcp-local python format-conversion/html2pdf.py \
  input.html output-sepia.pdf --theme sepia

# One Dark Pro background and matching portable-report colors
uv run --project environments/mcp-local python format-conversion/html2pdf.py \
  input.html output-dark.pdf --theme one-dark-pro
```

HTML conversion uses the same three theme names. The selection colors the page
canvas, margins, default text, and page numbers while preserving authored
component styles. Recognized portable analytics reports additionally receive
matching card, table, and chart colors, including dark chart variants.

### Engine Selection

| Engine | Pros | Cons | Use Case |
|---|---|---|---|
| `chromium` (default) | flex/grid fully consistent with Chrome | Requires Playwright + Chromium (~300 MB), cold start 1-2s | Complex web layouts, high visual fidelity requirements |
| `weasyprint` | Lightweight (~30 MB), cold start 200ms, full Paged Media support | flex/grid not aligned with Chrome | Simple documents, Paged Media page number needs |

### How It Works

1. Read the HTML file
2. Inject `@font-face` fonts + `@page @bottom-center` page number CSS
3. Default Chromium engine: Playwright launches headless Chrome → `page.pdf()` output
4. Fallback WeasyPrint engine: set `base_url` to HTML directory → WeasyPrint renders

Both engines render to a same-directory temporary PDF, validate that the result
is non-empty, and atomically replace the requested output. A renderer crash or
permission failure therefore does not replace an existing valid PDF with a
partial file.

### Known Limitations

- WeasyPrint's rendering of `display:flex` / `display:grid` does not fully match Chrome Blink (known technical debt, still not aligned in v68.1). Use the Chromium backend (default) for complex layouts.
- Chromium backend does not support CSS Paged Media `@page { @bottom-center { content: counter(page) } }` syntax; page numbers are implemented via injected `@page @bottom-center` CSS (Chrome 131+ supported).
- `<link rel="stylesheet" href="...">` supports relative paths (because `base_url` is set)
- No JavaScript support, static HTML only

### CJK Font Behavior

Both engines pick up CJK fonts from the system (`fc-list :lang=zh`). The Chromium backend uses Chrome's font fallback chain — if Noto Sans SC (or another CJK font) is installed in `~/.local/share/fonts/`, Chinese/Japanese/Korean text renders correctly without any HTML-side declaration. The WeasyPrint backend uses the explicit `@font-face` injection from `converter.py` (same Noto Sans SC). If no CJK font is installed, both engines render CJK as tofu boxes — install Noto Sans SC and run `fc-cache -f`.

---

## svg_to_png — SVG → PNG

Rasterizes a local `.svg` file with CairoSVG 2.9.0. The default output uses the
SVG's intrinsic width and height; a `viewBox` supplies those dimensions when
the root width or height is omitted.

### MCP Usage

```python
# Saves /home/user/diagram.png at the SVG's intrinsic size.
svg_to_png("/home/user/diagram.svg")

# Preserve aspect ratio while setting one dimension.
svg_to_png("/home/user/diagram.svg", output_width=1600)

# Double both intrinsic dimensions.
svg_to_png("/home/user/diagram.svg", scale=2)

# Set an exact canvas and fill otherwise transparent pixels.
svg_to_png(
    "/home/user/diagram.svg",
    "/home/user/diagram-white.png",
    output_width=1200,
    output_height=800,
    background_color="#ffffff",
)
```

Successful calls return:

```json
{
  "status": "success",
  "output_path": "/home/user/diagram.png",
  "size_bytes": 18427,
  "width": 1600,
  "height": 900,
  "external_resources": "blocked"
}
```

### Safety and Reliability Boundaries

- Input is limited to 16 MiB.
- `scale` must be greater than zero and no more than 16; it cannot be combined
  with explicit dimensions.
- Each output side is limited to 8192 pixels and the complete canvas to 32
  million pixels.
- XML entities and external XML resources are rejected. External file, HTTP,
  and HTTPS references in images or styles are blocked. Embedded `data:` URLs
  remain supported.
- Rendering uses a same-directory temporary PNG. The file signature, complete
  Pillow decode, and dimensions are checked before atomic replacement, so a
  malformed SVG or renderer failure preserves any previous destination.

The tool runs entirely on CPU and does not start or overlap any GPU workload.

---

## pdf_to_text — PDF → Plain Text

### Overview

Extracts plain text from a **born-digital PDF** using PyMuPDF (`fitz`). Born-digital means the PDF has an embedded text layer — text you can select and copy in Adobe Reader / Chrome / `pdftotext`. Common producers: LaTeX, Microsoft Word's "Save as PDF", browser "Print to PDF", `markdown_to_pdf` / `html_to_pdf`.

**Scanned PDFs return an empty string.** Scanned PDFs are PDFs where each page is a bitmap image with no text layer (typical of paper documents fed through a scanner). For those, use `ocr_document`, which runs the configured local OCR model on rendered pages.

### MCP Usage

```python
# Default — also writes a .txt next to the source PDF
pdf_to_text("/home/user/paper.pdf")
# → {"text": "Attention is all you need...", "page_count": 15,
#    "size_chars": 39512, "text_path": "/home/user/paper.txt"}

# Return text only, do not save .txt
pdf_to_text("/home/user/paper.pdf", save_text=False)
# → {"text": "...", "page_count": 15, "size_chars": 39512}

# Empty result on scanned PDF — fall back to ocr_document
pdf_to_text("/home/user/scanned.pdf")
# → {"text": "", "page_count": 96, "size_chars": 0}    # nothing extractable
```

### How It Works

```
PDF file
  → fitz.open() (PyMuPDF)
  → for each page: page.get_text()
  → '\n'.join(pages)
  → return string + optionally write .txt alongside
```

### Decision Matrix: when to use what

| Symptom | Tool to call |
|---|---|
| `pdf_to_text` returned non-empty text | ✅ Done — born-digital extraction worked |
| `pdf_to_text` returned `size_chars: 0` | Fall back to `ocr_document(pdf_path)` for VLM-based OCR |
| Need formula recognition (LaTeX) | Use `ocr_document` even on born-digital PDFs (PyMuPDF returns formula text but loses LaTeX structure) |
| Need table structure preservation | Use `ocr_document` — PyMuPDF flattens tables to a single text stream |
| Just need raw text and PDF was generated digitally | `pdf_to_text` (millisecond-level, no GPU) |

### Performance

| Scenario | Time |
|---|---|
| 1-page born-digital | < 50 ms |
| 15-page born-digital (e.g. arXiv paper) | ~150 ms |
| 100-page born-digital | ~1 s |
| Scanned PDF (any size) | ~10 ms (returns `""` immediately) |

PyMuPDF runs on CPU only — no GPU, no model loading, no idle-timeout server. Always available, always fast.

### Auto-Save Behavior

- `save_text=True` (default) and `text.strip() != ""` → write `<source>.txt` alongside the PDF
- `save_text=False` → no file written
- `save_text=True` but extraction returned empty → no file written (avoids zero-byte `.txt` clutter)
- `text_path` in the return dict is only present when a file was actually written
