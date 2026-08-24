# Reliability Test Matrix

This document records the risk-based reliability hardening run performed on
2026-08-24 against the `v0.11.0` code line. It covers all 24 repository-owned
MCP tools. It is evidence for the tested environment, not a universal
performance specification.

## 1. Assessment and scope

Overall status: **Acceptable with known limits**.

The normal CPU gate, focused fault injection, real local-browser integration,
real OCR and Vision workloads, ASR long-audio diagnostics, CPU ASR lifecycle
tests, real CPU speaker diarization, and the dedicated bounded ASR/Vision GPU
profiles all passed after the defects listed below were fixed. The remaining
limits are material: only WSL2/Linux x86-64 was exercised on a live host and
simultaneous heavyweight GPU inference is intentionally unsupported. The
default ASR 1.7B and Vision 9B profiles remain explicitly outside the 8 GB
claim.

Tested host:

- WSL2, Ubuntu 24.04.4 LTS, Linux x86-64, kernel
  `6.18.33.2-microsoft-standard-WSL2`.
- Python 3.12.13 through the repository-owned locked uv environments; uv
  0.12.5.
- NVIDIA GeForce RTX 4070 Ti, 12,282 MiB, Windows driver 610.88 / WSL driver
  610.57.01, CUDA UMD 13.3.
- 851 GiB free disk at the start of the run and a 1,048,576 file-descriptor
  soft limit.
- No macOS, native Windows, Ubuntu 22.04, ARM64, AMD/ROCm, or non-WSL Linux host
  was available. Native Ubuntu 24.04 was also not exercised on the live GPU
  host. The CPU-safe GitHub Actions job now targets both `ubuntu-22.04` and
  `ubuntu-24.04`; this code change is not a claim that those remote jobs have
  already completed.

During the run, an additional requirement capped subsequent stress-test GPU
usage at 8 GB. The new integration harness used an explicit 8000 MiB
whole-device budget, sampled every 50 ms, and terminated a backend at the
conservative 7744 MiB abort threshold. It ran official Qwen3-ASR-0.6B and the
bounded Qwen3.5-4B Vision profile sequentially twice, observed conservative
maxima of 4658 MiB and 5518 MiB, and verified memory release between them.
Baseline and final readings were 2413 MiB and 2366 MiB with no compute process
reported by `nvidia-smi`.
The test used a 12,282 MiB physical GPU constrained by the monitor, not a
physical 8 GB card. Earlier default-profile diagnostics reached 11,977 MiB;
those results remain explicitly **not 8 GB-compliant** and were not repeated.

Result labels:

- **Pass**: the scoped contracts and relevant stress/failure cases passed.
- **Pass with limit**: the tool passed the exercised cases, with a concrete
  unverified or unsupported boundary stated in the row.

## 2. Priority model

- **P0**: long-running, durable, batch, GPU-heavy, concurrency-sensitive, or
  external-process-heavy.
- **P1**: important runtime and unusual-input behavior without the same
  long-running risk.
- **P2**: lightweight status and contract behavior; no expensive stress.

The distribution is 12 P0, 7 P1, and 5 P2 tools.

## 3. Per-tool matrix

### 3.1 Format Conversion

