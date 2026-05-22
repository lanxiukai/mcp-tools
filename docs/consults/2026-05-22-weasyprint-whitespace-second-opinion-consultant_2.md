# 顾问报告：WeasyPrint 68.1 后 HTML→PDF 标题前空白问题第二意见

> 顾问：consultant_2 | 模型：GPT-5.5 | 日期：2026-05-22

## 1. 任务理解

`format_conversion_html_to_pdf` 目前在 `format-conversion/converter.py` 中读取用户 HTML，先用 `_process_emoji()` 包裹或替换 emoji，再把 `_build_injected_css()` 生成的 `@font-face`、`.emoji` 和 `@page @bottom-center` 页码样式插入到 `</head>` 前，最后用 WeasyPrint 输出 PDF。

当前具体问题是：同一份 HTML 用 Chrome 打印 PDF 没有异常，但用 WeasyPrint 67.0 和升级后的 68.1 都会在 `🧠 刺激控制（这两条是一切的基础）` 标题前看到额外空白。用户希望在不改源 HTML 的前提下，判断根因、找出 converter.py 层面的止血方案，并重新评估是否要引入 Playwright/Chromium 后端。

我的核心判断：**升级 67.0 → 68.1 无效，不足以排除 WeasyPrint 布局差异；但它确实把嫌疑从“某个已修复的 67.x flex bug”转移到“仍未完全对齐浏览器的 flex / font metrics / paged-layout 组合问题”。`@page` 页码注入导致标题前空白的概率很低。另一个重要时效性更新是：consultant_1 关于 Chromium 不支持 `@page @bottom-center` 页码的警告在 2026 年已经过时或至少不完整——Chrome 131 起已支持 page margin boxes 和 page counters，Playwright 1.60.0 捆绑 Chromium 148，理论上可以直接沿用 CSS `@page` 页码机制。**

## 2. 现状分析

### 2.1 WeasyPrint 68.1 无效说明了什么

WeasyPrint 68.1 是当前 PyPI 最新稳定版，发布于 2026-02-06。其发布说明列出的 68.1 bugfix 主要是 SVG、bounding box、透明度、URL scheme、charset、`calc()` 等问题，没有直接命中本案例的 flex-to-block 间距问题。WeasyPrint 官方文档仍将 flexbox 描述为“simple use cases 可用，但 not deeply tested”。因此：

- 不能说“升级无效，所以不是 WeasyPrint flex 问题”；
- 更准确的说法是：**这不是 68.1 已覆盖的那批 bug；仍可能是 WeasyPrint 的 flex 高度计算、字体行盒计算、分页碎片化或这些因素叠加。**

本案例的视觉症状还要小心区分两种“空白”：

1. 如果空白是 `.two-col` 和 `.mech-box` 之间的白底区域，优先怀疑前一个 flex 容器高度、`gap`、子项末尾 margin 或分页碎片化；
2. 如果空白是蓝底区域，并且与 `.mech-box` / `.sec-title.mech` 的 `#eff6ff` 背景融为一体，则优先怀疑 `.mech-box` 的 `padding-top:10px`、`.sec-title` 自身行高、emoji 字体 metrics 或标题 flex cross-size。

用户描述中特别指出 `.mech-box` 与 `.sec-title.mech` 同色，所以我会把“**标题自身行盒 / emoji 字体 metrics 被同色背景放大**”的权重提高，而不是只盯着前一个 `.two-col`。

### 2.2 `@page` 注入是否可能冲突

当前用户 HTML 有：

```css
@page { size: A4; margin: 8mm 12mm; }
```

converter 注入：

```css
@page {
  @bottom-center { content: counter(page); ... }
}
```

按 CSS Paged Media 的模型，后一个 `@page` 规则不会重置前一个规则里的 `size` 或 `margin`，只是在同一个 page context 里增加 / 覆盖对应 margin box。WeasyPrint 官方文档明确支持 `@page`、page margin boxes 和 page counters。正常情况下，这个 footer margin box 不参与正文 normal flow，不应在某个正文标题前制造空白。

但它有两个真实风险：

