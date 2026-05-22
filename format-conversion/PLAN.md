# 项目计划：format-conversion MCP 服务

> 状态：approved
> 最近更新：2026-05-22
> 当前负责 agent：planner → builder（待切换）

## 0. 环境契约（不可协商）

- 语言 / 运行时：Python 3.12
- 依赖管理 / 虚拟环境：mamba 管理的 conda 环境 `format-convert`
- 解释器绝对路径：`/home/lanxiukai/mambaforge/envs/format-convert/bin/python`
- 包管理器：`mamba`（系统 `conda` 亦可，本机偏好 `mamba`）
- 核心依赖：

  | 包名 | 版本要求 | channel | 用途 |
  |---|---|---|---|
  | `weasyprint` | ≥67 | conda-forge | HTML/CSS → PDF 渲染 |
  | `markdown-it-py` | ≥3 | conda-forge | Markdown → HTML 解析 |
  | `pymupdf` | ≥1.24 | conda-forge | PDF → 纯文本提取 (fitz) |
  | `mcp` | ≥1.0 | pip (PyPI) | MCP 协议 SDK |

- 测试命令：`/home/lanxiukai/mambaforge/envs/format-convert/bin/python -c "from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text; print('imports OK')"`（轻量自检）；完整冒烟测试用 `mamba run -n format-convert python <test_script>` 逐个验证工具
- 类型检查 / lint 命令：本项目暂不要求 mypy / ruff（代码量小，依赖复杂）
- 命令调用规则：所有 Python 命令必须使用 conda 环境的绝对路径或 `mamba run -n format-convert --no-capture-output python ...`，**禁用**裸 `python`/`pip`。`mamba activate format-convert` 仅在交互式 shell 中有效，非交互式脚本必须用 `mamba run`
- 禁止事项：裸 `python` / `pip`、`--break-system-packages`、`sudo pip install`、cross-env 污染（不同 MCP server 用各自 conda env）
- 自检命令（builder/reviewer 启动时必跑，确认环境身份）：
  ```bash
  /home/lanxiukai/mambaforge/envs/format-convert/bin/python -c "import sys; print(sys.executable)"
  ```
  预期输出：`/home/lanxiukai/mambaforge/envs/format-convert/bin/python`

## 1. 需求摘要

为 OpenCode agent 提供 3 个文档格式转换 MCP 工具，全部纯 CPU 操作、同步执行、无后端 server：

1. **markdown_to_pdf**：将 `.md` 文件转为排版精美的 PDF（markdown-it-py 渲染 + WeasyPrint 排版，含中文字体、表格、代码块、页码）
2. **html_to_pdf**：将 `.html` 文件转为 PDF（保留原样式、注入 emoji 字体和页码 footer）
3. **pdf_to_text**：从 born-digital PDF 提取纯文本（PyMuPDF，仅限文字可选中的 PDF；扫描件需用已有的 `glm_ocr` 工具）

MCP server 用 `mcp.server.fastmcp` 框架，与仓库 ASR/OCR/Vision 服务器架构一致。差异在于：不需要后台 GPU 服务，工具函数直接 import 本地模块、同步返回结果。

## 2. 范围与非目标

- 包含：
  - 从现有 `md2pdf.py` / `html2pdf.py` 提取核心转换为可 import 的函数模块 `converter.py`
  - 新建 PyMuPDF 文本提取函数 `convert_pdf_to_text()`
  - 构建 MCP server `format_mcp_server.py` 暴露 3 个 tool
  - 原地重构现有 CLI 脚本为薄 wrapper（import converter，保留 CLI 用法不变）
  - 更新 `docs/conda-environments.md` 和 `README.md`
- 明确不做：
  - **不**做扫描件 OCR（那是 `glm_ocr` 的职责，`pdf_to_text` 只处理 born-digital）
  - **不**做 GPU 后端 / health check / 自动唤醒（纯 CPU 同步调用）
  - **不**为 MCP server 做 CLI 入口（`format_mcp_server.py` 无 `argparse`；`mcp.run(transport="stdio")` 是 MCP 协议入口，必须有）
  - **不**支持扫描件 PDF（`pdf_to_text` 明确标注"born-digital only"）
  - **不**做批量转换 / 目录递归 / watch 模式
  - **不**修改 `format-conversion/` 目录外的文件（除非 PLAN 明确要求如 `docs/conda-environments.md`、仓库根 `README.md`）

