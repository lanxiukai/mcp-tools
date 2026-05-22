# 07 — 进一步扩展的思路

> 适合读者：想基于这个项目做点自己的实践的人。

以下扩展按**从易到难**排列。每个扩展都标明了难度、涉及的文件和预计代码量。

---

## 扩展 1：添加正式测试（★★☆☆☆）

**当前状态**：没有 pytest 测试文件。REVIEW 验证靠手动运行命令。

**要做的事**：
1. 在 `format-conversion/` 下创建 `tests/` 目录（含 `__init__.py`）
2. 创建 `test_converter.py`，测试三个公开函数的正常路径和异常路径
3. 创建 `test_mcp.py`，测试 MCP tool 函数的参数处理和错误返回
4. 考虑模拟（mock）第三方库，使得测试不需要真实文件也能跑

**涉及文件**：
- 新建 `tests/test_converter.py`（~80 LOC）
- 新建 `tests/test_mcp.py`（~60 LOC）
- 可能需要 `conftest.py` 放 fixture

**难点**：
- WeasyPrint 不安装 cairo 跑不了，但 CI 环境不一定有——可以用 `unittest.mock` 替换 `weasyprint.HTML.write_pdf`
- 字体文件在每台机器上不同——`_check_fonts()` 在不同环境返回不同值，需要 mock

**学习价值**：你会学到如何对一个"有系统依赖"的 Python 项目做单元测试——mock 文件系统和第三方库。

**预计 LOC**：~140

---

## 扩展 2：支持 `--help` 参数完善（★☆☆☆☆）

**当前状态**：CLI 脚本的 `--help` 只是打印了 `__doc__`：

```python
if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
    print(f"Usage: {sys.argv[0]} <input.md> [output.pdf]")
    print(__doc__)
    sys.exit(0)
```

**要做的事**：改用 `argparse` 或 `click` 框架，提供更丰富的 CLI 体验。

**涉及文件**：
- `md2pdf.py`（修改 main 函数，+20 LOC）
- `html2pdf.py`（同上，+20 LOC）

**改进点**：
- `--output` 替代位置参数 `[output.pdf]`
- `--verbose` 控制日志级别
- `--version` 打印版本号

**难点**：要保持向后兼容——现有的 `md2pdf.py input.md output.pdf` 用法不能断。

**预计 LOC**：+40

---

## 扩展 3：添加批量转换支持（★★★☆☆）

**当前状态**：三个工具都只接受单个输入文件。

**要做的事**：在 MCP server 中新增一个 `batch_convert` 工具（或修改现有工具），接受文件列表或通配符模式：

```python
@mcp.tool()
def markdown_to_pdf_batch(file_paths: list[str], output_dir: str = "") -> dict:
    """批量转换多个 Markdown 文件为 PDF。

    所有输出 PDF 放在 output_dir 目录下，
    文件名与原 .md 相同，后缀改为 .pdf。
    """
    results = []
    for fp in file_paths:
        try:
            out = convert_markdown_to_pdf(fp, ...)
            results.append({"file": fp, "status": "success", "output": out})
        except Exception as e:
            results.append({"file": fp, "status": "error", "error": str(e)})
    return {"results": results}
```

**涉及文件**：
- `format_mcp_server.py`（加新的 `@mcp.tool()`，+40 LOC）
- `converter.py`（可能需要加一个辅助函数处理批量逻辑）

**难点**：
- MCP 协议对 `list[str]` 参数的支持取决于 MCP 客户端（OpenCode 支持）
- 一个文件失败不应该中断整个批量
- 需要决定"部分成功"的返回格式

**学习价值**：你会学到如何处理"批量操作"的设计模式——原子性（全部或全不？）vs 局部成功（部分成功部分失败？）。

**预计 LOC**：+50

---

## 扩展 4：支持扫描件 PDF（调用 `glm_ocr` 自动降级）

**当前状态**：`pdf_to_text` 明确标注 "born-digital only"，扫描件返回空字符串。

**要做的事**：在 `pdf_to_text` 中增加自动检测——如果 PyMuPDF 提取文本为空，自动调用 `glm_ocr_ocr_glm`（OCR 工具）进行 OCR 识别。

```python
@mcp.tool()
def pdf_to_text(file_path: str) -> dict:
    text = convert_pdf_to_text(file_path)
    if not text.strip():
        # born-digital 返回空文本——可能是扫描件
        # 这里可以调用 OCR 工具（如果可用）
        # text = call_ocr(file_path)
        pass
    ...
```

**涉及文件**：
- `format_mcp_server.py`（修改 `pdf_to_text`，+15 LOC）
- 可能需要跨 MCP 服务调用（本仓库的 `glm_ocr` 是另一个 MCP 服务）

**难点**：
- 跨 MCP 服务调用：`pdf_to_text` 是 format-conversion MCP 的 tool，`glm_ocr` 是另一个 MCP server 的 tool。在 MCP 协议层面，一个 tool 直接调用另一个 tool 不是标准做法。
- 一种方式是让 `format_mcp_server.py` 直接 import `glm_ocr` 的 converter——但这样就有了跨服务依赖，部署时要同时部署两个环境。
- 更实际的方案：让 agent（调用方）自己做降级——如果 `pdf_to_text` 返回空文本，就自动换调 `glm_ocr`。

