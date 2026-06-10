# Qwen Vision — 视觉分析 MCP 服务

通过 OpenRouter 调用 Qwen-3.7-Plus 的视觉能力，给纯文本模型（如 DeepSeek V4）提供 image-to-text 补充。

纯 API 调用，无需本地 GPU。OpenRouter API key 通过环境变量注入。

---

## MCP 工具

| 工具 | 用途 | 典型场景 |
|---|---|---|
| `analyze_image` | 通用图片分析（可自定义 prompt） | 截图理解、UI 描述、图片问答 |
| `extract_text_from_image` | OCR 式文字提取 | 扫描件、幻灯片、收据文字提取 |
| `analyze_chart` | 图表/数据可视化分析 | 柱状图、折线图、饼图数据解读 |
| `analyze_pdf` | PDF 分析（逐页渲染为图片后分析） | PDF 文档理解、幻灯片分析、颜色/布局识别 |

---

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `OPENROUTER_API_KEY` | ✅ | — | OpenRouter API key |
| `QWEN_VISION_MODEL` | | `qwen/qwen3.7-plus` | 模型 ID |
| `OPENROUTER_BASE_URL` | | `https://openrouter.ai/api/v1/chat/completions` | API 端点 |
| `QWEN_VISION_REFERER` | | `https://github.com/lanxiukai/mcp-tools` | HTTP Referer header |
| `QWEN_VISION_APP_TITLE` | | `mcp-tools-qwen-vision` | X-Title header |

---

## opencode.jsonc 配置

```jsonc
"qwen_vision": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/qwen-vision/qwen_vision_mcp_server.py"],
  "enabled": true,
  "timeout": 120000
}
```

`<YOUR-PYTHON>` 可为系统 Python 或任意 `mcp>=1.0.0` 已安装的环境。

同时确保 `OPENROUTER_API_KEY` 在你的环境变量中已设置。可在 `opencode.jsonc` 的 `mcp` 块中通过 `env` 字段注入：

```jsonc
"qwen_vision": {
  "type": "local",
  "command": ["python3", "/path/to/mcp-tools/qwen-vision/qwen_vision_mcp_server.py"],
  "enabled": true,
  "timeout": 120000,
  "env": {
    "OPENROUTER_API_KEY": "${env:OPENROUTER_API_KEY}"
  }
}
```

Agent 权限（在对应 agent 的 `permission` 块中添加）：

```jsonc
"analyze_image": "allow",
"extract_text_from_image": "allow",
"analyze_chart": "allow",
"analyze_pdf": "allow"
```

---

## Agent 使用示例

```python
# 描述图片
analyze_image("/home/user/screenshot.png")
# → {"text": "This is a login form with...", "model": "qwen/qwen3.7-plus", "usage": {...}}

# 自定义问题
analyze_image("/home/user/diagram.jpg", prompt="这个架构图中的数据流是怎样的？")
# → {"text": "数据从客户端经 API Gateway...", ...}

# 提取文字
extract_text_from_image("/home/user/slide.png")
# → {"text": "Q3 Revenue Report\nTotal: $1.2M\n...", ...}

# 分析图表
analyze_chart("/home/user/sales-chart.png")
# → {"text": "1. CHART TYPE: Line chart\n2. AXES: ...", ...}

# 分析 PDF（保留颜色/图片/布局信息）
analyze_pdf("/home/user/report.pdf", prompt="描述每页的布局和图表配色")
# → {"text": "Page 1: ...", "page_count": 12, "rendered_pages": 5, ...}

# PDF 只分析前 3 页
analyze_pdf("/home/user/slides.pdf", max_pages=3)
```

---

## 设计意图

**为什么是 MCP 而不是 subagent？**

DeepSeek V4 等纯文本模型无法原生接收图片输入。MCP 工具模式解决了这个问题：主 agent 看到用户提供的文件路径 → 调用 MCP tool → 拿到文字 → 继续推理。整个过程对主 agent 而言和调用 `read` 或 `grep` 没有区别。

Qwen-3.7-Plus 的视觉能力（OmniDocBench 91.4、CharXiv RQ 85.9、ScreenSpot Pro 79.0）在这些 benchmark 上位于前沿水平，是当前最适合作为纯文本模型视觉补充的模型之一。

---

## 依赖

仅需 Python 3.10+ 和 `mcp>=1.0.0`：

```bash
pip install "mcp>=1.0.0"
```

无其他外部依赖——图片编码使用标准库 `base64`，HTTP 调用使用标准库 `urllib`。

---

## 文件

| 文件 | 用途 |
|---|---|
| `qwen_vision_mcp_server.py` | MCP stdio 前端（单文件，无外部后端） |
| `README.md` | 本文档 |
