# 02 — 关键处理管线详解

> 适合读者：想弄懂"一个 Markdown 文件是怎么变成 PDF 的"每一步细节的人。

这个项目没有传统意义上的"算法"（排序、搜索、图遍历），但有一个**多阶段数据变换管线**——Markdown 文本经过 5-6 步变换，最终变成 PDF。理解这条管线，就等于理解了整个项目的核心。

---

## 1. 全管线总览（Markdown → PDF）

```
输入: example.md (文本文件)
  │
  ▼
【阶段 1】读取文件
  │  Path.read_text(encoding='utf-8')
  │  → 字符串"# 我的日记\n\n今天学习了 Python...⭐\n"
  │
  ▼
【阶段 2】Emoji 预处理
  │  文本替换：⭐ → ★, 📅 → [日历], ...
  │  → "# 我的日记\n\n今天学习了 Python...★\n"
  │  ★ 为什么在 markdown 解析前做？见下文
  │
  ▼
【阶段 3】Markdown → HTML
  │  markdown-it-py 解析
  │  → "<h1>我的日记</h1><p>今天学习了 Python...<span class=\"star\">★</span></p>"
  │
  ▼
【阶段 4】HTML body 后处理
  │  _process_body():
  │  ① ★ → <span class="star">★</span>（金色着色）
  │  ② 剩余 emoji → <span class="emoji">😊</span>（字体隔离）
  │
  ▼
【阶段 5】拼装完整 HTML
  │  <!DOCTYPE html> + <style>CSS</style> + <body>处理后的 HTML</body>
  │
  ▼
【阶段 6】WeasyPrint 渲染
  │  HTML(string=...).write_pdf(...)
  │  → example.pdf (A4, 排版完整, 带页码)
```

以下逐阶段详解。

---

## 2. 阶段 1 & 2：文件读取与 Emoji 预处理

### 代码位置

`converter.py:327-332`

```python
text = md_path.read_text(encoding='utf-8')

# Emoji→text fallback applied early (before markdown parsing)
for emoji_char, replacement in _EMOJI_TEXT_MAP.items():
    text = text.replace(emoji_char, replacement)
```

### 为什么 emoji 替换要在 markdown 解析前？

这是一个**关键但又容易忽略的顺序问题**。

假设输入中有 emoji `⭐`。如果不做预处理，markdown-it-py 在解析时可能会把它转义成 HTML 实体 `&#11088;`（因为 commonmark 规范要求特殊字符要转义）。转义之后，后续 `_process_body` 里的正则就匹配不到 `★` 了，金色 CSS 也加不上。

所以策略是：**先替换，再解析**。把 `⭐` 提前替换成普通字符 `★`，markdown-it 就不会碰它了。

### Emoji 映射表

`_EMOJI_TEXT_MAP`（converter.py:32-40）是一个字典，把常用的 emoji 映射成可读的文字标签：

```python
_EMOJI_TEXT_MAP = {
    '📅': '[日历]', '🔔': '[铃]', '⭐': '★', '✅': '✔',
    '❌': '✘', '💡': '●', '🎯': '◎', ...
}
```

当一个 emoji 不在映射表里时，有两种情况：
- 如果 Noto Emoji 字体可用 → 用 CSS 的 `.emoji` 类包起来，让字体渲染
- 如果字体不可用 → emoji 在 PDF 里显示为一个方块（豆腐块），但不会崩溃

### 手算演示

输入文本：
```
# 今日任务

- ✅ 跑步 30 分钟
- 📅 预约体检
- ⭐ 重要
```

预处理后：
```
# 今日任务

- ✔ 跑步 30 分钟
- [日历] 预约体检
- ★ 重要
```

注意：`✅` 被映射为 `✔`（勾号字符），`📅` 被映射为 `[日历]`（文字标签），`⭐` 被映射为 `★`（实心星号）。

---

## 3. 阶段 3：Markdown → HTML

### 代码位置

`converter.py:335-337`

```python
md = MarkdownIt('commonmark', {'breaks': True, 'html': True})
md.enable(['table', 'strikethrough'])
body = md.render(text)
```

### MarkdownIt 配置

`markdown-it-py` 的构造函数有三个关键配置：

