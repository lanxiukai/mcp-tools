# Format Conversion — 文档格式转换 MCP 服务

提供 3 个文档格式转换工具：Markdown/HTML → PDF + PDF → 纯文本。HTML→PDF 支持双引擎（Chromium / WeasyPrint），PDF→Text 自动保存 `.txt`。

---

## MCP 工具

| 工具 | 输入 | 输出 | 引擎 |
|---|---|---|---|
| `markdown_to_pdf` | `.md` | `.pdf`（A4 排版，中文/表格/代码块/页码） | markdown-it-py + WeasyPrint |
| `html_to_pdf` | `.html` | `.pdf`（保留原样式，flex/grid 与 Chrome 一致） | Chromium（默认）/ WeasyPrint |
| `pdf_to_text` | `.pdf`（born-digital） | 纯文本字符串 + 自动保存 `.txt` | PyMuPDF (fitz) |

> `html_to_pdf` 默认使用 Chromium 后端（Playwright），与 Chrome 打印效果像素一致。对简单文档可用 `engine="weasyprint"` 切换到轻量后端。`pdf_to_text` 默认在同目录保存 `.txt` 文件；`save_text=False` 可关闭。

> `pdf_to_text` 仅处理 born-digital PDF（文字可选中/复制）。扫描件 PDF 请使用 `glm_ocr` 工具。

MCP Server 入口：`format_mcp_server.py`（FastMCP，stdio 协议）。

---

## 模块 API

核心转换逻辑在 `converter.py` 中，可被 MCP server、CLI 脚本或外部代码直接 import：

```python
from converter import (
    convert_markdown_to_pdf,  # (source_path: str, output_path: str) -> None
    convert_html_to_pdf,      # (source_path, output_path, *, engine="chromium", page_numbers=True) -> None
    convert_pdf_to_text,      # (source_path: str) -> str
)
```

所有函数共享同一套字体发现（`~/.local/share/fonts/` → Noto Sans SC / Noto Emoji）和 emoji 降级策略。

---

## CLI 脚本

| 脚本 | 输入 | 用途 |
|---|---|---|
| `md2pdf.py` | `.md` | Markdown → PDF（含表格/引用/代码块全套样式） |
| `html2pdf.py` | `.html` | HTML → PDF（保留原样式，仅追加页码和 emoji 字体） |

两者已重构为 converter 的薄 wrapper（`from converter import ...`），保持原 CLI 用法不变。底层 WeasyPrint 引擎和 conda 环境共用。

---

## md2pdf.py — Markdown → PDF

### 概述

**管线**：`Markdown` → `markdown-it-py` → `HTML` → `WeasyPrint` → `PDF`

**特点**：
- A4 纸张，18-20mm 页边距，页码自动居中
- 中文字体（Noto Sans SC）+ emoji 字体（Noto Emoji Regular）
- 表格带边框/斑马条纹/深蓝表头白字
- 引文暖 amber 灰底竖线、代码块高亮、标题 teal 色系
- ⭐→★ 金色映射（不改源文件），其余 emoji 由字体覆盖
- 单文件脚本，零配置

---

## 环境准备（一次性）

```bash
# 创建专用 conda 环境
mamba create -n format-convert python=3.12 -y
mamba install -n format-convert -c conda-forge weasyprint markdown-it-py pymupdf -y
mamba run -n format-convert pip install "mcp>=1.0.0" playwright
mamba run -n format-convert playwright install chromium

# 安装中文字体（如未安装）
# Noto Sans SC → 放到 ~/.local/share/fonts/，然后 fc-cache -f

# 安装 emoji 字体（如未安装）
# Noto Emoji Regular → 放到 ~/.local/share/fonts/，然后 fc-cache -f
fc-list :lang=zh | grep Noto
fc-list | grep Emoji
```

**系统要求**：
- `weasyprint` 68+、`markdown-it-py` 4+、`pymupdf` 1.27+、`playwright` 1.60+
- 系统需安装 cairo / pango / gdk-pixbuf（Ubuntu 默认已装）
- Chromium 后端需额外系统库（`libnss3`、`libatk-bridge2.0-0`、`libxkbcommon0` 等，`playwright install --with-deps chromium` 可自动处理）
- 中文字体：Noto Sans SC（放到 `~/.local/share/fonts/` 并 `fc-cache -f`）

**字体缺失时的行为**：
- Noto Sans SC 缺失 → 降级为系统 sans-serif（DejaVu Sans），中文可能显示为豆腐块
- Noto Emoji 缺失 → emoji 自动替换为文字标签（如 📅→[日历]、⭐→★），PDF 不含 emoji 但可读
- 启动时打印缺失字体警告，不会报错退出

---

## 使用方法

```bash
# 基本用法（输出 PDF 与 .md 同名同目录）
conda run -n format-convert python md2pdf.py "notebooks/health-daily/睡前书单.md"

# 指定输出路径
conda run -n format-convert python md2pdf.py input.md output.pdf
```

> **注意**：必须通过 conda 环境 `format-convert` 的 Python 运行（`conda run -n format-convert python` 或 `/home/lanxiukai/mambaforge/envs/format-convert/bin/python`），因为 weasyprint 安装在 conda 而非系统 Python。

---

## 格式对照

