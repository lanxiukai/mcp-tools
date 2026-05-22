# 05 — 工程实践与代码风格

> 适合读者：想学"好的 Python 代码长什么样"的人，不管项目大小。

PLAN.md 第 0 节说"本项目暂不要求 mypy / ruff（代码量小，依赖复杂）"——但这**不意味着代码可以随便写**。读一遍 `converter.py`，你会发现它有很多值得学习的工程习惯。

---

## 1. 错误处理哲学

### 1.1 三区分错误

本项目的错误处理分三个层次：

| 层次 | 处理方式 | 示例 |
|------|---------|------|
| **输入校验** | 在函数开始处检查前置条件，不满足就抛出特定异常 | 文件不存在 → `raise FileNotFoundError` |
| **操作失败** | 在调用处捕获并翻译为友好的错误信息 | `except Exception as e: return {"error": f"Conversion failed: {e}"}` |
| **不可恢复** | 不捕获，让程序崩溃（本项目没有这种情况） | — |

### 1.2 外层不捕获 FileNotFoundError

在 MCP server 中（`format_mcp_server.py`），`FileNotFoundError` 和 `Exception` 是**分开捕获**的：

```python
try:
    convert_markdown_to_pdf(file_path, output_path)
    return {"status": "success", ...}
except FileNotFoundError as e:
    return {"error": str(e)}           # 错误信息直接回传：文件找不到
except Exception as e:
    return {"error": f"Conversion failed: {e}"}  # 通用错误包装
```

**为什么这样分开？**

- `FileNotFoundError` 是"预期中的错误"——用户可能给了一个不存在的路径，这个错误信息（"Markdown file not found: /path/to/nonexistent.md"）对用户很有帮助，不需要额外包装
- 其他异常是"意外的错误"——可能是 WeasyPrint 渲染出错了，可能是磁盘空间满了，需要一个通用前缀让 agent 知道"转换过程出了问题"

初学者经常犯的错误是：`except Exception as e: return {"error": str(e)}` ——这样 `FileNotFoundError` 和渲染错误都返回同样格式，agent 无法区分"路径错了"还是"引擎崩了"。

### 1.3 catch 后一定要返回

注意 MCP tool 函数中，错误分支都以 `return` 结束：

```python
except FileNotFoundError as e:
    return {"error": str(e)}   # ✅ 返回错误 dict，不是打印后继续执行
```

一个常见的反模式是：

```python
# ❌ 坏例子：print 了错误但没有 return
except FileNotFoundError as e:
    print(f"Error: {e}")
# 代码继续执行，尝试访问 output_path 时可能抛出未处理的异常
```

---

## 2. Logger 替代 Print

converter.py 里所有"输出消息"都用 `logger` 而不是 `print`：

```python
logger = logging.getLogger(__name__)

logger.info("Converting: %s → %s", md_path, out_path)
logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)
logger.warning("Missing font(s): %s. Using system fallback.", ', '.join(missing))
```

### Logger 比 print 好在哪里？

| 维度 | print | logger |
|------|-------|--------|
| 控制输出 | 不能开关 | 可以设置级别（INFO/WARNING/ERROR），生产环境关掉 DEBUG |
| 调用者控制 | 不能 | 调用方可以 `logger.addHandler()` 决定输出到哪里 |
| 结构化 | 纯字符串 | 支持 %s 格式化（惰性求值，不构建不用的字符串） |
| 标准实践 | 个人脚本可以 | 库/服务级别的代码必须用 logger |

一个典型的`logging`配置示例（来自 `md2pdf.py`）：

```python
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
```

这行配置让 logger 输出到 stderr（不是 stdout），格式是 `INFO: Converting: /path/to/file.md → /path/to/file.pdf`。CLI 脚本可以选择配置 logger，但 converter.py 本身不配置——它只声明 `logger = logging.getLogger(__name__)`，把配置权留给调用者。

---

## 3. 异常链与 raise / return 的选择

