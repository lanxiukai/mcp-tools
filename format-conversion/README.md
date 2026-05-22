# Markdown / HTML → PDF 转换工具

## 脚本

| 脚本 | 输入 | 用途 |
|---|---|---|
| `md2pdf.py` | `.md` | Markdown → PDF（含表格/引用/代码块全套样式） |
| `html2pdf.py` | `.html` | HTML → PDF（保留原样式，仅追加页码和 emoji 字体） |

两者共用 WeasyPrint 引擎和 conda 环境。

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
# 安装依赖（conda 环境，已在本机 base 完成）
conda install -c conda-forge weasyprint markdown-it-py

# 安装中文字体（如未安装）
# Noto Sans SC → 放到 ~/.local/share/fonts/，然后 fc-cache -f

# 安装 emoji 字体（如未安装）
# Noto Emoji Regular → 放到 ~/.local/share/fonts/，然后 fc-cache -f
fc-list :lang=zh | grep Noto
fc-list | grep Emoji
```

**系统要求**：
- `weasyprint` 67+、`markdown-it-py` 3+
- 系统需安装 cairo / pango / gdk-pixbuf（Ubuntu 默认已装）
- 中文字体：Noto Sans SC（放到 `~/.local/share/fonts/` 并 `fc-cache -f`）
- Emoji 字体：Noto Emoji Regular（同上）

**字体缺失时的行为**：
- Noto Sans SC 缺失 → 降级为系统 sans-serif（DejaVu Sans），中文可能显示为豆腐块
- Noto Emoji 缺失 → emoji 自动替换为文字标签（如 📅→[日历]、⭐→★），PDF 不含 emoji 但可读
- 启动时打印缺失字体警告，不会报错退出

---

## 使用方法

```bash
# 基本用法（输出 PDF 与 .md 同名同目录）
conda run -n base python scripts/md2pdf.py "notebooks/health-daily/睡前书单.md"

# 指定输出路径
conda run -n base python scripts/md2pdf.py input.md output.pdf
```

> **注意**：必须通过 conda 环境的 Python 运行（`conda run -n base python` 或 `/home/lanxiukai/mambaforge/bin/python`），因为 weasyprint 安装在 conda 而非系统 Python。

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
|---|---|---|
| **weasyprint**（当前） | Python 原生，CSS 控制精确，conda 安装 | 需系统 cairo/pango 库 |
| `pandoc + wkhtmltopdf` | 生态成熟，支持更多格式 | 需 apt install（本机 sudo 受限） |
| `pandoc + xelatex` | 排版最优，学术出版级 | texlive 安装 2GB+，过重 |
| VS Code Markdown PDF 插件 | 图形界面，一键导出 | 不可脚本化，不可批量 |

---

## html2pdf.py — HTML → PDF

### 概述

直接渲染 HTML 文件为 PDF，**保留原 HTML 的全部样式**（颜色、渐变、卡片、`@page` 指令等），仅追加：
- 页码（8pt 灰色，页面底部居中）
- emoji 字体（Noto Emoji Regular）

**适合场景**：已带内联样式的 HTML（如日历、周计划表、速查表、发票等），不需要任何 markdown 解析。

### 使用方法

```bash
conda run -n base python scripts/html2pdf.py input.html [output.pdf]
```

### 工作原理

1. 读取 HTML 文件
2. 在 `</head>` 前注入页码 CSS + emoji 字体 `@font-face`
3. 设置 `base_url` 为 HTML 所在目录（确保相对路径的 CSS/images 可解析）
4. WeasyPrint 渲染输出

### 已知限制

- 原 HTML 的 `@page` 指令保留，如果 `margin-bottom` 太紧（如 11mm），footer 可能被挤到单独一页。解决方法：在源 HTML 中把 `margin-bottom` 调到 14mm+
- `<link rel="stylesheet" href="...">` 支持相对路径（因为 `base_url` 已设置）
- 不支持 JavaScript，纯静态 HTML
