# 01 — 从需求到 PLAN 的设计思路

> 适合读者：想知道"这个项目为什么这么设计"的人。本文带你走过 planner 在动笔之前脑中走过的推理链。

## 1. 需求回顾：我们要做什么

用户想要一个 MCP 服务（MCP = Model Context Protocol，一种让 AI agent 调用本地工具的协议），提供 3 个文档格式转换工具：

- **markdown_to_pdf**：把 `.md` 文件转成排好版的 PDF
- **html_to_pdf**：把 `.html` 文件转成 PDF，保留它本来的样式
- **pdf_to_text**：从 PDF 里提取纯文本（只限"出生就是数字版"的 PDF，不能是扫描件）

阅读全文（代码 + PLAN）之后你会发现：**这个项目最值得学习的不是代码怎么写，而是"哪些东西不做"比"做了多少"更重要**。整份代码不到 400 行，但每个技术决策背后都有一条清晰的推理链。

---

## 2. 推理链：每个决策的三步法

以下逐一拆解 planner 在做技术选型时的思考过程。

---

### 2.1 架构形态：要不要跟 ASR/OCR 一样用 HTTP 后端？

**原本想怎么做：**

仓库里已有的 ASR（语音识别）和 OCR（文字识别）服务都是"agent → MCP → HTTP → 后端 GPU 服务"的架构——`format_mcp_server.py` 接到请求后，通过 HTTP 调用一个长期运行的 FastAPI 服务。新手看到这个模式，自然想：format-conversion 也这样搞吧，统一架构嘛。

**如果那样会出什么问题：**

ASR/OCR 用 HTTP 后端是因为它们需要 GPU 推理（加载模型可能要几十秒），所以后台必须有个常驻进程。但 format-conversion 是纯 CPU 操作——WeasyPrint 渲染、PyMuPDF 提取都是毫秒到秒级完成的。引入 HTTP 后端意味着：

- 要多维护一个后台进程（systemd unit 或 `_start.sh` 启动脚本）
- 需要 health check 和自动唤醒逻辑
- MCP server 要处理 HTTP 连接失败/超时/重试
- 对调试和部署都增加一层复杂度

而这些复杂度的"收益"是零——因为没有 GPU 需要预热，没有模型需要常驻内存。

**所以最后怎么做的：**

```text
OpenCode Agent
     │ MCP stdio (FastMCP)
     ▼
format_mcp_server.py          ← 3 个 @mcp.tool()，薄到 transparent
     │ import
     ▼
converter.py                  ← 核心转换，纯函数，同步调用
     │
     ▼
markdown-it-py / WeasyPrint / PyMuPDF
```

工具函数**直接 import 并调用**，没有网络层、没有后台服务、没有 health check。`mcp.run(transport="stdio")` 就是唯一的入口——数据走标准输入输出，不需要开端口，不需要 HTTP 请求。这一点和仓库其他 MCP 服务的最大区别，恰恰是初学者最需要理解的：**不要因为别人用某种架构，就盲目照搬**。

> 引用：`PLAN.md` 第 4 节："无后端 server：与 ASR/OCR 的'MCP → HTTP → FastAPI'架构不同，format-conversion 是纯 CPU 同步操作，工具函数直接 import 本地模块。"

---

### 2.2 Markdown → HTML：为什么选 markdown-it-py 而不是 pandoc？

**原本想怎么做：**

提到 Markdown 转 HTML，很多人的第一反应是 `pandoc`——"万能文档转换器"，支持几十种格式互转。直觉：用 pandoc 一步到位，万一以后要 Markdown→DOCX 也能用同一个工具。

**如果那样会出什么问题：**

`pandoc` 是一个外部命令行工具，不是 Python 库。要从 Python 调用它，只能用 `subprocess.run(["pandoc", ...])`。这会带来几个实际问题：