- 如果用户 HTML 自己已经定义了 `@bottom-center`，当前 converter 会在后面再注入一个 `@bottom-center`，从而覆盖或混合用户页脚；
- 如果底部页边距太小，页码可能与正文末尾视觉重叠，但这仍不是“标题前多一行”的典型成因。

结论：**`@page` 注入应做 A/B 验证，但不是首要嫌疑。**建议新增一个 `page_numbers=False` 的调试路径，跑一次无页码转换；如果空白仍在，就可以基本排除。

### 2.3 更值得怀疑的 converter.py 层因素

当前 `_process_emoji()` 是对原始 HTML 字符串做正则替换。如果存在 Noto Emoji 字体，它会把 `🧠` 变成：

```html
<span class="emoji">🧠</span>
```

这会带来两个影响：

1. `.sec-title { display:flex; align-items:center; gap:6px; }` 的子项从“文本的一部分”变成了一个独立 flex item；
2. `.emoji { font-family: 'Noto Emoji', ... }` 没有显式 `line-height`，其字体 ascender / descender 与中文字体不同，WeasyPrint/Pango 和 Blink 的行盒计算可能不同。

这类差异不一定表现为 emoji 旁边错位，也可能表现为标题 flex box 高度变大。由于 `.sec-title.mech` 与 `.mech-box` 同色，多出来的标题上半部行盒或父容器 padding 会被肉眼看成“标题前多一行”。

另外，`gap: 10px` 在 flex 中等价于 row-gap 和 column-gap 都为 10px。对单行横向 flex 来说 row-gap 理论上不应影响垂直间距，但如果 WeasyPrint 的 flex/gap/fragmentation 有边界问题，显式拆成 `column-gap:10px; row-gap:0` 是一个低成本验证点。

### 2.4 不太值得投入的方向

- **BeautifulSoup 去注释 / 空白文本节点**：HTML 注释不会生成盒；块级元素之间的普通折叠空白通常不会生成可见行；flex 容器中的纯空白文本也不应造成这种垂直空白。为了删空白而全量重序列化 HTML，风险大于收益，可能破坏 `pre`、`code`、内联文本、属性顺序或脚本内容。
- **强制 WeasyPrint 某种“浏览器模式”**：WeasyPrint 没有 Blink-compatible 模式。`HTML(..., media_type='print')` 本来就是默认值；改成 `screen` 只会改变 `@media` 选择，不会换布局引擎。
- **继续等待小版本升级**：68.1 已是当前最新稳定版；继续赌小版本修复不如先做可控 A/B 和后端分流。

## 3. 具体建议

### P0：先做 4 个 converter-only A/B 验证，不要直接大改架构

**问题描述**：现在还没确认空白来自 `@page`、emoji/font、`.sec-title` flex，还是 `.two-col` flex。直接上 Chromium 会解决“与 Chrome 一致”的目标，但会掩盖 converter 自身是否有小修即可解决的问题。

**建议方案**：临时把 `_build_injected_css()` 拆出几个开关，生成 4 份 PDF 对比。

```python
def _build_injected_css(fonts, *, page_numbers: bool = True, compat_css: str = "") -> str:
    ...
    if page_numbers:
        rules.append("""
@page {
  @bottom-center { content: counter(page); ... }
}
""")
    if compat_css:
        rules.append(compat_css)
```

对比顺序：

1. **无页码**：`page_numbers=False`。若问题不变，排除 `@page` 注入。
2. **emoji metrics 修正**：仅加：
   ```css
   .emoji {
     line-height: 1 !important;
     display: inline-block !important;
     vertical-align: -0.1em !important;
   }
   .sec-title {
     line-height: 1.15 !important;
     row-gap: 0 !important;
   }
   ```
   若问题消失，根因大概率是 emoji span / 字体行盒 / title flex cross-size。
3. **flex gap / 末尾 margin 修正**：仅加：
   ```css
   .two-col {
     column-gap: 10px !important;
     row-gap: 0 !important;
     align-items: flex-start !important;
   }
   .two-col > .col > :last-child {
     margin-bottom: 0 !important;
   }
   ```
   若问题消失，根因更偏 `.two-col` flex 高度或 gap/margin。
