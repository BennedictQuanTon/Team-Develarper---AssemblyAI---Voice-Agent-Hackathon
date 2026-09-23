# Waiter Turn Latency — Optimization Update

> **Author**: Asterios (Windows + uv) · **Date**: 2026-09-16
> **Scope**: latency only. Free-tier Gemini quota (15 RPM) unchanged.
> **Raw data**: [`results.json`](results.json) · [`summary.md`](summary.md)
>
> This is a new file on purpose. The existing reports in `reports/` are QuanTon's measurements from
> 2026-09-11/12 and are left untouched so this branch's diff does not overwrite them.

---

## 1. Result

`uv run python -m legacy.lantern_v1.eval.regression_vs_baseline` — 6-turn ordering conversation, real AssemblyAI + Gemini + Cartesia,
compared against the archived baseline in `reports/restaurant/realtime-waiter/historical-pre-2026-09-13/turns.json`:

| Metric | Baseline (2026-09-12) | Now | Change |
| :--- | ---: | ---: | ---: |
| Avg time to first audio (TTFB) | 42,920 ms | **5,831 ms** | **−86.4%** |
| Avg end-to-end per turn | 43,889 ms | **7,930 ms** | **−81.9%** |
| Whole conversation (wall clock) | 276.3 s | **74.2 s** | **−73.1%** |
| Gemini calls for the conversation | 16 | **10** | −38% |
| Accuracy score | — | **100.0%** | basket, total, tools, speech all pass |
| Final total | $46.00 (expected $36.50) — wrong | **$36.50** — correct | fixed |
| Rate-limit 429s / timeouts | — | **0 / 0** | STABLE at 8.09 RPM (cap 15) |

Per-turn, from the separate `eval.waiter_smoke --scenario` run:

| Turn | Baseline TTFB | Now | Speedup |
| :--- | ---: | ---: | ---: |
| recommend | 17,861 ms | 4,993 ms | 3.6x |
| those-two | 25,863 ms | 6,594 ms | 3.9x |
| 86-squid | 28,230 ms | 4,951 ms | 5.7x |
| sub-seabass | 46,511 ms | 5,518 ms | 8.4x |
| add-morning-glory | 85,967 ms | 4,929 ms | **17.4x** |
| place | 53,087 ms | 4,769 ms | 11.1x |
| **median** | **37,371 ms** | **4,972 ms** | **7.5x** |
| **worst case** | **85,967 ms** | **6,594 ms** | **13.0x** |

**Accuracy improved as well as speed.** The baseline scored `total_ok=False` because it was silently
adding the sold-out Crispy Squid to the basket. The agent now refuses it and offers a substitute, and the
final total matches the expected $36.50 exactly.

---

## 2. Root cause: the rate limiter, not the model

`backend/app/pipeline/llm_live.py` defined a class called `AsyncTokenBucket` that held no tokens and had
no capacity. It was a **fixed 4.0 s spacer** (`interval = 60 / 15`), and `waiter_agent.py` acquired it once
per **tool-calling round**, not once per turn:

```python
self.interval = self.window / self.rate          # 60 / 15 = 4.0 s
if now < self._next_at:
    await asyncio.sleep(self._next_at - now)     # mandatory 4.0 s gap, every round
self._next_at = max(now, self._next_at) + self.interval   # idle never accrues credit
```

A 3-round turn therefore slept a guaranteed **8.0 s** before any network time, and a 5-round turn 16.0 s.
The previous run's own telemetry shows the cost bought nothing: **3.474 requests/min against a cap of 15,
`under_cap: true`** — it was throttling at under a quarter of the available quota.

A provider RPM cap is a **count inside a rolling window**, not a minimum gap between calls. The limiter is
now a sliding window: at most `GEMINI_RPM` (15) per rolling minute and `GEMINI_BURST` (6) per 10 s. A
turn's whole tool loop fires back to back and the window refills while the guest talks. Measured
`bucket_wait_ms` is **0.0 on every live turn**, and the benchmark reports 0 x 429 and 0 timeouts.

---

## 3. Changes

| # | Change | Files |
| :-- | :--- | :--- |
| 1 | Sliding-window rate limiter plus `GEMINI_BURST`; bounded 429 retry (3 attempts, 2 s/4 s backoff) replacing an unbounded `while True` with a flat 5 s sleep | `pipeline/llm_live.py`, `pipeline/waiter_agent.py`, `app/config.py` |
| 2 | `order_items` compound tool — folds add + modifier + place into one call. Every mutating result now carries `order_lines` / `total`, and `seat_party` carries the floor, so `readback` and `get_floor` never need a round of their own. `max_output_tokens` 90 to 256 so a nested call cannot truncate | `domain/waiter.py`, `pipeline/waiter_agent.py` |
| 3 | `prefetch_for_case` — runs the read-only lookups the filler's intent case implies and hands the results to the model, saving it a round trip. Costs no API call and no quota; the results are in-memory dict lookups | `pipeline/waiter_agent.py`, `pipeline/realtime_session.py` |
| 4 | Reuse one `genai.Client` and one tool spec per agent instead of rebuilding both every turn (a TLS handshake per turn) | `pipeline/waiter_agent.py` |
| 5 | Per-turn metrics: `llm_rounds`, `llm_total_ms`, `bucket_wait_ms`, `tool_ms`, `prefetch_used`, plus `filler_case` and `prefetched` | `metrics/spans.py`, `pipeline/realtime_session.py` |
| 6 | **Blocker fix** — `AssemblyAIRealtimeStream` now sets `connect_timeout` (see section 5) | `pipeline/asr_stream.py` |
| 7 | `eval/benchmark_today.py` repaired: it called `CartesiaTTSClient.synthesize_stream`, which does not exist, and failed on every turn. Tool expectations updated for the new tool surface (see section 6) | `eval/benchmark_today.py` |