| Tool | Tier | Existing coverage | Added and executed coverage | Hardware | Network / credentials | Long, concurrency, and recovery relevance | Platform scope | Result |
|---|---:|---|---|---|---|---|---|---|
| `markdown_to_pdf` | P0 | Themes, page numbering, MathJax, Chromium and WeasyPrint paths | Empty input; Unicode, spaces, CJK, emoji, missing fonts/MathJax/Chromium; long equations and malformed LaTeX; large table and long line; repeat and four-way parallel runs; permission and injected renderer failures; atomic output and temp cleanup | CPU; Chromium for default engine | No external network or credentials for local fixtures | Large document, external renderer lifecycle, fallback, crash-safe publication | Linux x86-64 / WSL2 tested | **Pass** |
| `html_to_pdf` | P0 | Basic Chromium and WeasyPrint rendering | Empty HTML; Unicode paths; repeated/parallel rendering; missing Chromium; injected crash; preservation of prior output; temp cleanup | CPU; Chromium for default engine | No external network or credentials for local fixtures | External process and atomic output are primary risks | Linux x86-64 / WSL2 tested | **Pass** |
| `pdf_to_text` | P1 | Basic embedded-text extraction and MCP round trip | 60-page ordered extraction; image-only PDF; corrupt and encrypted PDF; missing and invalid paths; Unicode path | CPU only | None | Many-page ordering and actionable parse failures | Linux x86-64 / WSL2 tested | **Pass** |
| `svg_to_png` | P1 | New intrinsic-size, scaling, aspect-ratio, handler, and PNG-content coverage | Real CairoSVG output and pixel checks; safe bytes-only invocation; XXE and malformed XML; missing paths; invalid controls; 8192-side and 32-million-pixel preflight; atomic preservation and temp cleanup | CPU only | External file and network resources blocked; embedded `data:` URLs allowed | Bounded raster allocation and crash-safe publication | Linux x86-64 / WSL2 tested | **Pass** |

### 3.2 Browser Fetch

| Tool | Tier | Existing coverage | Added and executed coverage | Hardware | Network / credentials | Long, concurrency, and recovery relevance | Platform scope | Result |
|---|---:|---|---|---|---|---|---|---|
| `fetch_page` | P0 | Installer checks and standalone smoke behavior | Controllable local HTTP server: immediate, redirects, loop, slow/hang, 1.5 MB HTML, delayed JavaScript, 4xx/5xx, malformed page, reset, unreachable host, cookies, proxy, cancellation, output modes, automatic fallback; 100 sequential and concurrency 4/8 | CPU plus local Chrome/Chromium | No external network or credentials in deterministic tests | Browser/page/context cleanup, timeout, fallback, process and RSS stabilization | Linux x86-64 / WSL2 tested | **Pass with limit**: public-site behavior remains target-dependent |
| `fetch_page_with_engine` | P0 | Explicit engine smoke paths | Real nodriver and Playwright calls against the local endpoint matrix; invalid timing and proxy inputs; cleanup after navigation errors | CPU plus local Chrome/Chromium | No external network or credentials in deterministic tests | Explicit engine failure and child-process cleanup | Linux x86-64 / WSL2 tested | **Pass** |
| `screenshot` | P0 | Basic screenshot path | Real PNG creation; Unicode/output failures; simulated Chromium crash; same-directory atomic publication; invalid PNG and temp cleanup | CPU plus Playwright Chromium | No external network or credentials in deterministic tests | Prevents corrupt/partial destination after renderer failure | Linux x86-64 / WSL2 tested | **Pass** |
| `browser_status` | P2 | Dependency probes | Verified lightweight import/path reporting without browser launch | CPU only | None | No stress; must not launch a browser | Linux x86-64 / WSL2 tested | **Pass** |

### 3.3 ASR and speaker-aware transcription