1. **系统依赖**：pandoc 需要 `apt install pandoc` 或者下载二进制，而这台机器上 `sudo` 受限（`PLAN.md` 第 0 节：本机 sudo 受限）
2. **错误处理复杂**：子进程的 stdout/stderr 要手动解析，退出码要检查，子进程可能 core dump
3. **mypy 不可达**：subprocess 调用是字符串拼接，类型系统管不了
4. **conda 管理困难**：pandoc 是系统级包，不归 conda 管，环境复现时需要手动装

**所以最后怎么做的：**

选 `markdown-it-py`，纯 Python 包，`pip install` / `conda install` 即可。用法极其简单：

```python
from markdown_it import MarkdownIt
md = MarkdownIt('commonmark', {'breaks': True, 'html': True})
md.enable(['table', 'strikethrough'])
body = md.render(text)
```

三行代码，类型安全，无外部进程，无系统依赖。代价是 markdown-it-py 只做 Markdown→HTML，不做别的格式——但恰好本项目只需要这一个方向，所以"功能少"反而是优势（依赖链最短）。

> 引用：`converter.py:335-337`，实际调用代码。

---

### 2.3 HTML → PDF 引擎：为什么选 WeasyPrint 而不是 wkhtmltopdf？

**原本想怎么做：**

HTML 转 PDF 最常见的工具是 `wkhtmltopdf`——基于 WebKit 引擎，渲染效果和浏览器一致。名字里就带着 "html to pdf"，直觉首选。

**如果那样会出什么问题：**

`wkhtmltopdf` 同样是一个系统级工具：

- 需要 `sudo apt install wkhtmltopdf`——本机 sudo 受限
- 通过 `subprocess` 调用，和 pandoc 一样的错误处理困境
- 而且 wkhtmltopdf 基于一个非常老版本的 WebKit，对现代 CSS（Flexbox、Grid、`@page`）支持很差
- 这个项目要渲染中文 + emoji，wkhtmltopdf 的老渲染引擎可能会出奇怪的排版问题

另外有一个方案是 `pandoc + xelatex`——先用 pandoc 转 LaTeX，再用 xelatex 编译 PDF。排版质量最高（学术出版级），但 texlive 全套安装要 2GB+，严重过重。

**所以最后怎么做的：**

选 WeasyPrint——纯 Python，conda 可安装，CSS 支持现代标准。最关键的是：

- 现有 `md2pdf.py` / `html2pdf.py` 已经在用 WeasyPrint，效果已验证
- 对中文排版支持好（只要字体给对）
- `@page` 指令、`counter(page)` 页码等功能原生支持

```python
from weasyprint import HTML
HTML(string=full_html).write_pdf(str(output_path))
```

两行代码生成 PDF。WeasyPrint 唯一的"缺点"是底层依赖系统库 cairo/pango——但 Ubuntu 24.04 默认已装，所以实际上不算问题。

> 引用：`PLAN.md` 第 3 节技术选型表："WeasyPrint——现有脚本已验证；CSS 控制精确、纯 Python"。备选栏写的是："`wkhtmltopdf`（需 apt，本机 sudo 受限）、`pandoc + xelatex`（texlive 2GB+ 过重）"。

---

### 2.4 PDF → 文本：为什么选 PyMuPDF 而不是 pdfplumber？

**原本想怎么做：**

说到 Python 处理 PDF，很多人会推荐 `pdfplumber`——因为它既能提取文本，还能提取表格，功能丰富。

**如果那样会出什么问题：**

`pdfplumber` 的问题是对"born-digital PDF"（即原生数字 PDF，非扫描件）的文本提取**不够快**。它内部做了很多额外工作（表格检测、图形分析），这些在提取纯文本时不需要。

而且本项目的需求很明确：只提取纯文本，不做表格解析，**并且只处理 born-digital PDF**（扫描件由另一个工具 `glm_ocr` 负责）。用 pdfplumber 等于开了一辆越野车去超市——功能过剩，油耗还高。

**所以最后怎么做的：**

选 `PyMuPDF`（import 时叫 `fitz`），业界标准的 PDF 处理库，纯 Python binding，C 底层实现，提取速度极快。

```python
import fitz
doc = fitz.open(source_path)
try:
    pages_text = [page.get_text() for page in doc]
finally:
    doc.close()
result = '\n'.join(pages_text)
```

