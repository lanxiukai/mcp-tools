# QwenVision 图片描述格式支持详情

> 数据来源：本仓库 `vl/vision_mcp_server.py` + `vl/llama_start_guide.md` 源码  
> 最后更新：2026-05-11

---

## 1. MCP 工具接口

通过 `opencode.jsonc` 配置后，agent 可调用以下 MCP 工具：

| 工具 | 参数 | 说明 |
|---|---|---|
| `describe_image(file_path)` | `file_path`（必填，图片绝对路径） | 发送图片到 Qwen3.6-35B-A3B，返回英文描述 |
| `vision_status()` | 无 | 返回 llama-server 状态（在线/离线/错误信息） |

**自动唤醒**：首次调用 `describe_image` 时，MCP Server 自动检测 llama-server 是否在线；离线则执行 `bash vl/llama_start.sh restart` 启动服务，并轮询等待就绪（最长 120s）。

> **注意**：与 ASR / OCR 不同，QwenVision 后端**不设空闲超时自动退出**。llama-server 作为独立进程运行，需通过 `llama_start.sh stop` 手动停止。

---

## 2. 输入格式

### 2.1 支持列表

| 格式 | 扩展名 | MIME 类型 |
|---|---|---|
| PNG | `.png` | `image/png` |
| JPEG / JPG | `.jpg`, `.jpeg` | `image/jpeg` |
| GIF | `.gif` | `image/gif` |
| BMP | `.bmp` | `image/bmp` |
| WEBP | `.webp` | `image/webp` |

MIME 类型通过 Python 标准库 `mimetypes.guess_type()` 自动推断，无法推断时回退为 `application/octet-stream`。

### 2.2 图片尺寸建议

| 建议 | 值 |
|---|---|
| 推荐最大分辨率 | ≤ 4096 × 4096 像素 |
| 实际限制 | 无硬性限制，但超大图片会消耗大量 context token |

---

## 3. 输出格式

`describe_image` 的返回结构：

```json
{
  "description": "The image shows a modern office space with...",
  "model": "Qwen3.6-35B-A3B",
  "tokens_used": 342
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `description` | string | 英文图片内容描述 |
| `model` | string | 固定为 `"Qwen3.6-35B-A3B"` |
| `tokens_used` | int | 本次请求消耗的总 token 数 |
| `error` | string（仅错误时）| 错误信息 |

---

## 4. 推理参数

| 参数 | 值 | 说明 |
|---|---|---|
| 系统提示 | `"Describe this image in detail..."` | 固定英文提示词，要求描述所有可见物体、人物、文字、颜色和空间关系 |
| `max_tokens` | 1024 | 单次生成上限 |
| `temperature` | 0.7 | 中等随机性 |
| `enable_thinking` | `false` | 关闭 MoE 模型的"思考链"以提速 |
| 输出语言 | 英文 | 由系统提示锁定 |

---

## 5. 图片传输

图片通过 **Base64 编码** 内嵌到 OpenAI Chat Completion API 请求中：

```
本地文件 → 二进制读取 → Base64 编码 → data:{MIME};base64,{base64_data}
                                          ↓
                   OpenAI Vision API 格式的 "image_url"
```

传输流程：
```
OpenCode Agent
    → describe_image("/home/user/photo.jpg")
    → MCP Server 读取文件，Base64 编码
    → POST http://localhost:8080/v1/chat/completions
    → llama-server 返回 JSON
    → 解析 description 字段
    → 返回给 Agent
```

---

## 6. 模型详情

| 属性 | 值 |
|---|---|
| 模型 | `Qwen3.6-35B-A3B` |
| 架构 | MoE (Mixture of Experts) VLM |
| 量化方式 | Q4_K_XL（4-bit 量化，保留 XL 精度） |
| 模型文件大小 | ~22 GB（GGUF 格式） |
| GPU 显存占用 | ~8 GB |
| 推理引擎 | llama.cpp 的 `llama-server` |
| REST 端口 | `8080` |
| 下载方式 | `huggingface-cli download unsloth/Qwen3.6-35B-A3B-GGUF` |

### 6.1 手动下载模型

模型文件需放在 `~/.llama/models/` 下：

```bash
huggingface-cli download unsloth/Qwen3.6-35B-A3B-GGUF \
  Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
  mmproj-F16.gguf \
  --local-dir ~/.llama/models/
```

两个必需文件：
- `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` — LLM 权重（约 22GB）
- `mmproj-F16.gguf` — 多模态投影层权重

---

## 7. 服务架构

```
OpenCode Agent
    │ MCP stdio
    ▼
MCP Server (vision_mcp_server.py)     ← 轻量前端，HTTP client
    │ HTTP REST (OpenAI Compatible API)
    ▼
llama-server (llama.cpp)              ← 独立进程，管理脚本 vl/llama_start.sh
    │
    ▼
Qwen3.6-35B-A3B GGUF (本地模型文件)
```

- **llama-server 管理**：`bash vl/llama_start.sh {start|stop|restart|status}`
- **日志**：`~/.llama/llama-server.log`
- **API 格式**：OpenAI Chat Completion API 兼容 (`/v1/chat/completions`)
- **健康检查**：`GET http://localhost:8080/health`

---

## 8. 错误处理

常见错误及对应消息：

| 错误场景 | 返回的 `error` 字段 |
|---|---|
| 文件不存在 | `"File not found: /path/to/img.jpg"` |
| 路径非文件 | `"Not a regular file: /path/to/dir"` |
| llama-server 启动失败 | `"Failed to start llama-server. Check logs at ~/.llama/llama-server.log..."` |
| API 请求超时 | `"llama-server API request failed: ..."` |
| HTTP 非 200 | `"llama-server returned HTTP 503: ..."` |
| 空响应 | `"llama-server returned empty choices"` |

---

## 9. 与其他工具的对比

| 特性 | QwenVision | GLM-OCR |
|---|---|---|
| 核心能力 | 图片**内容描述** | 图片/PDF **文字提取** |
| 输出格式 | 英文自然语言段落 | Markdown（含公式、表格） |
| 模型规模 | 35B-3B MoE | 0.9B VLM |
| 显存占用 | ~8 GB | ~2.5 GB |
| 启动时间 | 1-2 分钟 | 30-60 秒 |
| 空闲退出 | 不支持（手动管理） | 30 秒自动退出 |