| Tool | Tier | Existing coverage | Added and executed coverage | Hardware | Network / credentials | Long, concurrency, and recovery relevance | Platform scope | Result |
|---|---:|---|---|---|---|---|---|---|
| `transcribe_audio` | P0 | Language normalization, model resolution, basic endpoint behavior | Empty/corrupt audio, FFmpeg fallback, mono/stereo, 7,350 Hz, short, silence, clipped, Unicode/spaces, JSON/text/verbose JSON; default 479/480/481/961-second boundaries and long diagnostics; four serialized clients; responsive health; lifecycle/recovery; dedicated 0.6B `8gb` profile with a real 61-second/two-chunk request | NVIDIA CUDA; lifecycle also verified on CPU | No network after local model is present; no credential | Profile-bounded chunking, serialized GPU inference, event-loop responsiveness, backend recovery and temp cleanup | Linux x86-64 / WSL2 tested | **Pass with limit**: 0.6B `8gb` profile peaked at 4658 MiB; default 1.7B remains outside the 8 GB claim |
| `transcribe_diarized` | P0 | Merge, timestamps, missing token, invalid speaker count, surfaced diarization failure | Real 48-second two-speaker pipeline on CPU; 95 words, two attributed segments, monotonic valid boundaries, JSON/SRT/TXT; real empty-speech and unmet exact-count behavior; pre-ASR rejection; interrupted/write-failed rollback; `ASR_PROFILE=8gb` fail-closed regression | NVIDIA CUDA by default; real hardening run used CPU | `HF_TOKEN`; network only for uncached gated models | Multi-stage failure visibility, verified exact speaker count, atomic result set | Linux x86-64 / WSL2 tested | **Pass with limit**: 1.7B plus forced aligner is intentionally unavailable in the 8 GB profile |
| `transcribe_podcast` | P0 | Missing token, successful timeline, explicit speaker hint, visible diarization failure | Real CPU pyannote one-speaker and two-speaker timelines; empty/mismatched timeline failure contracts; full transcript/timeline invariants; regression proves ASR release failure prevents diarization startup | NVIDIA CUDA by default; real hardening run used CPU | `HF_TOKEN`; network only for uncached gated models | Separate transcript/timeline semantics, stage failure reporting, serialized GPU stages | Linux x86-64 / WSL2 tested | **Pass with limit**: the complete tool was not measured under the dedicated 8 GB monitor |
| `asr_status` | P2 | Online/offline health contract | Health responsiveness during active inference; CPU cold start, idle exit, restart and invalid-model checks | CPU query only | None | Must remain responsive and must not load a model itself | Linux x86-64 / WSL2 tested | **Pass** |

### 3.4 OCR

| Tool | Tier | Existing coverage | Added and executed coverage | Hardware | Network / credentials | Long, concurrency, and recovery relevance | Platform scope | Result |
|---|---:|---|---|---|---|---|---|---|
| `ocr_document` | P0 | Durable scheduler/store/API recovery and artifact validation | Real image and scanned PDF runs at 1/23/24/25/48/49/96 pages; Unicode/spaces, rotation, blank, mixed language, handwriting/formula, extreme aspect, corrupt PDF and unsupported type; structured errors and no orphan job | NVIDIA CUDA; real OCR peak was about 4,586 MiB whole-device usage | No network after local models; no credential | Synchronous wait over bounded durable queue, ordered artifacts, restart | Linux x86-64 / WSL2 tested | **Pass with limit**: aspect ratio 256:1 is rejected cleanly by the upstream model |
| `ocr_submit` | P0 | Queue capacity, scheduler ownership and job IDs | Ten concurrent submissions behind a 25-page lead job: seven accepted, three structured HTTP 429 responses; unique IDs and bounded queue | NVIDIA CUDA | No network after local models; no credential | Burst submission, bounded FIFO queue, durable manifest creation | Linux x86-64 / WSL2 tested | **Pass** |
| `ocr_wait` | P0 | Wait windows, recovery wake-up and artifact-only responses | Real force-kill during a 49-page job, restart after one completed chunk, reuse by inode/mtime/SHA, corrupt-artifact selective retry, successful completion | NVIDIA CUDA | No network after local models; no credential | Long wait, restart, completed-chunk reuse and corruption detection | Linux x86-64 / WSL2 tested | **Pass** |
| `ocr_status` | P2 | Queue/job status and offline contract | Concurrent queue observation and restart state; verified no model load from status call | CPU query only | None | Lightweight queue/job inspection | Linux x86-64 / WSL2 tested | **Pass** |

### 3.5 Vision Local

