# 00 — Format Conversion 项目学习总览

## 项目是什么

**Format Conversion** 是一个文档格式转换 MCP 服务（MCP = Model Context Protocol，一种让 AI agent 调用本地工具的协议）。它提供 3 个工具，全部纯 CPU 操作、同步执行、无需后台服务：

| 工具 | 做什么 | 用什么引擎 |
|------|--------|-----------|
| `markdown_to_pdf` | 把 `.md` 文件转为排好版的 PDF | markdown-it-py + WeasyPrint |
| `html_to_pdf` | 把 `.html` 文件转为 PDF，保留原样式 | WeasyPrint |
| `pdf_to_text` | 从 born-digital PDF 提取纯文本（非扫描件） | PyMuPDF (fitz) |

项目位置：`/home/lanxiukai/project/mcp-tools/format-conversion/`

---

## 架构总览

```
                    ┌──────────────────────┐
                    │   AI Agent (OpenCode) │
                    │   通过 MCP stdio 调用  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │ format_mcp_server.py │  ← FastMCP 入口
                    │  3 个 @mcp.tool()    │    薄适配器
                    └──────────┬───────────┘
                               │ import
                    ┌──────────▼───────────┐
                    │    converter.py      │  ← 核心转换模块
                    │                      │    所有逻辑在这里
                    └──┬──────┬──────┬─────┘
                       │      │      │
              ┌────────▼┐ ┌──▼───┐ ┌▼────────┐
              │markdown │ │Weasy │ │ PyMuPDF │
              │-it-py   │ │Print │ │ (fitz)  │
              │(md→html)│ │→PDF  │ │PDF→text │
              └─────────┘ └──────┘ └─────────┘
                       │      │
                       └──┬───┘
                          │
               ┌──────────▼──────────┐
               │  字体文件系统         │
               │ ~/.local/share/fonts │
               │ Noto Sans SC        │
               │ Noto Emoji          │
               └─────────────────────┘

同时，CLI 脚本也共享 converter.py：
  md2pdf.py ──import──► converter.convert_markdown_to_pdf()
  html2pdf.py ──import──► converter.convert_html_to_pdf()
```

**与仓库中 ASR/OCR 服务的最大不同**：ASR/OCR 用"MCP → HTTP → GPU 后端"架构，而 format-conversion 是**纯 CPU 同步**——工具函数直接 import 并调用，无网络层、无后台服务、无 health check。

---

## 学到什么

通过这个项目，你可以学到：

1. **多阶段数据变换管线**：Markdown 文本经过 emoji 预处理 → markdown-it-py 解析 → HTML 后处理 → CSS 装配 → WeasyPrint 渲染，最终变成 PDF。每一步做什么、顺序为什么不换——体会"数据处理管线"的设计思路。

2. **从设计决策看工程取舍**：为什么选 WeasyPrint 不选 wkhtmltopdf？为什么用单模块不用包？为什么 emoji 处理在 md→pdf 和 html→pdf 中不同？每个决策背后都有可追溯的推理链。

3. **资源管理与异常安全**：一个 `try/finally` 放错位置导致的文件句柄泄漏（Bug 1），展示了"资源获取后必须确保释放"的编程纪律。

4. **MCP 服务与 CLI 的共享架构**：同一个 `converter.py` 被 MCP server 和 CLI 脚本同时引用——学会写"与接口无关"的核心逻辑。

5. **日志 vs print、异常分层、类型注解**：虽然是不到 500 行的小项目，但代码中体现了良好的工程习惯。

---

## 推荐阅读顺序

| 如果你最关心… | 从这篇开始 |
|---|---|
| **整体印象，想知道项目是干什么的** | `00-overview.md`（当前）—— 总览，3 个工具概览，架构图 |
| **设计决策背后的推理链** | `01-design-thinking.md`—— 带你走过 planner 的每一步取舍，每个决策用"原本想怎么做 → 会出什么问题 → 所以怎么做"三步展开 |
| **Markdown → PDF 每一步的细节** | `02-algorithm-deep-dive.md`—— 逐阶段拆解 6 步变换管线，附"手算演示"：输入文本在每一步怎么变化 |
| **代码文件怎么划分、依赖关系** | `03-code-structure.md`—— 目录树、模块依赖图、flat layout vs src layout 的决策 |
| **没有测试文件但项目怎么验证的** | `04-testing-philosophy.md`—— REVIEW 的验证层次分析，以及"如果写测试应该怎么写"的建议 |
| **代码风格和工程习惯** | `05-engineering-practices.md`—— 错误处理哲学（三区分异常）、logger 替代 print、import 顺序约定 |
| **历史上踩过的坑（4 个 bug 全解剖）** | `06-pitfalls-and-debugging.md`—— 每个 bug 用"四段式"展示：错误代码 → 数据推演根因 → 修复代码 → 避坑规则 |
| **如何扩展这个项目** | `07-extension-ideas.md`—— 从"加测试"到"加 DOCX 支持"的 7 个扩展思路，附难度评估 |

---

## 关键数字

| 指标 | 数值 |
|------|------|
| Python 源码文件 | 5（`__init__.py` + `converter.py` + `format_mcp_server.py` + `md2pdf.py` + `html2pdf.py`） |
| 核心代码总行数 | ~480 LOC（仅 `.py` 文件的逻辑代码） |
| 其中 `converter.py` | ~440 行（占总代码 ~90%） |
| 第三方依赖 | 4（weasyprint、markdown-it-py、pymupdf、mcp） |
| MCP 工具数 | 3 |
| CLI 脚本数 | 2（md2pdf.py、html2pdf.py） |
| conda 环境 | `format-convert`（Python 3.12） |
| 测试文件 | 0 个正式测试（T06 被用户指示跳过；REVIEW 靠手工命令验证） |
| 字体依赖 | Noto Sans SC + Noto Emoji（可选，缺失时降级） |
| 执行模式 | 纯 CPU、同步、无后台服务 |

---

## 如何阅读本项目

1. **先跑起来**：激活 conda 环境 → `python format_mcp_server.py`（会阻塞等待 MCP 输入）或者 `python md2pdf.py some.md out.pdf`
2. **读 `00-overview.md`**：了解全貌
3. **按兴趣选 01-07**：参考上面的推荐阅读顺序表格
4. **边读边动手**：每篇文档末尾都有"动手任务"，建议每个任务花 5-15 分钟实践
5. **最后改代码**：读完 01-07 后，试着做 `07-extension-ideas.md` 里的扩展

---

## 动手任务

1. **跑第一个转换**：找一个本地的 `.md` 文件，用 `python md2pdf.py <你的文件.md>` 转成 PDF，用 PDF 阅读器打开看看排版效果。
2. **数数文件行数**：在 `format-conversion/` 目录下运行 `wc -l *.py`，看实际行数是否和上方"关键数字"表一致。
3. **对照阅读**：打开 `01-design-thinking.md` 和 `PLAN.md` 逐节对比，看看"学习材料讲的设计决策"和"PLAN 原文的表格"有什么异同。