4. **调试 outline**：只为定位，不作为修复：
   ```css
   .two-col { outline: 0.5px solid red !important; }
   .mech-box { outline: 0.5px solid blue !important; }
   .sec-title.mech { outline: 0.5px solid green !important; }
   ```
   看空白到底在 `.mech-box` 内，还是在 `.two-col` 与 `.mech-box` 之间。

**预期收益**：10–20 分钟内把嫌疑范围缩到一个可解释原因，避免把通用工具改成一堆不可维护的全局 hack。

### P1：如果只想在 WeasyPrint 后端止血，优先试低侵入 CSS，而不是改 HTML DOM

**问题描述**：源 HTML 不能改，但 converter 可以按 WeasyPrint 后端注入兼容 CSS。风险在于这会污染所有 HTML，所以不应默认启用过于具体的业务 class hack。

**建议方案**：按侵入性从低到高尝试。

第一档，低风险，适合先试：

```css
/* WeasyPrint flex/emoji compatibility, opt-in only */
.emoji {
  line-height: 1 !important;
  display: inline-block !important;
  vertical-align: -0.1em !important;
}

.two-col {
  column-gap: 10px !important;
  row-gap: 0 !important;
  align-items: flex-start !important;
}
.two-col > .col > :last-child {
  margin-bottom: 0 !important;
}
```

第二档，如果确认是 `.sec-title` 的 flex 行盒问题：

```css
.sec-title {
  line-height: 1.15 !important;
  row-gap: 0 !important;
}
```

第三档，如果要彻底绕开标题 flex：

```css
.sec-title {
  display: block !important;
  line-height: 1.15 !important;
}
.sec-title > .emoji {
  margin-right: 6px !important;
}
```

第四档，如果确认 `.two-col` flex 是根因，且这个文档必须留在 WeasyPrint：

```css
.two-col {
  display: table !important;
  width: 100% !important;
  table-layout: fixed !important;
  border-collapse: separate !important;
  border-spacing: 10px 0 !important;
}
.two-col > .col {
  display: table-cell !important;
  width: 50% !important;
  vertical-align: top !important;
}
```

**预期收益**：第一档/第二档有机会以最小改动修复本症状；第三档/第四档能更强力规避 WeasyPrint flex 差异，但已经是“针对这份 HTML 的兼容 profile”，不应悄悄变成通用默认行为。

### P2：修正 converter.py 的注入架构，给调试和多后端留接口

**问题描述**：当前 `_build_injected_css()` 把 font、emoji、页码混在一个 HTML 片段里，并且函数总是返回 `</head>`。这让 A/B 测试和后端切换都很别扭。

**建议方案**：拆成纯 CSS builder，再由不同 backend 决定如何注入。

```python
def _build_font_face_css(fonts: dict[str, str | None]) -> str: ...
def _build_emoji_css(fonts: dict[str, str | None]) -> str: ...
def _build_page_number_css(page_font: str) -> str:
    return f"""
@page {{
  @bottom-center {{
    content: counter(page);
    font-family: {page_font};
    font-size: 8pt;
    color: #94a3b8;
  }}
}}
"""

def _compose_injected_css(fonts, *, page_numbers=True, compat_css="") -> str:
    parts = [_build_font_face_css(fonts), _build_emoji_css(fonts)]
    if page_numbers:
        parts.append(_build_page_number_css(_page_font(fonts)))
    if compat_css:
        parts.append(compat_css)
    return "\n".join(parts)
```

同时建议显式使用 WeasyPrint 的 `FontConfiguration`，这是官方文档对 `@font-face` 的推荐写法：

```python
from weasyprint.text.fonts import FontConfiguration

font_config = FontConfiguration()
HTML(string=html_text, base_url=str(html_path.parent), media_type="print").write_pdf(
    str(out_path),
    font_config=font_config,
)
```

如果未来把注入 CSS 改为 `CSS(string=...)` 传给 `stylesheets=[...]`，则同一个 `font_config` 也要传给 `CSS(..., font_config=font_config)` 和 `write_pdf(..., font_config=font_config)`。