| Tool | Tier | Existing coverage | Added and executed coverage | Hardware | Network / credentials | Long, concurrency, and recovery relevance | Platform scope | Result |
|---|---:|---|---|---|---|---|---|---|
| `analyze_image` | P1 | Basic vision runtime and smoke fixture | PNG/JPEG/WEBP, RGBA, grayscale, 1 px, unusual aspect, Unicode, corrupt/missing/unsupported image, prompt length, `max_tokens` and `max_edge` boundaries, repeated default calls; two real bounded 4B `8gb` calls | NVIDIA CUDA, default 9B and bounded 4B profiles | No network after local GGUF/projector; no credential | Repeated single-image schema/invariant stability | Linux x86-64 / WSL2 tested | **Pass with limit**: `8gb` maximum was 5518 MiB; default 9B remains outside the 8 GB claim |
| `extract_text_from_image` | P1 | Vision smoke fixture | Real formula/text image plus dense, CJK, rotated, blank and invalid-image contract checks; schema rather than exact prose | NVIDIA CUDA, default 9B profile | No network after local model; no credential | Repeated request and structured error behavior | Linux x86-64 / WSL2 tested | **Pass with limit**: model prose/accuracy is fixture-dependent |
| `analyze_chart` | P1 | Chart smoke fixture | Normal/dense/tiny-label/non-chart inputs and long questions; real backend schema checks | NVIDIA CUDA, default 9B profile | No network after local model; no credential | Input resizing and output invariants | Linux x86-64 / WSL2 tested | **Pass with limit**: semantic accuracy is not an exact-output contract |
| `classify_eyewear` | P1 | Labeled portrait smoke set | Glasses/no glasses/rimless/sunglasses/reflection/occlusion/multiple-face/non-portrait fixtures and edge image forms; stable structured schema | NVIDIA CUDA, batch/default profile as configured | No network after local model; no credential | Repeated structured classification | Linux x86-64 / WSL2 tested | **Pass with limit**: classification accuracy is model-dependent |
| `verify_eyewear` | P1 | Misclassification verification path | Same difficult portrait/non-portrait matrix; structured retry/error invariants and real calls | NVIDIA CUDA | No network after local model; no credential | Verification after provisional classification | Linux x86-64 / WSL2 tested | **Pass with limit**: classification accuracy is model-dependent |
| `classify_eyewear_batch` | P0 | Batch artifacts and basic resume | Real 1/10/104-image runs; concurrency 1/4/8; mixed corrupt/unsupported files; failed-record retry; SIGINT/SIGTERM/SIGKILL resume; truncated-tail and malformed-middle JSONL; missing/malformed coordination JSON; existing output refusal; read-only/deleted output; atomic-write failure | NVIDIA CUDA, 4B batch profile | No network after local model; no credential | Highest-risk resumable batch, signals, append log, progress/manifest durability | Linux x86-64 / WSL2 tested | **Pass with limit**: four-slot batch profile is not the bounded `8gb` profile and has no 8 GB claim |
| `vision_status` | P2 | Runtime artifact and health reporting | Verified lightweight status behavior without starting a model; reports default, batch, and bounded `8gb` settings plus explicit test ceiling only for `8gb` | CPU query only | None | No stress; must not wake a backend | Linux x86-64 / WSL2 tested | **Pass** |
| `eyewear_batch_status` | P2 | Manifest/progress/summary reporting | Active real batch observation, stale PID and unrelated PID-reuse checks, missing/malformed coordination state | CPU query only | None | Discoverability of background work without model load | Linux x86-64 / WSL2 tested | **Pass** |

## 4. Supplementary Brave launcher coverage

The upstream Brave package's tools are not part of the 24 repository-owned
tools and its full suite was intentionally not duplicated. Repository-owned
launcher behavior passed five tests: missing Node.js, missing `npx`, missing
`BRAVE_API_KEY`, credential/proxy/argument propagation, and visible upstream
exit code/stderr. With configured credentials, real MCP startup discovered the
eight upstream tools without issuing a search request.

## 5. Defects found and fixed

