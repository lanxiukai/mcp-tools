# Conda Runtime Records

This directory stores reproducible records for the three Conda runtimes owned
by this repository:

- `mcp-local` for shared CPU-side MCP tools;
- `mcp-local-asr` for Qwen3-ASR and speaker diarization;
- `mcp-local-ocr` for PaddleOCR-VL and PP-DocLayoutV3.

`install.sh` remains the declarative provisioning workflow. The generated
records capture the exact tested state, including Conda artifacts, pip package
versions, CUDA-specific package indexes, checksums, and repository revision.

The unified manager and documentation live in the sibling
`ai-agent-framework` repository at `config/conda/`. Do not edit generated lock
files manually; refresh them after an intentional runtime update and successful
verification.
