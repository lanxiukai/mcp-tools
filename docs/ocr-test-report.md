# OCR migration and verification report

**Date:** 2026-07-22
**Performance update:** 2026-07-23
**Host:** NVIDIA GeForce RTX 4070 Ti 12 GB
**Status:** Complete. Implementation, smoke, real-handwriting, staged PDF tests
through the full 96-page document, configuration migration, and post-restart
Codex MCP checks all passed.

## Outcome

The model-specific `glm_ocr` service was replaced with a stable `ocr` MCP
surface exposing `ocr_document`, `ocr_submit`, `ocr_wait`, and `ocr_status`.
The old `glm_ocr_*` entry points were deleted. The current adapter combines:

1. PP-DocLayoutV3 in a short-lived isolated PaddlePaddle process for ordered
   page regions;
2. local PaddleOCR-VL-1.6 through Transformers for element recognition;
3. bounded crop batches across up to four consecutive pages;
4. horizontal-tile fallback when layout detection is unavailable.

This boundary is model-neutral: client tool names and durable artifact schemas
do not include Paddle- or GLM-specific fields.

## Model and runtime inventory

| Component | Verified value |
|---|---|
| Recognition model | `PaddlePaddle/PaddleOCR-VL-1.6` (0.9B) |
| Local snapshot | `~/project/hf-models/models/safetensors/PaddlePaddle/PaddleOCR-VL-1.6` |
| Snapshot check | 19/19 repository files present, approximately 1.8 GB |
| Unified runtime | `mcp-local-ocr`: Python 3.12, PyTorch 2.11.0+cu130, Transformers 5.8.0, PaddlePaddle GPU 3.2.1, PaddleOCR 3.7.0, PaddleX 3.7.2 |
| Layout model | `~/project/hf-models/models/safetensors/PaddlePaddle/PP-DocLayoutV3` |
| Default generation | 512 tokens per element, 60-second batch ceiling, crop batch 4 |
| PDF render | 200 DPI |

The requested `~/hf-modes` directory was not present; the existing downloader
and model root are under `~/project/hf-models`. Its configured snapshot was
already complete, so no redundant download or downloader rewrite was needed.

Official references: [PaddleOCR-VL-1.6 model
card](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) and [PaddleOCR-VL
pipeline documentation](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html).

The recognition and layout dependencies were later consolidated into the
single `mcp-local-ocr` Conda environment. GPU inference remains process-based:
the resident server uses PyTorch and the short-lived layout subprocess uses
PaddlePaddle (PaddleX optional dependencies may import Torch transitively).
The obsolete `mcp-paddle-ocr` environment was removed after GPU verification.

## Why VRAM was full while the GPU was idle

The first official native page-pipeline trial held about 11.4/12 GB of VRAM,
while sampled GPU utilization was normally 0–2% with an occasional 10% peak.
The model weights and CUDA caches were resident, but CPU preprocessing,
fine-grained scheduling, synchronization, and token-by-token decoding left the
SMs idle. A one-page smoke input had still not returned after more than seven
minutes, so this configuration was rejected.

The isolated-layout/Transformers design showed materially different behavior:

| Workload | GPU utilization | VRAM | Power |
|---|---:|---:|---:|
| Four-crop real engineering note batch | 50–86% | about 6.2 GB peak | 136–183 W |
| Dense PDF recognition | 92–96% | about 6.0–7.67 GB | 199–212 W |
| Recognizer after work | 0–6% | about 3.9 GB process memory | about 16–45 W |

Thus high memory use during the final tests corresponded to sustained compute,
and memory dropped when batches ended.

## Automated and protocol verification

| Test | Result |
|---|---|
| Final OCR unit/protocol suite | 65/65 passed after cross-page pooling, durable recovery, and runtime restoration checks |
| Final model-adapter subset | 11/11 passed |
| Python compilation | All five generic server/adapter/protocol modules passed |
| Shell syntax | `ocr_start.sh` and `install.sh` passed `bash -n` |
| Dependency check | Torch CUDA, Transformers, MCP/FastAPI, Paddle/PaddleX, both local models passed |
| Real MCP stdio tools | Exact list: `ocr_document`, `ocr_submit`, `ocr_wait`, `ocr_status` |
| Durable result protocol | Artifact-only metadata with ordered page lists and SHA-256 passed |

The MCP smoke fixture completed with the heading and all five formula lines.
With a warm final backend it took 9.10 seconds. A cold MCP invocation also
exercised the documented non-blocking auto-start/retry path.

