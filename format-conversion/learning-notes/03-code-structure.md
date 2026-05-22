# 03 — 代码组织与模块划分

> 适合读者：想快速了解"这个项目的每个文件是干什么的、它们之间如何协作"的人。

## 1. 目录树

```
format-conversion/
├── __init__.py              ← 空文件，目录标记为 Python 包
├── converter.py             ← 核心转换模块（~440 LOC）
├── format_mcp_server.py     ← MCP 服务入口（~120 LOC）
├── md2pdf.py                ← Markdown→PDF CLI（~50 LOC）
├── html2pdf.py              ← HTML→PDF CLI（~50 LOC）
├── README.md                ← 项目说明文档
├── PLAN.md                  ← 项目计划
├── PROGRESS.md              ← Builder 进度追踪
├── REVIEW.md                ← 审查报告
└── learning-notes/          ← 你正在读的学习材料（当前文件在这里）
```

### 每个文件的职责（一句话）

| 文件 | 一句话职责 | 公开 API |
|------|-----------|---------|
| `__init__.py` | 让 Python 把 `format-conversion/` 当成一个可 import 的包 | 空 |
| `converter.py` | 提供 3 个文档格式转换函数 + 字体发现 + CSS 构建 | `convert_markdown_to_pdf()`、`convert_html_to_pdf()`、`convert_pdf_to_text()` |
| `format_mcp_server.py` | 通过 FastMCP 暴露 3 个 MCP 工具，调用 converter | `markdown_to_pdf()`、`html_to_pdf()`、`pdf_to_text()`（均为 `@mcp.tool()` 装饰） |
| `md2pdf.py` | 命令行调用 `convert_markdown_to_pdf()` | 无（CLI 入口 `main()`） |
| `html2pdf.py` | 命令行调用 `convert_html_to_pdf()` | 无（CLI 入口 `main()`） |
| `README.md` | 使用说明 + MCP 工具表 + 模块 API 文档 | 无（纯文档） |

---

## 2. 模块依赖关系图

```
                   ┌─────────────┐
                   │  md2pdf.py  │ (CLI wrapper — 薄)
                   └──────┬──────┘
                          │ import
                          ▼
┌──────────────┐   ┌─────────────┐   ┌──────────────┐
│ html2pdf.py  │──▶│ converter   │◀──│ format_mcp   │
│ (CLI wrapper)│   │ .py         │   │ _server.py   │
└──────────────┘   │ (核心逻辑)   │   │ (MCP 入口)   │
                   └──────┬──────┘   └──────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   markdown-it-py    WeasyPrint      PyMuPDF (fitz)
   (md→html)         (html→pdf)      (pdf→text)
```

**关键观察：`converter.py` 是唯一的依赖汇聚点**。MCP server 和两个 CLI 脚本都只 import `converter`，不直接 import 任何第三方库。这意味着：

- 如果要替换 markdown-it-py 为另一个解析器 → 只改 `converter.py`
- 如果要给 WeasyPrint 加参数 → 只改 `converter.py`
- 如果要换掉 PyMuPDF → 只改 `converter.py`
- MCP server 和 CLI 脚本**完全不需要修改**

这就是"依赖反转"（Dependency Inversion）的简单形式：高层模块（MCP server、CLI）依赖抽象（`converter.py` 的公开函数），而非依赖具体第三方库。

---

## 3. 为什么用 flat layout 而不是 src layout？

这是初学者常纠结的问题。先解释两个概念：

- **Flat layout**：所有 `.py` 文件和项目目录在同一层。如 `format-conversion/converter.py`，`from converter import xxx` 直接可用。
- **Src layout**：源代码放在 `src/` 子目录下。如 `format-conversion/src/converter.py`，引用时用 `from format_conversion.converter import xxx`，需要 `pip install -e .`。

很多 Python 项目推荐 src layout，因为它更清晰地分离"源码"和"项目根目录下的杂项文件"（README、配置文件等）。但本项目选用了 flat layout。

**理由**（来自 PLAN.md 第 3 节）：

1. **代码量极小**（<400 LOC），src layout 的目录嵌套反而增加文件定位成本
2. **CLI 脚本和 converter 在同一目录**：`md2pdf.py` 里 `from converter import ...` 不需要 `sys.path` 调整，直接工作
3. **没有需要安装的分发包**：本项目是一个 MCP 服务，不是分发给其他人安装的库。不需要 `setup.py` 或 `pyproject.toml`

什么时候该用 src layout？**当你的项目需要 pip install 或者有多个子包时**。比如仓库里的 ASR 服务，如果有多个子模块，用 src layout 更合适。

---

## 4. converter.py 的内部模块划分

虽然整个文件只有一个文件，但内部有明显的分区（按功能顺序排列）：

```python
# ── 文件头 ──
# 模块级 docstring（声明 3 个公开函数）
# import 语句（标准库 → 第三方库，分组排列）
# logger 初始化

# ── 模块级常量 ──
# _EMOJI_RE: 匹配 emoji 字符范围的正则
# _EMOJI_TEXT_MAP: emoji → 文字标签的映射字典

# ── 字体发现 ──
# _check_fonts(): 检查 ~/.local/share/fonts/ 下是否有字体文件

# ── CSS 构建 ──
# _build_css(): 构建 md→pdf 的完整 CSS 字符串
# _build_injected_css(): 构建 html→pdf 的注入 CSS 字符串

# ── Emoji / body 辅助函数 ──
# _process_body(): 后处理 md→pdf 的 HTML body
# _process_emoji(): 处理 html→pdf 的原始 HTML 文本

# ── 公开 API ──
# convert_markdown_to_pdf()
# convert_html_to_pdf()
# convert_pdf_to_text()
```

