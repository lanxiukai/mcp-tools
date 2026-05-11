# ASR Pipeline 播客长音频转写格式支持详情

> 数据来源：本仓库 `asr-pipeline/` 源码（`pipeline.py`, `preprocess.py`, `diarize.py`, `transcribe.py`, `merge.py`）  
> 最后更新：2026-05-11

---

## 1. 工具概览

ASR Pipeline 是一个**离线批处理 CLI 工具**，专门设计用于 2-3 小时播客/会议/访谈的长音频转写。与 `transcribe_audio`（MCP 工具，最长 20 分钟）不同，它有**四阶段管线**和**说话人分离**能力。

> **与 Qwen3-ASR MCP 工具的区别**：`transcribe_audio` 适合短音频（≤20 分钟），无说话人分离。`pipeline.py` 适合长音频（无时长上限），支持多说话人标注和词级时间戳。

### 基本用法

```bash
python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./output/
python asr-pipeline/pipeline.py interview.mp3 --language Chinese --context "人工智能 深度学习" -o ./output/
python asr-pipeline/pipeline.py lecture.wav --no-diarize -f txt
```

---

## 2. 输入格式

### 2.1 音频文件

| 特性 | 说明 |
|---|---|
| 支持格式 | 任意 ffmpeg 兼容格式（MP3, WAV, FLAC, OGG, M4A, AAC, WMA, OPUS 等） |
| 原始参数 | 任意采样率 / 声道数 / 位深度 |
| 时长上限 | 无硬限制（磁盘和 GPU 显存决定了实际上限） |
| 多文件输入 | 支持，逐个处理：`python pipeline.py a.mp3 b.wav c.flac` |

### 2.2 标准输入 (stdin)

也支持从管道接收 PCM 音频数据：

```bash
cat audio.pcm | python asr-pipeline/pipeline.py - --language English -o ./output/
```

stdin 输入被保存为临时 WAV 文件后进入管线处理。

---

## 3. 预处理流程（Stage 1）

`preprocess.py` 使用 ffmpeg 将任意音频转换为统一格式：

```
原始音频（任意格式/参数）
  → ffmpeg
  → 16 kHz 采样率（-ar 16000）
  → 单声道（-ac 1）
  → 16-bit signed PCM（-sample_fmt s16）
  → WAV 输出
```

| 属性 | 转换目标 | 说明 |
|---|---|---|
| 采样率 | **16,000 Hz** | 匹配 Qwen3-ASR 期望 |
| 声道 | **1（mono）** | 多声道→单声道混合 |
| 位深度 | **16-bit signed PCM** | 标准整数格式 |
| 容器 | **WAV** | 无损无压缩 |

**幂等性**：如果输入已经是 16kHz mono WAV，预处理阶段自动跳过，直接复用原始文件。

**依赖**：系统必须有 `ffmpeg`。脚本会自动在 conda 环境的 `bin/` 目录下查找。

---

## 4. 四阶段管线

```
[1/4] Preprocess     → 音频格式统一（ffmpeg）
[2/4] Diarize        → 说话人分离（pyannote.audio，可选跳过）
[3/4] Transcribe     → ASR 转写 + 词级时间戳对齐（Qwen3-ASR）
[4/4] Merge & Output → 合并结果 + 写入文件（JSON / SRT / TXT）
```

每个阶段串行执行，前一阶段失败会导致管线中止（返回非零退出码）。

---

## 5. 输出格式

### 5.1 格式选择

| `--format` 参数 | 输出文件 | 内容 |
|---|---|---|
| `json` | `{basename}.json` | 结构化数据，含词级时间戳 + 说话人段 |
| `srt` | `{basename}.srt` | 标准字幕格式 |
| `txt` | `{basename}.txt` | 纯文本（带说话人标注） |
| `all`（默认） | 以上三种 | 全部生成 |

### 5.2 JSON 输出结构

```json
{
  "version": "0.1.0",
  "audio_duration_sec": 3600.5,
  "language": "English",
  "num_speakers": 3,
  "segments": [
    {
      "speaker": "SPEAKER_00",
      "start_sec": 0.0,
      "end_sec": 12.5,
      "text": "Welcome to today's episode...",
      "words": [
        {"word": "Welcome", "start_sec": 0.0, "end_sec": 0.6},
        {"word": "to", "start_sec": 0.6, "end_sec": 0.8},
        {"word": "today's", "start_sec": 0.8, "end_sec": 1.2}
      ]
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `segments[].speaker` | 说话人标识（`SPEAKER_00`, `SPEAKER_01`, ...） |
| `segments[].start_sec` / `end_sec` | 说话段起止时间（秒） |
| `segments[].text` | 该段转写文本 |
| `segments[].words[]` | 词级时间戳（`word`, `start_sec`, `end_sec`） |
| `audio_duration_sec` | 音频总时长（秒） |
| `language` | 识别语言 |
| `num_speakers` | 检测到的说话人数 |

### 5.3 SRT 输出结构

标准 SRT 字幕格式，每条字幕对应一个说话段：

```
1
00:00:00,000 --> 00:00:02,500
[SPEAKER_00] Good morning everyone.

