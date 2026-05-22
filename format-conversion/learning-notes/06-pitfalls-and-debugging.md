# 06 — 常见 Bug 与避坑

> 适合读者：想知道"这个项目踩过哪些坑、怎么避免同样错误"的人。

本文档解剖 4 个具体的 bug/隐患，每一个都用"四段式"结构展示：**错误版本 → 根因分析（附数据演示）→ 修复版本 → 避坑方法**。

---

## Bug 1：`convert_pdf_to_text` 文件句柄泄漏

> 来源：REVIEW.md NH-03

### 1.1 Bug 的版本

原始代码（`converter.py`，在 NH-03 修复前）：

```python
def convert_pdf_to_text(source_path: str) -> str:
    pdf_path = Path(source_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {source_path}")

    doc = fitz.open(source_path)
    pages_text = []
    for page in doc:
        text = page.get_text()
        pages_text.append(text)
    doc.close()  # ← 如果 for 循环中抛异常，这一行永远不会执行

    result = '\n'.join(pages_text)
    return result
```

### 1.2 根因分析

问题出在 `doc.close()` 的位置——它在 `try` 块外面，而不是在 `finally` 块里。

```text
执行流程：

main.py 调用 convert_pdf_to_text("report.pdf")
    │
    ▼
doc = fitz.open("report.pdf")  ← 打开文件，获得句柄
    │
    ▼
for page in doc:               ← 逐页迭代
    │                             如果某页 PDF 数据损坏，
    │                             page.get_text() 抛出异常
    ▼
    ✗ 异常发生！执行立即跳转到调用者的 except
    │
    ▼
    doc.close()  ★ 这一行被跳过了！★
    │
    ▼
文件句柄泄漏。在 Linux 上问题不大（进程退出时 OS 回收），
但在 Windows 上文件会被锁定，无法删除/移动。
```

**为什么在 Linux 上 issue 没被发现？** 因为 Linux 的 POSIX 语义允许其他进程读/删一个已被打开的文件（只要句柄还在，文件数据不会被真正删除）。而在 Windows 上，打开的文件会被锁定，其他进程不能删除或重命名。

这也是一个很好的例子，说明**"在测试环境中没出问题"不等于"代码没有 bug"**。

### 1.3 修复的版本

```python
def convert_pdf_to_text(source_path: str) -> str:
    pdf_path = Path(source_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {source_path}")

    logger.info("Extracting text from: %s", source_path)
    doc = fitz.open(source_path)
    try:                              # ← 新增 try
        pages_text: list[str] = []
        for page in doc:
            text = page.get_text()
            pages_text.append(text)
    finally:                          # ← finally 块
        doc.close()                   # ← 无论异常与否都会执行

    result = '\n'.join(pages_text)
    logger.info("Extracted %d chars from %d pages", len(result), len(pages_text))
    return result
```

`try...finally` 保证：无论 `for page in doc` 循环体是否抛出异常，`doc.close()` 都一定会执行。

同时注意到还加了 `logger.info`（这是 NH-01 的修复——也是同一次 review 发现的问题，见下文 Bug 2）。

### 1.4 避坑方法

**通用规则：任何涉及资源获取（文件句柄、网络连接、数据库游标）的代码，释放操作必须放在 `finally` 块中。**

在 Python 中，还有更简洁的方式——使用 `with` 语句（上下文管理器）：

```python
# 更 Pythonic 的方式 —— 如果 fitz 支持上下文管理器
with fitz.open(source_path) as doc:
    pages_text = [page.get_text() for page in doc]
result = '\n'.join(pages_text)
```

`with` 块结束时自动调用 `doc.close()`，不需要显式的 `try/finally`。但要看 PyMuPDF 的 `fitz.open` 是否实现了 `__enter__` / `__exit__` 方法（建议使用前检查文档）。

如果一定要用 `try/finally`，记住格式口诀：**获取资源后立即 try，重要操作后立即 finally**。

---

## Bug 2：`convert_pdf_to_text` 缺少日志

> 来源：REVIEW.md NH-01

### 2.1 Bug 的版本

修复前的 `convert_pdf_to_text`（converter.py，NH-01 修复前）：

```python
def convert_pdf_to_text(source_path: str) -> str:
    pdf_path = Path(source_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {source_path}")

    doc = fitz.open(source_path)
    try:
        pages_text = [page.get_text() for page in doc]
    finally:
        doc.close()

    result = '\n'.join(pages_text)
    return result
```

对比同一文件中其他两个函数：

