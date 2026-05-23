# QwenVision — 图片内容描述

基于 Qwen3.6-35B-A3B (MoE) 的本地图像描述服务，使用 llama.cpp 的 llama-server 作为推理后端。提供 MCP 工具（`describe_image`）和 HTTP API。

## 文件

| 文件 | 用途 |
|---|---|
| `vision_mcp_server.py` | MCP stdio 前端（自动唤醒 llama-server） |
| `llama_start.sh` | llama-server 独立启停脚本 |

## 手动运行

```bash
# REST API 方式（llama-server 启动后）
bash vl/llama_start.sh start
curl -X POST http://localhost:8100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///path/to/image.jpg"}}]}]}'
```

## 模型

Qwen3.6-35B-A3B GGUF (Q4_K_XL 量化, ~22 GB)，需手动下载到 `~/.llama/models/`：

```bash
huggingface-cli download unsloth/Qwen3.6-35B-A3B-GGUF \
  Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf mmproj-F16.gguf \
  --local-dir ~/.llama/models/
```

显存约 8 GB（MoE 稀疏激活）。
