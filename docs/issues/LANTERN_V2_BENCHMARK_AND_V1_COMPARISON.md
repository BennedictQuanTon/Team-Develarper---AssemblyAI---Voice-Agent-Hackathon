# Benchmark Lantern V2 and compare latency, correctness, and operational behavior with V1

Suggested labels: `benchmark`, `performance`, `architecture`, `multilingual`, `needs-validation`

## Summary

Lantern V2 passed its deterministic architecture checks, live Qwen/Kokoro cases, and application WebSocket latency target on the current development machine.

The latest measured V2 path reached the first playable generated audio in **2,648.98 ms** after a final transcript was submitted. The validated order workflow update arrived in **1,810.12 ms**, and the turn completed in **2,658.82 ms**. This is real Qwen processing, deterministic validation, SQLite persistence, WebSocket delivery, and Kokoro audio—not a prerecorded filler response.

The often-quoted V1 result of approximately **800 ms** was a perceived TTFB created by a prerecorded acknowledgement. The same V1 local-Qwen report measured **6,553.48 ms average E2E** and **4,886.59 ms best E2E** for the actual workflow. An older live Gemini/Cartesia run was significantly slower, with individual turn E2E results ranging from **19,232.23 ms to 86,525.06 ms**.

V2 therefore demonstrates a materially faster post-transcript response and stronger correctness guarantees. However, the current automated V2 result does not include live AssemblyAI audio transcription time, so a controlled microphone-to-audio comparison remains required before claiming full speech-to-speech superiority.

## Evidence and provenance

### Lantern V2

- Benchmark runner: [`eval/benchmarks/restaurant/new_architecture.py`](../../eval/benchmarks/restaurant/new_architecture.py)
- Dataset: [`eval/datasets/restaurant/new_architecture_strengths.v1.json`](../../eval/datasets/restaurant/new_architecture_strengths.v1.json)
- Latest passing summary: [`reports/restaurant/new-architecture/2026-09-19T044721Z/summary.md`](../../reports/restaurant/new-architecture/2026-09-19T044721Z/summary.md)
- Latest full result: [`reports/restaurant/new-architecture/2026-09-19T044721Z/results.json`](../../reports/restaurant/new-architecture/2026-09-19T044721Z/results.json)
- Run manifest: [`reports/restaurant/new-architecture/2026-09-19T044721Z/manifest.json`](../../reports/restaurant/new-architecture/2026-09-19T044721Z/manifest.json)

### Lantern V1 local Qwen baseline

- Historical summary: [`reports/restaurant/llm-provider-comparison/historical-pre-2026-09-13/summary.md`](../../reports/restaurant/llm-provider-comparison/historical-pre-2026-09-13/summary.md)
- Historical result: [`reports/restaurant/llm-provider-comparison/historical-pre-2026-09-13/results.json`](../../reports/restaurant/llm-provider-comparison/historical-pre-2026-09-13/results.json)

### Lantern V1 live Gemini/Cartesia baseline

- Historical summary: [`reports/restaurant/realtime-waiter/historical-pre-2026-09-13/summary.md`](../../reports/restaurant/realtime-waiter/historical-pre-2026-09-13/summary.md)
- Historical turns: [`reports/restaurant/realtime-waiter/historical-pre-2026-09-13/turns.json`](../../reports/restaurant/realtime-waiter/historical-pre-2026-09-13/turns.json)

Historical reports are treated as immutable evidence. They were not rewritten for this comparison.

## Test environment

- Operating system: Windows
- Python: 3.11.15
- LLM runtime: Ollama
- V2 LLM: `qwen3:4b`, thinking disabled
- V2 TTS: `hexgrad/Kokoro-82M`
- PyTorch in the active environment: `2.14.0+cpu`
- Kokoro benchmark device: CPU
- ASR configuration: AssemblyAI `universal-3-5-pro`
- Persistence: SQLite
- Application probe: FastAPI WebSocket at `/ws/realtime?table_id=T4`
- Tested live TTS languages in the passing run: English and Spanish

The AssemblyAI project key, Python package, model configuration, and streaming adapter were present. The automated benchmark did not submit a real speech recording because there is no committed 16 kHz PCM fixture.

## Measurement boundaries

The following boundaries must remain visible when interpreting the numbers:

1. **V2 application first audio** starts when a final transcript is submitted to the WebSocket and stops when the browser-facing application receives the first playable PCM chunk.
2. The V2 application probe includes Qwen, deterministic validation, SQLite persistence, response rendering, Kokoro, and WebSocket delivery.
3. It excludes the duration of the guest's speech and AssemblyAI endpointing/transcription.
4. **V1 perceived TTFB** includes a prerecorded filler response and is not the generated answer latency.
5. **V1 E2E** comes from the historical V1 benchmark methodology and is not yet a same-audio, same-hardware, same-boundary comparison with V2.