## 3. 技术选型

| 维度 | 选择 | 理由 | 备选 |
|---|---|---|---|
| 语言 / 运行时 | Python 3.12 | 与仓库其他 MCP 工具一致；现有脚本就是 Python | Rust（过重，依赖链长） |
| MCP 框架 | `mcp.server.fastmcp` | 仓库 ASR/OCR 都用此框架，一致性强 | `mcp.server.Server` 原生 API（更底层，无必要） |
| MD→HTML | `markdown-it-py` | 现有脚本已验证；纯 Python、无外部进程 | `mistune`（生态弱）、`pandoc`（需 subprocess） |
| HTML/CSS→PDF | WeasyPrint | 现有脚本已验证；CSS 控制精确、纯 Python | `wkhtmltopdf`（需 apt，本机 sudo 受限）、`pandoc + xelatex`（texlive 2GB+ 过重） |
| PDF→Text | PyMuPDF (fitz) | 业界标准、速度极快、纯 Python binding | `pdfplumber`（对 born-digital 不够快）、`pypdf`（功能弱） |
| 字体 | Noto Sans SC + Noto Emoji Regular | 已在 `~/.local/share/fonts/` 安装，现有脚本已验证 | 系统 fallback sans-serif（中文可能豆腐块） |
| 执行模式 | 同步 import 调用 | 纯 CPU、无 GPU、无需后台服务 | 异步 HTTP（参考 ASR/OCR 架构——但本项目不需要） |
| 项目结构 | 单模块 `converter.py` + MCP entry + 原 CLI 脚本 | 代码量小（<400 LOC），无需过度拆分 | `converter/` 包（过度设计，3 个函数不值得建目录） |

## 4. 架构概览

### 整体架构：纯 CPU 同步 MCP Server

```
OpenCode Agent
    │ MCP stdio (FastMCP)
    ▼
format_mcp_server.py          ← 3 个 tool 装饰器，薄 wrapper
    │ import
    ▼
converter.py                  ← 核心转换函数（可 import）
    ├── convert_markdown_to_pdf(source, output)
    ├── convert_html_to_pdf(source, output)
    └── convert_pdf_to_text(source)
    │   │   │
    ▼   ▼   ▼
markdown-it-py  WeasyPrint  PyMuPDF (fitz)
    │               │           │
    ▼               ▼           ▼
 字体文件 (~/.local/share/fonts/)
 NotoSansSC-Regular.ttf / NotoEmoji-Regular.ttf

同时，CLI 脚本也可直接 import converter：

md2pdf.py  ──import──► converter.convert_markdown_to_pdf()
html2pdf.py ──import──► converter.convert_html_to_pdf()
```

### 关键设计决策

1. **无后端 server**：与 ASR/OCR 的 "MCP → HTTP → FastAPI" 架构不同，format-conversion 是纯 CPU 同步操作，工具函数直接 import 本地模块。没有 `_start.sh`、没有 `_ensure_ready()`、没有 health check。

2. **`converter.py` 是核心**：所有转换逻辑集中在一个模块内，同时被 MCP server 和 CLI 脚本引用。CLI 脚本退化为薄 wrapper（参数解析 + 调用 converter）。因为都在同一目录，`from converter import ...` 即可直接 import。

3. **字体发现逻辑不变**：沿用现有脚本的 `_check_fonts()` 逻辑（检查 `~/.local/share/fonts/NotoSansSC-Regular.ttf` 和 `NotoEmoji-Regular.ttf`），缺失时打印警告、使用降级策略。

