# format-conversion — Builder 进度追踪

## Done

2026-05-22 14:30 | T01 | done | 创建 conda 环境 format-convert (Python 3.12)，安装 weasyprint/markdown-it-py/pymupdf/mcp，更新 docs/conda-environments.md
2026-05-22 14:35 | T02 | done | 创建 converter.py（3 个公开函数 + 字体发现 + CSS 构建）和 __init__.py，单文件 ~290 LOC，所有功能测试通过
2026-05-22 14:40 | T03 | done | 重构 md2pdf.py 和 html2pdf.py 为薄 wrapper（~35 LOC each），import converter，CLI 用法向后兼容
2026-05-22 14:50 | T04 | done | 创建 format_mcp_server.py（~100 LOC），3 个 @mcp.tool() 装饰函数，错误处理 + auto output_path
2026-05-22 14:55 | T05 | done | 更新 format-conversion/README.md（MCP 工具表 + 模块 API + 环境准备），更新仓库根 README.md（工具概览 + 目录结构 + 文档导航）
2026-05-22 14:55 | T06 | skipped | 用户指示跳过——opencode 配置未加入该 MCP，暂不执行集成冒烟测试
2026-05-22 15:05 | — | delivered | 项目交付。DoD 全部达标，review 两轮 APPROVED，learning-notes 已生成（8 篇）

## Blocked

（无）

## Plan-Issue

（无）

## Need-Approval

（无）

## Review-Round

（无）
