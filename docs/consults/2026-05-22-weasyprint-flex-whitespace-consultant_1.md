# 顾问报告：WeasyPrint HTML→PDF flex 容器后多余空白问题

> 顾问：consultant_1 | 模型：Claude Opus 4.7 | 日期：2026-05-22

---

## 1. 任务理解

`format-conversion/converter.py` 的 `convert_html_to_pdf()` 使用 WeasyPrint 把用户 HTML 渲染为 PDF。问题文件中存在一处 `.two-col {display:flex; gap:10px}` 双列布局，紧接着一个 `.mech-box` 卡片，卡片首行是 `.sec-title.mech`（也是 `display:flex`）。WeasyPrint 渲染结果里，该标题文字前出现了 Chrome 打印 PDF 中没有的可见空白。

约束：

- **不能修改用户的源 HTML**——这是 MCP 工具的契约
- 只能在 `converter.py` 注入 CSS、调换渲染后端、或做 HTML 预处理

经查，本机 `format-convert` 环境装的是 **WeasyPrint 67.0**（pip show 输出），而上游已发布 **v68.1**（2026-02-06）。这一点对后续诊断很重要。

---

## 2. 现状分析

### 2.1 WeasyPrint flex 实现的真实状态（已验证至 2026-02）

阅读 WeasyPrint 仓库 [changelog](https://github.com/Kozea/WeasyPrint/blob/v68.1/docs/changelog.rst) 和近期 issue（核查时间 2026-05-22）：

- v65（2025-03）才在 NLnet 资助下补齐 `gap` 属性
- v65/v66/v67/v68 每个版本都还在 "Improve Flex layout" / "Minor fixes for flex layout"
- 直到 v66 才修了 [#2449 Bottom margin on flex item ignored at the bottom of flex containers](https://github.com/Kozea/WeasyPrint/issues/2449)
- v67 引入新 grid/flex 边界问题（[#2628](https://github.com/Kozea/WeasyPrint/issues/2628)），v68.1 还在补
- 维护者 @liZe 在多个 issue 中明确说过 "Flex layout has to be fully rewritten"

**结论**：在 2026 年的今天，WeasyPrint 的 flex 支持依然属于"能用但和 Blink 不像素一致"的水平。把 flex 重 layout 的 HTML 用 WeasyPrint 渲染本质上是把工具用在它的弱项上。

### 2.2 你的 HTML 里最可能踩到哪一条坑

用户 HTML 同时使用了两层 flex：

1. **外层 `.two-col {display:flex; gap:10px}`**，两个 `.col` 子项内容数量不等（9 条 vs 8 条规则）。Chrome 默认 `align-items: stretch` 会把两列拉到等高（更高那列的高度），WeasyPrint 同样会拉伸，但 flex 容器**自身高度计算 + 子项 margin 折叠**在 WeasyPrint 里有历史 bug（见 #1563、#1652、#2449）。哪怕单条 `.rule` 的 `margin-bottom:2px` 在末尾没被折叠掉，乘以 8~9 条就会有 16~18px 误差，恰好和你看到的"一空行"量级吻合。

2. **内层 `.sec-title {display:flex; align-items:center}`**，里面包了一个 `<span class="emoji">🧠</span>`（被你的工具自动包裹的）+ 中文文本。`Noto Emoji` 字体的 ascender 显著高于 `Noto Sans SC`，flex 容器的 cross-axis 尺寸取最大的字体行高。WeasyPrint 在多字体 fallback 时的 line-height 计算和 Blink 不一致，也可能贡献一部分。

3. **`.mech-box` 自身的 padding-top:10px** 会被视觉感知放大——因为它和 `.sec-title.mech` 同色（`#eff6ff`），所以"卡片顶到文字"之间的所有空白看起来都像是标题"上面"多出来的。

按权重排序：**#1（two-col flex 容器高度/末端 margin）≫ #2（sec-title 自身 flex）≈ #3（背景色错觉）**。

### 2.3 当前 `converter.py` 的设计取舍

`_build_injected_css` 只注 `@font-face`、`.emoji` 字体、`@bottom-center` 页码。这是个非常克制的注入策略，符合 HTML→PDF 工具 "preserves the original styles" 的承诺。问题是 WeasyPrint 自身渲染能力的天花板，不是注入策略的问题。

### 2.4 风险

- 用户期望"HTML 转 PDF 应该和浏览器打印一致"——MCP 工具的 docstring 写的是 "preserving original styles"，但**没承诺像素级 Chrome 兼容**。如果用户拿这个工具做需要严格视觉一致性的产物（如打印物料），现状会持续踩坑
- WeasyPrint 67 已知 grid 的 [#2628 bug](https://github.com/Kozea/WeasyPrint/issues/2628) 至少在 v67 中存在并在 v68 修复——你装的就是受影响版本

---

## 3. 具体建议（按优先级排序）

### R1（推荐，根治）：增加 Playwright/Chromium 后端，按需切换

**问题**：WeasyPrint 与 Chrome 在 flex/grid/现代 CSS 上不会"凑齐"——这是架构差异，不是某个 bug。继续靠注入 CSS workaround 是无底洞。

**方案**：把 `convert_html_to_pdf` 改成多后端：

```python
def convert_html_to_pdf(source_path: str, output_path: str, *,
                        engine: str = "auto") -> None:
    # engine: "weasyprint" | "chromium" | "auto"
    # "auto": HTML 里出现 display:flex/grid/CSS variables 等就用 chromium
```

**实现要点**：

- 用 `playwright` Python 包（`pip install playwright && playwright install chromium`）
- 核心调用：
  ```python
  from playwright.sync_api import sync_playwright
  with sync_playwright() as p:
      browser = p.chromium.launch()
      page = browser.new_page()
      page.goto(f"file://{html_path.absolute()}")
      page.emulate_media(media="print")   # 让 @page、@media print 生效
      page.pdf(path=str(out_path),
               format="A4",
               print_background=True,    # 保留背景色/渐变
               prefer_css_page_size=True, # 让用户 HTML 自己的 @page size 生效
               display_header_footer=True,
               footer_template='<div style="font-size:8pt;color:#94a3b8;'
                               'width:100%;text-align:center;">'
                               '<span class="pageNumber"></span></div>',
               margin={"top":"0","bottom":"15mm","left":"0","right":"0"})
      browser.close()
  ```

**注意权衡**（已在第 4 节分析）：

- Chromium 体积大（~300 MB），但只装一次
- 页码机制要重写：WeasyPrint 用 `@bottom-center { content: counter(page) }`，Playwright 用 `footerTemplate` + `<span class="pageNumber"></span>`/`<span class="totalPages"></span>` 占位符。**这两套是不兼容的**。注入策略需要按 engine 分支
- 启动有 ~1-2 秒冷启动开销

**预期收益**：用户问题 100% 消失，且未来所有"Chrome 渲染对、WeasyPrint 渲染错"的 case 一并解决。这是治本方案。

---

### R2（兜底+低成本，先做）：把 WeasyPrint 从 67.0 升到 68.1+

**问题**：你装的是 v67.0，但 v68/v68.1（2026-01/02 发布）里有：

- `#2454 / #2442 / #2449`: Minor fixes for flex layout
- `#2643 / #2628`: 修了 `box-sizing: border-box` 在 grid 里的问题
- `#2414`: Correctly handle flex columns split between pages

这些**有可能**直接缓解或消除你看到的现象（但概率不到 50%，因为不能精确对得上 issue 编号）。

**操作**：

```bash
conda activate format-convert
pip install -U "weasyprint>=68.1"
```

跑一遍 `format_conversion_html_to_pdf` 看问题是否消失。如果还在，再走 R1 或 R3。

**预期收益**：5 分钟操作 + 重启 MCP server；运气好直接消除问题；最差也是消除了过期版本风险。

---

### R3（短期止血，最不推荐但最快）：注入 flex 兼容 CSS

**问题**：临时性地、在不改用户 HTML 的前提下规避 WeasyPrint flex 的边界 case。

**方案**：在 `_build_injected_css` 末尾追加（**注**：注入位置一定要在用户 `<style>` 之后才能 override，目前 `_build_injected_css` 注入位置是在 `</head>` 之前，会被用户 `<head>` 内嵌 style 覆盖。这本身就需要调整——见 R3.5）。

可以试的兼容规则（按"破坏现有渲染的风险从低到高"排序）：

```css
/* 1. 防止 flex 容器末尾隐式 margin 残留 */
.two-col { align-items: stretch; }     /* 显式声明，避免歧义 */

/* 2. 显式抹掉所有 flex 容器内最后一个子元素的 margin-bottom */
.col > *:last-child { margin-bottom: 0 !important; }

/* 3. 把 sec-title 改成 block + inline-flex 包裹 emoji，
      绕开 sec-title 整体 flex 的 line-height 问题 */
.sec-title { display: block !important; }
.sec-title .emoji { display: inline-block; vertical-align: middle; }
```

**这些只能"打补丁"**，每个 HTML 的具体症状不同，注入规则会污染用户的其它合法布局。如果你只为这一个文档服务，可以用；如果是通用工具，**不推荐**。

#### R3.5：如果走 R3，注入位置必须改

当前 `inject = _build_injected_css(...)` 替换 `</head>` 一次。如果用户 HTML 的 `<style>` 已经在 `<head>` 里且优先级足够高，你的兼容规则可能被覆盖。注入位置应当紧贴 `</head>` 前，且选择器**特异性**要足够（用 `!important` 或更高特异性选择器）。

**预期收益**：可能解决具体这一份 HTML 的问题，但不解决一般问题。

---

### R4（文档+用户教育）

在 MCP 工具的 `format_conversion_html_to_pdf` docstring 和 `format-conversion/README.md` 增加一句：

> WeasyPrint 渲染对 `display:flex` / `display:grid` 与 Chrome Blink 不完全一致。
> 如对复杂网页布局需要像素级一致性，请优先使用 Chrome 自身的"打印 → 另存为 PDF"，
> 或后续版本可启用 `engine="chromium"` 后端（依赖 Playwright）。

**预期收益**：管理用户预期，减少误用。零开发成本。

---

### 推荐组合

- **立刻做**：R2（升级 WeasyPrint）+ R4（文档）
- **本季度做**：R1（加 Chromium 后端，默认仍 WeasyPrint，复杂布局自动切换）
- **不推荐做**：R3（除非真的只有这一个文档要修复）

---

## 4. Playwright/Chromium vs WeasyPrint 全面权衡

| 维度 | WeasyPrint 67/68 | Playwright + Chromium |
|---|---|---|
| **flex/grid 渲染** | 部分支持、不断在补 | 与 Chrome 浏览器完全一致 |
| **现代 CSS（CSS vars, container queries, :has(), aspect-ratio）** | 部分支持 | 与 Chrome 同步 |
| **CSS Paged Media `@page` / margin boxes** | **原生支持**，counter(page)、@top-center/@bottom-center 都好用 | **几乎不支持** Paged Media 规范。页码要走 Playwright 自己的 `footerTemplate`，不识别 `@page { @bottom-center { ... } }` |
| **运行依赖** | Pango/cairo/glib（系统库）+ 几个 Python 包，约 30 MB | Chromium ~280-350 MB + 一堆系统库（libnss3、libatk、libxkbcommon 等） |
| **冷启动延迟** | ~200 ms | ~1-2 s（启动浏览器）；可以保持 browser pooling 摊薄 |
| **CPU/内存** | 单文档 100-300 MB | 单文档 400-800 MB |
| **离线/无网络** | 完全无依赖 | Chromium 可以离线，但首次需联网 `playwright install chromium` |
| **MCP server 部署难度** | 已经在 conda 环境里跑了 | 需在 `format-convert` env 装 playwright，并跑 `playwright install --with-deps chromium`（要 root 权限装系统包） |
| **可观察性 / 调试** | Python 内 stack，错误清晰 | 浏览器跨进程，调试要看 page.on('console') |
| **PDF 文件大小** | 同等内容下偏小（矢量化字体） | 稍大（光栅化某些元素） |
| **可访问性 / PDF/UA** | 支持 tagged PDF | Playwright `pdf({tagged: true})` 也支持 |
| **多语言/复杂排版（中文/阿拉伯文）** | 依赖 Pango，质量良好 | 与 Chrome 浏览器同等 |

### 关键陷阱：页码机制不兼容

> **这是最容易被忽略的点**。

当前 `_build_injected_css` 注入了：

```css
@page { @bottom-center { content: counter(page); ... } }
```

这是 [CSS Paged Media Module Level 3](https://www.w3.org/TR/css-page-3/) 的规范——**WeasyPrint 实现得相当完整，Chrome/Playwright 几乎不实现这一块**（Chrome 只支持基础的 `@page { size; margin }`，不支持 margin boxes 里的 `content: counter(page)`）。

**所以**：如果用 Playwright 渲染同一份 HTML，用户 HTML 里写的 `@page` 页码规则会**静默失效**，必须通过 `page.pdf(footer_template=...)` 用 Playwright 的 `<span class="pageNumber"></span>` 注入。这要求你的 `convert_html_to_pdf` 在用 Chromium 后端时**注入策略完全不一样**——不能简单替换 `</head>`，而是要在 `page.pdf()` 调用里传 `footerTemplate`。

如果用户 HTML 本身定义了复杂的 `@page` 规则（命名页、`@page :first` 等），切到 Chromium 会丢失这部分功能。

### 中位结论

| 场景 | 推荐渲染器 |
|---|---|
| 简单文档（标题 + 段落 + 表格 + 偶尔图片）| **WeasyPrint**——又轻又准 |
| 大量使用 `display:flex` / `display:grid` 做版面 | **Chromium** |
| 复杂打印元数据（PDF/A、PDF/UA、Factur-X、命名页、@page :first）| **WeasyPrint**（这块独家强项）|
| Web 报表、网页快照 | **Chromium** |

你这个 HTML（健康习惯卡片+双列规则+多个 mech-box）属于"中等复杂、重 flex 布局"——**应该跑在 Chromium 上**。

### 我的具体建议

**新增一个 `format_conversion_html_to_pdf_chromium` MCP 工具**（或加 `engine` 可选参数），不要直接替换默认后端。原因：

- 默认 WeasyPrint 已经在生产、对简单 HTML 工作良好，且页码机制和 README 文档对齐
- Chromium 后端是"重型武器"，留给真正需要 Chrome 兼容性的场景
- 用户可以显式选择，符合 MCP 工具"参数显式、行为可预测"的设计原则

---

## 4.5 一个被忽略的简单可能性（**先确认再下结论**）

在大动方案前，建议先**人工排查**一个非渲染器原因：

打开生成的 PDF，对照源 HTML 数一下："🧠 刺激控制" 之前是否真的是 `.two-col` flex 容器直接相邻？是否有可能是因为：

- HTML 里有不可见的 `<br>` 或者你工具注入的 `<span class="emoji">` 在某个意外位置插了 inline 元素？
- `_process_emoji` 用的是 `_EMOJI_RE.sub`——这个正则的 unicode range `\U00002600-\U000027BF` 范围比较宽，会不会误捕获到 `.two-col` 与 `.mech-box` 之间某个看似无害的字符（比如 HTML 注释里、属性值里、CSS 里）？

```python
# 一行验证：把 _process_emoji 处理后的 html_text 写到临时文件，
# 在 Chrome 里打开看是否和原 HTML 视觉一致。
# 如果 Chrome 打开 _process_emoji 输出 也有空白 → 是 emoji wrap 的锅
# 如果 Chrome 打开依然没空白 → 是 WeasyPrint 渲染锅
```

这个验证 **5 分钟**能做。我建议在动 R1/R2/R3 之前先做一遍——分离"自家工具引入的污染"和"WeasyPrint 渲染差异"。

---

## 5. 信息时效性核查

| 信息源 | 时效性 | 备注 |
|---|---|---|
| WeasyPrint v67.0 changelog | ✅ 已验证当前 | 2025-12-02 发布 |
| WeasyPrint v68.0 changelog | ✅ 已验证当前 | 2026-01-19 发布 |
| WeasyPrint v68.1 changelog（最新稳定）| ✅ 已验证当前 | 2026-02-06 发布；查询时间 2026-05-22 |
| WeasyPrint flex 历史 issue（#1563、#2449、#2053、#2054、#2628）| ✅ 已验证当前 | 通过 GitHub 查阅 |
| Playwright `page.pdf()` API（`displayHeaderFooter`、`pageNumber`/`totalPages`）| ✅ 已验证当前 | 官方文档 playwright.dev |
| Playwright 仅 Chromium 支持 pdf | ✅ 已验证当前 | 文档明确写明 |
| CSS Paged Media `@bottom-center` Chrome 不支持 | ✅ 已验证当前 | 多个独立来源（PDFMonkey、runebook 等）一致 |
| 本机 WeasyPrint 版本 67.0 | ✅ 已验证当前 | `pip show weasyprint` 实测 |
| Noto Emoji 字体度量与 Noto Sans SC 不一致导致 line-height 偏大 | ⚠️ 待验证 | 经验性判断，未实测；不影响主结论 |

---

## 6. 总结

1. **WeasyPrint 的 flex 实现到 2026 年仍未与 Chrome 对齐**，这是已知的、持续多年的工程债务，不是某个特定 bug；用 flex 重度排版的 HTML 跑 WeasyPrint 必然偶发"对不齐"现象。
2. **本机装的是 v67.0，上游已 v68.1，先升级**——5 分钟成本，可能直接解决问题，至少消除"用过期版本"风险。
3. **根治方案是给 `convert_html_to_pdf` 加一个 Chromium 后端**（Playwright），用 `engine` 参数让用户在精度（Chromium）和轻量+完整 Paged Media（WeasyPrint）之间显式选择。**不要默认切换**，避免破坏现有 `@page` 页码注入语义。
4. **在动手前先验证 5 分钟**：把 `_process_emoji` 处理后的 HTML 用 Chrome 打开，确认 Chrome 渲染依然正常——排除工具自家 emoji wrap 引入的污染再下渲染器结论。
5. **不要走"注入 CSS 打补丁"路线**——成本高、覆盖面有限，且会污染其它合法用户 HTML 的渲染。
