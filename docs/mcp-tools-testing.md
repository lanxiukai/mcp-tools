# MCP 工具使用与测试指南

本文档整合 2 个 MCP 工具 + ASR Pipeline 的 API 说明、使用方法，以及对应的测试文件路径。

> 各工具底层格式的详细说明见对应的格式文档：
> - `qwen3-asr-audio-formats.md` — ASR 音频格式、语言支持、切块机制
> - `glm-ocr-formats.md` — OCR 图片/PDF 格式、输出格式、公式处理
> - `asr-pipeline-formats.md` — Pipeline 管线阶段、输出格式、说话人分离

---

## 目录

- [1. Qwen3-ASR — 语音转文字](#1-qwen3-asr--语音转文字)
- [2. GLM-OCR — 文档解析](#2-glm-ocr--文档解析)
- [3. ASR Pipeline — 长音频转写管线（可选）](#3-asr-pipeline--长音频转写管线可选)
- [4. 冒烟测试](#4-冒烟测试)
- [5. 测试样本目录](#5-测试样本目录)

---

## 1. Qwen3-ASR — 语音转文字

### 1.1 功能概述

将音频文件转写为文本，支持 **52 种语言**，自动语种检测。模型：Qwen3-ASR-1.7B，显存 ~3.5 GB。

### 1.2 MCP 接口

| 调用 | 参数 | 返回值 |
|------|------|--------|
| `transcribe_audio(file_path, language?)` | `file_path`: 音频绝对路径；`language`: 可选 `"en"` / `"zh"` 等 | `{"text": "...", "language": "zh"}` |
| `asr_status()` | 无 | 服务状态（模型名、GPU 显存） |

**参数说明**：
- `file_path`（必填）：本地音频文件绝对路径。支持 WAV / MP3 / FLAC / OGG / M4A 等。
- `language`（可选）：不传则自动检测语种；显式指定可提升转写准确率。常用值：`"en"`、`"zh"`、`"ja"`、`"ko"`。

**自动唤醒**：首次调用时后台启动 REST 服务（最长 60 秒）。后端无请求 30 秒后自动释放 GPU。

### 1.3 使用示例

```python
# 英文短句转写（自动检测语言）
transcribe_audio("/home/user/interview.mp3")

# 中文音频显式指定语言
transcribe_audio("/home/user/meeting.wav", language="zh")

# 中英夹杂自动检测
transcribe_audio("/home/user/mixed_talk.m4a")

# 查看服务状态
asr_status()
```

### 1.4 测试文件

| 场景 | 测试文件 | 大小 | 内容 |
|------|----------|------|------|
| 冒烟测试 | `mcp-tool-test/smoke-test/asr_smoke_test.wav` | 327 KB | 6 秒英文朗读短句，22050 Hz mono |
| 英文播客（单人） | `mcp-tool-test/asr/podcast/en_single/greatinventors_01_watt_steam.mp3` | 10.8 MB | 詹姆斯·瓦特与蒸汽机 (~24 分钟) |
| 英文演讲 | `mcp-tool-test/asr/podcast/en_dialogue/JFK_inaugural_address.mp3` | 11.1 MB | 肯尼迪就职演说 (~14 分钟) |
| 中英日常（单人） | `mcp-tool-test/asr/daily/zh_en_single/*.wav` | 合计 2.1 MB | CS-Dialogue 短片段 (1-5 秒) |
| 中英播客（多人） | `mcp-tool-test/asr/podcast/zh_en_dialogue/*.wav` | 合计 1.8 MB | CS-Dialogue 不同说话人 |

> **预期通过标准**：冒烟测试返回 `"The examination and testimony of the experts enabled the Commission to conclude that five shots may have been fired."`

---

## 2. GLM-OCR — 文档解析

### 2.1 功能概述

将图片/PDF 解析为结构化 Markdown，支持中英文、手写体、LaTeX 公式、表格。模型：GLM-OCR 0.9B，显存 ~2.5 GB。

### 2.2 MCP 接口

| 调用 | 参数 | 返回值 |
|------|------|--------|
| `ocr_glm(file_path, output_format?)` | `file_path`: 文档绝对路径；`output_format`: `"markdown"`（默认）或 `"json"` | 结构化 OCR 结果 |
| `ocr_glm_status()` | 无 | 服务状态 |

**参数说明**：
- `file_path`（必填）：本地图片/PDF 绝对路径。支持 PNG / JPG / JPEG / BMP / GIF / PDF。
- `output_format`（可选）：`"markdown"` 返回含 LaTeX 公式的 Markdown；`"json"` 返回结构化 JSON。

**自动唤醒**：首次调用时后台启动 REST 服务（最长 90 秒）。后端无请求 30 秒后自动释放 GPU。

### 2.3 使用示例

```python
# 图片 → Markdown（默认）
ocr_glm("/home/user/scan.jpg")

# PDF → Markdown（含公式）
ocr_glm("/home/user/report.pdf")

# 手写体 → Markdown
ocr_glm("/home/user/whiteboard.png")

# 输出 JSON 格式
ocr_glm("/home/user/document.png", output_format="json")

# 查看服务状态
ocr_glm_status()
```

### 2.4 测试文件

| 场景 | 测试文件 | 大小 | 内容 |
|------|----------|------|------|
| 冒烟测试 | `mcp-tool-test/smoke-test/ocr_smoke_test.png` | 15 KB | 数学公式图（含 f(x)=x²+2x+1 等 5 个公式） |
| 英文印刷体 | `mcp-tool-test/ocr/printed/en/us_constitution_page1.png` | 1.9 MB | 美国宪法首页 |
| 中文印刷体 | `mcp-tool-test/ocr/printed/zh/taipei_taxi_fare.jpg` | 1.0 MB | 现代横排中文费率表 |
| 公式印刷体 | `mcp-tool-test/ocr/printed/formulas/pure_math_blackboard.jpg` | 167 KB | 黑板上代数/微积分公式 |
| 英文手写体 | `mcp-tool-test/ocr/handwriting/en/willa_cather_letter.png` | 339 KB | 1905 年草书手信 |
| 中文书法 | `mcp-tool-test/ocr/handwriting/zh/boyuan_calligraphy.jpg` | 3.6 MB | 王珣《伯远帖》行书 |
| 公式手写体 | `mcp-tool-test/ocr/handwriting/formulas/einstein_blackboard.jpg` | 846 KB | 爱因斯坦宇宙学公式黑板 |
| 扫描 PDF | `mcp-tool-test/ocr/pdf/scanned_chinese_yuzhidaao.pdf` | 2.5 MB | 清代刻本（96 页，竖排中文） |
| 公式 PDF | `mcp-tool-test/ocr/pdf/scanned_formulas_trigonometry.pdf` | 2.0 MB | 三角学教科书 (1896, 135 页) |

> **预期通过标准**：冒烟测试返回结构化 Markdown，含标题 "OCR Smoke Test - Math Formulas" 及全部数学公式的 LaTeX 表示（上标、分式等）。

---

## 3. ASR Pipeline — 长音频转写管线（可选）

### 4.1 功能概述

离线批处理 CLI 工具，提供**四阶段管线**和**说话人分离**能力。既支持长音频（通过 `--no-timestamps` 快速模式适配 2-3 小时播客），也可生成词级时间戳。

### 4.2 CLI 接口

```bash
python asr-pipeline/pipeline.py <audio_file> [选项]
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `audio_file` | 必填 | 音频文件路径 |
| `--language` / `-l` | 可选 | 语种，`English` / `Chinese`，不传则自动检测 |
| `--output-dir` / `-o` | 可选 | 输出目录（默认 `./output/`） |
| `--context` / `-c` | 可选 | 术语注入，如 `--context "人工智能 深度学习"` |
| `--format` / `-f` | 可选 | 输出格式：`json` / `srt` / `txt` / `all`（默认 `all`） |
| `--no-diarize` | 可选 | 跳过说话人分离 |
| `--no-timestamps` | 可选 | **推荐长音频**：跳过词级时间戳，提速 4×+ |
| `--max-new-tokens` | 可选 | 生成 token 上限（默认 4096，长音频建议 4096-8192） |
| `--batch-size` | 可选 | 推理批量（默认 1，≥16GB 显存可设为 2） |

**管线阶段**：
1. `preprocess.py` — ffmpeg 转码为 16kHz mono WAV
2. `diarize.py` — pyannote.audio 说话人分离（需 `HF_TOKEN`）
3. `transcribe.py` — Qwen3-ASR 转写（`--no-timestamps` 跳过时间戳对齐加速）
4. `merge.py` — 合并结果，输出 JSON/SRT/TXT

> **注意**：Pipeline 是独立 CLI 工具，直接加载模型推理，**不需要**启动 ASR REST 后端服务。

### 4.3 使用示例

```bash
# 英文播客（完整管线，含说话人分离）
python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./output/

# 长音频加速（推荐 1h+）：跳过词级时间戳
python asr-pipeline/pipeline.py long_podcast.mp3 --language English --no-timestamps -o ./output/

# 中文播客 + 术语注入
python asr-pipeline/pipeline.py interview.mp3 --language Chinese \
  --context "人工智能 深度学习 大模型" -o ./output/

# 单人讲座（跳过说话人分离，更快）
python asr-pipeline/pipeline.py lecture.wav --language English --no-diarize --no-timestamps -o ./output/

# 仅输出 JSON
python asr-pipeline/pipeline.py audio.mp3 --language English -f json -o ./output/
```

### 4.4 输出产物

| 格式 | 文件 | 内容 |
|------|------|------|
| JSON | `{basename}.json` | 结构化数据（metadata + segments + full_text） |
| SRT | `{basename}.srt` | 字幕文件（可导入视频编辑） |
| TXT | `{basename}.txt` | 纯文本转写 |

### 4.5 测试文件

| 场景 | 测试文件 | 大小 | 内容 |
|------|----------|------|------|
| 冒烟测试 | `mcp-tool-test/smoke-test/pipeline_smoke_test.mp3` | 3.5 MB | 布克·华盛顿演讲 (~3.5 分钟) |

```bash
# 冒烟测试命令
python asr-pipeline/pipeline.py \
  mcp-tool-test/smoke-test/pipeline_smoke_test.mp3 \
  --language English --no-diarize -o /tmp/pipeline_test/
```

> **前提条件**：Pipeline 独立运行，无需启动 ASR REST 服务。说话人分离功能需要 `HF_TOKEN` 环境变量和 pyannote 访问权限，详见 `asr-pipeline/docs/pyannote-setup.md`。

---

## 4. 冒烟测试

`mcp-tool-test/smoke-test/` 目录提供 **3 个极简文件**（合计 <4 MB），用于快速验证所有工具是否正常。

| 文件 | 大小 | 工具 | 预期结果 |
|------|------|------|----------|
| `ocr_smoke_test.png` | 19 KB | GLM-OCR | 返回 Markdown，含数学符号 |
| `asr_smoke_test.wav` | 327 KB | Qwen3-ASR | 返回 6 秒英文短句转写 |
| `pipeline_smoke_test.mp3` | 3.5 MB | ASR Pipeline | 生成 JSON/SRT/TXT 产物 |

```bash
# 冒烟测试一键脚本思路（需启动对应后端）
# OCR
ocr_glm("mcp-tool-test/smoke-test/ocr_smoke_test.png")

# ASR
transcribe_audio("mcp-tool-test/smoke-test/asr_smoke_test.wav")

# Pipeline（推荐加 --no-timestamps 提速）
python asr-pipeline/pipeline.py mcp-tool-test/smoke-test/pipeline_smoke_test.mp3 \
  --language English --no-diarize --no-timestamps -o /tmp/pipeline_test/
```

---

## 5. 测试样本目录

完整测试样本位于 `mcp-tool-test/`，共 66 个文件约 179 MB。目录结构：

```
mcp-tool-test/
├── README.md                  # 样本目录详细说明
├── smoke-test/                # 冒烟测试（3 文件，<4 MB）
├── ocr/                       # OCR 测试 (23 文件)
│   ├── printed/en/            #   英文印刷体 (4)
│   ├── printed/zh/            #   中文印刷体 (2)
│   ├── printed/formulas/      #   公式印刷体 (3)
│   ├── handwriting/en/        #   英文手写体 (2)
│   ├── handwriting/zh/        #   中文手写体 (2)
│   ├── handwriting/formulas/  #   公式手写体 (4)
│   └── pdf/                   #   PDF 文档 (6)
└── asr/                       # ASR 测试 (43 文件)
    ├── daily/zh_en_single/    #   中英日常单人 (9)
    ├── daily/zh_en_dialogue/  #   中英日常多人 (7)
    └── podcast/               #   播客场景
        ├── en_single/         #     英文单人 (9)
        ├── en_dialogue/       #     英文多人 (2)
        ├── zh_en_single/      #     中英单人 (8)
        └── zh_en_dialogue/    #     中英多人 (8)
```

所有样本来自公开 CC0 / Public Domain / CC-BY 来源。采样许可和来源详情见 `mcp-tool-test/README.md`。