所以这个扩展不是"纯代码改动"，而是"agent 行为改进"。

**学习价值**：你会学到微服务/工具间的职责划分——"不要在一个工具里做另一个工具该做的事"。

**预计 LOC**：不适用（主要是概念设计）

---

## 扩展 5：添加 DOCX 支持（★★★★☆）

**当前状态**：只支持 Markdown、HTML、PDF 三种格式。

**要做的事**：在 `converter.py` 中新增 `convert_docx_to_pdf(source_path: str, output_path: str) -> None`：

```python
import docx  # python-docx 包

def convert_docx_to_pdf(source_path: str, output_path: str) -> None:
    """将 DOCX 文件转换为 PDF。

    管线: python-docx 解析 → 提取 HTML 或直接操作 XML → WeasyPrint → PDF
    """
    doc = docx.Document(source_path)
    # 解析段落、表格、图片等 → 生成 HTML
    # → WeasyPrint.write_pdf()
```

**涉及文件**：
- `converter.py`（新增函数 + 导入，~80 LOC）
- `format_mcp_server.py`（加 `@mcp.tool()`，~25 LOC）
- 可以加一个 `docx2pdf.py` CLI 脚本（~40 LOC）
- `pyproject.toml` 或 conda 环境文件（添加 `python-docx` 依赖）

**难点**：
- DOCX 格式极其复杂：段落样式、内联图片、表格嵌套、页眉页脚——要完美转 PDF 几乎不可能
- python-docx 不提供"DOCX → HTML"的直接转换，需要自己构建转换逻辑
- 为什么不在 PLAN.md 的"不做"列表里？因为用户没提这个需求。如果要加，建议先做批量转换（扩展 3），因为批量转换不依赖新格式

**学习价值**：你会学到处理 DOCX 格式的复杂性，以及"为什么要拒绝过度发挥"。

**预计 LOC**：+150

---

## 扩展 6：添加 Watch / 监看模式（★★★★☆）

**要做的事**：`md2pdf.py` 增加 `--watch` 参数，监看输入文件的修改，自动重新转换。

```python
# 伪代码
if args.watch:
    from watchdog.observers import Observer
    observer = Observer()
    observer.schedule(..., path=str(md_path.parent))
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
```

**涉及文件**：
- `md2pdf.py`（新增 `--watch` 参数解析 + 文件监看逻辑，~40 LOC）
- 需要新增依赖 `watchdog`（conda/pip 安装）

**难点**：
- 监看模式和 MCP 服务模式冲突——MCP server 已经是"一直运行"了，监看应该放在 MCP server 里还是 CLI 里？
- 答案：放在 CLI 里更合理，因为 MCP server 的工具是"请求-响应"模式，不适合主动监看

**学习价值**：你会学到"同样一个功能在 CLI 和 MCP 下的不同形态"。

**预计 LOC**：+60

---

## 扩展 7：尝试把代码拆成包结构（★★☆☆☆）

**当前状态**：单模块 `converter.py` (~440 LOC)。

**要做的事**：当 `converter.py` 超过某个阈值（比如 800 LOC）后，拆成包：

```
converter/               ← 把 converter.py 变成包
├── __init__.py          ← 导出 3 个公开函数
├── fonts.py             ← 字体发现
├── css.py               ← CSS 构建
├── emoji.py             ← Emoji 处理
└── api.py               ← 3 个公开函数
```

**涉及文件**：
- 新建 `converter/` 目录
- 把 `converter.py` 拆成 4 个文件
- MCP server 和 CLI 脚本的 `from converter import ...` 不需要改（因为 `__init__.py` 重新导出了公开 API）

**难点**：
- 拆分后要确保模块间没有循环 import（如 `css.py` 不会 import `emoji.py` 而 `emoji.py` 又 import `css.py`）
- `_EMOJI_RE` 和 `_EMOJI_TEXT_MAP` 在包内要放在哪里？所有文件都需要引用时怎么办？

**学习价值**：你会学到"什么时候该把单文件拆成包"的判断力。

**预计 LOC**：~0（代码不变，只是文件拆分）

---

## 扩展优先级建议

如果你只想做一个扩展，选**扩展 1（加测试）**。没有测试的代码就像没有地基的房子——加功能越多，倒塌风险越大。

如果你想练手，选**扩展 2（完善 CLI）**。最简单，最适合初学者。

如果你想挑战，选**扩展 5（加 DOCX）**。让你真正理解"格式转换全栈"的复杂度。

## 动手任务

1. **最小化扩展**：在 `converter.py` 里加一个 `convert_markdown_to_text()` 函数——从 Markdown 提取纯文本（剥离所有 markdown 语法）。不需要 WeasyPrint，只需要 `markdown-it-py` 渲染后，再用一个 HTML 解析器（如 `html.parser`）提取纯文本。
2. **做批量转换的 CLI**：写一个简单的 `batch_md2pdf.py`，接受一个目录路径作为参数，把目录下所有 `.md` 文件转成 PDF。
3. **读 PLAN.md 的"不做"列表**：选其中一条（如"不做批量转换"），自己实现一个最小版本，然后体会为什么 planner 当初说"不做"。
