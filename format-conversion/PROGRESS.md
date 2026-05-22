# format-conversion — Builder 进度追踪

## Done

2026-05-22 14:30 | T01 | done | 创建 conda 环境 format-convert (Python 3.12)，安装 weasyprint/markdown-it-py/pymupdf/mcp，更新 docs/conda-environments.md
2026-05-22 14:35 | T02 | done | 创建 converter.py（3 个公开函数 + 字体发现 + CSS 构建）和 __init__.py，单文件 ~290 LOC，所有功能测试通过
2026-05-22 14:40 | T03 | done | 重构 md2pdf.py 和 html2pdf.py 为薄 wrapper（~35 LOC each），import converter，CLI 用法向后兼容

## Blocked

（无）

## Plan-Issue

（无）

## Need-Approval

（无）

## Review-Round

（无）