## Genuine handwriting tests

Only the clean real samples under
`mcp-tool-test/ocr/handwriting/real/20260722/` were used. The synthetic
`handwriting/generated/` data was excluded. `SOURCES.md` beside the fixtures
records dataset rows, reference strings, license notes, and SHA-256 values.

| Domain | Reference | Observed result | Assessment |
|---|---|---|---|
| English (IAM) | `and Rock Hudson ... Rolls-Royce .` | All words correct; terminal spacing normalized | Pass |
| Chinese (CASIA) | `制度改革积极稳妥地推进。去年，全国共有12个省、自治区、` | All characters/numerals correct; one comma normalized to ASCII | Pass |
| Calculus | `\int e^x dx=e^x+C` | Semantically exact LaTeX | Pass |
| Linear algebra | block matrix/vector product | Lost `g`/`y`; matrix syntax confused | Partial |
| Probability | `f(\theta)=P(D\mid\theta,M)` | `theta`/`M` confused with Latin glyphs | Partial |
| Statistics | `(\Sigma_0^{-1}+n\Sigma^{-1})^{-1}` | `Sigma` confused with summation | Partial |
| Physics | `E_k=mc^2-m_0c^2` | Semantically exact LaTeX | Pass |
| Machine learning | `K(x,x_i)=x_i^Tx` | One `x_i` read as `y_i` | Partial |
| Computer science | `O(n^2 log(r) log(q))` | `n` read as `x` | Partial |
| Integrated circuits | circuit notes, Ohm/KVL equations, data table | Prose and Markdown table recovered; circuit/equation glyphs partly confused | Partial |

Short images below 384 pixels now skip the six-second page-layout cold start.
Warm single-line calls fell from about 9.1 seconds to about 3.1 seconds. A
compact-line heuristic routes formula-like images to a generic formula intent,
but the remaining Greek/Latin ambiguity is a recognition-model limitation, not
a layout failure.

## PDF progression

Tests used `ocr_submit` followed by `ocr_wait`; Markdown was copied from the
returned durable artifact paths. The scanned source is the 96-page public-domain
Chinese woodblock fixture `scanned_chinese_yuzhidaao.pdf`.

| Document | Pages | End-to-end time | Result |
|---|---:|---:|---|
| Dense born-digital paper page | 1 | 75.53 s warm | 2,959-byte complete first page; no whole-page truncation |
| Scanned Chinese prefix | 1 | 9 s job / 28.94 s including cold start | Pass; cover text |
| Scanned Chinese prefix | 3 | 99.39 s | Pass; 855-byte artifact |
| Scanned Chinese prefix | 5 | 136.33 s | Pass; 1,756-byte artifact |
| Scanned Chinese prefix | 15 | 421.52 s (28.1 s/page) | Pass; 6,257-byte, 313-line artifact |
| Scanned Chinese prefix | 30 | 876.52 s (29.2 s/page) | Pass; 2 artifacts, 13,476 bytes, 524 lines |
| Full scanned Chinese document | 96 | 3,705 s (61 min 45 s) wall time, including recovery | Pass; 4 artifacts, 42,120 bytes, 1,375 lines; first 72 pages reused after restart |

The 15-, 30-, and 96-page outputs preserved page ranges and artifact order. The
96-page run also exercised durable recovery: after an interrupted inference
worker, its first three verified 24-page artifacts were retained and only pages
73–96 were recomputed. That recovered chunk took 757.22 seconds. Kernel logs
contained no OOM or NVIDIA Xid. The launcher now serializes simultaneous
auto-start attempts, and `ocr_wait` can initiate the non-blocking restart path.
The source scan has blurred historical glyphs: a few characters became Unicode
replacement marks, and some vertical catalog columns were not ordered perfectly.
The staged artifacts looked complete during the original run. A later
controlled cache-off/cache-on comparison found that several cache-off batches
had in fact reached the 60-second generation ceiling; the 2026-07-23
performance section below supersedes that earlier observation.

All raw results and Markdown snapshots are under
`/tmp/ocr-paddleocr-vl-tests/20260722-hybrid/`.

## 2026-07-23 performance optimization

The root cause of the approximately 30-second/page behavior was disabled
autoregressive KV caching. Both the downloaded model configuration and its
generation configuration declare `use_cache=false`. The adapter now passes
`use_cache=true` explicitly by default, with `OCR_USE_KV_CACHE=0` retained only
as a compatibility fallback.

