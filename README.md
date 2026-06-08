# MCP Tools — 开箱即用的 AI Agent 工具集

本地 MCP (Model Context Protocol) 工具集，为 LLM 补充文档 OCR 解析和听觉（语音转文字）能力。适用于纯文本模型需要多模态输入的场景，也可作为独立的本地批处理工具使用——底层 FastAPI 后端可直接 HTTP 调用，不绑定任何特定 MCP 客户端。如果你使用多模态模型，可直接依赖其原生能力处理同类任务，本工具集为可选补充。所有推理 GPU 本地完成，无需联网、零 API 费用。

> **推荐搭配**: [ai-agent-framework](https://github.com/lanxiukai/ai-agent-framework) >= **v0.3.8** 提供开箱即用的 MCP 编排与 agent 权限管理。本工具集兼容任何 MCP 客户端（Claude Desktop 等），也可脱离 MCP 直接调用 REST API。

## 工具概览

| 工具 | 功能 | 模型 | 显存 |
|---|---|---|---|
| **Qwen3-ASR** | 语音转文字（52 语言）、说话人分离 | Qwen3-ASR-1.7B | ~3.5 GB |
| **GLM-OCR** | 文档解析（图片/PDF → Markdown，含表格/公式） | GLM-OCR 0.9B | ~2.5 GB |
| **ASR Pipeline** | 播客长音频转写 + 说话人分离（CLI） | Qwen3-ASR + pyannote | ~6 GB |
| **Format Conversion** | 文档格式转换（MD/HTML→PDF, PDF→Text） | Chromium + WeasyPrint + PyMuPDF | 纯 CPU |

各工具的 MCP 接口、opencode.jsonc 配置、API 参数详见 [`docs/tools-reference.md`](docs/tools-reference.md)。各子项目文件结构、手动运行方法见各自 README。

> **PDF 处理优先级**：先调 `pdf_to_text`（毫秒级文本提取）→ 文本为空时再调 `ocr_glm`（~20 秒/页 VLM OCR）。避免对 born-digital PDF 做无意义的 GPU 推理。

## 前置条件

- **操作系统**: Linux (Ubuntu 22.04+) 或 WSL2
- **GPU**: NVIDIA GPU，建议 ≥ 12 GB 显存
- **CUDA**: 12.4+
- **conda / mamba**: 用于环境管理
- **[ai-agent-framework](https://github.com/lanxiukai/ai-agent-framework) >= v0.3.8**: 提供 MCP 编排和 agent 权限管理

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/lanxiukai/mcp-tools.git
cd mcp-tools

# 2. 一键安装
bash install.sh

# 3. 配置 OpenCode
# 将 install.sh 输出的配置片段复制到 ~/.config/opencode/opencode.jsonc 的 "mcp" 块中

# 4. 重启 OpenCode 即可使用
```

> 首次调用工具时，HuggingFace 会自动下载模型权重到本地缓存。你也可以运行 `bash install.sh` 预下载。

## 文档导航

| 文档 | 内容 |
|---|---|
| [`docs/tools-reference.md`](docs/tools-reference.md) | 各工具 API、opencode.jsonc 配置、模型说明与性能数据 |
| [`docs/mcp-tools-testing.md`](docs/mcp-tools-testing.md) | 工具使用指南、测试样本、冒烟测试 |
| [`docs/tools-verification-report.md`](docs/tools-verification-report.md) | 性能/准确性验证报告（RTX 4070 Ti） |
| [`docs/qwen3-asr-audio-formats.md`](docs/qwen3-asr-audio-formats.md) | ASR 音频格式、语言支持、切块机制 |
| [`docs/glm-ocr-formats.md`](docs/glm-ocr-formats.md) | OCR 图片/PDF 格式、输出格式、公式处理 |
| [`docs/asr-pipeline-formats.md`](docs/asr-pipeline-formats.md) | Pipeline 管线阶段、输出格式、说话人分离 |
| [`format-conversion/README.md`](format-conversion/README.md) | 格式转换工具：MCP 接口、模块 API、CLI 用法、引擎对比 |
| `asr/`、`ocr/`、`asr-pipeline/` | 各子项目均有独立 README（文件结构 + 手动运行方法） |

## 目录结构

```
mcp-tools/
├── asr/              # Qwen3-ASR → README
├── ocr/              # GLM-OCR  → README
├── asr-pipeline/     # 播客管线 CLI → README
├── format-conversion/ # 格式转换 → README
├── docs/             # 工具参考、测试、验证文档
├── install.sh        # 一键安装脚本
└── README.md
```

## 架构

每个 MCP 工具由 MCP stdio 前端 + FastAPI GPU 后端组成，前端首次调用时自动唤醒后端。详见各子项目 README。

## 版本更新

| 版本 | 日期 | 变更概述 |
|---|---|---|---|
| v0.3.1 | 2026-06-08 | 删除 qwen_vision MCP 工具（vl/ 目录 + 关联文档 + 测试脚本）；config 同步移除 describe_image/vision_status 权限；.gitignore 新增 .omo/ |
| v0.3.0 | 2026-05-23 | Format Conversion: Chromium 后端（Playwright）解决 WeasyPrint flex/grid 渲染差异，默认引擎切换；pdf_to_text 自动保存 .txt；GLM-OCR: 异步任务队列（submit/wait/status），非阻塞服务器启动，MCP timeout 180s→1800s；WeasyPrint 67.0→68.1；converter 模块重构（CSS 注入可组合化） |
| v0.2.1 | 2026-05-13 | README 拆分（302→110 行）：工具详情移入 docs/tools-reference.md，测试段移除（mcp-tool-test/ 未追踪对远程用户无效）；修正工具定位措辞（去 ai-agent-framework 绑定，明确独立可用）；补充 License 段；GitHub 链接 your-org → lanxiukai |
| v0.2.0 | 2026-05-13 | ASR 长音频全线支持：MCP 服务端 + pipeline 480s 分块、diarization 900s 分块；修复 4 个 bug（max_new_tokens 256→4096、device_map GPU 缺失、batch_size 8→1、单 chunk 1200s OOM）；新增 `--no-timestamps`/`--max-new-tokens`/`--batch-size` CLI；新增 `transcribe_podcast` MCP 工具；全仓文档审计（13 文件 628 行）；opencode 配置同步（timeout 30min + permissions）；RTX 4070 Ti 12GB 实测 2h 播客 19-23min |
| v0.1.1 | 2026-05-12 | 修复 MCP 启动与显存管理：竞态杀互斥加载（防 12GB OOM）、3 服务统一 30s 空闲超时、修复 bash shift + set -e 静默退出 bug |
| v0.1.0 | 2026-05-12 | 初始发布：3 个 MCP 工具（ASR/OCR/ASR Pipeline），含测试样本、冒烟测试与一键安装脚本 |

## License

MIT — 见 [`LICENSE`](./LICENSE)。

本仓库所有源码、MCP Server 实现、文档均受 MIT 保护。可自由使用、修改、分发——保留版权声明即可。
