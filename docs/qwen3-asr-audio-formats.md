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
| `text` | 纯文本：`你好世界`（`Content-Type: text/plain`） |
| `verbose_json` | `{"task": "transcribe", "language": "zh", "duration": 3.2, "text": "...", "segments": [{"start": 0.0, "end": 1.2, "text": "你好"}]}` |

> MCP 工具 `transcribe_audio` 固定返回 `json` 格式。如需 `verbose_json`（含时间戳分段），可直调 REST API：`curl -F file=@audio.wav -F response_format=verbose_json http://localhost:8000/v1/audio/transcriptions`

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

### 为什么选 bfloat16 而非 float16？

bfloat16 动态范围与 float32 相同（8 位指数），推理时两者显存相同（2 字节），bfloat16 对溢出容忍度更高，几乎无精度损失。Qwen3-ASR 采用 bfloat16 是官方推荐。

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
- **空闲超时**：30 秒无请求后自动退出释放 GPU（可通过 `ASR_IDLE_TIMEOUT` 环境变量调整）
- **日志**：`/tmp/qwen3-asr-server.log`
- **并发行为**：FastAPI 基于 asyncio，端点层面支持并发请求。但 `model.transcribe()` 是同步 GPU 推理——多个请求实际**排队串行处理**，不会并行加速，也不会丢失请求。如需批量处理多个音频，依次提交即可

---

## 11. 已知问题与排障

### 11.1 常见坑点

| 现象 | 根因 | 解决 |
|---|---|---|
| `max_new_tokens=256` 导致长音频转写截断 | 旧版代码默认值太小 | `qwen3_asr_server.py` 已改为 `max_new_tokens=4096`，支持最长 ~10 分钟音频 |
| `NoBackendError`（ffmpeg 找不到） | 启动脚本 `nohup` 启动时 PATH 不含 conda bin | `qwen3_asr_start.sh` 已自动将 conda bin 加入 PATH；如仍有问题，手动 `export PATH="<CONDA-ENV>/bin:$PATH"` 后重启 |
| opencode 重启后 MCP 工具离线 | ASR API 服务（`localhost:8000`）是独立进程，不会随 opencode 重启自动恢复 | 重启 opencode 后，先手动启动 ASR 服务：`bash asr/qwen3_asr_start.sh start`。虽然 MCP Server 内置自动唤醒，但在 opencode 沙箱中可能受限——手动启动更可靠 |
| 音频末尾句子被截断（"…"） | 模型生成到 `max_new_tokens` 上限时强制停止，无错误提示 | 增大 `max_new_tokens`（编辑 `qwen3_asr_server.py` 后重启）；检查返回文本是否有未完成的句子 |
| `librosa` 加载 m4a 报 warning | PySoundFile 不支持 m4a，fallback 到 audioread | **正常行为**，不影响结果——确保 ffmpeg 在 PATH 即可 |
| bash 工具超时杀子进程 | opencode 沙箱限制，bash 工具超时后杀掉 `nohup &` 子进程 | 用 Python `subprocess.Popen(start_new_session=True)` 脱离终端；或使用 `setsid` + `disown` |

### 11.2 服务自动关闭后显存未释放

服务通过 SIGTERM 优雅退出，lifespan shutdown 中调用 `torch.cuda.empty_cache()`。极少数情况下如显存未释放：

```bash
nvidia-smi                    # 确认显存占用
bash asr/qwen3_asr_start.sh stop  # 强制停止
```

### 11.3 调整空闲超时时间

默认 30 秒。通过 `ASR_IDLE_TIMEOUT` 环境变量调整（需在启动 ASR 服务前设置）：

```bash
export ASR_IDLE_TIMEOUT=60    # 60 秒
export ASR_IDLE_TIMEOUT=120   # 2 分钟
```

### 11.4 opencode 重启后的恢复流程

opencode 重启时：MCP Server（`asr_mcp_server.py`）由 opencode 通过 stdio **自动拉起**，无需手动操作。但 ASR API 服务（`localhost:8000`）是独立进程，**不会自动恢复**。

推荐做法：

```bash
# opencode 重启后，先手动启动 ASR 服务
bash asr/qwen3_asr_start.sh start

# 验证服务就绪
curl localhost:8000/health
```

> MCP Server 内置了自动唤醒（`_ensure_asr_ready`），但在 opencode 沙箱中 `subprocess.Popen(start_new_session=True)` 可能被运行时限制——**手动启动更可靠**。

### 11.5 服务启动后立即崩溃

- 用 `--fg` 前台启动查看错误：`bash asr/qwen3_asr_start.sh --fg`
- 检查 GPU 显存：`nvidia-smi`
- 确认 conda 环境已激活且 `qwen-asr` 包已安装
