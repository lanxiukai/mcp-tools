# Format Conversion — Document Format Conversion MCP Service

Provides 3 document format conversion tools: Markdown/HTML → PDF + PDF → plain text. HTML→PDF supports dual engines (Chromium / WeasyPrint), PDF→Text auto-saves `.txt`.

---

## MCP Tools

| Tool | Input | Output | Engine |
|---|---|---|---|
| `markdown_to_pdf` | `.md` | `.pdf` (A4 layout, selectable print/sepia/One Dark Pro theme, CJK/tables/code blocks/MathJax SVG math) | markdown-it-py + Chromium (default) / WeasyPrint |
| `html_to_pdf` | `.html` | `.pdf` (preserves original styles, flex/grid matches Chrome) | Chromium (default) / WeasyPrint |
| `pdf_to_text` | `.pdf` (born-digital) | Plain text string + auto-saved `.txt` | PyMuPDF (fitz) |

> `markdown_to_pdf` defaults to `engine="chromium"` and `theme="print"` in the MCP tool. MathJax SVG preprocessing works with both engines; Chromium is recommended for math-heavy documents because its SVG/CSS rendering matches Chrome. The underlying `converter.py` function defaults to `engine="weasyprint"`; the MCP server overrides to Chromium.
>
> `html_to_pdf` defaults to the Chromium backend (Playwright), producing pixel-identical output to Chrome Print. For simple documents, use `engine="weasyprint"` to switch to the lightweight backend. `pdf_to_text` auto-saves a `.txt` file in the same directory by default; set `save_text=False` to disable.

> `pdf_to_text` only handles born-digital PDFs (text selectable/copyable). For scanned PDFs, use `ocr_document`.

MCP Server entry point: `format_mcp_server.py` (FastMCP, stdio protocol).

---

## Module API

Core conversion logic lives in `converter.py`, importable by MCP server, CLI scripts, or external code:

```python
from converter import (
    convert_markdown_to_pdf,  # (..., engine="weasyprint" | "chromium", theme="print" | "sepia" | "one-dark-pro") -> None
    convert_html_to_pdf,      # (source_path, output_path, *, engine="chromium", page_numbers=True) -> None
    convert_pdf_to_text,      # (source_path: str) -> str
)
```

All functions share fontconfig-aware discovery (user fonts first, then system Noto CJK/Emoji fonts) and the same emoji fallback strategy.

---

## CLI Scripts

| Script | Input | Purpose |
|---|---|---|
| `md2pdf.py` | `.md` | Markdown → PDF (full styling: tables/blockquotes/code blocks) |
| `html2pdf.py` | `.html` | HTML → PDF (preserves original styles, only adds page numbers and emoji fonts) |

Both have been refactored as thin wrappers around converter (`from converter import ...`), keeping original CLI usage unchanged. The underlying WeasyPrint engine and conda environment are shared.

---

## md2pdf.py — Markdown → PDF

### Overview

**Pipeline**: `Markdown` → `markdown-it-py` → `HTML` → Chromium (default via MCP) / WeasyPrint → `PDF`

**Features**:
- A4 paper, 18-20mm margins, auto-centered page numbers
- CJK font (Noto Sans SC/CJK SC) + emoji font (Noto Emoji or Noto Color Emoji)
- Tables with borders/zebra striping/dark blue header with white text
- Blockquotes with warm amber gray background and left bar, code block highlighting, teal-colored headings
- Three complete PDF color themes: `print` (white), `sepia` (warm low-glare), and `one-dark-pro` (dark screen reading)
- ⭐→★ gold mapping (does not modify source file), other emoji covered by font
- Pinned local MathJax runtime for offline LaTeX-to-SVG rendering

---

## Shared CPU Runtime Setup (One-Time)