For that reason, comparisons between V1 E2E and V2 post-transcript first audio are directional, not a final scientific apples-to-apples result.

## V2 benchmark results

### Application latency

| Measurement | Result | Target | Status |
|---|---:|---:|---|
| Session-ready delivery | 0.15 ms | Informational | PASS |
| Validated workflow update | 1,810.12 ms | < 4,000 ms | PASS |
| First playable generated audio | 2,648.98 ms | < 4,000 ms | PASS |
| Complete streamed turn | 2,658.82 ms | < 4,000 ms | PASS |
| Server-reported pipeline | 1,579.30 ms | Informational | PASS |
| Server-reported voice TTFB | 2,418.38 ms | < 4,000 ms | PASS |
| Audio chunks delivered | 32 | > 0 | PASS |

The difference between the server-reported and probe-observed timings includes WebSocket delivery and benchmark-client scheduling.

### Qwen intent extraction

| Case | Latency | Expected behavior | Status |
|---|---:|---|---|
| English valid modifier | 1,285.51 ms | Create `MAIN_SEABASS`, quantity 1, `no chili` | PASS |
| Japanese original script | 1,227.21 ms | Preserve `ja`; create the canonical seabass order | PASS |
| Invalid SKU | 341.88 ms | Clarify; create no item | PASS |
| Invalid modifier | 1,381.89 ms | Clarify with allowed modifier options | PASS |

Average across these four live Qwen cases: **1,059.12 ms**.

### Kokoro synthesis

| Language | Warmup | First PCM chunk | Total measured synthesis | Device | Status |
|---|---:|---:|---:|---|---|
| English | 6,891.60 ms | 857.40 ms | 859.04 ms | CPU | PASS |
| Spanish | 768.61 ms | 940.65 ms | 942.07 ms | CPU | PASS |

The English warmup includes initial model/pipeline loading. Warmup occurs during application startup and is not charged to each conversational turn.

### Deterministic architecture and safety checks

All of the following passed:

- Canonical intent validation: 4/4 cases.
- Unknown SKU rejection.
- Unsupported modifier rejection.
- Allergen conflict detection for shellfish.
- Original Japanese transcript preservation.
- Stale kitchen revision rejection.
- Monotonic revisions from revision 1 to revision 2.
- Immutable historical revisions.
- SQLite restart recovery.
- Eight configured Kokoro language routes.
- Caption-only fallback for an unsupported language.
- Clarification results do not persist empty order revisions.

## V1 comparison

### V1 local Qwen benchmark

| Measurement | V1 result |
|---|---:|
| Model | `qwen2.5:3b` |
| Average LLM inference | 2,022.47 ms |
| Perceived filler TTFB | 800.00 ms |
| Actual answer TTFB | 6,552.94 ms |
| Best measured E2E turn | 4,886.59 ms |
| Average measured E2E turn | 6,553.48 ms |

The 800 ms value came from simulated ASR followed by a local prerecorded filler. It represented responsiveness feedback, not the completion of the LLM and generated voice path.

V2's four-case Qwen average of **1,059.12 ms** is approximately **47.6% lower** than the V1 local-Qwen average of **2,022.47 ms**. Dataset and prompt differences mean this is useful directional evidence, not a controlled model-only benchmark.

### V1 live Gemini/Cartesia benchmark

| Turn | V1 TTFB | V1 E2E |
|---|---:|---:|
| Recommendation | 17,860.78 ms | 19,232.23 ms |
| “Those two” order | 25,863.29 ms | 27,120.31 ms |
| Squid request | 28,230.36 ms | 28,684.52 ms |
| Seabass substitution | 46,511.13 ms | 47,639.66 ms |
| Morning glory addition | 85,967.16 ms | 86,525.06 ms |
| Place order | 53,087.39 ms | 54,130.08 ms |

That historical run also failed its total check: the final total was `$46.00` while the expected total was `$36.50`. V2 moves mutation authority out of the LLM and validates canonical SKUs, modifiers, allergens, revisions, and kitchen decisions before state changes.

## Why V2 is faster

The current improvements are structural rather than benchmark-only shortcuts:

