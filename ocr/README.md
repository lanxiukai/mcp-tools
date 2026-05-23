# GLM-OCR — 文档解析（图片/PDF → Markdown）

基于 GLM-OCR 0.9B VLM 的本地文档 OCR 服务，支持中英文、手写体、公式（LaTeX）、表格。提供 MCP 工具（`ocr_glm` / `ocr_glm_submit` / `ocr_glm_wait`）和 HTTP API 两种调用方式。

## 文件

| 文件 | 用途 |
|---|---|
| `glm_ocr_server.py` | FastAPI REST 后端（GPU 推理，端口 8002），含异步任务队列 |
| `glm_ocr_mcp_server.py` | MCP stdio 前端（自动唤醒 REST 后端，PDF 异步提交+轮询） |
| `glm_ocr_start.sh` | 独立启停脚本（`start` / `stop` / `status` / `restart`） |

## 手动运行

```bash
# REST API 方式
conda run -n glm-ocr python ocr/glm_ocr_server.py
curl -F "file=@image.png" http://localhost:8002/v1/ocr/parse

# 异步任务（大 PDF）
curl -F "file=@report.pdf" http://localhost:8002/v1/ocr/submit   # → job_id
curl http://localhost:8002/v1/ocr/jobs/{job_id}                  # → 进度
curl http://localhost:8002/v1/ocr/jobs/{job_id}/result           # → 结果
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OCR_PORT` | `8002` | REST 服务端口 |
| `OCR_HOST` | `localhost` | REST 服务地址 |
| `OCR_IDLE_TIMEOUT` | `30` | 空闲超时秒数 |

## 模型

GLM-OCR 0.9B，bfloat16，HuggingFace 自动下载，显存约 2.5 GB。PDF 需 `pymupdf`。
