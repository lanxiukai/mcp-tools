# 04 — 测试设计（在没有测试的情况下）

> 适合读者：想知道"项目没有测试文件，那怎么保证它工作？"的人。

## 坦诚声明

本项目**没有正式的测试文件**。PLAN.md 中原本有一个 T06（集成冒烟测试），但被用户指示跳过了。所以这份文档的重点不是"看测试代码"，而是**"在不写测试的情况下，这个项目是如何验证正确性的，这种做法有什么风险"**。

对于初学者，这是一个很好的反面教材 + 思考素材。

---

## 1. REVIEW 验证：人肉测试策略

虽然没有 pytest，但 REVIEW.md 记录了 reviewer 做的完整验证流程。以下是验证策略的全景：

### 验证金字塔（实际执行）

```
         ┌──────────┐
         │ MCP 协议  │ ← 未验证（T06 跳过）
         │ 端到端测试│
         ├──────────┤
         │ 函数级    │ ← 直接 import 验证
         │ 冒烟测试  │   用真实文件转换
         ├──────────┤
         │ 导入 +    │ ← 验证 import 无错误
         │ 实例化    │   FastMCP 实例可创建
         ├──────────┤
         │ 类型注解  │ ← 人工检查类型签名
         │ docstring │   3 个 tool 参数名一致
         └──────────┘
```

理论上应该有的全金字塔（对比）：

```
         ┌──────────┐
         │ 端到端     │ 用 MCP 客户端发送 stdio 请求，验证响应
         ├──────────┤
         │ 集成测试   │ 用实际文件调用 converter 函数，断言输出
         ├──────────┤
         │ 单元测试   │ 单独测试 _check_fonts、_process_body、
         │           │ _build_css 等内部函数
         ├──────────┤
         │ 类型检查   │ mypy --strict 确保类型安全
         └──────────┘
```

可以看到差距很大。下面逐层分析。

---

## 2. 第一层：导入验证

来源：REVIEW.md 第 2 节

```bash
$ PYTHONPATH=format-conversion /home/lanxiukai/mambaforge/envs/format-convert/bin/python \
  -c "from format_mcp_server import markdown_to_pdf, html_to_pdf, pdf_to_text; print('import OK')"
import OK
```

这一步验证了：
- `format_mcp_server.py` 可以正确 import
- `converter.py` 可以正确 import（因为 MCP server import 了 converter）
- 所有第三方库（WeasyPrint、markdown-it-py、PyMuPDF、FastMCP）都已安装且可导入

**这不是正式的测试，但比什么都不做强**。如果在 conda 环境里漏装了某个依赖，这一步就会报 ImportError。

---

## 3. 第二层：工具签名验证

来源：REVIEW.md 第 2 节

```bash
$ markdown_to_pdf: params=['file_path', 'output_path']
$ html_to_pdf: params=['file_path', 'output_path']
$ pdf_to_text: params=['file_path']
$ All 3 tools: callable + docstring OK
$ 3 @mcp.tool() decorators confirmed
```

这里 reviewer 用程序化方式检查了每个工具函数的参数名。为什么检查这个？因为 MCP 协议依赖于函数签名——如果参数名拼写错了（如 `fill_path` 而不是 `file_path`），agent 调用时传的参数就对不上。

在正式的测试框架中，这个检查应该这样写：

```python
# 伪代码：如果是 pytest，应该有这样一条测试
def test_tool_signatures():
    assert hasattr(markdown_to_pdf, '__wrapped__')  # @mcp.tool() 装饰后
    import inspect
    sig = inspect.signature(markdown_to_pdf)
    assert list(sig.parameters.keys()) == ['file_path', 'output_path']
```

但这里 reviewer 用命令行+肉眼检查替代了。**对于项目初期，这可以接受；但对于长期维护，这是不可靠的**——下一个人改参数名时可能忘记同步。

---

## 4. 第三层：功能冒烟测试

来源：REVIEW.md 第 2 节

reviewer 用真实文件做了 3 个转换：

```bash
# md→pdf
$ 1.0-睡眠与CPTSD管理.md → /tmp/review_test_md.pdf (146579 bytes)  ✅

# html→pdf
$ 2.0-情绪闪回13步管理法.html → /tmp/review_test_html.pdf (130870 bytes)  ✅

# pdf→text
$ 体检报告-2026.04.24.pdf → 24676 chars, 27 pages, 含"体检" ✅
```