**预期收益**：能单独开关页码、emoji、compat CSS；减少 raw HTML 注入耦合；为 Chromium 后端复用同一套 `@page` 页码 CSS 打基础。

### P3：Playwright/Chromium 后端值得做，但页码策略应更新为“优先 CSS @page”，不是旧式 footerTemplate

**问题描述**：consultant_1 提醒 Chromium 不支持 `@page @bottom-center`，这个判断在 2026 年已经不再可靠。Chrome 131 release notes 明确写明已支持 `@page` margin boxes，并且支持 `counter(page)` / `counter(pages)`；PyPI 上 Playwright 1.60.0 发布于 2026-05-18，捆绑 Chromium 148.0.7778.96。因此，新后端设计不应默认放弃 CSS Paged Media 页码。

**建议方案**：后端分层，但共享 CSS 页码注入。

```python
from dataclasses import dataclass
from typing import Literal

HtmlPdfEngine = Literal["weasyprint", "chromium"]

@dataclass(frozen=True)
class HtmlPdfOptions:
    engine: HtmlPdfEngine = "weasyprint"
    page_numbers: bool = True
    weasy_compat_css: str = ""
    chromium_wait_until: Literal["load", "networkidle"] = "networkidle"

def convert_html_to_pdf(source_path: str, output_path: str, *,
                        options: HtmlPdfOptions | None = None) -> None:
    options = options or HtmlPdfOptions()
    html_path = Path(source_path)
    out_path = Path(output_path)
    fonts = _check_fonts()
    if options.engine == "weasyprint":
        _convert_html_to_pdf_weasyprint(html_path, out_path, fonts, options)
    elif options.engine == "chromium":
        _convert_html_to_pdf_chromium(html_path, out_path, fonts, options)
    else:
        raise ValueError(f"Unknown HTML PDF engine: {options.engine}")
```

WeasyPrint 后端：保留 emoji 包裹和 CSS `@page` 页码。

```python
def _convert_html_to_pdf_weasyprint(html_path, out_path, fonts, options):
    html_text = html_path.read_text(encoding="utf-8")
    html_text = _process_emoji(html_text, fonts["Noto Emoji"] is not None)

    css = _compose_injected_css(
        fonts,
        page_numbers=options.page_numbers,
        compat_css=options.weasy_compat_css,
    )
    html_text = _inject_style_before_head_end(html_text, css)

    font_config = FontConfiguration()
    HTML(string=html_text, base_url=str(html_path.parent), media_type="print").write_pdf(
        str(out_path),
        font_config=font_config,
    )
```

Chromium 后端：建议先不做 `_process_emoji()`，让 Chrome 使用原生 emoji 渲染；只通过 `page.add_style_tag()` 注入字体和 `@page` 页码。关键是 `display_header_footer=False`，避免 Playwright 的模板页眉页脚与 CSS margin boxes 冲突。

```python
def _convert_html_to_pdf_chromium(html_path, out_path, fonts, options):
    from playwright.sync_api import sync_playwright

    css = _compose_injected_css(fonts, page_numbers=options.page_numbers)
    css += """
html {
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
"""

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until=options.chromium_wait_until)
        page.emulate_media(media="print")
        page.add_style_tag(content=css)

        # Chrome 131+ supports @page margin boxes and counters.
        # Playwright 1.60 bundles Chromium 148, so prefer CSS @page over footerTemplate.
        page.pdf(
            path=str(out_path),
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=False,
        )
        browser.close()
```

如果运行时检测到 Chromium major < 131，再 fallback 到 `footer_template`：

```python
major = int(browser.version.split(".", 1)[0])
if options.page_numbers and major < 131:
    pdf_kwargs.update({
        "display_header_footer": True,
        "footer_template": _legacy_chromium_footer_template(),
        "header_template": "<span></span>",
        "margin": {"top": "8mm", "right": "12mm", "bottom": "10mm", "left": "12mm"},
    })
```

**预期收益**：

- 对 flex/grid/浏览器 CSS 布局，Chromium 与用户手动 Chrome 打印更一致；
- 页码机制不用拆成两套，现代 Chromium 可以直接吃 `@page @bottom-center`；
- 仍保留 WeasyPrint 作为轻量、无浏览器依赖、Paged Media 更传统稳定的后端。