- Qwen stays loaded in Ollama with `keep_alive` instead of cold-loading each turn.
- Thinking is disabled, temperature is zero, context is compact, and output tokens are bounded.
- Qwen returns a structured schema and receives deterministic entity hints.
- One Kokoro model is loaded and warmed once.
- Kokoro audio is emitted as 100 ms PCM16 chunks instead of waiting for full-response packaging.
- AssemblyAI uses one long-lived realtime connection per table session.
- AssemblyAI connection setup happens asynchronously and does not block `session_ready`.
- The browser sends mono PCM16 at 16 kHz and plays PCM using the sample rate carried in each audio message.
- Barge-in cancels queued synthesis/playback.
- The response task no longer cancels itself at its first await.
- Clarification cannot create an empty order revision.

V2 also uses a short deterministic, localized acknowledgement after a validated revision is persisted. Unlike V1's prerecorded filler, this acknowledgement is synthesized by the configured Kokoro voice and its measured PCM TTFB is reported as the response latency.

## Supplemental live observations

The configured AssemblyAI realtime session successfully reached `provider_ready`. One observed connection setup took approximately **4.25 seconds**, but this is a one-time table-session setup rather than per-turn latency.

This observation proves that the current key and connection path can establish a realtime session. It does **not** prove microphone-to-transcript accuracy or latency because real speech audio was not submitted as part of the automated report.

## Remaining gaps and blockers

- [ ] Add a versioned 16 kHz mono PCM fixture with an expected transcript and language.
- [ ] Measure AssemblyAI audio-end to final-transcript latency for English, Spanish, Japanese, and code switching.
- [ ] Run a true microphone-to-first-audio benchmark through the browser.
- [ ] Run V1 and V2 against the same audio, transcript, menu state, hardware, and timing boundaries.
- [ ] Install CUDA-enabled PyTorch or keep `KOKORO_DEVICE=cpu`. The current environment is CPU-only.
- [ ] Install `pyopenjtalk` for Japanese Kokoro on Windows using Visual Studio C++ Build Tools/NMake, or run Japanese voice validation under WSL/Linux.
- [ ] Synthesize and listen to every configured Kokoro voice; routing tests alone do not prove voice quality.
- [ ] Exercise a full kitchen exception flow live: sold-out item, substitute, guest acceptance, revision N+1, kitchen acceptance, localized confirmation.
- [ ] Record cold-start and warm-turn metrics separately for every provider.
- [ ] Add p50, p95, and p99 results over repeated runs rather than relying on one passing application probe.

## Reproduction

### 1. Start the warmed backend

```powershell
.\.venv\Scripts\Activate.ps1
$env:KOKORO_DEVICE = "cpu"
uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

### 2. Run the complete benchmark in another terminal

```powershell
.\.venv\Scripts\Activate.ps1
$env:KOKORO_DEVICE = "cpu"
python -m eval.benchmarks.restaurant.new_architecture `
  --live `
  --languages en,es `
  --server-url http://127.0.0.1:8000
```

Every invocation writes a new immutable directory:

```text
reports/restaurant/new-architecture/<UTC-run-id>/
├── manifest.json
├── results.json
└── summary.md
```

### 3. Run the browser microphone path

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173/?table_id=T4`, select **Start voice service**, allow microphone access, wait for **Speak your order**, and say:

```text
One grilled seabass, no chili.
```

Expected behavior:

1. Interim and final captions appear.
2. The intent resolves to `MAIN_SEABASS` with `no chili`.
3. Revision 1 is persisted as `pending_kitchen`.
4. A localized acknowledgement is synthesized.
5. The UI reports pipeline and first-audio latency.
6. Speaking during playback stops the queued audio.

## Validation completed for this issue

- [x] Python compilation passed.
- [x] Seven unit/provider contract tests passed.
- [x] Frontend production build passed.
- [x] `git diff --check` passed.
- [x] Live Qwen cases passed 4/4.
- [x] Live English/Spanish Kokoro cases passed 2/2.
- [x] Application WebSocket first-audio target passed.
- [x] Allergen and revision-safety probes passed.
- [ ] Real AssemblyAI speech fixture executed.
- [ ] Japanese Kokoro synthesis executed on this Windows environment.
- [ ] Full browser microphone-to-audio timing recorded.

## Acceptance criteria for closing this issue

- [ ] V1 and V2 run against a common, versioned speech dataset.
- [ ] Both architectures report the same latency boundaries: speech end, final transcript, validated state, first generated audio, and turn completion.
- [ ] V2 warm p95 microphone-to-first-audio is below 4,000 ms for the selected demo languages.
- [ ] Intent, SKU, modifier, allergy, revision, and final-order accuracy meet the agreed threshold.
- [ ] Japanese, Spanish, English, and one code-switched flow have recorded evidence.
- [ ] Cold-start costs are reported separately from steady-state turns.
- [ ] Every comparison report is stored in a new immutable run directory.