这种"常量 → 内部辅助函数 → 公开函数"的顺序是 Python 模块的经典组织方式，类似 C 语言的"先声明后调用"。注意 **公开函数全部在文件末尾**，读者翻到底部就能找到 API。

---

## 5. 命名约定

| 模式 | 示例 | 含义 |
|------|------|------|
| `_` 前缀 | `_check_fonts()`、`_EMOJI_RE`、`_build_css()` | **模块内部使用**，外部不应直接引用（Python 私有约定） |
| 大写下划线 | `_EMOJI_RE`、`_EMOJI_TEXT_MAP` | **模块级常量**，不应修改 |
| 无前缀 | `convert_markdown_to_pdf()` | **公开 API**，供外部 import |

Python 没有真正的"私有"机制（不像 Java 的 `private` 关键字），`_` 前缀是一种**君子协定**——意思是"这个函数/变量是这个模块的内部实现，你不应该直接从外面 import 它"。工具的提示（如 IDE 的代码补全）会相应地降低它的优先级。

---

## 6. 类型注解的颗粒度

所有函数签名都有类型注解，但**内部变量有些有、有些没有**。看例子：

```python
def _check_fonts() -> dict[str, Optional[str]]:      # ✅ 返回值有完整注解
    home = os.path.expanduser('~')                     # 内部变量无注解（类型明显）
    fonts = {
        'Noto Sans SC': os.path.join(home, '.local/share/fonts/NotoSansSC-Regular.ttf'),
        'Noto Emoji':   os.path.join(home, '.local/share/fonts/NotoEmoji-Regular.ttf'),
    }
    available: dict[str, Optional[str]] = {}           # ✅ 局部变量有注解（类型不明显）
    ...
    return available
```

原则：**公开函数签名必须有完整类型注解，内部局部变量在不明显时才加注解**。`available` 变量的类型是 `dict[str, Optional[str]]`，不容易一眼看出来，所以加了注解。而 `home` 是 `os.path.expanduser` 的返回值，显然是 `str`，就没加。

虽然 PLAN 第 0 节说"本项目暂不要求 mypy / ruff"，但代码依然写了类型注解——因为注解是**给人读的文档**，不只是给类型检查器用的。

---

## 7. format_mcp_server.py 的设计模式

```python
from mcp.server.fastmcp import FastMCP
from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text

mcp = FastMCP(name="Format Conversion", ...)

@mcp.tool()
def markdown_to_pdf(file_path: str, output_path: str = "") -> dict:
    try:
        # ... 调用 converter ...
        return {"status": "success", "output_path": ..., "size_bytes": ...}
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Conversion failed: {e}"}
```

三个工具函数的模式完全一致：

```
try:
    ① 处理参数（如自动推导 output_path）
    ② 调用 converter 函数
    ③ 组装成功返回字典
except FileNotFoundError:
    返回 {"error": ...}
except Exception:
    返回 {"error": "Conversion failed: ..."}
```

这个模式叫做"**薄适配器模式**"（thin adapter pattern）：MCP tool 函数只做两件事——参数适配和错误翻译，真正的业务逻辑全部委托给 `converter.py`。

注意 `FileNotFoundError` 和 `Exception` 分开捕获：前者是"用户给的路径错了"，错误信息明确告诉用户文件不存在；后者是"转换过程中出了意料之外的错误"，错误信息包含异常详情。如果不分开，用户看到 "Conversion failed: ..." 时不知道是路径错还是转换错。

---

## 8. CLI 脚本的薄 wrapper 模式

以 `md2pdf.py` 为例（47 行），重构后只剩：

```
#!/usr/bin/env python3
"""文档字符串"""
import logging, sys
from pathlib import Path
from converter import convert_markdown_to_pdf

def main():
    # 1. 解析 sys.argv（输入路径、输出路径）
    # 2. 设置 logging
    # 3. try: convert_markdown_to_pdf()
    #    except: print error + sys.exit(1)

if __name__ == '__main__':
    main()
```

原脚本 ~100 行，包含字体发现、CSS 构建、emoji 处理、WeasyPrint 调用。重构后只剩参数解析和错误处理。

**为什么保留 CLI 脚本而不删掉？** 因为仓库里可能存在依赖这些脚本的工作流（如 cron job、shell 脚本中的 `python md2pdf.py ...`）。保留它们并保持向后兼容，比删除或重命名更稳妥。

---

## 9. 动手任务

1. **画出模块依赖的反向图**：如果要给 `converter.py` 加一个 `convert_markdown_to_text()` 函数（剥离所有样式），需要改哪些文件？需要改哪些不？
2. **尝试把 flat layout 改为 src layout**：新建 `src/` 目录，把 `converter.py` 移进去，然后修改其他文件的 import 路径。看看需要改几处，体会 src layout 的成本。
3. **数数 LOC**：运行 `wc -l *.py` 统计每个文件的行数。确认 converter.py 确实占了总代码量的 ~70%。思考：如果哪天 converter.py 超过 1000 行，该怎么拆？
