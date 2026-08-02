# pyannote.audio Access Setup Guide

> This guide helps you configure access to the pyannote speaker-diarization model in the `mcp-local-asr` conda environment.

## Prerequisites

- `pyannote.audio` installed by `bash install.sh --asr-only` in `mcp-local-asr`
- A Hugging Face account

## Step 1: Accept Model Usage Terms

You need to visit the following HuggingFace model pages in order and click the "Agree and access repository" button:

1. [`pyannote/segmentation-3.0`](https://hf.co/pyannote/segmentation-3.0)
2. [`pyannote/speaker-diarization-3.1`](https://hf.co/pyannote/speaker-diarization-3.1)

> Note: pyannote.audio 4.x uses the `speaker-diarization-3.1` model, but the underlying voice segmentation depends on `segmentation-3.0` — both require accepting the terms.

## Step 2: Create a Hugging Face Access Token

1. Visit https://hf.co/settings/tokens
2. Click "New token"
3. Select "Read" as the token type (read permission is sufficient)
4. Copy the generated token (format: `hf_xxxxxxxxxxxxxxxxxxxx`)

## Step 3: Configure the Token

### Secure process environment (recommended)

The current pipeline reads `HF_TOKEN` directly. Supply it through the secure
environment inherited by the MCP client or export it in the shell that starts
the client. For a temporary interactive shell without echoing the token:

```bash
read -rsp "Hugging Face token: " HF_TOKEN
printf '\n'
export HF_TOKEN
```

Do not store the token in this repository, a committed `.env` file, MCP
configuration, logs, or documentation. Restart an already-running MCP client
after changing its inherited environment.

Hugging Face CLI login can populate the local Hugging Face credential cache,
but it does not replace `HF_TOKEN` for this repository's current pipeline:

```bash
hf auth login
```

## Step 4: Verify

```bash
# Confirm presence without printing the token
if [[ -n "${HF_TOKEN:-}" ]]; then echo "HF_TOKEN is set"; else echo "HF_TOKEN is missing"; fi

# Test pyannote Pipeline loading (run inside the mcp-local-asr conda env)
conda run -n mcp-local-asr python -c "
from pyannote.audio import Pipeline
import os
pipeline = Pipeline.from_pretrained(
    'pyannote/speaker-diarization-3.1',
    token=os.environ.get('HF_TOKEN')
)
print('Pipeline loaded successfully!')
"
```

## Common Issues

### 401 Unauthorized

**Cause**: Token not set or expired.

**Solution**:
1. Check whether `HF_TOKEN` is present without printing its value
2. Confirm you have accepted both model terms on HF
3. Regenerate the token

### GatedRepoError: Cannot access gated repo

**Cause**: pyannote/segmentation-3.0 usage terms not accepted.

**Solution**: Visit https://hf.co/pyannote/segmentation-3.0 and click "Agree and access repository".

### CUDA Out of Memory

pyannote may consume 2-4 GB VRAM on long audio. The pipeline is designed to load/unload sequentially, avoiding co-residency with the ASR model on GPU. If OOM still occurs, specify `device="cpu"` to run diarization on CPU (slower but reliable).

## Alternative Model Note

As verified against the official model card on 2026-08-02,
`pyannote/speaker-diarization-community-1` is not a no-approval fallback: it
also requires accepting user conditions and creating an access token. It is
not used by the current pipeline, which is pinned to
`speaker-diarization-3.1`; switching models requires a code change and a full
compatibility/accuracy check.
