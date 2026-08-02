# Vision Local Verification Report — 2026-07-23

## Outcome

`vision_local` ran the current two-profile deployment — both profiles on Unsloth `UD-Q4_K_XL` dynamic-quant weights — through a CUDA llama.cpp backend and exposed eight model-neutral MCP tools. Real stdio smoke testing passed all eight cases on the 9B default profile, and the 4,500-image glasses audit completed on the 4B batch profile with zero residual errors. These measurements replace the earlier Q4_K_M numbers and should not be compared with them as a like-for-like quant benchmark.

The automated audit produced a 26-image disagreement queue, all from `G`. A 1024-pixel second pass on the 9B profile resolved 11 of them and left 15 machine-flagged candidates, but Kimi K3 native visual inspection of all 26 original-resolution candidates found their directory labels consistent with the visible eyewear; every candidate wears exceptionally thin or rimless glasses. A native-resolution crop spot-check of the faintest candidate (`face-45.png`) confirmed a rimless temple arm and hinge. The final visually reviewed misclassification list is therefore empty. This is not an independent human annotation. The result also reconfirms an important model limitation: even a high-confidence structured answer is not ground truth for nearly invisible rimless eyewear.

## Reproducible deployment

Reference hardware: NVIDIA RTX 4070 Ti 12 GB, Ada compute capability 8.9.

| Component | Pinned artifact |
|---|---|
| Default model repo | `unsloth/Qwen3.5-9B-GGUF`, revision `3885219b6810b007914f3a7950a8d1b469d598a5` |
| Default weights | `Qwen3.5-9B-UD-Q4_K_XL.gguf`, 5,966,095,584 bytes, SHA-256 `6f5d30666c2d8ae16a306e616d95341dcf3cc46810df84d7e6f5a7d1e4c1b293` |
| Default vision projector | `mmproj-BF16.gguf`, 921,705,024 bytes, SHA-256 `853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383` |
| Batch model repo | `unsloth/Qwen3.5-4B-GGUF`, revision `e87f176479d0855a907a41277aca2f8ee7a09523` |
| Batch weights | `Qwen3.5-4B-UD-Q4_K_XL.gguf`, 2,912,109,728 bytes, SHA-256 `b252c5610a42ca82d20fe2a12813e9d069eed89292907e26c783eeb0bc961bc7` |
| Batch vision projector | `mmproj-BF16.gguf`, 675,569,344 bytes, SHA-256 `302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6` |
| Runtime | llama.cpp tag `b9637`, commit `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`, CUDA build for SM 8.9 |

Every SHA-256 above was recomputed locally and matches the Hub LFS object ID at the pinned revision. NVFP4 was not selected because native FP4 matrix multiplication requires Blackwell-class hardware; Ada supports FP8 and older integer formats, but not Blackwell FP4 Tensor Cores. Unsloth's UD-Q4_K_XL dynamic 4-bit quant is the practical CUDA path for this 12 GB card. See the NVIDIA TensorRT RTX [quantized-types support table](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/inference-library/work-quantized-types.html) and [performance guidance](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/performance/best-practices.html).

The model files live in the sibling `hf-models` repository under `../hf-models/models/gguf/unsloth/Qwen3.5-{9B,4B}-GGUF/`. The repository-local llama.cpp source/build trees live under Git-ignored `.runtime/`. No package was installed system-wide and no dependency lockfile was changed.

## Public smoke suite

Seven public/free-license fixtures cover four portrait labels, a natural object photo, a bar chart, and a formula image. Their source URLs and expected labels are recorded in `mcp-tool-test/vision-local/samples.json`.

The final real stdio MCP run on the warm 9B UD-Q4_K_XL backend passed 8/8 cases:

- four fast portrait classifications matched their expected labels;
- general image analysis returned the expected object;
- chart analysis identified both series and their peaks;
- text extraction preserved every formula line;
- high-resolution eyewear verification identified visible frame cues.

The final warm-backend run took 25.859 seconds. Fast 512-pixel portrait calls took 0.596–0.991 seconds each on the warm backend. The chart response intentionally used a much larger decode budget and took 14.348 seconds. An earlier identical run against a cold backend also passed 8/8 in 40.378 seconds, including a 12.337-second first call that covered model load and backend startup.

