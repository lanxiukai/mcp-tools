# MCP Tools — 开箱即用的 AI Agent 工具集

为 [ai-agent-framework](https://github.com/your-org/ai-agent-framework) 设计的本地 MCP (Model Context Protocol) 工具集，为 OpenCode agent 提供语音转文字、文档 OCR 解析、图片理解等本地能力——适用于纯文本模型，或需要离线 / 隐私 / 零成本处理的场景。如果你使用多模态模型，可直接依赖其原生能力处理同类任务，本工具集为可选补充。

> **依赖**: ai-agent-framework >= **v0.3.4**

## 工具概览

| 工具 | 功能 | 模型 | 显存 |
|---|---|---|---|
| **Qwen3-ASR** | 语音转文字（52 语言） | Qwen3-ASR-1.7B | ~3.5 GB |
| 　├ `transcribe_audio` | 快捷转写（支持长音频） | | |
| 　└ `transcribe_podcast` | 转写 + 说话人分离 | + pyannote | +2~4 GB |
| **GLM-OCR** | 文档解析（图片/PDF → Markdown） | GLM-OCR 0.9B | ~2.5 GB |
| **QwenVision** | 图片内容描述 | Qwen3.6-35B-A3B (MoE) | ~8 GB |
| **ASR Pipeline** | 播客长音频转写 + 说话人分离 | Qwen3-ASR + pyannote | ~6 GB |

## 前置条件

- **操作系统**: Linux (Ubuntu 22.04+) 或 WSL2
- **GPU**: NVIDIA GPU，建议 ≥ 12 GB 显存
- **CUDA**: 12.4+
- **conda / mamba**: 用于环境管理
- **[ai-agent-framework](https://github.com/your-org/ai-agent-framework) >= v0.3.4**: 提供 MCP 编排和 agent 权限管理

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/mcp-tools.git
cd mcp-tools

# 2. 一键安装
bash install.sh

# 3. 配置 OpenCode
# 将 install.sh 输出的配置片段复制到 ~/.config/opencode/opencode.jsonc 的 "mcp" 块中

# 4. 重启 OpenCode 即可使用
```

> 首次调用工具时，HuggingFace 会自动下载模型权重到本地缓存。你也可以运行 `bash install.sh` 预下载。

---

## 工具详情

### 1. Qwen3-ASR — 语音转文字

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

### 2. GLM-OCR — 文档解析

调用 `ocr_glm()` 将图片/PDF 解析为结构化 Markdown，支持中英文、手写体、公式（LaTeX）、表格。

```python
ocr_glm("/home/user/report.pdf")                    # → Markdown（含 LaTeX 公式）
ocr_glm("/home/user/whiteboard.png")                # → 手写文字识别
ocr_glm("/home/user/scan.jpg", output_format="json") # → 结构化 JSON
ocr_glm_status()                                     # 查看服务状态
```

**模型**: GLM-OCR 0.9B（HuggingFace 自动下载，约 2.5GB）

**opencode.jsonc 配置**:
```jsonc
"glm_ocr": {
  "type": "local",
  "command": "<YOUR-PYTHON>",
  "args": ["<REPO-DIR>/ocr/glm_ocr_mcp_server.py"],
  "enabled": true,
  "timeout": 15000
}
```

---

### 3. QwenVision — 图片内容描述

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

### 4. ASR Pipeline — 播客长音频转写

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

**关键参数**:

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--no-timestamps` | off | 跳过词级时间戳，长音频推荐 |
| `--no-diarize` | off | 跳过说话人分离（单声道内容可加速） |
| `--num-speakers` N | 自动 | 限定最大说话人数 |
| `--max-new-tokens` | 4096 | 生成 token 上限，长音频建议 4096-8192 |
| `--batch-size` | 1 | 推理批量，≥16GB 显存可设为 2 |

**实测性能**（RTX 4070 Ti 12GB）：

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

## 目录结构

```
mcp-tools/
├── README.md
├── install.sh                 # 一键安装脚本
├── docs/                      # 工具格式与测试文档
│   ├── qwen3-asr-audio-formats.md
│   ├── glm-ocr-formats.md
│   ├── qwen-vision-formats.md
│   ├── asr-pipeline-formats.md
│   ├── mcp-tools-testing.md   #   工具使用与测试指南
│   └── tools-verification-report.md  # 性能/准确性验证报告
├── mcp-tool-test/             # 测试样本（91 文件 179MB）
│   ├── README.md              #   样本目录说明
│   ├── smoke-test/            #   冒烟测试（4 文件 3.9MB）
│   ├── ocr/                   #   OCR 样本（23 个）
│   ├── asr/                   #   ASR 样本（43 个）
│   └── vl/                    #   VL 样本（25 个）
├── asr/                       # Qwen3-ASR MCP 工具
│   ├── asr_mcp_server.py      #   MCP Server (OpenCode 入口)
│   ├── qwen3_asr_server.py    #   FastAPI 后端服务
│   └── qwen3_asr_start.sh     #   启动/停止/状态管理
├── ocr/                       # GLM-OCR MCP 工具
│   ├── glm_ocr_mcp_server.py  #   MCP Server
│   ├── glm_ocr_server.py      #   FastAPI 后端服务
│   └── glm_ocr_start.sh       #   启动/停止/状态管理
├── vl/                        # QwenVision MCP 工具
│   ├── vision_mcp_server.py   #   MCP Server
│   ├── llama_start.sh         #   llama-server 管理脚本
│   └── llama_start_guide.md   #   使用指南
└── asr-pipeline/              # 播客长音频转写管线
    ├── pipeline.py            #   CLI 入口
    ├── preprocess.py          #   音频预处理 (ffmpeg)
    ├── diarize.py             #   说话人分离 (pyannote)
    ├── transcribe.py          #   ASR + 时间轴对齐
    ├── merge.py               #   合并 + 格式输出
    ├── test_pipeline.py       #   测试
    └── docs/
        └── pyannote-setup.md  #   pyannote 设置指南
```

## 测试

本仓库提供一套完整的测试样本和工具使用指南，方便验证各 MCP 工具是否正常工作。

### 冒烟测试（快速验证）

4 个极简文件，总计 <4 MB，适合每次部署/改代码后快速跑一遍：

```bash
# OCR 冒烟测试（公式图 → Markdown）
ocr_glm("mcp-tool-test/smoke-test/ocr_smoke_test.png")

# ASR 冒烟测试（6 秒英文短句 → 转写文本）
transcribe_audio("mcp-tool-test/smoke-test/asr_smoke_test.wav")

# VL 冒烟测试（办公桌照片 → 英文描述）
describe_image("mcp-tool-test/smoke-test/vl_smoke_test.jpg")

# ASR Pipeline 冒烟测试（可选，3.5 分钟演讲）
python asr-pipeline/pipeline.py mcp-tool-test/smoke-test/pipeline_smoke_test.mp3 \
  --language English --no-diarize --no-timestamps -o /tmp/pipeline_test/
```

### 完整测试样本

`mcp-tool-test/` 目录包含 **91 个公开样本**（约 179 MB），覆盖中英印刷体/手写体、公式、多场景播客音频、生活照片。详见 [`mcp-tool-test/README.md`](mcp-tool-test/README.md)。

### 工具使用与测试指南

每个工具的详细 API、参数、测试文件说明，见 [`docs/mcp-tools-testing.md`](docs/mcp-tools-testing.md)。

## 架构

每个 MCP 工具由两部分组成：

```
OpenCode Agent
    │ MCP stdio
    ▼
MCP Server (asr_mcp_server.py)   ← 轻量前端，stdio 通信，自动唤醒后端
    │ HTTP REST
    ▼
FastAPI Server (qwen3_asr_server.py)  ← GPU 推理后端，有独立的启停脚本
    │
    ▼
HuggingFace Model (自动下载/缓存)
```

**自动唤醒**: MCP Server 首次被调用时，自动检测后端是否在线，离线则后台启动并轮询等待就绪。后端空闲超时后自动释放 GPU。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ASR_PORT` | `8000` | ASR 服务端口 |
| `ASR_HOST` | `localhost` | ASR 服务地址 |
| `OCR_PORT` | `8002` | OCR 服务端口 |
| `OCR_HOST` | `localhost` | OCR 服务地址 |
| `HF_TOKEN` | — | HuggingFace token（pyannote 需要） |
| `MCP_PYTHON` | — | 覆盖启动脚本中的 Python 路径 |

## 版本更新

| 版本 | 日期 | 变更概述 |
|---|---|---|---|
| v0.2.0 | 2026-05-13 | ASR 长音频全线支持：MCP 服务端 + pipeline 480s 分块、diarization 900s 分块；修复 4 个 bug（max_new_tokens 256→4096、device_map GPU 缺失、batch_size 8→1、单 chunk 1200s OOM）；新增 `--no-timestamps`/`--max-new-tokens`/`--batch-size` CLI；新增 `transcribe_podcast` MCP 工具；全仓文档审计（13 文件 628 行）；opencode 配置同步（timeout 30min + permissions）；RTX 4070 Ti 12GB 实测 2h 播客 19-23min |
| v0.1.1 | 2026-05-12 | 修复 MCP 启动与显存管理：竞态杀互斥加载（防 12GB OOM）、3 服务统一 30s 空闲超时、修复 bash shift + set -e 静默退出 bug |
| v0.1.0 | 2026-05-12 | 初始发布：4 个 MCP 工具（ASR/OCR/Vision/ASR Pipeline），含 91 个测试样本、冒烟测试与一键安装脚本 |

## License

MIT