注意这个函数**返回字符串而不是写入文件**——这是因为 PDF→text 的输出是字符串而非文件，与其他两个函数的签名风格不同。PLAN 明确说明了"这种不一致是合理的"。

> 引用：`converter.py:405-437`，完整的 `convert_pdf_to_text` 实现。

---

### 2.5 项目结构：为什么用单模块而不是包？

**原本想怎么做：**

三个转换函数，加上字体发现、CSS 构建、emoji 处理——功能挺多，新手可能会想：建一个 `converter/` 包吧，里面拆成 `fonts.py`、`css.py`、`emoji.py`、`api.py`，整洁！

**如果那样会出什么问题：**

过度拆分在小项目中是真正的反模式。这个项目的核心代码不到 400 行：

- 字体发现函数 `_check_fonts()` 只有 15 行
- CSS 构建两个函数加起来不到 150 行
- emoji 处理两个辅助函数只有 30 行
- 三个公开函数加起来约 130 行

拆成 4 个文件意味着：每个文件不到 100 行，import 链变长，开发者要在 4 个文件之间跳转。**在代码量足够小时，单文件比多文件更容易理解**。

**所以最后怎么做的：**

全部放在 `converter.py` 一个模块里，用空文件 `__init__.py` 让目录成为 Python 包（这样 `from converter import ...` 即可）。内部函数用 `_` 前缀标记为私有（Python 约定：`_xxx` 表示"模块内部使用，外部不应直接调用"）。

```
format-conversion/
├── __init__.py           ← 空文件，使目录成为 Python 包
├── converter.py          ← 核心：三个公开函数 + 内部辅助
├── format_mcp_server.py  ← MCP 入口，import converter
├── md2pdf.py             ← CLI 薄 wrapper，import converter
└── html2pdf.py           ← CLI 薄 wrapper，import converter
```

> 引用：`PLAN.md` 第 3 节："单模块 `converter.py` + MCP entry + 原 CLI 脚本——代码量小（<400 LOC），无需过度拆分"。备选栏："`converter/` 包（过度设计，3 个函数不值得建目录）"。

---

### 2.6 CLI 脚本：重构还是重写？

**原本想怎么做：**

项目目录里已经有 `md2pdf.py` 和 `html2pdf.py` 两个独立的 CLI 脚本，各自包含完整的转换逻辑（字体发现、CSS、emoji 处理、WeasyPrint 调用）。按一般想法，新写一个 `converter.py`，然后重写这两个 CLI 脚本就行。

**如果那样会出什么问题：**

完全重写意味着：

1. **破坏现有的用法**：如果有人已经在用 `python md2pdf.py input.md output.pdf`，重写后可能就不兼容了
2. **代码重复**：如果重写时不从 `converter.py` import，就会有两份转换逻辑，改一个忘了另一个
3. **测试成本**：重写后的 CLI 需要重新验证全部场景

实际上这两个脚本的逻辑已经经过验证（在仓库其他项目中用了一段时间），最好的做法是"原地重构"——保留 CLI 用法不变，把核心逻辑移到 `converter.py`，让 CLI 脚本变成薄到不能再薄的 wrapper。

**所以最后怎么做的：**

`md2pdf.py` 从 ~100 行缩减到 47 行——只做三件事：

```python
# md2pdf.py 现在的全部逻辑
from converter import convert_markdown_to_pdf

def main() -> None:
    # 1. 解析 sys.argv
    # 2. 调用 convert_markdown_to_pdf()
    # 3. 错误处理
```

原来的字体发现、CSS 构建、emoji 处理等全部在 `converter.py` 里。CLI 脚本不再"自己做转换"，而是"把参数传给 converter，让 converter 做"。

同理 `html2pdf.py` 从 ~100 行缩减到 50 行。

用户接口完全不变：`python md2pdf.py <input.md> [output.pdf]` 仍然工作，输出效果也一样。

---

### 2.7 Emoji 处理策略：为什么 md→pdf 和 html→pdf 不一样？

**原本想怎么做：**