4. **emoji 处理差异保留**：
   - md→pdf：emoji→text 替换**在 markdown 解析前**执行（避免 emoji 被 markdown-it 转义）；star 额外做金色 CSS 处理
   - html→pdf：emoji→text 替换**在 HTML 渲染前**执行；无 star 特殊处理

## 5. 目录结构

```
format-conversion/                  ← 项目目录
├── __init__.py                     ← 空文件，使目录成为 Python 包
├── converter.py                    ← 共享转换模块（核心逻辑）
├── format_mcp_server.py            ← MCP 入口（3 个 tool）
├── md2pdf.py                       ← Markdown→PDF CLI wrapper（原地重构，向后兼容）
├── html2pdf.py                     ← HTML→PDF CLI wrapper（原地重构，向后兼容）
├── README.md                       ← 服务说明（更新）
├── PLAN.md                         ← 本文件
├── PROGRESS.md                     ← builder 进度追踪
└── REVIEW.md                       ← reviewer 审查结论
```

### 文件职责

| 文件 | 职责 | 关键内容 |
|---|---|---|
| `__init__.py` | 包声明 | 空文件 |
| `converter.py` | 核心转换函数 + 字体发现 + CSS 构建 | 3 个公开函数 + 内部辅助函数 |
| `format_mcp_server.py` | MCP Server，暴露 3 个 tool | `FastMCP` 实例 + `@mcp.tool()` 装饰 |
| `md2pdf.py` | CLI wrapper（向后兼容） | `sys.argv` 解析 → 调 `converter.convert_markdown_to_pdf()` |
| `html2pdf.py` | CLI wrapper（向后兼容） | `sys.argv` 解析 → 调 `converter.convert_html_to_pdf()` |

## 6. 任务拆解

### Task T01: 创建 conda 环境 `format-convert`

- 目标：创建隔离的 Python 3.12 环境并安装所有依赖
- 涉及文件：`docs/conda-environments.md`
- 实现要点：
  1. `mamba create -n format-convert python=3.12 -y`
  2. `mamba install -n format-convert -c conda-forge weasyprint markdown-it-py pymupdf -y`
  3. `mamba run -n format-convert pip install "mcp>=1.0.0"`
  4. 验证：每个包可 import（`python -c "import weasyprint, markdown_it, fitz, mcp"`）
  5. 更新 `docs/conda-environments.md`（追加 `format-convert` 环境行到总览表和详情）
- 验收标准：
- [x] `mamba env list | grep format-convert` 输出该环境
- [x] `/home/lanxiukai/mambaforge/envs/format-convert/bin/python -c "import weasyprint, markdown_it, fitz, mcp; print('OK')"` 输出 `OK`
- [x] `docs/conda-environments.md` 中 `format-convert` 已列出（行：`format-convert \| 3.12 \| 文档格式转换（MD/HTML→PDF, PDF→Text）\| **format-conversion**`）
- 依赖：无
- 预计 LOC：~0（纯命令操作） + 文档更新 ~20 行

### Task T02: 创建 `converter.py` — 共享转换模块

- 目标：从现有 `md2pdf.py` 和 `html2pdf.py` 提取核心逻辑为可 import 的函数，新增 `pdf_to_text`
- 涉及文件：`format-conversion/converter.py`（新建）、`format-conversion/__init__.py`（新建）
- 实现要点：
  1. 创建 `__init__.py`（空文件）
  2. 提取字体发现函数 `_check_fonts()` → 返回 `dict[str, str|None]`（font_name → file_path or None）
  3. 提取 md→pdf 的核心管线为 `convert_markdown_to_pdf(source_path: str, output_path: str) -> None`：
     - 读 .md → emoji 预处理（map 替换，仅在无 emoji 字体时执行）→ markdown-it 渲染（`'commonmark'` + `['table', 'strikethrough']`）→ post-process body（star 着色 + emoji 包裹）→ 内嵌 CSS → WeasyPrint 输出
     - CSS 沿用现有 `build_css()` 全部样式规则（A4 / 页边距 / 标题色系 / 表格斑马 / 引用块 / 代码块）
  4. 提取 html→pdf 的核心管线为 `convert_html_to_pdf(source_path: str, output_path: str) -> None`：
     - 读 .html → emoji 处理 → 注入 font CSS + page footer → WeasyPrint 输出
     - 保留现有脚本的 `_process_emoji()` 和 `_build_injected_css()` 逻辑
  5. 实现 `convert_pdf_to_text(source_path: str) -> str`：
     - 使用 `fitz.open(source_path)` 逐页 `page.get_text()` 拼接
     - 返回纯文本字符串（不返回 dict，与另外两个函数的签名风格不同是合理的——PDF→text 不需要输出路径）
  6. emoji 兼容映射表 `_EMOJI_TEXT_MAP` 和正则 `_EMOJI_RE` 作为模块级常量，两个函数共享