| Severity | Area / affected tools | Reproduction and root cause | Fix and regression |
|---|---|---|---|
| High | `classify_eyewear_batch` | Interrupt while appending `results.jsonl`; a truncated final record made all resume attempts fail because every malformed line was fatal | Ignore only an unterminated malformed final line, retry that image, and continue rejecting malformed middle or newline-terminated records |
| High | `transcribe_audio`, `asr_status` | A synchronous GPU call ran directly on FastAPI's event loop; `/health` timed out during a two-minute inference | Offload inference to an AnyIO worker and serialize it with a process lock; real health response remained below two seconds during inference |
| High | `transcribe_audio` | The host proxy configuration did not bypass `127.0.0.1`; a two-hour request received an empty HTTP 503 after about 600 seconds while the healthy backend continued | Force direct transport for loopback MCP/backend URLs and preserve normal proxy behavior for non-loopback hosts; unit regression verifies routing |
| Medium | `ocr_document`, `ocr_submit` | Corrupt PDF staging escaped as a raw PyMuPDF exception/plain HTTP 500 and left a partial job; REST accepted `.txt` while MCP rejected it | Normalize staging exceptions, share one suffix allowlist, reject before directory creation, and remove partial job directories on every creation failure |
| Medium | `markdown_to_pdf`, `html_to_pdf` | Injected Chromium/WeasyPrint failure overwrote an existing destination with partial bytes | Render to a same-directory temporary PDF, validate it, atomically replace the destination, and always clean the temporary file |
| Medium | `screenshot` | Injected Chromium failure overwrote a prior PNG; the first temporary suffix also caused Playwright to reject the screenshot type | Use a same-directory `.png` temporary file, validate PNG signature/non-empty content, atomically replace, and clean on every exit path |
| Medium | `fetch_page_with_engine` | nodriver navigation could hang beyond the public timeout and browser cleanup was delayed | Bound the entire nodriver operation with `asyncio.timeout` and stop the browser in all failure/cancellation paths |
| Medium | `transcribe_diarized` / Pipeline CLI | A formatter failure escaped as a traceback after JSON had already overwritten the previous complete result | Stage the whole requested output set beside the destination, publish only after every formatter succeeds, and clean staging on errors or interruption |
| Medium | `transcribe_diarized`, `transcribe_podcast`, Pipeline CLI | Real short dialogue requested as exactly two speakers produced one pyannote label; wrappers continued into ASR or reported completion, and the CLI could publish the requested count rather than the detected count | Reject empty timelines and unmet exact counts before speaker-attributed ASR; report failed podcast diarization while preserving its independent transcript; always derive published metadata from verified labels |
| Medium | ASR backend lifecycle | `SIGKILL` during a request left a generically named upload file; restart succeeded but never reclaimed it | Use PID-scoped ASR temp directories, reclaim only dead ASR process directories at startup, and remove the current directory on graceful shutdown |
| Medium | ASR/Vision 8 GB profiles | The only available ASR/Vision defaults were 1.7B/9B reference settings, so selecting a service did not identify a configuration validated below the new ceiling | Add bounded 0.6B/4B profiles, reject relaxed memory maxima, expose the active profile, and add a monitored sequential real-GPU regression |
| Medium | `transcribe_podcast` | The REST ASR model remained resident when GPU diarization started, creating unintended heavyweight overlap | Stop and verify release of the REST backend before diarization; fail the diarization stage without starting it when release fails |
| Medium | `classify_eyewear_batch`, `eyewear_batch_status` | SIGINT waited for all queued executor work; any live reused PID was reported as the batch process | Cancel pending futures on `BaseException`; validate `/proc/<pid>/cmdline` against the batch script and exact output directory |
| Low | `classify_eyewear_batch` | Injected atomic rename failure preserved the valid JSON but left `*.tmp` | Add unconditional temporary-file cleanup while preserving the original exception |
| Low | Browser Fetch tools | `timeout=0` disabled Playwright's timeout, negative waits were accepted, and nodriver could not find a valid current Playwright Chromium revision | Validate timing inputs and resolve explicit/system/current cached browser executables with actionable errors |
| Low | ASR Pipeline CLI | Repeating `--format all --no-timestamps` left an older SRT beside the new fast-mode JSON/TXT | Remove the obsolete SRT only after the new output set is generated successfully |
| Low | ASR MCP errors | Backend HTTP details were reduced to a generic failure | Parse bounded JSON error detail and return an actionable MCP error without a traceback |
| Low | Combined test collection | Two new `test_reliability.py` modules collided when collected together | Make the Browser Fetch and Format Conversion test directories explicit packages |