看到两个函数都要处理 emoji，直觉是"统一一个函数，md 和 html 都调同一个处理逻辑"。

**如果那样会出什么问题：**

实际上，md→pdf 和 html→pdf 的 emoji 处理有着本质差异：

| 维度 | md→pdf | html→pdf |
|------|--------|----------|
| 处理时机 | markdown 解析**之前** | 直接替换 HTML 文本 |
| 为什么要在解析前做？ | 因为 emoji（如 ⭐）如果留在 markdown 文本里，会被 markdown-it 转义成 HTML 实体，CSS 样式就套不上了 | HTML 没有"解析"环节，直接替换字符串即可 |
| star 特殊处理 | 有——⭐→★，然后给 ★ 加金色 CSS | 没有——star 和其他 emoji 统一处理 |

如果统一成一个函数，要么 md 路径的逻辑污染 html 路径，要么反之。

**所以最后怎么做的：**

两个独立函数，各有针对性：

- `_process_body()`（converter.py:265-277）：专为 md→pdf 设计，**在 markdown 解析之后**处理 HTML body。先给 ★ 上金色，再给其他 emoji 包 `.emoji` span
- `_process_emoji()`（converter.py:280-291）：专为 html→pdf 设计，**直接处理原始 HTML 文本**。有 emoji 字体就包 span，没有就替换为文字标签

注意 md→pdf 还有一个关键细节：emoji→文字映射的替换**发生在 markdown 解析之前**（converter.py:331-332）：

```python
# Emoji→text fallback applied early (before markdown parsing)
# to avoid emoji being escaped by markdown-it.
for emoji_char, replacement in _EMOJI_TEXT_MAP.items():
    text = text.replace(emoji_char, replacement)
```

意思是：如果 Noto Emoji 字体缺失，我们不是等到渲染时由 WeasyPrint 来处理缺失字体，而是**提前把 emoji 替换成文字标签**（如 📅→[日历]）。这样做的好处是 markdown-it 不会尝试转义这些替换后的文字，坏处是**有 emoji 字体时也会先做替换**（然后又被 `_process_body` 用 `.emoji` span 包回来），多了一步无意义操作。但这样的代码路径最简单，读者可以自行优化（加个条件判断跳过替换）。

---

## 3. 业务边界：少做反而更好

学习这个项目最重要的收获之一，是理解 **"明确不做什么"和"做什么"同等重要**。

PLAN.md 第 2 节列出了 6 个"不"：

| 不做 | 原因 |
|------|------|
| 不做扫描件 OCR | 那是 `glm_ocr` 的职责，各工具各管一摊 |
| 不做 GPU 后端 / health check / 自动唤醒 | 纯 CPU 同步，不需要 |
| 不为 MCP server 做 CLI 入口 | `mcp.run(transport="stdio")` 就是协议入口 |
| 不支持扫描件 PDF | `pdf_to_text` 标注了 "born-digital only" |
| 不做批量转换 / 目录递归 / watch 模式 | MCP 工具的粒度是单文件，批量是 agent 的逻辑 |
| 不修改外层文件 | `format-conversion/` 外的文件不碰（除非 PLAN 明确要求） |

边界画得清晰，代码就不会膨胀。每个新人看到需求都会想"再加个批量功能吧""顺便支持 DOCX 吧"——但代码量和维护成本会指数增长。**说"不"是一种设计能力，不是无能。**

## 4. 动手任务

1. **试着重写一个统一 emoji 处理函数**：把 `_process_body` 和 `_process_emoji` 合并成一个，看看能不能让 md→pdf 和 html→pdf 共用。合并后代码是变简单了还是变复杂了？
2. **看看如果你来加 DOCX 支持**：需要加哪些依赖？需要改哪些文件？在 PLAN.md 第 2 节的"不做"列表里加一行"不做 DOCX 支持"，理由是什么？
3. **验证字体缺失降级**：在你的机器上临时重命名 Noto Emoji 字体文件，然后转一个带 emoji 的 Markdown 文件，看输出 PDF 中 emoji 是否被文字标签替换。
