# Qwen3-ASR — 语音转文字

基于 Qwen3-ASR-1.7B 的本地语音识别服务，支持 52 种语言。提供 MCP 工具（`transcribe_audio` / `transcribe_podcast`）和 HTTP API 两种调用方式。

## 文件

| 文件 | 用途 |
|---|---|
| `qwen3_asr_server.py` | FastAPI REST 后端（GPU 推理，端口 8000） |
| `asr_mcp_server.py` | MCP stdio 前端（自动唤醒 REST 后端） |
| `qwen3_asr_start.sh` | 独立启停脚本（`start` / `stop` / `status` / `restart`） |

## 手动运行

```bash
# REST API 方式
conda run -n qwen-asr python asr/qwen3_asr_server.py
curl -F "file=@audio.mp3" -F "language=zh" http://localhost:8000/v1/asr/transcribe

# MCP 方式（opencode.jsonc 配置后自动接入，无需手动启动）
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ASR_PORT` | `8000` | REST 服务端口 |
| `ASR_HOST` | `localhost` | REST 服务地址 |
| `ASR_IDLE_TIMEOUT` | `300` | 空闲超时秒数 |

## 模型

Qwen3-ASR-1.7B，HuggingFace 自动下载，显存约 3.5 GB。
