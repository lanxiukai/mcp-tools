# GLM-OCR 文档解析格式支持详情

> 数据来源：本仓库 `ocr/glm_ocr_server.py` + `ocr/glm_ocr_mcp_server.py` 源码  
> 最后更新：2026-05-11

---

## 1. MCP 工具接口

通过 `opencode.jsonc` 配置后，agent 可调用以下 MCP 工具：

| 工具 | 参数 | 说明 |
|---|---|---|
| `ocr_glm(file_path, output_format?)` | `file_path`（必填，文档绝对路径）, `output_format`（可选，默认 `"markdown"`） | 解析文档，返回结构化 OCR 结果 |
| `ocr_glm_status()` | 无 | 返回服务状态（模型、GPU 显存占用等） |

**自动唤醒**：首次调用 `ocr_glm` 时，MCP Server 自动检测后端是否在线；离线则后台启动并轮询等待就绪（最长 90s）。后端无请求 30 秒后自动退出释放 GPU。

---

## 2. 文件格式

### 2.1 支持列表

| 格式 | 扩展名 | 备注 |
|---|---|---|
| PNG | `.png` | 推荐无损格式 |
| JPEG / JPG | `.jpg`, `.jpeg` | 广泛兼容 |
| BMP | `.bmp` | 无损位图 |
| TIFF | `.tiff`, `.tif` | 多页扫描文档常用 |
| WEBP | `.webp` | 体积更小的现代格式 |
| PDF | `.pdf` | 多页文档，需 `pymupdf` |

### 2.2 不支持的格式

以下格式**不在**白名单中，调用会返回 `error`：

```
Unsupported file type: .xxx. Supported: .bmp, .jpeg, .jpg, .pdf, .png, .tif, .tiff, .webp
```

---

## 3. 输出格式

`ocr_glm` 工具支持两种输出格式：

### 3.1 Markdown（默认）

```python
ocr_glm("/home/user/report.pdf")               # 返回纯 Markdown 文本
ocr_glm("/home/user/whiteboard.png")           # 同上
```

返回结构：
```json
{
  "markdown": "# 第一章\n\n这是文档内容...\n\n| 列1 | 列2 |\n|---|---|\n| A | B |\n\n$$E=mc^2$$",
  "_note": "Formulas are in LaTeX format (e.g., $E=mc^2$ for inline, $$...$$ for display). Tables are in Markdown table format."
}
```

### 3.2 JSON（完整结构）

```python
ocr_glm("/home/user/scan.jpg", output_format="json")
```

返回结构：
```json
{
  "success": true,
  "model": "zai-org/GLM-OCR",
  "input_path": "scan.jpg",
  "page_count": 3,
  "markdown": "## Page 1\n\n...\n\n---\n\n## Page 2\n\n...",
  "pages": [
    {"page_index": 0, "markdown": "..."},
    {"page_index": 1, "markdown": "..."},
    {"page_index": 2, "markdown": "..."}
  ]
}
```

---

## 4. 支持的内容类型

GLM-OCR 是 prompt-driven VLM，通过内置 prompt `"Text Recognition:"` 触发识别：

| 内容类型 | 说明 | 输出格式 |
|---|---|---|
| 印刷体文字 | 中英文混排、多栏布局 | 纯文本 |
| 手写体 | 笔记、批注、板书 | 纯文本 |
| 数学公式 | 行内 / 独立公式 | `$...$`（行内）, `$$...$$`（独立） |
| 表格 | 有线/无线表格 | Markdown 表格格式 |
| 多栏排版 | 报纸、学术论文 | 自然阅读顺序 |

---

## 5. PDF 多页处理

PDF 解析流程（`_predict_pdf` 方法）：

```
PDF 文件
  → pymupdf (fitz) 逐页渲染
  → 300 DPI 位图
  → 逐页 OCR（每页独立调用 GLM-OCR）
  → 多页拼接："\n\n---\n\n".join(all_markdown)
```

| 属性 | 值 |
|---|---|
| 渲染引擎 | `pymupdf` (fitz) |
| 渲染 DPI | 300 |
| 像素格式 | RGB |
| 多页分隔符 | `\n\n---\n\n` |
| 最大页数 | 无硬限制（受 GPU 显存/时间约束） |

> **依赖**：PDF 解析需要 `pymupdf`。未安装时调用 `ocr_glm` 传入 PDF 会返回 `RuntimeError`，错误消息中会提示 `pip install pymupdf`。

---

## 6. 模型详情

| 属性 | 值 |
|---|---|
| 模型 | `zai-org/GLM-OCR` |
| 架构 | VLM (Vision-Language Model) |
| 参数量 | 0.9B |
| 数据类型 | bfloat16 |
| 注意力实现 | flash_attention_2 → sdpa（自动回退） |
| 最大生成 token | 4096（`max_new_tokens=4096`） |
| 解码策略 | 贪心解码（`do_sample=False`） |
| GPU 显存占用 | ~2.5 GB |
| 设备 | cuda（默认）/ cpu |

### 6.1 注意力实现回退

启动时按以下顺序尝试：

1. `flash_attention_2` — 最快，需安装 `flash-attn`
2. `sdpa`（PyTorch 内建）— 回退方案，无需额外依赖

日志示例：
```
Trying flash_attention_2 ...
flash_attention_2 OK
```

或回退时：
```
flash_attention_2 failed (...), falling back to sdpa
sdpa fallback OK
```

---

## 7. 图片预处理

对每张输入图片，OCR 执行前自动进行预处理：

| 步骤 | 说明 |
|---|---|
| 色彩空间 | 非 RGB 图片自动 `convert("RGB")`，确保 VLM 输入一致性 |
| 缩放 | 不做缩放，保持原始分辨率 |
| 格式限制 | 无硬性尺寸限制 |

---

## 8. 性能参考

| 场景 | 预估时间 | 说明 |
|---|---|---|
| 单张图片（A4, 300 DPI） | 1~5 秒 | 取决于文本密度 |
| 手写笔记 | 2~6 秒 | 手写体识别稍慢 |
| PDF（10 页） | 10~50 秒 | 线性累加，每页约 1~5 秒 |
| 复杂表格 + 公式 | 3~8 秒 | 结构化输出解析时间更长 |

---

## 9. 服务架构

```
OpenCode Agent
    │ MCP stdio
    ▼
MCP Server (glm_ocr_mcp_server.py)   ← 轻量前端，自动唤醒 REST 后端
    │ HTTP REST
    ▼
FastAPI Server (glm_ocr_server.py)   ← GPU 推理后端，独立启停脚本
    │
    ▼
GLM-OCR 0.9B (HuggingFace 自动下载)
```

- **REST 端口**：`8002`（可通过 `OCR_PORT` 环境变量调整）
- **空闲超时**：30 秒无请求后自动退出释放 GPU（可通过 `OCR_IDLE_TIMEOUT` 环境变量调整）
- **日志**：`/tmp/glm-ocr-server.log`