## 6. Executed stress and recovery results

### 6.1 ASR

- The dedicated `8gb` REST profile used official Qwen3-ASR-0.6B revision
  `5eb144179a02acc5e5ba31e748d22b0cf3e303b0`, 60-second chunks, 1024 output
  tokens, inference batch size 1, and a 6144 MiB PyTorch allocator limit. A
  61-second FLAC forced two chunks in both runs, returned non-empty text in
  17.308-19.386 s, and reached a maximum 4658 MiB whole-device usage.
- Real chunk boundaries: 479 seconds (one chunk, 8.655 s), 480 (one,
  6.008 s), 481 (two, 3.268 s), and 961 (three, 7.565 s). Order and cleanup
  passed. These pre-cap GPU diagnostics observed 11,955-11,977 MiB
  whole-device usage and are not 8 GiB-compliant results.
- Sparse synthetic long audio: 30 minutes completed in 10.825 s across four
  chunks; two hours completed in 47.414 s across 15 chunks. This exercised
  chunk/resource mechanics, not continuous-speech throughput.
- Real continuous speech: 1,863.2 seconds completed in 330.306 s, four chunks,
  28,687 output characters. Repeated real speech totaling two hours completed
  all 15 backend chunks in 1,284.61 s. The original client path was cut by the
  loopback proxy at 596.94 s; the backend completed and cleaned up, and the
  proxy-bypass fix is covered by regression tests. It was not rerun because of
  the later 8 GiB cap.
- Four hot overlapping requests completed in 3.813 s and were serialized by
  design. Health remained responsive during active inference.
- CPU lifecycle: cold start, request during startup, five-second idle exit,
  force-kill during request, restart, stale-temp reclamation, recovery smoke,
  and invalid model path all passed. Peak test-process tree RSS was about
  5.1 GiB; GPU remained below 1 GiB.
- CPU speaker-aware run: one-speaker input produced one label; a 48-second
  two-voice fixture produced two labels and 17 diarization segments in
  21.271 s. The complete diarization + ASR + alignment + merge run finished in
  96.5 s, produced 95 timestamped words and two attributed output segments,
  and peaked at about 6.70 GiB RAM with 884 MiB whole-device GPU usage. A real
  short-dialogue request for exactly two speakers returned only one upstream
  label; CLI and MCP regressions now reject that mismatch before attribution.

### 6.2 OCR

- Real scanned-PDF timings: 1 page 9.223 s; 23 pages 19.522 s; 24 pages
  19.582 s; 25 pages 27.230 s; 48 pages 39.806 s; 49 pages 46.333 s; 96 pages
  81.161 s. Pages were covered exactly once and stayed ordered.
- PyTorch peak allocated/reserved memory was 1.83/1.95 GiB; observed
  whole-device usage was about 4,586 MiB, within the later 8 GiB limit.
- In a ten-client burst behind a running 25-page job, seven submissions were
  accepted and three received structured queue-full HTTP 429 responses. Every
  accepted job completed and IDs were unique.
- A real 49-page force-kill/restart reused the verified first chunk without
  changing inode, mtime, or SHA-256. Corrupting that artifact caused only that
  chunk to be regenerated. Unit fault injection additionally covered missing
  artifacts, simulated `ENOSPC`, manifest write failure, and read-only roots.

### 6.3 Vision

