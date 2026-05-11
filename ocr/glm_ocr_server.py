"""
GLM-OCR API 服务器 (FastAPI)

用法:
    mamba run -n glm-ocr python ocr/glm_ocr_server.py

API 端点:
    GET  /health                      — 健康检查
    POST /v1/ocr/parse                — 文档解析 (图片/PDF → Markdown/JSON)
    GET  /v1/models                   — 模型列表
    GET  /docs                        — 自动生成的 API 文档

调用示例:
    curl -F "file=@image.png" http://localhost:8002/v1/ocr/parse
    curl -F "file=@image.png" -F "output_format=markdown" http://localhost:8002/v1/ocr/parse
"""

import argparse
import logging
import os
import signal
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 仓库根目录
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("glm-ocr-server")

# ---------------------------------------------------------------------------
# 模型持有者（模块级单例）
# ---------------------------------------------------------------------------
class OCRModel:
    """线程安全的 GLM-OCR 模型包装器"""

    def __init__(self):
        self.model = None
        self.processor = None
        self.model_name: str = ""
        self.device: str = "cuda"

    def load(self, model_name: str = "zai-org/GLM-OCR", device: str = "cuda"):
        from transformers import GlmOcrForConditionalGeneration, AutoProcessor

        self.model_name = model_name
        self.device = device

        # 解析模型路径：优先作为本地路径，回退到 HuggingFace ID
        local_path = REPO_DIR / "models" / "safetensors" / model_name.replace("/", "/")
        model_path = str(local_path) if local_path.is_dir() else model_name

        logger.info(
            "Loading GLM-OCR model: %s (device=%s)", model_path, device
        )
        t0 = time.time()

        # 加载 processor（轻量，先加）
        self.processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
        logger.info("AutoProcessor loaded")

        # 加载模型 —— 优先 flash_attention_2，编译/导入失败回退 sdpa
        attn_impl = "sdpa"
        try:
            logger.info("Trying flash_attention_2 ...")
            model = GlmOcrForConditionalGeneration.from_pretrained(
                model_path,
                torch_dtype=torch.bfloat16,
                device_map="auto",
                attn_implementation="flash_attention_2",
                trust_remote_code=True,
            )
            attn_impl = "flash_attention_2"
            logger.info("flash_attention_2 OK")
        except Exception as e:
            logger.warning(
                "flash_attention_2 failed (%s), falling back to sdpa", e
            )
            try:
                model = GlmOcrForConditionalGeneration.from_pretrained(
                    model_path,
                    torch_dtype=torch.bfloat16,
                    device_map="auto",
                    attn_implementation="sdpa",
                    trust_remote_code=True,
                )
                attn_impl = "sdpa"
                logger.info("sdpa fallback OK")
            except Exception as e2:
                logger.error("Both flash_attention_2 and sdpa failed: %s", e2)
                raise

        self.model = model
        self.model.eval()

        elapsed = time.time() - t0
        logger.info(
            "GLM-OCR model loaded in %.1fs (attn=%s)", elapsed, attn_impl
        )

    def predict_single(self, image) -> str:
        """对单张 PIL Image 执行 OCR，返回 Markdown 文本"""
        assert self.processor is not None, "Processor not loaded"
        assert self.model is not None, "Model not loaded"

        # 转换为 RGB
        if image.mode != "RGB":
            image = image.convert("RGB")

        # GLM-OCR 是 prompt-driven VLM：需要 chat template 格式
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": "Text Recognition:"},
                ],
            }
        ]

        inputs = self.processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )

        # 移动到模型所在设备
        device = next(self.model.parameters()).device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=4096,
                do_sample=False,
            )

        # 只解码生成部分（跳过输入 / prompt token）
        input_len = inputs["input_ids"].shape[1]
        generated_ids = outputs[0][input_len:]
        response = self.processor.decode(generated_ids, skip_special_tokens=True)
        return response.strip()

    def predict(self, file_path: str) -> dict:
        """对文件执行 OCR，返回 {page_count, markdown, pages: [{page_index, markdown}]}"""
        path = Path(file_path)
        suffix = path.suffix.lower()

        # 判断是 PDF 还是图片
        if suffix == ".pdf":
            return self._predict_pdf(str(path))
        else:
            return self._predict_image(str(path))

    def _predict_image(self, image_path: str) -> dict:
        """处理单张图片"""
        from PIL import Image

        img = Image.open(image_path)
        markdown = self.predict_single(img)

        return {
            "page_count": 1,
            "markdown": markdown,
            "pages": [{"page_index": 0, "markdown": markdown}],
        }

    def _predict_pdf(self, pdf_path: str) -> dict:
        """使用 pymupdf 逐页渲染 PDF 为图片，逐页 OCR，拼接结果"""
        try:
            import fitz  # pymupdf
        except ImportError:
            raise RuntimeError(
                "PDF processing requires pymupdf. Install it with: "
                "pip install pymupdf"
            )

        doc = fitz.open(pdf_path)
        pages = []
        all_markdown = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            # 渲染为图片（300 DPI 兼顾质量与速度）
            pix = page.get_pixmap(dpi=300)
            from PIL import Image
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            page_md = self.predict_single(img)
            pages.append({"page_index": page_num, "markdown": page_md})
            all_markdown.append(page_md)

        doc.close()

        # 多页拼接
        full_markdown = "\n\n---\n\n".join(all_markdown)

        return {
            "page_count": len(pages),
            "markdown": full_markdown,
            "pages": pages,
        }


ocr_model = OCRModel()