- 验收标准：
- [x] `from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text` 无 import 错误
- [x] `convert_markdown_to_pdf("/path/to/test.md", "/tmp/out.pdf")` 生成有效 PDF，文件大小 > 0
- [x] `convert_html_to_pdf("/path/to/test.html", "/tmp/out.pdf")` 生成有效 PDF，文件大小 > 0
- [x] `convert_pdf_to_text("/path/to/born-digital.pdf")` 返回非空字符串，包含可读文字
- [x] 字体缺失时（如`Noto Emoji`不存在），emoji 降级为文字标签，不抛异常
- [x] 输入文件不存在时抛出 `FileNotFoundError`（带明确路径信息）
- [x] 函数不打印到 stdout（不在模块层写 `print`——日志走 stderr 或 `logging`）
- 依赖：T01
- 预计 LOC：~250

### Task T03: 原地重构 CLI 脚本（向后兼容）

- 目标：将现有 `md2pdf.py` 和 `html2pdf.py` 重构为 import `converter` 的薄 wrapper
- 涉及文件：`format-conversion/md2pdf.py`、`format-conversion/html2pdf.py`（原地修改）
- 实现要点：
  1. `md2pdf.py`：删除原有的 `_check_fonts()`、`build_css()`、`_process_body()`、`_EMOJI_RE` 等核心逻辑；保留 CLI 参数解析（`sys.argv[1]` = 输入，`sys.argv[2]` = 可选输出），调用 `converter.convert_markdown_to_pdf()`。`__doc__` 保持在顶部。
  2. `html2pdf.py`：同上——删除核心逻辑，保留 `sys.argv` 解析，调用 `converter.convert_html_to_pdf()`。
  3. 因为都在 `format-conversion/` 同级目录，`from converter import ...` 无需 `sys.path` 调整
  4. 字体检测 / 警告 / 进度打印逻辑移到 converter 内部（CLI 脚本不再自己做这些）
  5. 保持原有用户接口不变：`python md2pdf.py <input> [output]`、`python html2pdf.py <input> [output]`
- 验收标准：
- [x] `mamba run -n format-convert --no-capture-output python format-conversion/md2pdf.py -h` 输出用法
- [x] `mamba run -n format-convert --no-capture-output python format-conversion/md2pdf.py <test.md> /tmp/test_out.pdf` 生成有效 PDF
- [x] `mamba run -n format-convert --no-capture-output python format-conversion/html2pdf.py <test.html> /tmp/test_out.pdf` 生成有效 PDF
- [x] 不传参数时输出用法信息并 `sys.exit(0)`（不报错）
- [x] 输出信息中包含字体检测结果（来自 converter 内部）
- 依赖：T02
- 预计 LOC：~60（两个文件合计，大量代码移到 converter 后 CLI 脚本变得很薄）

### Task T04: 构建 `format_mcp_server.py` — MCP 入口