| 配置 | 值 | 含义 |
|------|-----|------|
| 第一个参数 | `'commonmark'` | 使用 CommonMark 规范（标准 Markdown 语法，不包含扩展语法） |
| `breaks: True` | 允许在 Markdown 中直接用 `<br>` |
| `html: True` | 允许在 Markdown 中嵌入原始 HTML（如 `<div>`、`<table>`） |
| `.enable(['table', 'strikethrough'])` | 开启表格和删除线支持（CommonMark 默认不含这俩） |

一个常见的坑是：**CommonMark 默认不支持表格**。如果忘记 `.enable(['table'])`，Markdown 里的表格会原样输出为文本（`| 列1 | 列2 |`），而不是渲染成 `<table>`。这是 README.md 中"已知问题"里写到的（`converter.py` 里已正确 enable）。

### 输入 → 输出

```
输入文本：
# 今日任务
- ✔ 跑步 30 分钟
- [日历] 预约体检
- ★ 重要

解析后的 HTML：
<h1>今日任务</h1>
<ul>
<li>✔ 跑步 30 分钟</li>
<li>[日历] 预约体检</li>
<li>★ 重要</li>
</ul>
```

---

## 4. 阶段 4：HTML body 后处理

### 代码位置

`converter.py:265-277`

```python
def _process_body(body_html: str, has_emoji_font: bool) -> str:
    body_html = re.sub(r'★+', lambda m: f'<span class="star">{m.group()}</span>', body_html)

    if has_emoji_font:
        body_html = _EMOJI_RE.sub(
            lambda m: f'<span class="emoji">{m.group()}</span>', body_html
        )
    return body_html
```

这个函数做两件事：

**① 给 ★ 上金色**（第 271 行）

正则 `★+` 匹配一个或多个连续的星号，用 `<span class="star">` 包起来。CSS 中 `.star` 的样式是：

```css
.star { color: #c47f2c; font-weight: bold; }
```

颜色 `#c47f2c` 是一种暖金色。这样输出 PDF 中 ★ 会显示为金色，用来做重要标记。

**② 包裹剩余 emoji**（第 273-274 行）

如果 Noto Emoji 字体可用，用 `<span class="emoji">` 包住所有剩余 emoji。CSS 中 `.emoji` 的样式是：

```css
.emoji { font-family: 'Noto Emoji', 'Noto Sans SC', sans-serif; }
```

为什么把 emoji 单独包起来？因为 emoji 字符需要专门的 emoji 字体来渲染。如果不包，emoji 会使用正文的字体（Noto Sans SC），而 Noto Sans SC 对 emoji 的支持有限，某些 emoji 可能显示为方块。

### 注意：两次正则扫描的先后

先做 ★ 着色，再做 emoji 包裹。顺序重要——因为 ★ 已经被替换为普通字符 `★`（在阶段 2），不会再被 `_EMOJI_RE` 匹配到（`_EMOJI_RE` 的字符范围不包含 `★`），所以不会冲突。

但 `★` 其实已经被阶段 2 从 `⭐` 替换过来了，所以阶段 4 的 `★+` 正则匹配到的就是页面中所有的"重要标记"。

---

## 5. 阶段 5：拼装完整 HTML

### 代码位置

`converter.py:343-352`

```python
html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{_build_css(fonts)}</style>
</head>
<body>
{body}
</body>
</html>"""
```

这一步很简单：拼接字符串。但拼进去的 CSS 内容值得关注——`_build_css()` 函数动态生成了包含字体声明和全套排版样式的 CSS 字符串。

### CSS 结构速览

`_build_css()`（converter.py:65-212）生成的 CSS 包含：

| 组成部分 | 关键内容 |
|----------|---------|
| `@font-face` 规则 | 用 `file://` 协议加载本地字体文件 |
| `@page` 规则 | A4 纸张、18-20mm 页边距、底部居中的页计数器 |
| body | 字体栈、字号 11pt、行高 1.7 |
| `.emoji` | 独立的 emoji 字体栈 |
| h1-h6 | 色系从深蓝渐变到浅蓝，自动分页控制 |
| table | 边框、斑马条纹、深蓝表头白字 |
| blockquote | 暖 amber 灰底 + 左侧竖线 |
| code / pre | 等宽字体 DejaVu Sans Mono，灰色背景 |

关键设计：**字体栈的构建顺序**。

```python
body_stack = ['sans-serif']           # 兜底
body_stack.insert(0, "'DejaVu Sans'") # Linux 标准无衬线字体
# 如果 Noto Sans SC 存在，插入到最前面
body_stack.insert(0, "'Noto Sans SC'")

body_font = ', '.join(body_stack)
# 结果: "'Noto Sans SC', 'DejaVu Sans', sans-serif"
```

