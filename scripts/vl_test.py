#!/usr/bin/env python3
"""
VL Smoke Test / Batch Test Helper

解决 bash 变量传递大 base64 图片会溢出的问题，提供一个
稳定可靠的命令行接口来测试 QwenVision 服务。

用法:
    # 单图测试
    python scripts/vl_test.py mcp-tool-test/vl/photos/01_street_market.jpg

    # 自定义 prompt 和参数
    python scripts/vl_test.py image.jpg -p "List all objects" --max-tokens 300

    # 批量测试一个目录
    python scripts/vl_test.py mcp-tool-test/vl/photos/ --batch

    # 查看服务状态
    python scripts/vl_test.py --status

环境变量:
    VL_HOST   — llama-server 地址 (默认 http://localhost:8080)
    VL_MODEL  — 模型名 (默认 default)
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError


def check_health(host: str, timeout: float = 5.0) -> dict | None:
    """检查 VL 服务健康状态"""
    try:
        req = Request(f"{host}/health")
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Health check failed: {e}", file=sys.stderr)
        return None


def describe_image(
    image_path: str,
    prompt: str = "Describe this image concisely. List main objects and scene type.",
    host: str = "http://localhost:8080",
    model: str = "default",
    max_tokens: int = 300,
    temperature: float = 0.2,
) -> dict:
    """发送图片到 VL 服务并返回描述结果"""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    data = json.dumps(payload).encode()
    req = Request(
        f"{host}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )

    start = time.time()
    with urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
    elapsed = time.time() - start

    result["_elapsed_s"] = round(elapsed, 1)
    return result


def print_result(result: dict, image_path: str):
    """格式化打印结果"""
    choice = result["choices"][0]
    content = choice["message"]["content"]
    usage = result.get("usage", {})
    elapsed = result.get("_elapsed_s", 0)

    print(f"\n{'='*60}")
    print(f"Image: {Path(image_path).name}")
    print(f"Model: {result.get('model', 'unknown')}")
    print(f"Time:  {elapsed}s")
    print(f"Tokens: {usage.get('total_tokens', '?')} "
          f"(prompt {usage.get('prompt_tokens', '?')} "
          f"+ completion {usage.get('completion_tokens', '?')})")
    print(f"{'='*60}")
    print(content)
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="VL Smoke Test / Batch Test Helper"
    )
    parser.add_argument(
        "image", nargs="?", help="Path to image file or directory (with --batch)"
    )
    parser.add_argument(
        "-p", "--prompt",
        default="Describe this image concisely. List main objects and scene type.",
        help="Text prompt to send with the image",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=300, help="Max completion tokens"
    )
    parser.add_argument(
        "--temperature", type=float, default=0.2, help="Sampling temperature"
    )
    parser.add_argument(
        "--host", default="http://localhost:8080", help="llama-server address"
    )
    parser.add_argument(
        "--model", default="default", help="Model name to use"
    )
    parser.add_argument(
        "--batch", action="store_true", help="Batch process: IMAGE is a directory"
    )
    parser.add_argument(
        "--status", action="store_true", help="Check service health and exit"
    )

    args = parser.parse_args()

    # Override host from env if set
    import os
    args.host = os.environ.get("VL_HOST", args.host)
    args.model = os.environ.get("VL_MODEL", args.model)

    if args.status:
        health = check_health(args.host)
        if health:
            print(f"VL Service: {json.dumps(health, indent=2)}")
        else:
            print("VL Service: UNREACHABLE", file=sys.stderr)
            sys.exit(1)
        return

    if not args.image:
        parser.print_help()
        sys.exit(1)

    if args.batch:
        image_dir = Path(args.image)
        if not image_dir.is_dir():
            print(f"Not a directory: {args.image}", file=sys.stderr)
            sys.exit(1)
        images = sorted(image_dir.glob("*.jpg")) + sorted(image_dir.glob("*.png"))
        if not images:
            print(f"No images found in {args.image}", file=sys.stderr)
            sys.exit(1)
        print(f"Batch processing {len(images)} images...")
        for img in images:
            try:
                result = describe_image(
                    str(img),
                    prompt=args.prompt,
                    host=args.host,
                    model=args.model,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                )
                print_result(result, str(img))
            except Exception as e:
                print(f"FAILED {img.name}: {e}", file=sys.stderr)
    else:
        try:
            result = describe_image(
                args.image,
                prompt=args.prompt,
                host=args.host,
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
            print_result(result, args.image)
        except Exception as e:
            print(f"FAILED: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
