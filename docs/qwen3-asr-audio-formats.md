# Qwen3-ASR 音频格式支持详情

> 数据来源：Hugging Face 模型卡 + `qwen_asr/inference/utils.py` 源码 + 本仓库 `asr/` 源码  
> 最后更新：2026-05-12

---

## 1. MCP 工具接口

通过 `opencode.jsonc` 配置后，agent 可调用以下 MCP 工具：

| 工具 | 参数 | 说明 |
|---|---|---|
| `transcribe_audio(file_path, language?)` | `file_path`（必填，音频绝对路径）, `language`（可选，如 `en`、`zh`） | 转写音频，返回 `{"text": "...", "language": "..."}` |
| `asr_status()` | 无 | 返回服务状态（模型、GPU 显存占用等） |

**自动唤醒**：首次调用 `transcribe_audio` 时，MCP Server 自动检测后端是否在线；离线则后台启动并轮询等待就绪（最长 60s）。后端无请求 30 秒后自动退出释放 GPU。

---

## 2. 文件格式

底层加载逻辑位于 `qwen_asr/inference/utils.py` 的 `load_audio_any()`：

| 输入方式 | 加载库 | 支持格式 | 备注 |
|---|---|---|---|
| 本地文件路径 | `librosa.load()` | WAV, MP3, FLAC, OGG, M4A/AAC, WMA 等 | 依赖系统 `ffmpeg` / `audioread` |
| URL / Base64 | `soundfile.read()` (libsndfile) | WAV, FLAC, OGG | **不支持 MP3**（libsndfile 不含 MP3 解码器） |

> **结论**：通过本仓库 `qwen3_asr_server.py` 上传文件 → 存本地临时文件 → 走 `librosa` → 支持 MP3。直接传 URL 则不支持 MP3。

---

## 3. 音频参数

| 参数 | 值 | 说明 |
|---|---|---|
| 采样率 | **16,000 Hz** | 自动重采样，不强求原始 16kHz |
| 声道 | **单声道** | 自动转 mono（多声道取均值） |
| 位深度 | **float32** | 归一化到 [-1, 1] |
| 最长输入（ASR） | **480 秒（8 分钟）** | 服务端显式切块。底层库内上限 1200 秒 |
| 最短输入 | **0.5 秒** | 过短自动补零 |
| 最长时间戳对齐 | **180 秒（3 分钟）** | 仅 `Qwen3-ForcedAligner` 生效 |

---

## 4. 语言支持

Qwen3-ASR 支持 **52 种语言**，包括但不限于：

中文、英文、日文、韩文、法文、德文、西班牙文、俄文、阿拉伯文、葡萄牙文、意大利文、荷兰文、波兰文、土耳其文、越南文、泰文、印尼文、马来文、印地文、孟加拉文等。

**自动语言检测**：不传 `language` 参数时，模型自动识别音频语种。也可显式指定来提升准确率。

| `language` 参数 | 含义 |
|---|---|
| 不传 / `None` | 自动检测语言 |
| `"en"` | 强制英文 |
| `"zh"` | 强制中文 |
| `"ja"` | 强制日文 |
| `"ko"` | 强制韩文 |

---

## 5. 响应格式

REST API 端点 `POST /v1/audio/transcriptions` 支持三种 `response_format`：

| 格式 | 返回示例 |
|---|---|
| `json`（默认） | `{"text": "你好世界", "language": "zh"}` |
| `text` | 纯文本：`你好世界` |
| `verbose_json` | `{"task": "transcribe", "language": "zh", "duration": 3.2, "text": "...", "segments": [...]}` |

> MCP 工具 `transcribe_audio` 固定返回 `json` 格式。

---

## 6. 音频内容类型

Qwen3-ASR 不仅能转写口播语音，还覆盖歌声和带背景音乐的歌曲：

| 类型 | WER 参考 | 说明 |
|---|---|---|
| 语音（Speech） | 1.6% ~ 5.9% | LibriSpeech, WenetSpeech 等 |
| 歌声（Singing Voice） | 3.1% ~ 6.0% | M4Singer, Opencpop 等清唱数据集 |
| 带 BGM 歌曲（Songs with BGM） | 13.9% ~ 14.6% | EntireSongs 含背景音乐 |

---

## 7. 长音频切块机制

### 7.1 服务端显式切块

`qwen3_asr_server.py` 在调用模型前，先将超长音频切分为 ≤480 秒的 chunk，逐个转写后拼接文本。这确保了 12GB 显存不会溢出，同时避免了库内 1200 秒 chunk 带来的共享显存压力。

### 7.2 库内自动切块

底层 `split_audio_into_chunks()`（`qwen_asr` 库）：

- 目标切块长度：`max_chunk_sec`（调用时传入）
- 边界搜索：在切点 ±5 秒范围内找**最低能量点**，避免在说话中途切断
- 相邻块之间**无重叠、无间隙**，拼接可还原原始音频
- 最后一块长度 < 0.5 秒时自动零补足

---

## 8. 输入类型

`model.transcribe(audio=...)` 接受以下四种输入：

| 类型 | 示例 |
|---|---|
| 本地文件路径 | `"/path/to/speech.wav"` |
| URL | `"https://example.com/audio.flac"` |
| Base64 | `"data:audio/wav;base64,..."` 或纯 base64 字符串 |
| `(np.ndarray, sr)` 元组 | `(waveform_float32, 16000)` |

---

## 9. 模型详情

| 属性 | 值 |
|---|---|
| 模型 | `Qwen/Qwen3-ASR-1.7B` |
| 架构 | Transformer + CTC |
| 数据类型 | bfloat16（默认）/ float16 / float32 |
| 最大推理批次 | 1（`max_inference_batch_size=1`），12GB 显存安全值 |
| GPU 显存占用 | ~3.5 GB |
| 精度（dtype） | bfloat16（默认，兼顾速度与精度） |
| 设备 | cuda:0（默认）/ cpu |

---

## 10. 服务架构

```
OpenCode Agent
    │ MCP stdio
    ▼
MCP Server (asr_mcp_server.py)    ← 轻量前端，自动唤醒 REST 后端
    │ HTTP REST
    ▼
FastAPI Server (qwen3_asr_server.py)  ← GPU 推理后端，独立启停脚本
    │
    ▼
Qwen3-ASR-1.7B (HuggingFace 自动下载)
```

- **REST 端口**：`8000`（可通过 `ASR_PORT` 环境变量调整）
- **空闲超时**：30 秒无请求后自动退出释放 GPU
- **日志**：`/tmp/qwen3-asr-server.log`