converter.py 的公开函数接受文件路径，调用者负责处理异常：

```python
# converter.py — 抛出异常
def convert_markdown_to_pdf(source_path: str, output_path: str) -> None:
    if not md_path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {source_path}")
    # ... 正常转换 ...

# format_mcp_server.py — 捕获异常
@mcp.tool()
def markdown_to_pdf(file_path: str, output_path: str = "") -> dict:
    try:
        convert_markdown_to_pdf(file_path, output_path)
        return {"status": "success", ...}
    except FileNotFoundError as e:
        return {"error": str(e)}
```

这里有意的设计：**converter.py 只抛出异常不捕获，MCP server 捕获异常转成 dict 返回。**

为什么不让 converter.py 自己返回 `{"status": "error", ...}`？因为 converter.py 也可以被 CLI 脚本调用：

```python
# md2pdf.py — CLI 需要异常来设置退出码
try:
    convert_markdown_to_pdf(str(md_path), str(pdf_path))
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)  # ✅ 异常驱动退出码
```

如果 converter.py 返回 dict，CLI 脚本就要检查 `dict.get("status") == "error"`——这种风格的代码既啰嗦又容易遗漏。

**一个原则：库函数抛异常，应用层处理异常**。converter.py 是"库"，MCP server 和 CLI 是"应用层"。

---

## 4. Docstring 风格

项目使用 Google-style docstring（Google Python 风格指南推荐的格式）：

```python
def convert_pdf_to_text(source_path: str) -> str:
    """Extract plain text from a born-digital PDF using PyMuPDF.

    Only works with born-digital PDFs (text that can be selected/copied).
    Scanned-image PDFs will return an empty string; use the ``glm_ocr``
    tool for those.

    Args:
        source_path: Absolute path to the .pdf file.

    Returns:
        Extracted text as a single string (pages joined with newlines).

    Raises:
        FileNotFoundError: If source_path does not exist.
    """
```

包含什么：

| 部分 | 内容 |
|------|------|
| 一句话摘要 | "Extract plain text from a born-digital PDF using PyMuPDF." |
| 补充说明 | 什么情况下会返回空字符串，有什么替代工具 |
| Args | 每个参数的说明（没有类型——类型在函数签名里） |
| Returns | 返回值的含义 |
| Raises | 什么情况下会抛出什么异常 |

注意 **Args 里不重复写类型**——类型注解已经在函数签名里了，docstring 再写就是重复信息。Python 社区的主流风格已经在向"类型注解不写进 docstring"靠拢。

内部函数（`_` 前缀）的 docstring 更简短：

```python
def _check_fonts() -> dict[str, Optional[str]]:
    """Check which fonts are available.

    Returns:
        dict mapping font name → file path (or None if missing).
        Keys: 'Noto Sans SC', 'Noto Emoji'.
    """
```

只说明"做什么"和"返回什么"，不解释"为什么"——后者写在函数调用处的注释里或 PLAN 文档里。

---

## 5. Import 顺序约定

converter.py 的 import 语句遵循标准分组（组间空行隔开）：

```python
# 第一组：标准库
import logging
import os
import re
from pathlib import Path
from typing import Optional

# 第二组：第三方库
import fitz
from markdown_it import MarkdownIt
from weasyprint import HTML
```

这是 PEP 8 推荐的顺序：标准库 → 第三方库 → 本地库。每组内部按字母序排列。这种约定让读者能快速回答两个问题：

- "这个模块依赖哪些标准库？" → 只看第一组
- "这个模块依赖哪些第三方包？" → 只看第二组

---

## 6. 函数长度与单职责

converter.py 中最长的函数是 `convert_markdown_to_pdf`，约 60 行。这个长度合理吗？

看它的内部结构：