# 空闲超时配置（秒）：无请求超过此时间自动退出释放 GPU
IDLE_TIMEOUT = int(os.environ.get("OCR_IDLE_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时加载模型，关闭时释放 GPU 显存"""
    model_name = getattr(app.state, "model_name", "zai-org/GLM-OCR")
    device = getattr(app.state, "device", "cuda")

    try:
        ocr_model.load(model_name=model_name, device=device)
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        sys.exit(1)

    # 初始化活跃请求计数 & 最后请求时间 & 锁
    app.state._lock = threading.Lock()
    app.state.active_requests = 0
    app.state.last_request_time = time.time()

    # 启动空闲监控线程：无活跃请求 + 空闲超时 → 发 SIGTERM 优雅退出
    def idle_monitor():
        while True:
            time.sleep(5)
            with app.state._lock:
                idle_s = time.time() - app.state.last_request_time
                busy = app.state.active_requests > 0
            if not busy and idle_s > IDLE_TIMEOUT:
                logger.info(
                    "Idle timeout reached (%ds > %ds), shutting down to release GPU...",
                    int(idle_s), IDLE_TIMEOUT,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return

    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()

    yield

    # 关闭：释放 GPU 显存
    ocr_model.model = None
    ocr_model.processor = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="GLM-OCR API",
    description="Document parsing API powered by GLM-OCR (0.9B VLM). "
                "Supports Chinese/English text, formulas (LaTeX), tables, handwriting recognition.",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware：跟踪活跃请求 & 最后请求完成时间（供 idle_monitor 使用）
# ---------------------------------------------------------------------------
@app.middleware("http")
async def track_activity(request: Request, call_next):
    with app.state._lock:
        app.state.active_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        with app.state._lock:
            app.state.active_requests -= 1
            app.state.last_request_time = time.time()


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------
class ParseResponse(BaseModel):
    success: bool
    model: str
    input_path: str
    page_count: int
    pages: list[dict] = []
    markdown: str = ""
    error: Optional[str] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "glm-ocr"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def save_upload(upload: UploadFile) -> Path:
    """将上传文件保存到临时文件，返回路径"""
    suffix = Path(upload.filename or "document.png").suffix or ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        while chunk := await upload.read(1024 * 1024):  # 1 MB chunks
            tmp.write(chunk)
        tmp.close()
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    gpu_info = {}
    if torch.cuda.is_available():
        gpu_info = {
            "name": torch.cuda.get_device_name(0),
            "memory_total_gb": round(
                torch.cuda.get_device_properties(0).total_memory / 1024**3, 1
            ),
            "memory_allocated_gb": round(
                torch.cuda.memory_allocated(0) / 1024**3, 2
            ),
            "memory_reserved_gb": round(
                torch.cuda.memory_reserved(0) / 1024**3, 2
            ),
        }

    return {
        "status": "ok",
        "model": ocr_model.model_name,
        "device": ocr_model.device,
        "gpu_info": gpu_info,
    }


@app.get("/v1/models", response_model=ModelListResponse)
async def list_models():
    return ModelListResponse(
        data=[ModelInfo(id=ocr_model.model_name)]
    )


@app.post("/v1/ocr/parse", response_model=ParseResponse)
async def parse_document(
    file: UploadFile = File(..., description="图片或 PDF 文件 (PNG, JPG, PDF 等)"),
    output_format: str = Form("json", description="输出格式: 'json' 或 'markdown'"),
):
    """
    文档解析接口：上传图片或 PDF，返回结构化 OCR 结果。

    - 支持中文、英文、公式 (LaTeX)、表格
    - 手写体识别
    - PDF 多页处理 (需 pymupdf)

    示例 (curl):
        curl -F "file=@image.png" http://localhost:8002/v1/ocr/parse
        curl -F "file=@image.png" -F "output_format=markdown" http://localhost:8002/v1/ocr/parse
    """
    if ocr_model.model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    tmp_path = None
    try:
        tmp_path = await save_upload(file)
        logger.info(
            "Parsing document: %s (%s bytes)",
            file.filename, tmp_path.stat().st_size,
        )

        t0 = time.time()
        result = ocr_model.predict(str(tmp_path))
        elapsed = time.time() - t0
        logger.info(
            "Parsing complete (%.2fs), %d pages", elapsed, result["page_count"]
        )

        if output_format == "markdown":
            return PlainTextResponse(
                content=result["markdown"], media_type="text/plain; charset=utf-8"
            )

        return ParseResponse(
            success=True,
            model=ocr_model.model_name,
            input_path=file.filename or "upload",
            page_count=result["page_count"],
            pages=result["pages"],
            markdown=result["markdown"],
        )

    except Exception as e:
        logger.exception("Document parsing failed")
        raise HTTPException(status_code=500, detail=f"OCR error: {e}")
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


@app.get("/")
async def root():
    return {
        "service": "GLM-OCR API",
        "model": ocr_model.model_name,
        "version": "0.1.0",
        "endpoints": {
            "health": "/health",
            "models": "/v1/models",
            "parse": "POST /v1/ocr/parse",
            "docs": "/docs",
        },
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="GLM-OCR API Server")
    p.add_argument(
        "--model",
        default="zai-org/GLM-OCR",
        help="Model path: local (models/safetensors/zai-org/GLM-OCR) or HuggingFace ID (zai-org/GLM-OCR)",
    )
    p.add_argument("--device", default="cuda", help="Device (cuda, cpu)")
    p.add_argument("--host", default="0.0.0.0", help="Bind address")
    p.add_argument("--port", type=int, default=8002, help="Bind port")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.state.model_name = args.model
    app.state.device = args.device

    logger.info(
        "Starting GLM-OCR API Server on %s:%s", args.host, args.port
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