这些验证检查了什么：

| 测试 | 检查点 | 缺陷 |
|------|--------|------|
| md→pdf | 输出文件非空（146KB） | 没检查 PDF 内容是否正确——可能生成了一页空白或者乱码，但文件大小仍然 >0 |
| html→pdf | 输出文件非空（130KB） | 同上。没有"期望输出"做对比 |
| pdf→text | 返回的文本包含中文关键词"体检" | 最好的一个检查——至少确认了文本里有预期的中文内容 |

**注意 md→pdf 和 html→pdf 的验证缺陷**：只检查了"文件存在且 >0 byte"，没检查 PDF 内容是否正确。比如如果 markdown-it-py 配置错了，输出 PDF 可能全是原始 Markdown 文本而不是渲染后的 HTML——但文件大小仍然 >0。

一个稍微好一点的检查方式（如果当时有时间）：

```python
# 更好的冒烟测试：至少检查 PDF 里包含预期字符串
import fitz
doc = fitz.open("/tmp/review_test_md.pdf")
text = "".join(page.get_text() for page in doc)
assert "睡眠" in text  # 确认 PDF 里真的有中文内容
assert "CPTSD" in text  # 确认英文内容也正确
doc.close()
```

---

## 5. 边缘情况测试（被忽略的）

**正式项目应该测试但本项目没测的场景**：

| 场景 | 风险 | 预计写法 |
|------|------|---------|
| 输入文件不存在 | MCP server 已处理，返回 `{"error": ...}` | 用 pytest 的 `tmp_path` 创建一个不存在的路径 |
| 字体文件缺失 | 降级为 sans-serif，不崩溃 | 临时重命名字体目录，验证 logger.warning 被调用 |
| 输入文件为空 | WeasyPrint 能否处理空内容？ | 创建一个空 `.md` 文件，验证 output 存在但可能只有 1 页 |
| emoji 字体缺失但 emoji 存在 | 降级为文字标签 | 模拟字体缺失场景，验证输出文字而非 emoji |
| 特殊字符 | Markdown 中含 ````、`<script>` 等 | 用包含特殊字符的输入，验证输出不报错 |
| 大文件 | 600 页 PDF 提取文本 | 用 600 页 PDF 调用 `convert_pdf_to_text`，检查不超时 |

---

## 6. REVIEW 验证中的"等人测试"

一个有趣的现象：REVIEW.md 第 2 节的验证输出是**运行结果直接粘贴**的：

```bash
$ # ── T04 markdown_to_pdf: 1.0-睡眠与CPTSD管理.md → /tmp/review_test_md.pdf ──
All fonts found (Noto Sans SC + Noto Emoji)
Done: /tmp/review_test_md.pdf (146579 bytes)
{'status': 'success', 'output_path': '/tmp/review_test_md.pdf', 'size_bytes': 146579}  ✅
```

这是 reviewer **真正运行了这些命令然后把输出贴进文档**，而不是 reviewer 猜测的结果。这种"可执行文档"的做法在开源项目中很常见——文档里的每段 shell 输出都是真实运行过的，不是手打的。

但这种方式的问题是：**没法自动化重跑**。下次修改代码后，reviewer 需要重新执行所有这些命令，再手动比对结果。

---

## 7. 如果给这个项目加测试，怎么加？

假设你要加正式的 pytest 测试，推荐的策略：

### 单元测试（测试内部函数）

```python
# test_converter.py — 测试内部函数

def test_emoji_re_matches_star():
    """_EMOJI_RE 应该匹配 ⭐ 但不匹配 普通文字"""
    assert _EMOJI_RE.search('⭐') is not None
    assert _EMOJI_RE.search('hello') is None

def test_emoji_text_map_contains_key():
    """所有 _EMOJI_RE 能匹配的字符都应该有映射或 fallback"""
    # 这个测试确保 emoji 覆盖不会漏
    pass

def test_build_css_contains_font_face():
    css = _build_fake_fonts()  # 模拟字体存在
    assert '@font-face' in css
    assert 'Noto Sans SC' in css
```

### 集成测试（测试公开函数）

