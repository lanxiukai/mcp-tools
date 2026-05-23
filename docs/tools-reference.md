# 工具参考手册

本文档列出 4 个 MCP 工具的 API、配置、模型说明与性能数据。工具的使用方法、测试文件与冒烟测试见 [`docs/mcp-tools-testing.md`](mcp-tools-testing.md)。

---

## 1. Qwen3-ASR — 语音转文字

调用 `transcribe_audio()` 将音频文件转写为文本，支持 52 种语言。短音频秒级响应，长音频（2h+）通过 480s 分块 + GPU 加速自动处理。

```python
# Agent 直接调用
transcribe_audio("/home/user/interview.mp3")               # 自动语言检测
transcribe_audio("/home/user/meeting.wav", language="zh")  # 指定中文
transcribe_audio("/home/user/long_podcast.mp3", language="en")  # 长音频也支持

# 播客模式：转写 + 说话人分离（需 HF_TOKEN）
transcribe_podcast("/home/user/podcast.mp3", language="en", num_speakers=3)

asr_status()                                                # 查看服务状态
```

**模型**: Qwen3-ASR-1.7B（HuggingFace 自动下载，约 3.4GB）

**opencode.jsonc 配置**:
```jsonc
"qwen3_asr": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/asr/asr_mcp_server.py"],
  "enabled": true,
  "timeout": 15000
}
```

---

## 2. GLM-OCR — 文档解析

调用 `ocr_glm()` 将图片/PDF 解析为结构化 Markdown，支持中英文、手写体、公式（LaTeX）、表格。多页 PDF 自动使用异步提交 + 轮询，避免 MCP 超时。

```python
ocr_glm("/home/user/report.pdf")                    # → Markdown（含 LaTeX 公式 / 表格），自动保存 _ocr.md
ocr_glm("/home/user/whiteboard.png")                # → 手写文字识别，自动保存 .md
ocr_glm("/home/user/scan.jpg", output_format="json") # → 结构化 JSON
ocr_glm("/home/user/doc.pdf", save_markdown=False)   # → 仅返回文本，不保存文件

# 异步提交模式（适合并行处理多个大 PDF）
result = ocr_glm_submit("/home/user/large.pdf")      # → {job_id, total_pages}（立即返回）
ocr_glm_status(job_id=result["job_id"])              # → 查看进度
ocr_glm_wait(result["job_id"])                       # → 等待完成并返回结果（可设 max_wait）

ocr_glm_status()                                      # 查看服务状态 + GPU 占用
```

**模型**: GLM-OCR 0.9B（HuggingFace 自动下载，约 2.5GB）

**opencode.jsonc 配置**:
```jsonc
"glm_ocr": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/ocr/glm_ocr_mcp_server.py"],
  "enabled": true,
  "timeout": 1800000
}
```

> **timeout 必须设为 30 分钟（1800000ms）**。PDF 逐页 OCR 耗时约 15-25 秒/页（300 DPI VLM 推理），27 页约 9 分钟。短 timeout 会导致轮询超时。

---

## 3. QwenVision — 图片内容描述

调用 `describe_image()` 使用 Qwen3.6-35B-A3B 多模态模型获取图片的英文描述。

```python
describe_image("/home/user/photo.jpg")   # → 详细英文描述
vision_status()                           # 查看 llama-server 状态
```

**模型**: Qwen3.6-35B-A3B GGUF (Q4_K_XL 量化，约 22GB)。需手动下载到 `~/.llama/models/`：

```bash
huggingface-cli download unsloth/Qwen3.6-35B-A3B-GGUF \
  Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
  mmproj-F16.gguf \
  --local-dir ~/.llama/models/
```

**opencode.jsonc 配置**:
```jsonc
"qwen_vision": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/vl/vision_mcp_server.py"],
  "enabled": true,
  "timeout": 15000
}
```

---

## 4. ASR Pipeline — 播客长音频转写

离线批处理 CLI 工具，将 2-3 小时的播客长音频转写为带**说话人标注**和**词级时间戳**的结构化文本。内置 480s 分块策略，12GB 显存即可稳定运行。

```bash
# 基本用法
python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./output/

# 长音频加速（推荐 1h+）：跳过词级时间戳，提速 4×+
python asr-pipeline/pipeline.py long_podcast.mp3 --language English --no-timestamps -o ./output/

# 多人对话 + 限定说话人数
python asr-pipeline/pipeline.py meeting.mp3 --language English --num-speakers 3 -o ./output/

# 中文播客 + 术语注入
python asr-pipeline/pipeline.py interview.mp3 --language Chinese --context "人工智能 深度学习" -o ./output/

# 跳过说话人分离
python asr-pipeline/pipeline.py lecture.wav --no-diarize

# 自定义 token 预算（2h+ 音频建议 4096）
python asr-pipeline/pipeline.py podcast.mp3 --language English --max-new-tokens 4096

# 输出格式选择
python asr-pipeline/pipeline.py audio.mp3 --format json  # json/srt/txt/all
```

### 关键参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--no-timestamps` | off | 跳过词级时间戳，长音频推荐 |
| `--no-diarize` | off | 跳过说话人分离（单声道内容可加速） |
| `--num-speakers` N | 自动 | 限定最大说话人数 |
| `--max-new-tokens` | 4096 | 生成 token 上限，长音频建议 4096-8192 |
| `--batch-size` | 1 | 推理批量，≥16GB 显存可设为 2 |

### 实测性能（RTX 4070 Ti 12GB）

| 场景 | 用时时长 | 吞吐 |
|---|---|---|
| 22 分钟演讲（含说话人分离） | ~4 分钟 | 5.7× |
| 2 小时播客（含说话人分离，1002 段） | ~23 分钟 | 5.2× |
| 2 小时播客（无说话人分离） | ~19 分钟 | 5.9× |

**产物**: JSON（metadata + segments + full_text）、SRT（字幕）、TXT（纯文本）

**说话人分离**需要 pyannote.audio 访问权限：
1. 在 [hf.co/pyannote](https://hf.co/pyannote) 接受模型条款
2. 设置 `HF_TOKEN` 环境变量

---

## 5. Format Conversion — 文档格式转换

纯 CPU 工具集，提供 Markdown/HTML → PDF 和 PDF → 纯文本。

### HTML → PDF

```python
html_to_pdf("/home/user/doc.html")                      # 默认 engine="chromium"
html_to_pdf("/home/user/doc.html", engine="weasyprint")  # 轻量后端（适合简单文档）
html_to_pdf("/home/user/doc.html", engine="chromium")    # Pixel-identical to Chrome
```

**引擎对比**：

| 维度 | WeasyPrint | Chromium |
|---|---|---|
| flex/grid 布局 | 部分支持（与 Chrome 不对齐） | 完全一致 |
| 页码 | CSS `@page @bottom-center` | CSS `@page @bottom-center`（Chrome 131+） |
| 依赖 | cairo/pango（~30 MB） | Playwright + Chromium（~300 MB） |
| 冷启动 | ~200 ms | ~1-2 s |
| 适用场景 | 简单文档、Paged Media | 现代网页布局、flex/grid 视觉一致性 |

### Markdown → PDF

```python
markdown_to_pdf("/home/user/doc.md")  # markdown-it-py + WeasyPrint
```

### PDF → Text

```python
pdf_to_text("/home/user/report.pdf")           # 默认自动保存 .txt 到同目录
pdf_to_text("/home/user/report.pdf", save_text=False)  # 仅返回文本，不保存
```

### opencode.jsonc 配置

```jsonc
"format_conversion": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/format-conversion/format_mcp_server.py"],
  "enabled": true,
  "timeout": 120000
}
```