- The dedicated `8gb` interactive profile used the pinned Qwen3.5-4B
  UD-Q4_K_XL model/projector, context 2048, one slot, a 512 output-token cap,
  20 GPU layers, batch 256, and ubatch 128. The real 512-pixel/64-token request
  returned non-empty text in 5.766-6.954 s across two runs and reached a
  maximum 5518 MiB whole-device usage.
- The default 9B and four-slot 4B batch profiles were not executed under the
  8 GB monitor and retain no 8 GB compatibility claim.
- Sizes 1, 10, and 104 completed in 0.462 s, 4.933 s, and 32.228 s.
- Sixteen-image concurrency runs completed in 7.492 s at concurrency 1,
  5.250 s at 4, and 5.264 s at 8. Client peak RSS was about 184 MiB.
- SIGINT, SIGTERM, and SIGKILL interruption plus resume all passed after the
  executor fix. Completed records were retained, failed records retried, and
  no model process remained after exact-PID shutdown.
- Missing/malformed progress or manifest JSON is rebuilt from the append log;
  malformed middle JSONL remains a hard error. Read-only, deleted-output, and
  atomic-rename failures terminate visibly without publishing invalid JSON.

### 6.4 Browser Fetch

- One hundred sequential Playwright requests plus 16 requests at concurrency 4
  and 16 at concurrency 8 completed in 45.855 s total. The concurrent batches
  took 2.008 s and 1.419 s.
- Client RSS moved from 77.5 MiB to 153.9 MiB during the run and did not show an
  unbounded monotonic pattern. Peak measured client RSS was about 152 MiB.
- No Chrome/Chromium child remained after success, timeout, cancellation,
  redirect loop, connection reset, screenshot, or fallback cases.

### 6.5 Format Conversion

- The combined normal Format Conversion suite passed 27 tests with one opt-in
  stress case skipped. The post-fix large-table/very-long-line stress produced
  a valid PDF in 192.96 s.
- Ten repeated and four parallel WeasyPrint conversions completed without temp
  accumulation. Chromium and WeasyPrint fault injection preserved the previous
  destination. An instrumented large-input run peaked at about 339 MiB client
  RSS; no renderer process remained.
- Real SVG rasterization produced and decoded the expected PNG pixels and
  dimensions. Malformed XML, XXE input, invalid scaling, and oversized canvases
  failed before publication and preserved an existing destination.

### 6.6 Shared GPU

Correct harness sequencing released every backend before the next workload.
The new bounded-profile test also refuses startup when any GPU compute process
or port 8000, 8002, 8003, 8004, or 8005 is active, then runs ASR followed by
Vision and verifies memory release between them. `transcribe_podcast` now
follows the same rule between its ASR and diarization stages.

The planned deliberate overlap remains unexecuted because overlap intentionally
increases residency. `scripts/gpu_test_policy.py` rejects conflict mode unless
`MCP_TOOLS_GPU_CONFLICT_BUDGET_MIB` is explicitly greater than 8000 MiB. This
means conflict recovery remains unverified, not passed. Query the relevant
status tools and do not run ASR, OCR, or any Vision inference simultaneously on
a limited-VRAM machine.

## 7. Resource stability and known limits

- No repository MCP, ASR/OCR/Vision backend, or Chromium child from the tests
  remained after final cleanup.
- ASR upload/decode/chunk data, Vision progress temporaries, browser screenshot
  temporaries, format-conversion PDF temporaries, and OCR staging artifacts
  were checked after success and injected failure. Service-owned leftovers are
  either removed immediately or reclaimed on restart.
- ASR silence returned the model text `Yeah.` in one real case. Silence
  detection is not part of the current contract; callers needing voice
  activity detection must add it upstream.
- Pyannote may return fewer labels than an exact `num_speakers` request when
  audio is too short. Speaker-aware wrappers now expose this as a failure; the
  count parameter cannot manufacture speech evidence that is absent.
- OCR cleanly rejects an absolute aspect ratio of 256:1 because the upstream
  processor requires a ratio below 200.
- Forced alignment can emit zero-duration words. The contract permits
  `start == end`; ordering remains monotonic.