```python
# test_converter.py — 测试公开函数

def test_convert_markdown_to_pdf_basic(tmp_path):
    md_file = tmp_path / "test.md"
    md_file.write_text("# Hello\n\nWorld", encoding='utf-8')
    pdf_file = tmp_path / "test.pdf"

    convert_markdown_to_pdf(str(md_file), str(pdf_file))

    assert pdf_file.exists()
    assert pdf_file.stat().st_size > 0

    # 额外验证：检查 PDF 里真的有内容
    doc = fitz.open(str(pdf_file))
    text = "".join(page.get_text() for page in doc)
    assert "Hello" in text
    doc.close()
```

### 参数化测试（测试多种输入）

```python
@pytest.mark.parametrize("md_content,expected_text", [
    ("# A", "A"),
    ("**bold**", "bold"),
    ("- item", "item"),
    ("```code```", "code"),
])
def test_convert_markdown_to_pdf_content(tmp_path, md_content, expected_text):
    """不同 Markdown 语法都能正确渲染到 PDF 文本中"""
    ...
```

### 模拟字体缺失

```python
def test_emoji_fallback_when_font_missing(tmp_path, monkeypatch):
    """当 Noto Emoji 缺失时，emoji 被替换为文字标签"""
    # monkeypatch 让 _check_fonts 返回"字体缺失"
    monkeypatch.setattr('converter._check_fonts', lambda: {
        'Noto Sans SC': '/fake/path',
        'Noto Emoji': None,
    })
    md_file = tmp_path / "test.md"
    md_file.write_text("⭐ test", encoding='utf-8')

    convert_markdown_to_pdf(str(md_file), str(tmp_path / "test.pdf"))
    # 验证输出 PDF 中 ⭐ 被替换为 ★
```

---

## 8. 一个真实的测试陷阱（来自 REVIEW）

REVIEW.md 的 NH-03 发现 `convert_pdf_to_text` 中 `doc.close()` 不在 `try/finally` 内。如果当时有单元测试，这种 bug 在写代码时就能暴露：

```python
# 一个触发这个 bug 的场景
def test_pdf_to_text_closes_doc(tmp_path):
    """验证转换完成后文件句柄被释放"""
    pdf_file = tmp_path / "test.pdf"
    # 生成一个极小 PDF
    import fitz
    doc = fitz.open()
    doc.insert_page(doc.new_page())
    doc.save(str(pdf_file))
    doc.close()

    result = convert_pdf_to_text(str(pdf_file))

    # 在 Windows 上，如果 doc 没 close，删文件会失败
    pdf_file.unlink()  # 如果 doc 没close，这里可能报 PermissionError
```

因为 PyMuPDF 在 Linux 上不会锁文件，这个 bug 在 Linux 上不会导致可见的故障——但换到 Windows 上就会。**这就是测试的价值：在故障影响用户之前发现它。**

---

## 9. 从本项目的"无测试"状态能学到什么

### 什么时候"没有测试"是可以接受的？

- **一次性脚本**：用完即弃，不维护
- **原型验证**：先跑通再说，后续再补测试
- **个人工具**：只有自己用，出了问题马上能修

### 什么时候必须有测试？

- **多人协作的项目**：你不知道别人改了什么
- **对外发布的库**：用户依赖你的 API 稳定性
- **生产环境关键路径**：出问题 = 丢钱/丢数据
- **MCP 服务**：agent 自动调用，没有人工验证环节

本项目介于两者之间——它被 AI agent 调用，但没有正式测试（T06 跳过了）。这个风险是存在的，但考虑到代码量极小（<400 LOC）、逻辑相对简单，风险可控。

**给你的建议**：如果你要在本项目的基础上加功能，**第一件事不是写新功能，而是补测试**。没有测试的代码就像没有安全绳的攀岩——你可能很稳，但摔一次就完了。

---

## 10. 动手任务

1. **写一条真正的 pytest**：在 `format-conversion/` 下创建一个 `test_converter.py`，至少写一个测试函数测试 `convert_pdf_to_text` 返回字符串而非异常。
2. **模拟字体缺失测试**：用 `monkeypatch` 或临时重命名字体文件，验证 `_check_fonts()` 在字体缺失时返回 `None` 而非抛出异常。
3. **读 REVIEW.md 的验证命令**：选一条 reviewer 执行的命令，把它改写成一条 `assert` 语句。例如把 `grep "born-digital PDF only"` 改成 Python 代码 `assert "born-digital" in pdf_to_text.__doc__`。
