# pyannote.audio 访问权限设置指南

> 本指南帮助你在 `qwen-asr` conda 环境中配置 pyannote speaker-diarization 模型的访问权限。

## 前置条件

- 已安装 `pyannote.audio`（已在 T00 安装）
- 拥有 HuggingFace 账号

## 步骤 1：接受模型使用条款

你需要依次访问以下 HuggingFace 模型页面，点击 "Agree and access repository" 按钮：

1. **pyannote/segmentation-3.0** — https://hf.co/pyannote/segmentation-3.0
2. **pyannote/speaker-diarization-3.1** — https://hf.co/pyannote/speaker-diarization-3.1

> 注意：pyannote.audio 4.x 使用 `speaker-diarization-3.1` 模型，但底层的声纹分割依赖 `segmentation-3.0`，两者都需要接受条款。

## 步骤 2：创建 HuggingFace Access Token

1. 访问 https://hf.co/settings/tokens
2. 点击 "New token"
3. Token 类型选择 "Read"（读权限足够）
4. 复制生成的 token（格式：`hf_xxxxxxxxxxxxxxxxxxxx`）

## 步骤 3：配置 Token

### 方式 A：环境变量（推荐）

将以下行添加到 `~/.bashrc`：

```bash
export HF_TOKEN="hf_你的token"
```

然后执行 `source ~/.bashrc` 或重新打开终端。

### 方式 B：HuggingFace CLI 登录

```bash
<YOUR-PATH> login
```

按提示粘贴 token。token 会存入 `~/.cache/huggingface/token`。

## 步骤 4：验证

```bash
# 确认环境变量已设置
echo $HF_TOKEN | grep "^hf_"

# 测试 pyannote Pipeline 加载
<YOUR-PATH> -c "
from pyannote.audio import Pipeline
import os
pipeline = Pipeline.from_pretrained(
    'pyannote/speaker-diarization-3.1',
    token=os.environ.get('HF_TOKEN')
)
print('Pipeline loaded successfully!')
"
```

## 常见问题

### 401 Unauthorized

**原因**：Token 未设置或已失效。

**解决**：
1. 检查 `echo $HF_TOKEN` 是否输出 token
2. 确认已在 HF 上接受两条模型条款
3. 重新生成 token

### GatedRepoError: Cannot access gated repo

**原因**：未接受 pyannote/segmentation-3.0 使用条款。

**解决**：访问 https://hf.co/pyannote/segmentation-3.0 点击 "Agree and access repository"。

### CUDA Out of Memory

pyannote 在长音频上可能占用 2-4GB 显存。流程已设计为顺序加载/卸载，避免与 ASR 模型同时驻留 GPU。如仍 OOM，可指定 `device="cpu"` 在 CPU 上跑 diarization（速度较慢但可靠）。

## 备选方案：免审核社区模型

如果无法获取 pyannote 官方模型的访问权限，可以尝试社区版模型：

```python
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-community-1",
    token=os.environ.get("HF_TOKEN"),
)
```

> 注意：社区版精度略低于官方版，但免除了模型条款审核。需确认社区版在 pyannote.audio 4.x 上的兼容性。