- Browser results on public sites still depend on the target, authorization,
  network, anti-bot behavior, and browser version. Deterministic reliability
  claims come from the local test server.
- Linux-specific Bash launchers, `/proc` PID validation, `fcntl` locks,
  fontconfig, Playwright libraries, NVIDIA tools, and `.venv/bin/python` paths
  are intentional within the supported Linux/WSL2 scope.
- Ubuntu 22.04, native Ubuntu 24.04, physical 8 GB/16+ GB GPUs, and non-NVIDIA
  devices were not available locally. The PR workflow remains CPU-only but now
  declares explicit `ubuntu-22.04` and `ubuntu-24.04` jobs; live platform claims
  still depend on those jobs completing.

## 8. Test groups and commands

The normal pull-request gate remains CPU-safe:

```bash
scripts/check.sh
git diff --check
```

Expensive or environment-sensitive groups are opt-in and are not part of
normal CI:

```bash
# Local browser integration and stress
MCP_TOOLS_BROWSER_INTEGRATION=1 \
  uv run --project environments/mcp-local python -m pytest -q \
  test/browser_fetch/test_reliability.py
MCP_TOOLS_BROWSER_INTEGRATION=1 MCP_TOOLS_BROWSER_STRESS=1 \
  uv run --project environments/mcp-local python -m pytest -q \
  test/browser_fetch/test_reliability.py

# Large format-conversion input
MCP_TOOLS_LONG_TESTS=1 \
  uv run --project environments/mcp-local python -m pytest -q \
  test/format_conversion/test_reliability.py

# Real CPU lifecycle and diarization (no CUDA allocation)
MCP_TOOLS_ASR_CPU_LIFECYCLE=1 \
  uv run --project environments/mcp-local-asr python -m pytest -q \
  test/asr/test_cpu_lifecycle_integration.py
MCP_TOOLS_DIARIZATION_CPU_INTEGRATION=1 \
  uv run --project environments/mcp-local-asr python -m pytest -q \
  test/asr_pipeline/test_cpu_diarization_integration.py

# Real GPU suites; run only with an explicit local VRAM budget and monitoring
MCP_TOOLS_OCR_GPU_INTEGRATION=1 \
  uv run --project environments/mcp-local-ocr python -m pytest -q \
  test/ocr/test_gpu_integration.py
MCP_TOOLS_VISION_BATCH_INTEGRATION=1 \
  uv run --project environments/mcp-local python -m pytest -q \
  test/vision_local/test_batch_integration.py

# Dedicated 8 GB profiles: one test, ASR then Vision, with a hard whole-device monitor
MCP_TOOLS_8GB_GPU_INTEGRATION=1 \
MCP_TOOLS_GPU_BUDGET_MIB=8000 \
ASR_8GB_MODEL_PATH=/path/to/Qwen3-ASR-0.6B \
  uv run --project environments/mcp-local python -m pytest -q -s \
  test/gpu/test_8gb_profiles_integration.py
```

The ASR GPU long-running switches remain available in
`test/asr/test_gpu_integration.py`, but the default 1.7B/480-second profile
exceeded the audit's 8 GB ceiling. Do not run those cases under an 8 GB budget;
use the dedicated test above. Any future deliberate conflict/recovery harness
must call `require_conflict_budget()` and is authorized only when
`MCP_TOOLS_GPU_CONFLICT_BUDGET_MIB` is explicitly greater than 8000.

## 9. Release recommendation

The repository is suitable for broader open-source adoption by Linux x86-64
and WSL2 users who accept the documented NVIDIA/CUDA and local-browser setup,
run one heavyweight GPU workload at a time, and choose profiles that fit their
hardware. Before the next release, keep the new fast regressions in the normal
gate and publish these hardening fixes. The next useful evidence is a run on a
physical 8 GB GPU plus successful remote results from both Ubuntu CPU jobs.
Deliberate shared-GPU conflict testing remains deferred unless the operator
explicitly grants a budget above 8000 MiB.