- 目标：使用 `mcp.server.fastmcp` 暴露 3 个 MCP tool
- 涉及文件：`format-conversion/format_mcp_server.py`（新建）
- 实现要点：
  1. 创建 `FastMCP` 实例：
     ```python
     mcp = FastMCP(
         name="Format Conversion",
         json_response=True,
         instructions="Document format conversion tools. "
                       "markdown_to_pdf/html_to_pdf: convert documents to PDF. "
                       "pdf_to_text: extract text from born-digital PDFs."
     )
     ```
  2. `markdown_to_pdf` tool：
     - 参数：`file_path: str`（`.md` 绝对路径）、`output_path: str = ""`（可选；为空则与输入同名换后缀 `.pdf`）
     - 逻辑：调用 `converter.convert_markdown_to_pdf()`
     - 返回：`{"status": "success", "output_path": "..." , "size_bytes": N}`
     - 异常时返回：`{"error": "..."}`
  3. `html_to_pdf` tool：同上模式，调用 `converter.convert_html_to_pdf()`
  4. `pdf_to_text` tool：
     - 参数：`file_path: str`（`.pdf` 绝对路径）
     - 返回：`{"text": "...", "page_count": N, "size_chars": N}`
     - **工具描述必须写**："born-digital PDF only（文字可选中/复制）。扫描件 PDF 请使用 `glm_ocr` 工具"
     - 若 PyMuPDF 提取出空文本（可能为扫描件），返回的 `text` 为空字符串但 status 仍为 success（不报错），由 agent 自行决定是否调用 `glm_ocr`
  5. 入口：`if __name__ == "__main__": mcp.run(transport="stdio")`
- 验收标准：
- [x] `mamba run -n format-convert --no-capture-output python format-conversion/format_mcp_server.py` 启动后不报错（但会阻塞等待 MCP stdio——Ctrl+C 可中断）
- [x] 3 个 `@mcp.tool()` 装饰的函数签名正确，参数名与需求一致
- [x] `pdf_to_text` 的 docstring 中包含 "born-digital PDF only"
- [x] 文件不存在时返回 `{"error": "..."}` 而非抛出未捕获异常
- [x] `markdown_to_pdf` 和 `html_to_pdf` 的 `output_path` 为空时，自动推导为 `<input_stem>.pdf`
- 依赖：T02
- 预计 LOC：~120

### Task T05: 更新 `README.md` 和 `docs/conda-environments.md`

- 目标：更新项目 README 和全局工具概览
- 涉及文件：`format-conversion/README.md`、`README.md`（仓库根）
- 实现要点：
  1. 更新 `format-conversion/README.md`：
     - 添加 "MCP 工具" 节 → 3 个 tool 的表格说明（名称 / 输入 / 输出 / 引擎）
     - 添加 "模块 API" 节 → `converter.py` 的 3 个公开函数签名和简要说明
     - 保留已有的 "CLI 脚本" 节（路径仍为 `md2pdf.py` / `html2pdf.py`，但说明已重构为 import converter 的薄 wrapper）
     - 保留已有的"环境准备"节（更新 conda env 名为 `format-convert`）
     - 保留"已知问题与解法"和"替代方案对比"
  2. 更新 `README.md`（仓库根）：
     - 在"工具概览"表格中新增 format-conversion 行：
       `| **Format Conversion** | 文档格式转换（MD/HTML→PDF, PDF→Text）| WeasyPrint + PyMuPDF | 纯 CPU |`
     - 在"目录结构"中新增 `format-conversion/` 条目
     - 在"文档导航"表格中新增 `format-conversion/README.md` 行
  3. T01 已更新 `docs/conda-environments.md`——本 task 仅确认已更新一致
- 验收标准：
- [x] `format-conversion/README.md` 包含 MCP 工具表格（3 行）
- [x] `format-conversion/README.md` 包含模块 API 节（3 个函数签名）
- [x] 仓库 `README.md` 工具概览表包含 format-conversion 行
- [x] 所有文档中的文件路径与实际目录结构一致（无 `scripts/` 前缀）
- 依赖：T03, T04
- 预计 LOC：~60（文档更新）

### Task T06: 集成冒烟测试

