# Review Report

> 批次：T01, T02, T03
> 审阅时间：2026-05-22 15:25
> 审阅者：reviewer
> 总体结论：**APPROVED**

## 1. 验收标准核对
| Task | 标准 | 状态 | 证据 |
|---|---|---|---|
| T01 | `mamba env list \| grep format-convert` 输出该环境 | ✅ PASS | 见下方验证输出 |
| T01 | `import weasyprint, markdown_it, fitz, mcp; print('OK')` 输出 `OK` | ✅ PASS | 见下方验证输出 |
| T01 | `docs/conda-environments.md` 中 `format-convert` 已列出 | ✅ PASS | 行 25 包含 `format-convert \| 3.12 \| 文档格式转换` |
| T02 | `from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text` 无 import 错误 | ✅ PASS | 输出 `imports OK` |
| T02 | `convert_markdown_to_pdf()` 生成有效 PDF，文件大小 > 0 | ✅ PASS | `1.0-睡眠与CPTSD管理.md` → 146,583 bytes |
| T02 | `convert_html_to_pdf()` 生成有效 PDF，文件大小 > 0 | ✅ PASS | `1.0-CBT-I核心规则-CPTSD.html` → 227,297 bytes |
| T02 | `convert_pdf_to_text()` 返回非空字符串，包含可读文字 | ✅ PASS | `体检报告-2026.04.24.pdf` → 24,676 chars，含"体检"关键词 |
| T02 | 字体缺失时 emoji 降级为文字标签，不抛异常 | ✅ PASS | `convert_markdown_to_pdf` 始终在 markdown 解析前执行 `_EMOJI_TEXT_MAP` 替换（符合 PLAN 设计）；`convert_html_to_pdf` 中 `_process_emoji()` 在无 emoji 字体时分叉到文本替换 |
| T02 | 输入文件不存在时抛出 `FileNotFoundError`（带明确路径信息） | ✅ PASS | 3 个函数均正确抛出，消息含完整路径 |
| T02 | 函数不打印到 stdout | ✅ PASS | `converter.py` 零 `print()` 调用，仅用 `logging` |
| T03 | `md2pdf.py -h` 输出用法 | ✅ PASS | 输出 Usage + `__doc__` |
| T03 | `md2pdf.py <test.md> /tmp/out.pdf` 生成有效 PDF | ✅ PASS | `0.0-个人基线.md` → 208,287 bytes |
| T03 | `html2pdf.py <test.html> /tmp/out.pdf` 生成有效 PDF | ✅ PASS | `2.0-情绪闪回13步管理法.html` → 130,868 bytes |
| T03 | 不传参数时输出用法信息并 `sys.exit(0)` | ✅ PASS | 两个脚本均 exit code 0 |
| T03 | 输出信息中包含字体检测结果（来自 converter 内部） | ✅ PASS | CLI 输出含 `INFO: All fonts found (Noto Sans SC + Noto Emoji)` |

## 2. 客观验证结果
```
$ /home/lanxiukai/mambaforge/envs/format-convert/bin/python -c "import sys; print(sys.executable)"
/home/lanxiukai/mambaforge/envs/format-convert/bin/python

$ /home/lanxiukai/mambaforge/envs/format-convert/bin/python -c "import weasyprint, markdown_it, fitz, mcp; print('OK')"
OK

$ mamba env list | grep format-convert
  format-convert          /home/lanxiukai/mambaforge/envs/format-convert

$ /home/lanxiukai/mambaforge/envs/format-convert/bin/python -c "
import sys; sys.path.insert(0, 'format-conversion')
from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text
print('imports OK')
"
imports OK

$ # T02 md→pdf: 1.0-睡眠与CPTSD管理.md → /tmp/reviewer_test_md.pdf
$ # → 146,583 bytes, WeasyPrint 正常光栅化

$ # T02 html→pdf: 1.0-CBT-I核心规则-CPTSD.html → /tmp/reviewer_test_html.pdf
$ # → 227,297 bytes

$ # T02 pdf→text: 体检报告-2026.04.24.pdf → 24,676 chars, contains "体检"

$ # T02 FileNotFoundError: all 3 functions raise with clean message

$ # T02 no stdout from converter: verified (converter.py has 0 print() calls)

$ # T03 CLI -h: both scripts print usage
$ # T03 CLI no-args: both exit 0 with usage
$ # T03 CLI conversions: both produce valid non-empty PDFs
$ # T03 CLI font detection: "All fonts found (Noto Sans SC + Noto Emoji)" in output

本项目不要求 mypy / ruff（PLAN 第 0 节注明"代码量小，依赖复杂"）。
```

## 3. Must-Fix（必须修复才能进入下一批）

无。

## 4. Nice-to-Have（建议但不阻塞）

### NH-01: `convert_pdf_to_text` 缺少日志
- 位置：`converter.py:405-432`
- 建议：添加 `logger.info("Extracting text from: %s", source_path)` 和完成后的日志（与另外两个转换函数风格一致）
- 理由：统一模块内 3 个公开函数的日志行为，方便调试

### NH-02: `_build_css` 中的死分支
- 位置：`converter.py:80`
- 代码：`body_stack.insert(0, "'DejaVu Sans'" if fonts_available['Noto Sans SC'] else "'DejaVu Sans'")`
- 建议：简化为 `body_stack.insert(0, "'DejaVu Sans'")`（条件分支两侧完全相同）
- 理由：消除冗余代码，但不影响功能

### NH-03: `convert_pdf_to_text` 缺少 `try-finally` 保护 `fitz.open()`
- 位置：`converter.py:425-432`
- 建议：用 `with fitz.open(source_path) as doc:` 或 `try-finally` 确保 `doc.close()` 在异常时也被调用
- 理由：极端情况下（如页迭代中途异常）可能泄露文件句柄；当前 `doc.close()` 不会被调用

## 5. 安全 / 性能 / 可维护性观察

无重大发现。所有转换均在 10 秒内完成（纯 CPU，符合预期）。

`docs/conda-environments.md` 中 `format-convert` 条目已存在（行 25、116-161），但该文件未出现在本次 commit 的 `git diff` 中——可能是此前已提交或环境文档的提交流程独立于本批次。该条目内容符合 PLAN 要求，不影响验收。

## 6. 给 builder 的下一步指令

1. 本批次通过，builder 可进入下一批 task（T04: `format_mcp_server.py`）。
2. Nice-to-Have（NH-01~NH-03）可推迟到所有 task 完成后批处理，或在编码 T04/T05/T06 时顺手修复。

---

本批次通过，builder 可进入下一批 task。