### P4：不要默认自动切换后端；先暴露显式 engine

**问题描述**：自动根据 HTML 中是否出现 `display:flex` / `grid` 切 Chromium，看似智能，实际会让用户难以预测输出，尤其是依赖 WeasyPrint 特性的文档。

**建议方案**：先新增显式参数或新增 MCP tool：

- `format_conversion_html_to_pdf`：默认仍 WeasyPrint，保持兼容；
- `format_conversion_html_to_pdf_chromium` 或 `engine="chromium"`：用户需要 Chrome 一致性时显式选择；
- 文档写清楚：WeasyPrint 适合静态文档 / Paged Media；Chromium 适合现代网页布局 / flex-grid 视觉一致性。

**预期收益**：避免默认行为突然变重、变慢、引入 Chromium 依赖；同时给当前问题一个根治出口。

## 4. 信息时效性核查

| 信息源 | 时效性状态 | 核查时间 | 结论 |
|---|---:|---:|---|
| PyPI `weasyprint` 项目页 | 已验证当前 | 2026-05-22 | 最新稳定版为 68.1，发布于 2026-02-06，Python >=3.10。 |
| GitHub `Kozea/WeasyPrint` v68.1 release | 已验证当前 | 2026-05-22 | 68.1 bugfix 列表未直接命中本案例 flex 间距。 |
| WeasyPrint 68.1 API 文档：flexbox | 已验证当前 | 2026-05-22 | flexbox “works for simple use cases but is not deeply tested”。 |
| WeasyPrint 68.1 API 文档：CSS Paged Media | 已验证当前 | 2026-05-22 | 支持 `@page`、page margin boxes、page counters。 |
| WeasyPrint API 文档：`media_type` | 已验证当前 | 2026-05-22 | `HTML(..., media_type='print')` 是默认；没有浏览器兼容模式。 |
| WeasyPrint 字体文档：`FontConfiguration` | 已验证当前 | 2026-05-22 | 使用 `@font-face` 时推荐 / 需要传 `FontConfiguration`。 |
| Chrome 131 release notes / Chrome Developers blog | 已验证当前 | 2026-05-22 | Chrome 131 已支持 `@page` margin boxes，且 counters `page` / `pages` 可用于页码。 |
| PyPI `playwright` 项目页 | 已验证当前 | 2026-05-22 | Playwright 1.60.0 发布于 2026-05-18，捆绑 Chromium 148.0.7778.96，Python >=3.9。 |
| Playwright Python `page.pdf()` 文档 | 已验证当前 | 2026-05-22 | `page.pdf()` 使用 print CSS；支持 `print_background`、`prefer_css_page_size`、`display_header_footer`、`header_template` / `footer_template` 等。 |
| “Playwright + Chromium 下 CSS @page margin boxes 一定与 Chrome 打印 UI 完全一致” | 待验证 | 2026-05-22 | Chrome 131+ 已支持该特性，但 Playwright `page.pdf()` 与打印 UI 在 margin 细节上历史上有差异；实施前应做一次最小 smoke test。 |

## 5. 总结

升级到 WeasyPrint 68.1 后问题仍在，并不排除 WeasyPrint；更可能说明这是仍未完全与 Blink 对齐的 flex / 字体行盒 / 分页组合问题，而不是 68.1 已修复的具体 bug。`@page` 页码注入不太可能制造正文标题前空白，但应通过 `page_numbers=False` 做一次 A/B 快速排除。下一步我建议先做四个 converter-only 对比：无页码、emoji line-height、flex row-gap/last-margin、outline 定位；不要先做 BeautifulSoup 去空白，也不要继续赌 WeasyPrint 小版本。若需要根治 Chrome 视觉一致性，应实现显式 Playwright/Chromium 后端；但注入策略要按 2026 年现状更新，现代 Chromium 已支持 `@page` margin boxes 和 `counter(page)`，因此应优先复用 CSS 页码，而不是默认退回 Playwright `footerTemplate`。