```python
def convert_markdown_to_pdf(source_path: str, output_path: str) -> None:
    # 1. 输入校验（4 行）
    # 2. 字体检查（7 行）
    # 3. 读取文件（1 行）
    # 4. Emoji 预处理（3 行）
    # 5. Markdown 解析（3 行）
    # 6. 后处理（2 行）
    # 7. 拼装完整 HTML（10 行）
    # 8. WeasyPrint 输出（2 行）
    # 9. 日志（2 行）
```

每个阶段 1-3 行代码，**用空行分隔**。这使得 60 行的函数读起来像 9 个清晰的步骤。如果某个步骤超过 10 行，就应该提取为单独的函数（如 `_build_css`、`_process_body`）。

**经验法则**：如果函数里需要用 `# 注释` 来分段，就说明这一段应该是一个单独的函数。

---

## 7. import * 的陷阱（本项目避免了）

`format_mcp_server.py` 中：

```python
from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text
```

这是"明确导入"（explicit import）——只导入需要的 3 个函数，不导入整个模块。如果写成：

```python
# ❌ 坏例子
from converter import *
```

问题：你不知道 `converter.py` 里有什么被 import 进来了。内部函数 `_check_fonts`、`_build_css` 也会被导入，造成命名空间污染。

如果写：

```python
# ❌ 也不推荐（对于只用到 3 个函数的情况）
import converter
# 然后每次调用 converter.convert_markdown_to_pdf(...)
```

（其实这种更好——参见下文讨论）

---

## 8. 变量命名：用类型改进名

一个细微但值得注意的习惯：

```python
fonts = _check_fonts()                         # ✅ dict[str, Optional[str]]
missing = [n for n, p in fonts.items() if p is None]  # ✅ list[str]
```

变量名 `fonts` 和 `missing` 简洁但不含糊。如果写成 `f` 和 `m`，读者要猜。如果写成 `fonts_availability_dict` 和 `list_of_missing_font_names`，又过度了。

好的变量名 = **恰好够用的长度**。初学者常犯的两个极端：
- 太短：`d`、`tmp`、`x`——猜不出意思
- 太长：`dictionary_of_font_names_to_paths`——打断阅读节奏

---

## 9. 字符串构建：f-string 与 join 的选择

CSS 构建函数 `_build_css` 里有两种字符串拼接方式：

**多行 f-string**（用于大段 CSS）：

```python
return f"""
{"".join(font_rules)}

@page {{
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
    ...
}}
"""
```

**str.join**（用于拼接字体栈）：

```python
body_font = ', '.join(body_stack)
# 输出: "'Noto Sans SC', 'DejaVu Sans', sans-serif"
```

两者的选择依据：
- 大段固定模板 + 少量变量插入 → f-string（可读性更好）
- 动态列表的拼接 → str.join（比 `+` 和 f-string 循环更高效）

---

## 10. 三引号 docstring + shebang

注意 `format_mcp_server.py` 和 `md2pdf.py` 的文件头：

```python
#!/usr/bin/env python3
"""MCP server for document format conversion tools.

Exposes 3 tools via MCP stdio protocol:
...
"""
```

第一行是 shebang（`#!`），使得在 Unix 上可以直接 `./format_mcp_server.py` 运行（虽然本项目通过 MCP 协议调用，shebang 在实际使用中不必要，但保留它没有坏处）。
第二行是三引号包住的模块级 docstring（和 `converter.py` 风格一致）。

---

## 11. 动手任务

1. **把 logger 换成 print**：全局替换 `logger.info(` 为 `print(`，看输出效果有什么不同（注意 stderr vs stdout 的区别）。
2. **分析异常**：在 `convert_html_to_pdf` 中制造一个文件路径错误，看 MCP server 返回什么。再制造一个 WeasyPrint 渲染错误（比如传入非法 CSS），看错误信息变成什么。两次返回的 `{"error": ...}` 格式有何不同？
3. **重构练习**：把 `convert_markdown_to_pdf` 的一个阶段提取为独立的函数（比如把"字体检查"的 7 行代码提取为 `_log_font_status(fonts)`），看看函数是否因此变得更短、更清晰。