```python
def convert_markdown_to_pdf(...):
    logger.info("Converting: %s → %s", md_path, out_path)       # ✅ 有开始日志
    HTML(string=html).write_pdf(str(out_path))
    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)  # ✅ 有结束日志

def convert_html_to_pdf(...):
    logger.info("Converting: %s → %s", html_path, out_path)    # ✅
    HTML(string=html_text, ...).write_pdf(str(out_path))
    logger.info("Done: %s (%s bytes)", out_path, out_path.stat().st_size)  # ✅

def convert_pdf_to_text(...):
    # ★ 没有任何 logger.info()
    ...
    return result
```

### 2.2 根因分析

Builder 在写 `convert_pdf_to_text` 时，只关注了功能实现（打开 PDF → 提取文本 → 返回），**忘记了和其他两个函数保持一致的日志风格**。这导致：

- 调用者不知道 PDF 转换何时开始、何时结束
- 调试时无法确认 `pdf_to_text` 是否被执行了（没有日志输出）
- 无法日志中查看提取了多少字符（性能/用量信息缺失）

这是一个典型的**"多函数不一致"bug**——不是单个函数功能错了，而是多个函数之间的外部行为不一致。

数据演示对比：

```
# convert_markdown_to_pdf 执行时的日志：
INFO: Converting: /tmp/test.md → /tmp/test.pdf
INFO: Done: /tmp/test.pdf (150000 bytes)

# convert_pdf_to_text 执行时的日志（修复前）：
（无任何输出）

# convert_pdf_to_text 执行时的日志（修复后）：
INFO: Extracting text from: /tmp/test.pdf
INFO: Extracted 50000 chars from 27 pages
```

### 2.3 修复的版本

```python
def convert_pdf_to_text(source_path: str) -> str:
    ...
    logger.info("Extracting text from: %s", source_path)   # ← 新增
    doc = fitz.open(source_path)
    try:
        pages_text: list[str] = []
        for page in doc:
            text = page.get_text()
            pages_text.append(text)
    finally:
        doc.close()

    result = '\n'.join(pages_text)
    logger.info("Extracted %d chars from %d pages", len(result), len(pages_text))  # ← 新增
    return result
```

修复的不仅是"加两行日志"，而是**让 `convert_pdf_to_text` 的外部行为和另两个函数保持一致**：都是"开始时有日志、完成时有日志、包含关键量（字符数/页数/字节数）"。

### 2.4 避坑方法

**通用规则：当你在一个类/模块里加一个和既有函数类似的新函数时，先检查既有函数的外部行为模式（日志、返回值格式、异常类型），然后严格复制这个模式。**

具体做法：
1. 找出同类函数的"行为模板"（比如 `convert_markdown_to_pdf` 和 `convert_html_to_pdf` 的日志模式）
2. 列出模板的共同点：开始日志、结束日志、关键数量信息
3. 新函数严格按模板写

---

## Bug 3：`_build_css` 中的死分支（redundant code）

> 来源：REVIEW.md NH-02

### 3.1 Bug 的版本

修复前 `_build_css()` 中的相关代码（converter.py:71-82）：

```python
body_stack = ['sans-serif']

if fonts_available['Noto Sans SC']:
    font_rules.append(f"""@font-face {{
    font-family: 'Noto Sans SC';
    src: url('file://{fonts_available["Noto Sans SC"]}') format('truetype');
}}""")
    body_stack.insert(0, "'Noto Sans SC'")

if fonts_available['DejaVu Sans']:         # ★ 有问题的分支
    body_stack.insert(0, "'DejaVu Sans'")
```

### 3.2 根因分析

问题：第 2 个 `if` 检查 `fonts_available['DejaVu Sans']`，但 `fonts_available` 这个字典只包含两个键：`'Noto Sans SC'` 和 `'Noto Emoji'`（来自 `_check_fonts()` 的返回值）。

字典内容：

```python
# _check_fonts() 返回
{
    'Noto Sans SC': '/home/.../NotoSansSC-Regular.ttf',  # 或 None
    'Noto Emoji': '/home/.../NotoEmoji-Regular.ttf',     # 或 None
}
```

所以 `fonts_available['DejaVu Sans']` 永远会抛出 `KeyError`——除非根本不会执行到这里。

**但代码在项目中跑通了！** 为什么？因为 reviewer 发现时，实际的执行路径是这样的：

```python
body_stack = ['sans-serif']

if fonts_available['Noto Sans SC']:   # ✅ 存在，进入
    ...
    body_stack.insert(0, "'Noto Sans SC'")   # body_stack = ["'Noto Sans SC'", 'sans-serif']

if fonts_available['DejaVu Sans']:   # ★ KeyError! 但在实际测试中...
```