| Markdown 语法 | PDF 渲染效果 |
|---|---|
| `# 一级标题` | 20pt 加粗，底部 2px 黑线，自动分页 |
| `## 二级标题` | 16pt 加粗，底部 1px 灰线 |
| `### / #### / #####` | 13pt / 11.5pt / 11pt 递减 |
| `**加粗**` | 字体加粗 |
| `> 引用` | 灰底 + 左侧 3px 灰竖线，10pt 字号 |
| `---` | 1px 灰色分隔线 |
| 表格 | 边框 + 表头灰底 + 斑马条纹 |
| 代码块 | 灰底边框，等宽字体（DejaVu Sans Mono） |
| `⭐` / emoji | 自动替换为 `★` / 兼容字符 |

---

## 验证输出

生成 PDF 后，可用以下 MCP 工具验证：

### 1. OCR 验证（内容完整性）
```
调用 glm_ocr_ocr_glm(<pdf路径>)
→ 返回完整 Markdown 文本，核对标题、表格、段落是否齐全
→ 首次调用需 1-2 分钟加载模型
```

### 2. Vision 验证（排版效果）
```bash
# 先转单页 PNG
pdftoppm -png -f 1 -l 1 -r 150 input.pdf /tmp/opencode/preview

# 再调用 vision
调用 qwen_vision_describe_image(/tmp/opencode/preview-1.png)
→ 返回排版描述：字体、表格、引用框、页码
```

> **不要同时开两个 MCP**——OCR 和 Vision server 都占用 GPU 显存，一起跑会超时。

---

## 已知问题与解法

| 问题 | 原因 | 解法 |
|---|---|---|
| emoji 不显示 | 系统无 emoji 字体 | ① 安装 Noto Emoji（`~/.local/share/fonts/`）② ⭐→★ 做兼容替换 ③ CSS `@font-face` 注册字体 |
| emoji 部分不渲染 | WeasyPrint 对彩色 emoji 支持有限 | 使用单色 Noto Emoji Regular（非 Noto Color Emoji），多数常用 emoji 可正常渲染 |
| 表格不渲染（显示原始 `\|` 字符） | `MarkdownIt('commonmark')` 不含 table 扩展 | 加 `.enable(['table', 'strikethrough'])` |
| 代码块无语法高亮 | markdown-it 默认不输出语言 class | 如需高亮，改用 `pandoc` 方案 |
| Vision MCP 报 "Invalid url format" | Vision 不支持 PDF 直接输入 | 先用 `pdftoppm` 转 PNG 再输入 |

---

## 替代方案对比

| 方案 | 优点 | 缺点 |
|---|---|---|---|
| **Chromium + WeasyPrint**（当前） | Chromium 像素级 Chrome 兼容，WeasyPrint 轻量备选 | Chromium 需 Playwright（~300 MB） |
| `pandoc + wkhtmltopdf` | 生态成熟，支持更多格式 | 需 apt install（本机 sudo 受限） |
| `pandoc + xelatex` | 排版最优，学术出版级 | texlive 安装 2GB+，过重 |
| VS Code Markdown PDF 插件 | 图形界面，一键导出 | 不可脚本化，不可批量 |

---

## html2pdf.py — HTML → PDF

### 概述

渲染 HTML 文件为 PDF，**保留原 HTML 的全部样式**（颜色、渐变、卡片、`@page` 指令等）。默认使用 Chromium 后端（Playwright），与 Chrome 打印效果像素一致。WeasyPrint 后端可通过 `--engine weasyprint` 或代码中 `engine="weasyprint"` 切换。

**适合场景**：已带内联样式的 HTML（如日历、周计划表、速查表、发票等），不需要任何 markdown 解析。

### 使用方法

```bash
conda run -n format-convert python html2pdf.py input.html [output.pdf]
```

### 引擎选择

| 引擎 | 优点 | 缺点 | 适用场景 |
|---|---|---|---|
| `chromium`（默认） | flex/grid 与 Chrome 完全一致 | 需 Playwright + Chromium（~300 MB），冷启动 1-2s | 复杂网页布局、视觉一致性要求高 |
| `weasyprint` | 轻量（~30 MB），冷启动 200ms，Paged Media 完整 | flex/grid 与 Chrome 不对齐 | 简单文档、Paged Media 页码需求 |

### 工作原理

1. 读取 HTML 文件
2. 注入 `@font-face` 字体 + `@page @bottom-center` 页码 CSS
3. 默认 Chromium 引擎：Playwright 启动 headless Chrome → `page.pdf()` 输出
4. 备选 WeasyPrint 引擎：设 `base_url` 为 HTML 所在目录 → WeasyPrint 渲染

### 已知限制

- WeasyPrint 对 `display:flex` / `display:grid` 的渲染与 Chrome Blink 不完全一致（已知工程债务，v68.1 仍未对齐）。复杂布局应使用 Chromium 后端（默认）。
- Chromium 后端不支持 CSS Paged Media 的 `@page { @bottom-center { content: counter(page) } }` 语法，页码通过注入的 `@page @bottom-center` CSS 实现（Chrome 131+ 支持）。
- `<link rel="stylesheet" href="...">` 支持相对路径（因 `base_url` 已设置）
- 不支持 JavaScript，纯静态 HTML