| Workload | End-to-end or recognition time | Seconds/page | Result |
|---|---:|---:|---|
| Representative scanned content page, recognition only | 3.94 s | 3.94 | Cache-on output exactly matched cache-off |
| 3-page scanned PDF, full path | 14.62 s | 4.87 | Pass |
| 15-page scanned PDF, production default | 58.73 s | 3.92 | Pass; 2,614 characters |
| 30-page scanned PDF, production default | 112.34 s | 3.75 | Pass; 5,565 characters |

A final real MCP stdio smoke test used the printed
`calculus_made_easy_fig13.png` fixture. Cold start plus `ocr_document` completed
in 28.31 seconds, with the queued one-page OCR work itself completing in about
9 seconds. The live `/health` response reported `kv_cache_enabled=true`.

A same-process 15-page A/B isolated the cache change from model and process
startup:

| Setting | Total | Seconds/page | Characters | Peak allocated VRAM |
|---|---:|---:|---:|---:|
| KV cache off | 419.04 s | 27.94 | 2,329 | 2.419 GiB |
| KV cache on | 67.53 s | 4.50 | 2,614 | 2.416 GiB |

This is a 6.21x speedup with slightly lower peak allocation. The normalized
cache-off/cache-on document similarity was 0.9354. Inspection showed that the
additional cache-on text completed paragraphs cut off when cache-off batches
hit `OCR_MAX_GENERATION_SECONDS=60`; a representative two-crop page was exactly
identical between modes. Crop batch eight offered no benefit over batch four
(4.00 versus 3.94 seconds), so the safer batch-four default remains.

FlashAttention2 2.8.3.post1 was built from the official source for SM80 and
validated on the RTX 4070 Ti. Its BF16 CUDA kernel matched PyTorch SDPA exactly
in the numerical smoke test. The real five-crop, three-page OCR comparison also
produced identical SHA-256 output and the same 2.416 GiB peak allocation:

| Attention backend | Three steady runs | Median |
|---|---|---:|
| SDPA | 9.642 s, 9.431 s, 10.565 s | 9.642 s |
| FlashAttention2 | 14.714 s, 12.216 s, 11.797 s | 12.216 s |

FlashAttention2 was 26.7% slower at the median and had a larger first-use
startup cost, so it was not enabled and the experimental package was removed.
The built wheel remains in the local pip cache for future model testing.

The 15-page stage profile measured 1.85 seconds for PDF rendering and 6.79
seconds for all-page layout. One-page layout already took 6.65 seconds, showing
that nearly all layout time is the isolated model startup rather than per-page
work. Starting recognition earlier could therefore hide at most roughly the
1.85-second render stage for this single-document workload (about 3% of the
optimized total), while making Paddle and PyTorch contend on one 12 GB GPU and
complicating durable recovery. The production path remains sequential.

The official PaddleOCR deployment path supports vLLM for higher-concurrency VLM
serving and an asynchronous three-stage pipeline for large batched workloads.
Those changes were not introduced here: the local scheduler intentionally
serializes one GPU workload, current latency is below four seconds/page on
15–30 page documents, and a second resident inference service would add
substantial environment and VRAM cost without evidence of a single-job latency
gain.

Raw measurements are in `/tmp/ocr-speed-study-20260723/`.

## Post-restart verification

OpenCode and Codex now register the generic `ocr` MCP server and grant the four
generic tools. Both configuration files parse successfully and contain no
active `glm_ocr`, `ocr_glm`, or old script paths. The first Codex restart
confirmed the exact four-tool catalog, the expected offline status, and a
successful client-driven backend wakeup. That cold start also recovered an old
30-page durable job ahead of the smoke fixture. The Codex host ended the
long-running tool call at 300 seconds while the backend job continued, exposing
a missing client timeout setting rather than an OCR failure. Codex now has
`startup_timeout_sec = 90` and `tool_timeout_sec = 1800`; OpenCode already uses
an 1,800,000 ms timeout. The second Codex restart activated these settings.

After the second restart, Codex exposed the exact four generic tools and a clean
client-driven cold start completed smoke job
`6c00744c13874edb94d27ca78bb8089b`. `ocr_document`, job-specific
`ocr_status`, and `ocr_wait` all returned the same one-page artifact and
SHA-256 `ed3d5459b99a3b20245fb1455a4dc80e765ddfd41115f0ce311e6731d0f4c632`.
The 172-byte Markdown contained the expected heading and all five formula
lines. `opencode mcp list` independently reported the generic `ocr` server as
connected with the same adapter command. No post-restart checks remain.