等等，如果 `fonts_available['DejaVu Sans']` 会 KeyError，那代码怎么跑通的？

**答案：根本跑不通。** 这意味着 reviewer 发现的时候，这段代码从未被实际执行到——或者说，测试中用的输入文件恰好没有触发这个代码路径。

不，再仔细看。`_build_css` 是在 `convert_markdown_to_pdf` 中被调用的。`convert_markdown_to_pdf` 每次都被测试了（REVIEW 里验证了转换成功）。所以 `_build_css` 确实被执行了。

那为什么没报 `KeyError`？因为 `fonts_available['DejaVu Sans']` 这段代码可能是在一个更早的版本中，当时 `_check_fonts` 返回的字典包含 `'DejaVu Sans'` 键。然后 `_check_fonts` 被修改了，但 `_build_css` 忘了同步更新。

根据 REVIEW 的描述，修复方式是把这一行简化掉——直接插入 `'DejaVu Sans'`，不检查字典：

```python
body_stack.insert(0, "'DejaVu Sans'")  # 总是添加，不查字典
```

因为 DejaVu Sans 是 Linux 系统标配的无衬线字体，WeasyPrint 肯定能找到它。

### 3.3 修复的版本

```python
body_stack = ['sans-serif']

if fonts_available['Noto Sans SC']:
    font_rules.append(...)
    body_stack.insert(0, "'Noto Sans SC'")

body_stack.insert(0, "'DejaVu Sans'")   # ← 直接插入，不查字典
```

修复后，`body_stack` 的构建过程是：

1. 初始：`['sans-serif']`
2. 总是插入 `'DejaVu Sans'`：`["'DejaVu Sans'", 'sans-serif']`
3. 如果有 Noto Sans SC，插入到最前面：`["'Noto Sans SC'", "'DejaVu Sans'", 'sans-serif']`

最终字体栈：`'Noto Sans SC', 'DejaVu Sans', sans-serif`——按优先级排列。

### 3.4 避坑方法

**通用规则：字典的键集合发生变化时，所有访问该字典的地方都要同步更新。**

具体做法：
1. 如果用一个 `dict` 作为数据和配置的载体，**永远不要假设里面有哪些键**——要么 `get()` 带默认值，要么在文档/类型中明确声明键集合
2. 在这个项目中，`_check_fonts()` 返回的字典键集合是 `{'Noto Sans SC', 'Noto Emoji'}`，其他地方引用时应该用 `fonts_available.get('DejaVu Sans', None)` 或者注释说明"这个键不存在"

一个更好的设计是把字体配置定义为一个 `TypedDict` 或 `@dataclass`，让类型系统帮助检查：

```python
from typing import TypedDict, Optional

class FontDict(TypedDict):
    noto_sans_sc: Optional[str]
    noto_emoji: Optional[str]
```

这样 IDE 和 mypy 就能告诉你 "`DejaVu Sans` 不是这个 dict 的键"。

---

## Bug 4：潜在的 MCP server 二次打开 PDF

> 来源：REVIEW.md 第 5 节 NH-01（安全/性能/可维护性观察）

### 4.1 问题代码

`format_mcp_server.py:97-105` 中 `pdf_to_text` 函数：

```python
@mcp.tool()
def pdf_to_text(file_path: str) -> dict:
    try:
        import fitz

        text = convert_pdf_to_text(file_path)    # ← 第一次 open
        doc = fitz.open(file_path)                 # ← 第二次 open（只为获取 page_count）
        try:
            page_count = len(doc)
        finally:
            doc.close()

        return {
            "text": text,
            "page_count": page_count,
            "size_chars": len(text),
        }
    except ...
```

问题：`convert_pdf_to_text(file_path)` 内部已经 `fitz.open` 了一次，提取完文本后 `close` 了。紧接着 `fitz.open(file_path)` 又打开一次，只为了拿 `page_count`（`len(doc)`）。

### 4.2 根因分析

这个问题的根源在于**函数签名的设计选择**：`convert_pdf_to_text(source_path: str) -> str` 返回的是纯文本字符串，不包含页数信息。而 MCP tool 的返回格式需要 `page_count`。

```
convert_pdf_to_text 返回: "第1页内容...第2页内容..."（纯字符串）
MCP tool 需要: {"text": "...", "page_count": 27, "size_chars": 50000}
```

所以 MCP tool 不得不再次打开 PDF 来获取页数。