```bash
# Preferred: provision the shared CPU runtime from the repository root
bash install.sh --cpu-only

# Manual equivalent: mcp-local is shared with Browser Fetch and Qwen Vision
mamba create -n mcp-local python=3.12 -y
mamba install -n mcp-local -c conda-forge weasyprint markdown-it-py pymupdf -y
mamba run -n mcp-local pip install \
    "mcp>=1.0.0" nodriver playwright trafilatura markdownify
mamba run -n mcp-local playwright install chromium

# Install the pinned local MathJax runtime (no lifecycle scripts)
npm ci --prefix format-conversion --ignore-scripts --no-audit --no-fund

# Install CJK and emoji fonts through Ubuntu/fontconfig
sudo apt install fonts-noto-cjk fonts-noto-color-emoji
fc-cache -f
fc-list :lang=zh | grep Noto
fc-list | grep Emoji
```

**System Requirements**:
- `weasyprint` 68+, `markdown-it-py` 4+, `pymupdf` 1.27+, `playwright` 1.60+
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
conda run -n mcp-local python md2pdf.py "notebooks/health-daily/bedtime-reading-list.md"

# Specify output path
conda run -n mcp-local python md2pdf.py input.md output.pdf

# Warm, low-glare PDF for screen reading
conda run -n mcp-local python md2pdf.py input.md output-sepia.pdf --theme sepia

# Dark PDF inspired by VS Code One Dark Pro
conda run -n mcp-local python md2pdf.py input.md output-dark.pdf --theme one-dark-pro
```

> **Note**: Must run via the `mcp-local` conda environment's Python (`conda run -n mcp-local python` or `$(conda info --base)/envs/mcp-local/bin/python`), since WeasyPrint is installed there, not in system Python.

### Color Themes

The `theme` option applies to the entire PDF, including page margins, headings,
tables, blockquotes, inline code, fenced code blocks, and page numbers. Both
rendering engines preserve the selected background color.

| Theme | Background | Intended use |
|---|---|---|
| `print` (default) | White | Printing and general-purpose documents |
| `sepia` | Warm light beige | Lower-glare daytime or evening screen reading |
| `one-dark-pro` | One Dark Pro-inspired charcoal | Dark-room screen reading |

MCP example:

```python
markdown_to_pdf(
    "/absolute/path/notes.md",
    "/absolute/path/notes-dark.pdf",
    theme="one-dark-pro",
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

Renders HTML files to PDF, **preserving all original HTML styles** (colors, gradients, cards, `@page` directives, etc.). Defaults to Chromium backend (Playwright), producing pixel-identical output to Chrome Print. WeasyPrint backend can be switched via `--engine weasyprint` or `engine="weasyprint"` in code.

**Ideal for**: HTML with inline styles (e.g., calendars, weekly planners, cheat sheets, invoices), no markdown parsing needed.

### Usage

```bash
conda run -n mcp-local python html2pdf.py input.html [output.pdf]
```

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

### Known Limitations

- WeasyPrint's rendering of `display:flex` / `display:grid` does not fully match Chrome Blink (known technical debt, still not aligned in v68.1). Use the Chromium backend (default) for complex layouts.
- Chromium backend does not support CSS Paged Media `@page { @bottom-center { content: counter(page) } }` syntax; page numbers are implemented via injected `@page @bottom-center` CSS (Chrome 131+ supported).
- `<link rel="stylesheet" href="...">` supports relative paths (because `base_url` is set)
- No JavaScript support, static HTML only

### CJK Font Behavior

Both engines pick up CJK fonts from the system (`fc-list :lang=zh`). The Chromium backend uses Chrome's font fallback chain — if Noto Sans SC (or another CJK font) is installed in `~/.local/share/fonts/`, Chinese/Japanese/Korean text renders correctly without any HTML-side declaration. The WeasyPrint backend uses the explicit `@font-face` injection from `converter.py` (same Noto Sans SC). If no CJK font is installed, both engines render CJK as tofu boxes — install Noto Sans SC and run `fc-cache -f`.

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