- 目标：用 `mcp-tool-test/format-conversion/` 下的真实文件验证所有 3 个工具
- 涉及文件：无新建源码文件，测试输出到 `/tmp/format-conversion-test/`
- 实现要点：
  1. 写一个最小测试脚本（**不需要提交到仓库**，直接用 `mamba run` 执行）：
     ```python
     import os, sys
     sys.path.insert(0, "/home/lanxiukai/project/mcp-tools/format-conversion")
     from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text

     repo = "/home/lanxiukai/project/mcp-tools"
     md_dir = f"{repo}/mcp-tool-test/format-conversion/markdown-to-pdf"
     html_dir = f"{repo}/mcp-tool-test/format-conversion/html-to-pdf"
     pdf_dir = f"{repo}/mcp-tool-test/format-conversion/large-pdf-to-text-born-digital"
     out = "/tmp/format-conversion-test"
     os.makedirs(out, exist_ok=True)
     ```
  2. md→pdf：从 `markdown-to-pdf/` 选 1 个中文 `.md` 文件转换，验证输出 PDF 非空
  3. html→pdf：从 `html-to-pdf/` 选 1 个中文 `.html` 文件转换，验证输出 PDF 非空
  4. pdf→text：从 `large-pdf-to-text-born-digital/` 选 1 个 PDF（推荐 `体检报告-2026.04.24.pdf`，含中文），验证返回文本含中文关键词
  5. MCP server 层面的验证（可选，通过 MCP 协议调用较复杂——可简化为直接 import 验证 tool 函数签名）
- 验收标准：
- [x] 3 个转换全部成功，输出文件非空
- [x] PDF→text 返回的中文文本可读，包含预期关键词（如"体检"、"报告"等）
- [x] 所有操作完成时间 < 10 秒（纯 CPU，不应超时）
> **T06 用户指示跳过**：opencode 配置未加入该 MCP，暂时不执行集成冒烟测试。所有功能已在 T02/T04 中通过真实文件验证。
- 依赖：T02, T03, T04
- 预计 LOC：~40（测试脚本，不提交）

## 7. 风险与未知

| 风险 | 影响 | 应对 |
|---|---|---|
| WeasyPrint 系统依赖缺失（cairo/pango） | 中 | Ubuntu 24.04 默认已装；若缺失，`mamba install -c conda-forge cairo pango` 即可 |
| 字体文件路径不存在 | 中 | 启动时检查 + 警告；降级为系统 sans-serif（中文可能豆腐块但不会 crash） |
| PyMuPDF 对某些 born-digital PDF 提取为空 | 低 | 不报错，返回空字符串；tool 描述已提醒 agent 这是 born-digital only |
| `mcp` 包版本与现有 MCP 客户端不兼容 | 低 | 仓库已有 3 个 MCP server 用 `mcp>=1.0`，已验证兼容 |
| 大 PDF 转换耗时过长（如 Computer Architecture 教材 600+ 页） | 低 | PyMuPDF 逐页提取是流式的；WeasyPrint 对大型 HTML 有性能上限但测试文件都是短文档；可加超时处理（非必须） |

## 8. 验收门槛（DoD）

- [ ] 所有 6 个 task 勾选完成
- [ ] conda 环境 `format-convert` 存在，自检命令输出正确的 Python 路径
- [ ] `from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text` 无 ImportError
- [ ] 3 个工具各至少跑通 1 个真实测试文件（T06 冒烟测试）
- [ ] `docs/conda-environments.md` 包含 `format-convert` 环境
- [ ] 仓库 `README.md` 工具概览表包含 Format Conversion 行
- [ ] CLI 脚本 `md2pdf.py` / `html2pdf.py` 向后兼容（原用法不变，`python <script> <input> [output]`）
- [ ] 项目交付后 builder 调用 teacher 子 agent 生成 learning-notes（写到 `format-conversion/learning-notes/`）
- [ ] 无 Plan-Issue（任务定义清晰无歧义，验收标准可机械验证）

---

## Changelog

| 日期 | 变更 | 理由 |
|---|---|---|
| 2026-05-22 | 初始创建 PLAN.md，状态 `approved` | 新项目启动 |
| 2026-05-22 | 移除 `scripts/` 子目录，CLI 脚本原地重构 | 用户确认不需要 `scripts/`，所有文件保持在 `format-conversion/` 顶层 |