**为什么不修改 `convert_pdf_to_text` 返回 `(text, page_count)` 或者一个 dict？**

PLAN.md 第 6 节 T02 设计要点 5 明确说了："返回纯文本字符串（不返回 dict，与另外两个函数的签名风格不同是合理的——PDF→text 不需要输出路径）"。**设计上有意保持 `convert_pdf_to_text` 返回 `str`**，因为它是"提取文本"操作，不是"文件转换"操作，语义上就应该返回字符串。

所以这是**设计权衡导致的代码重复**——不是传统意义上的 bug，而是一个"已知的性能浪费"。

### 4.3 可能的改进方案

既然不能改 `convert_pdf_to_text` 的返回类型（因为设计决策），MCP tool 里的二次打开也就能接受了。REVIEW 的结论是"性能开销可忽略（PyMuPDF open 是轻量操作）"。

如果一定要优化，可以从 `convert_pdf_to_text` 内部"顺带"获取页数，通过一个全局变量或回调传递出来——但这样会破坏函数的纯净性，反而更差。

更好的方案：**对性能敏感时，把 `page_count` 信息缓存到模块级变量中，避免重复 open**。

```python
# 不推荐——过度设计。仅供思考
_PAGE_CACHE: dict[str, int] = {}

def convert_pdf_to_text(source_path: str) -> str:
    ...
    doc = fitz.open(source_path)
    try:
        _PAGE_CACHE[source_path] = len(doc)  # 缓存页数
        ...
    finally:
        doc.close()

# MCP tool 中
page_count = _PAGE_CACHE.pop(file_path, 0)  # 取缓存
```

但这个方案不是线程安全的，也不是必要的——`fitz.open` 一个几十页的 PDF 只需几毫秒。

### 4.4 避坑方法

**通用规则：当两个函数调用之间有隐含的共享数据需求时（如 A 需要 B 的结果 + B 的副产品），有三种解决方案：**

| 方案 | 适用场景 | 成本 |
|------|---------|------|
| ① 改 B 的返回类型，包含副产品 | B 的调用者都需要副产品 | 破坏 B 的向后兼容 |
| ② 调用者自己再获取一次 | 副产品获取成本很低 | 代码重复，但有性能开销 |
| ③ 加缓存/全局变量 | 副产品获取成本高、需要共享 | 增加复杂度，线程不安全 |

本项目选了方案 ②，原因是：**fitz.open 的成本极低（几毫秒），不值得为优化而引入复杂度**。这个判断是正确的——不要为了省几毫秒而写出更复杂的代码。

---

## 5. 典型用户预期陷阱

### 5.1 输出路径不传时

MCP tool 的 `output_path` 参数默认为空字符串：

```python
def markdown_to_pdf(file_path: str, output_path: str = "") -> dict:
    src = Path(file_path)
    if not output_path:
        output_path = str(src.with_suffix('.pdf'))
```

如果在调用时没传 `output_path`，PDF 会生成在和 `.md` 文件相同的位置——初学者可能不知道这一点，以为 PDF 会生成在当前工作目录。

### 5.2 Born-digital only 的陷阱

`pdf_to_text` 的 docstring 和 MCP tool 描述都写了 "born-digital PDF only"，但初学者可能不理解"born-digital"的含义：

- **Born-digital PDF**：直接在 Word/LaTeX/浏览器等软件中"另存为 PDF"生成的文件。文字是可选中的。
- **Scanned PDF**: 用扫描仪生成的 PDF，每页是一张图片。文字不可选中。

如果一个 agent 拿一个扫描件 PDF 调用 `pdf_to_text`，会返回空字符串——**不会报错**（by design）。这个行为是故意的：让 agent 自己判断"如果返回空文本，也许该试试 `glm_ocr` 工具"。

---

## 6. 动手任务

1. **复现 Bug 1**：在 `convert_pdf_to_text` 中，在 `for page in doc` 之后紧接一个 `raise ValueError("人工制造异常")`，观察 `doc.close()` 是否被执行。把 `doc.close()` 放在 `finally` 外再试一次。
2. **检查你的代码**：打开你之前写的 Python 项目，搜一下 `open(`、`connect(`、`lock(` 等资源获取操作——有多少个没有放在 `with` 或 `try/finally` 里？
3. **修改转换函数**：尝试修改 `convert_pdf_to_text` 返回 `tuple[str, int]`（文本 + 页数），然后修改 `format_mcp_server.py` 和 `md2pdf.py` 以适应新签名。数一数要改几处代码，体会"改返回类型"的连锁反应。