2
00:00:02,500 --> 00:00:05,800
[SPEAKER_01] Thanks for joining us today.
```

### 5.4 TXT 输出结构

纯文本，每个说话段一行：

```
[SPEAKER_00] Good morning everyone.
[SPEAKER_01] Thanks for joining us today.
[SPEAKER_00] Let's begin with the first topic...
```

---

## 6. 语言支持

| `--language` 参数 | 说明 |
|---|---|
| 不传 / 省略 | 自动检测语言（由 Qwen3-ASR 模型自动判断） |
| `English` | 强制英文识别 |
| `Chinese` | 强制中文识别 |

> 目前管线 CLI 仅暴露 `English` 和 `Chinese` 两个选项，但底层 Qwen3-ASR 支持 52 种语言。如需其他语言，可修改 `pipeline.py` 的 `choices` 列表或直接传语言标签。

---

## 7. 说话人分离（Stage 2 — 可选）

### 7.1 依赖

说话人分离基于 `pyannote.audio` 模型：

| 组件 | 说明 |
|---|---|
| 模型 | `pyannote/speaker-diarization-3.1` |
| 权限 | 需在 [hf.co/pyannote](https://hf.co/pyannote) 接受模型使用条款 |
| Token | 通过 `--hf-token` 或 `HF_TOKEN` 环境变量传递 |

### 7.2 参数

| 参数 | 说明 |
|---|---|
| `--no-diarize` | 跳过说话人分离，所有文字归入 `SPEAKER_00` |
| `--num-speakers N` | 限制最大说话人数（pyannote 的上限约束） |
| `--hf-token TOKEN` | HuggingFace token（默认读 `HF_TOKEN` 环境变量） |

### 7.3 管线中的位置

```
preprocess → [diarize?] → transcribe → merge
               ↑
         可选，--no-diarize 跳过
```

---

## 8. 术语注入（Context Injection）

`--context` 参数允许注入领域术语以提升 ASR 准确率：

```bash
# 财经播客
python pipeline.py finance.mp3 --language English --context "EBITDA ROI NASDAQ non-GAAP" -o ./out/

# 科技访谈
python pipeline.py tech.mp3 --language Chinese --context "大语言模型 注意力机制 强化学习" -o ./out/
```

术语以空格分隔，注入到 Qwen3-ASR 的识别上下文中。对专有名词、缩写、专业术语的识别有明显帮助。

---

## 9. 管线命令参考

### 9.1 CLI 参数完整列表

| 参数 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `input` | 是 | — | 音频文件路径（可多个），`-` 表示 stdin |
| `-l` / `--language` | 否 | 自动检测 | `English` 或 `Chinese` |
| `-o` / `--output-dir` | 否 | `./output/` | 输出目录 |
| `-f` / `--format` | 否 | `all` | `json` / `srt` / `txt` / `all` |
| `-c` / `--context` | 否 | `""` | 空格分隔的领域术语 |
| `-n` / `--num-speakers` | 否 | 自动 | 最大说话人数 |
| `--no-diarize` | 否 | false | 跳过说话人分离 |
| `--device` | 否 | `cuda:0` | PyTorch 设备 |
| `--hf-token` | 否 | `HF_TOKEN` 环境变量 | HuggingFace token |

### 9.2 使用示例

```bash
# 基本：英文播客，全部格式输出
python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./podcast-output/

# 中文 + 术语注入
python asr-pipeline/pipeline.py interview.mp3 --language Chinese \
  --context "神经网络 反向传播 梯度下降" -o ./out/

# 仅生成字幕
python asr-pipeline/pipeline.py lecture.wav -f srt -o ./out/

# 无说话人分离（独白/单人演讲）
python asr-pipeline/pipeline.py speech.mp3 --no-diarize

# 多文件批处理
python asr-pipeline/pipeline.py ep1.mp3 ep2.mp3 ep3.mp3 --language English -o ./batch-out/

# 限定 2 个说话人
python asr-pipeline/pipeline.py meeting.wav --language English --num-speakers 2 -o ./out/

# 查看版本
python asr-pipeline/pipeline.py --version
```

---

## 10. 性能参考

| 场景 | 预估时间 | 说明 |
|---|---|---|
| 1 小时音频（无 diarize） | 2-5 分钟 | 仅音频预处理 + ASR |
| 1 小时音频（含 diarize） | 5-15 分钟 | 增加说话人分离时间 |
| 3 小时音频（含 diarize） | 15-40 分钟 | 线性增长 |

> 实际时间取决于 GPU 型号、音频时长和说话人数。说话人分离（pyannote）是性能瓶颈。

---

## 11. 依赖关系

```
asr-pipeline/
├── pipeline.py       ← CLI 入口
├── preprocess.py     ← ffmpeg (系统依赖)
├── diarize.py        ← pyannote.audio (pip + HF_TOKEN)
├── transcribe.py     ← Qwen3-ASR (同 asr/ 模块)
├── merge.py          ← 纯 Python，无外部依赖
└── test_pipeline.py  ← pytest
```

| 依赖 | 安装方式 | 用途 |
|---|---|---|
| ffmpeg | `sudo apt install ffmpeg` 或 conda 安装 | 音频预处理 |
| pyannote.audio | `pip install pyannote.audio` | 说话人分离 |
| Qwen3-ASR | 随 `qwen-asr` conda 环境安装 | ASR 转写 + 时间戳对齐 |