All 12 tools remain registered. Nothing was removed, so there is no tool the model can no longer reach —
`order_items` is simply the fast path.

---

## 4. Three defects found by running it live

Offline tests passed all of these. Only real API runs exposed them.

1. **Prefetch framing made the agent over-order.** Labelling prefetched results "authoritative", combined
   with `search_menu` matching on *any single word*, meant "stir-fried morning glory on the side" pulled in
   Stir-fried Egg Noodles and Beef Pho — and the agent ordered all three. *Fixed:* reference-only framing
   that explicitly says these are candidates and not an order; result limit 5 to 3.
2. **Prefetch made it quote the wrong price.** Prefetching `readback` put the order total in context twice,
   once from the prefetch and once from the `place_order` result. The model summed the two copies and said
   **"total is thirty-one dollars"** on a $15.50 order. *Fixed:* dropped the `case_order_process` prefetch
   entirely — those turns always call a mutating tool that already returns the order state, so it was
   saving no round trip.
3. **The compound tool duplicated lines.** The model restates the whole intended order rather than the
   delta: "make it a grilled seabass instead" produced
   `order_items(items=[Lemongrass Chicken, Grilled Seabass])`, re-adding a dish already in the basket.
   *Fixed in the toolkit rather than the prompt* — `order_items` is idempotent: a named dish already present
   with the same modifiers and no explicit `qty` is treated as a restatement, not a second helping. An
   explicit `qty` still orders more, and different modifiers still make a separate line.

Rules 1 and 2 are written into `CLAUDE.md` so they are not re-introduced.

---

## 5. Environment: realtime voice could not connect from Windows

Every realtime session failed with an empty `Connection failed:` and no detail. Cause: the `assemblyai`
SDK defaults **`connect_timeout` to 1.0 s**, while the handshake measured **1.4–1.9 s** from this machine
(VPN in use). `asr_stream.CONNECT_TIMEOUT_S = 10.0` fixes it. The timeout is paid once per session, so a
generous value costs nothing, and it makes the client resilient on any slow or tunnelled link rather than
only on a fast one.

Related, and **not** a regression: barge-in measures 759–792 ms here versus 536 ms in the macOS baseline,
and per-turn latency varies by roughly ±20% between runs. Both are consistent with VPN round-trip
overhead. Reverting the endpointing change did not move the barge-in number, confirming it is
environmental rather than caused by this work.

One change was **reverted** after testing: `min_end_of_turn_silence_when_confident` looks like the obvious
way to shorten the endpoint wait, but AssemblyAI ignores it whenever `min_turn_silence` is set and only
logs a deprecation warning. The silence watchdog is back at its original 0.9 s.

---

## 6. A note on the benchmark's tool expectations

`eval/benchmark_today.py` asserts on **tool names**, and those lists predate `order_items`. Two were stale
and were updated; the outcome assertions — basket contents, final total, clean speech — are untouched and
are what gates the benchmark.

- Turns 4 and 5 expected `search_menu` + `add_item`; `order_items` does both in one call.
- Turn 3 (`86-squid`) expected `add_item` — that is, it expected the agent to **add a sold-out dish**. That
  is what the old agent did, and it is why the baseline's final total was wrong. The allowed set is now a
  lookup, and "no tool at all" is accepted because `prefetch_for_case` may have satisfied the availability
  lookup before the model ran: the answer is still grounded in the deterministic store, but the lookup
  never appears as a tool call on the wire, so this benchmark cannot see it.

---

## 7. Not done

- **Streaming the reply into TTS.** Worth roughly 300–800 ms of decode, but it restructures
  `_execute_waiter_turn` around barge-in, the audio commit point and rollback simultaneously — the smallest
  win in the plan and the highest risk. `stream_utterance` already accepts an async text generator, so the
  TTS half is ready.
- **Speculative Gemini calls on interim transcripts** (LiveKit's "preemptive generation"). Costs about 2
  requests per turn, which on a 15 RPM cap is roughly 3–4 turns per minute. Worth revisiting on a paid
  quota. Note that running the *prefetch* on interims is separately not worth doing: it measures
  0.002–0.016 ms, so warming it earlier saves microseconds out of a ~5,000 ms turn. The interim idea only
  pays off for something expensive, and the only expensive thing is the Gemini call itself.
- **Conversation memory.** `WaiterAgent.respond` still rebuilds the request from the current utterance
  alone — Gemini sees no prior turn. Continuity survives only via `WaiterSession.mentioned`, a 6-entry SKU
  stack. This is the underlying cause of defect 3 above: the agent had to guess what "instead" referred to.
- **AssemblyAI rubric features.** The installed SDK supports `speaker_labels` (diarization on the live
  stream, not async-only), `prompt` / `agent_context`, and `set_params` for mid-stream keyterm updates.
  Keyterms are currently set once at connect and never updated. The domain matrix weights diarization,
  keyterm prompting and context carryover — three unclaimed scoring criteria.

---

## 8. How to verify

```bash
cd frontend && npm ci && npm run build && cd ..        # only type-check in the repo
uv run python -m eval.eval_context_fillers             # offline, 40/40
uv run python scripts/dev.py start
uv run python -m legacy.lantern_v1.eval.regression_vs_baseline  # 6 turns vs baseline
uv run python -m eval.waiter_smoke --realtime          # voice E2E + barge-in
uv run python -m eval.waiter_smoke --scenario          # 6-turn native flow
uv run python scripts/dev.py stop
```

Note: bare `uv run python -m eval.waiter_smoke` runs **nothing** and exits FAIL — Part A only runs under
`--all`. Per-turn `llm_rounds` and `bucket_wait_ms` land in `reports/turns.jsonl` (gitignored).