CSS 字体栈的工作方式是：浏览器/WeasyPrint **从左到右尝试**，第一个可用的字体就使用。所以：
1. 优先用 Noto Sans SC（显示中文）
2. 没有则用 DejaVu Sans（Linux 标配，显示西文）
3. 再没有就用系统默认无衬线字体

---

## 6. 阶段 6：WeasyPrint 渲染

### 代码位置

`converter.py:355`

```python
HTML(string=html).write_pdf(str(out_path))
```

WeasyPrint 拿到完整的 HTML 字符串后，内部经过：

```
HTML 字符串
  → libxml2 解析 DOM 树
  → CSS 样式计算（级联、继承、@font-face 加载本地字体）
  → 布局引擎（分页、行断、表布局）
  → cairo 渲染为 PDF 页面（使用 pango 进行文本布局）
```

这个过程中，**字体搜索**是一个常见坑点：WeasyPrint 的 `@font-face` 用 `file://` 协议指向本地 `.ttf` 文件，路径必须是**绝对路径**。`_build_css` 里使用 `fonts_available["Noto Sans SC"]`（已经是绝对路径）来生成 `src: url('file:///home/.../NotoSansSC-Regular.ttf')`，确保 WeasyPrint 能找到字体。

---

## 7. HTML → PDF 管线的差异

HTML → PDF 的管线更短，因为不需要 markdown 解析：

```
输入: example.html
  │ Path.read_text()
  ▼
【阶段 1】Emoji 处理 (_process_emoji)
  │  有 emoji 字体 → 包 <span class="emoji">
  │  无 emoji 字体 → 文字替换
  ▼
【阶段 2】CSS 注入 (_build_injected_css)
  │  在 </head> 前插入页码 + emoji 字体声明
  ▼
【阶段 3】WeasyPrint 渲染
  │  HTML(string=..., base_url=...).write_pdf(...)
  ▼
输出: example.pdf
```

两个关键差异：

1. **没有 markdown-it-py 参与**：输入已经是 HTML，不需要解析
2. **保留原有样式**：`_build_injected_css` 只追加而非替换——原 HTML 的 `<link rel="stylesheet">`、`<style>`、内联样式全部保留
3. **`base_url` 参数**：`HTML(string=html_text, base_url=str(html_path.parent)).write_pdf(...)`——告诉 WeasyPrint 以 HTML 所在的目录为基准解析相对路径（如图片 `./img/logo.png`）

---

## 8. PDF → 文本：反向管线

```python
def convert_pdf_to_text(source_path: str) -> str:
    doc = fitz.open(source_path)
    try:
        pages_text = [page.get_text() for page in doc]
    finally:
        doc.close()
    return '\n'.join(pages_text)
```

PyMuPDF 的 `page.get_text()` 提取的是 PDF 内部的内容流中的文本——它不识别图片里的文字。所以：

- 如果是文字可选中/可复制的 PDF → 正常工作
- 如果是扫描件（图片式 PDF）→ 返回空字符串（或乱码）

这就是为什么 docstring 里写了 "born-digital PDF only"，MCP tool 描述也写了这句话。**这既是技术限制，也是设计决策**——不做扫描件识别，让专门的工具去做。

### try...finally 的重要性

`doc.close()` 放在 `finally` 里（converter.py:427-433）：无论 `for page in doc` 中间是否抛出异常，PDF 文件句柄都会被释放。这是从 NH-03 修复来的教训（见 `06-pitfalls-and-debugging.md` 详解）。

---

## 9. 动手任务

1. **手算验证**：拿以下 Markdown 文本，手动走过 6 个阶段，写出每一步的输出：

```markdown
# ⭐ 购物清单

- 📅 购买日期：明天
- ✅ 牛奶
- ❌ 鸡蛋（已买）
```

2. **修改尝试**：在 `_build_css` 中把 `h1` 的 `page-break-before: always` 改为 `page-break-before: avoid`，生成一份 PDF 观察差异。

3. **尝试加新的 emoji 映射**：在 `_EMOJI_TEXT_MAP` 中加一行 `'🐍': '[蛇]'` 或 `'🐍': 'Python'`，然后转换一个含 🐍 的 Markdown 文件，看输出效果。