Result: `mcp-tool-test/vision-local/smoke-results-20260723-1908.json` (warm) and `smoke-results-20260723-1907.json` (cold).

## Full glasses audit

The requested `NG/` directory is named `NoG/` on disk. It was used as the negative label without renaming user data. The coarse pass ran on the 4B UD-Q4_K_XL batch profile; the 1024-pixel verification ran on the 9B UD-Q4_K_XL default profile.

| Stage | Result |
|---|---:|
| `G/` input | 2,543 images |
| `NoG/` input | 1,957 images |
| Total | 4,500 images |
| Coarse pass | 4,500 successful, 0 residual errors |
| Coarse elapsed / throughput | 2,502.081 s / 1.798 images/s |
| Coarse disagreements | 26: `G` 26, `NoG` 0 |
| 1024-pixel verification | 26 successful, 0 errors, 52.876 s |
| Machine review queue after verification | 15: `G` 15, `NoG` 0 |
| Kimi K3 native visual review of all 26 original candidates | 0 visually confirmed label errors |

Two coarse outputs (`face-903.png`, `face-1921.png`) were initially truncated at the 32-token decode cap before the JSON object closed; the runner's resume pass reclassified both successfully in 1.2 seconds, leaving zero residual errors. The elapsed figure above is the initial 4,500-image pass. Because the runner rewrites `summary.json` on every invocation, that artifact's counts are final but its `elapsed_seconds` describes only the short resume pass; the initial-pass timing is recorded here.

The final visually reviewed error CSV contains only its header because no candidate was confirmed as mislabeled. The machine-generated review queue is intentionally retained separately for auditability. An independent human should still check candidates before labels are changed.

Primary artifacts:

| Artifact | Meaning |
|---|---|
| `glasses-audit-20260723-1910/results.jsonl` | Durable record for all 4,500 coarse classifications |
| `glasses-audit-20260723-1910/summary.json` | Coarse counts, timing, and 26 disagreements |
| `glasses-audit-20260723-1910/verification-results.jsonl` | High-resolution result for every coarse disagreement |
| `glasses-audit-20260723-1910/verified-misclassified.csv` | Fifteen machine-flagged items requiring review; not ground truth |
| `glasses-audit-20260723-1910/manual-review-summary.json` | Kimi K3 native visual disposition of all 26 candidates |
| `glasses-audit-20260723-1910/visually-reviewed-misclassified.csv` | Final visually reviewed label-error list; empty |

All paths above are relative to `mcp-tool-test/vision-local/`.

## Efficient local batch design

The implemented batch path is efficient and recoverable:

1. Load the model once, keep four continuous-batching slots warm, and use CUDA flash attention.
2. Resize the coarse input in memory to a 512-pixel longest edge and constrain the answer to a two-field JSON schema with a 32-token decode cap.
3. Send four concurrent requests, achieving 1.798 images/s across this collection.
4. Append each completed record to JSONL immediately; an interrupted run resumes from the latest successful record and retries only errors — in this audit it transparently recovered two schema-truncated outputs.
5. Write progress atomically and emit final JSON plus review-friendly CSV artifacts.
6. Spend the more expensive 1024-pixel/128-token pass only on label disagreements.
7. Keep an independent human checkpoint for thin/rimless eyewear because the tested models produced confident false negatives on this visual edge case.

This funnel reduced manual inspection from 4,500 originals to 26 candidates (0.58% of the collection). It is suitable for triage, but the measured rimless-glasses failure means the automated output must not be used to delete, relabel, or move images without review.

## Configuration and runtime behavior

Codex, Kimi Code, and Kilo Code all register the service as `vision_local`; the configuration does not expose the backing model name. During this report's audit, the default profile bound to `127.0.0.1:8003`, used four slots, and unloaded model/KV memory after 300 seconds idle; the batch profile used `127.0.0.1:8004` with the same slot and idle policy. Both profiles now default to the UD-Q4_K_XL weights pinned above; the earlier Q4_K_M files were removed from disk after this deployment was validated.

The MCP implementation and usage details are in [`../vision-local/README.md`](../vision-local/README.md). llama.cpp request compatibility is based on its [server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) and [multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md).
