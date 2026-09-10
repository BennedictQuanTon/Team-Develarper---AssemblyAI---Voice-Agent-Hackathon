# Waiter Real-Case Smoke Test — The Lantern Voice Waiter

Generated: 2026-09-10 18:44

## Part A — Turn-level accuracy (real Gemini tool loop, no audio)

Accuracy: **4/4**

| Scenario | Result |
|---|---|
| C1_recommend_order (recommend mild couple → "those two" → place) | PASS |
| C2_86_substitute (sold-out squid → NOT added + substitute offered) | PASS |
| C3_allergen_refuse (MSG allergy → refuse, check kitchen) | PASS |
| C4_barge_in_keep (interrupted turn → basket preserved) | PASS |

Verified behaviors:
- Mention stack resolves "those two" → the exact 2 recommended SKUs (Pomelo Salad + Lemongrass Chicken), basket total $15.50 correct.
- Sold-out item is never added; agent offers a substitute instead.
- Allergen not deterministically on label → agent defers, does not fabricate.
- Basket survives an interrupted/barge-in turn (snapshot+restore round-trip).

Scenario latencies (tool loop, real Gemini): p50 ~1.7–6.5s per scenario (includes network round-trips across multiple turns; not realtime audio path).

## Part B — Realtime E2E over WS /ws/realtime

### Metrics

```json
{
  "voice_to_voice_ttfb_ms": { "p50": 4147.31, "p95": 5693.5, "mean": 4615.85, "count": 3 },
  "e2e_turn_ms":         { "p50": 5574.03, "p95": 6459.54, "mean": 5853.55, "count": 3 }
}
```

### Barge-in

```json
{ "success": true, "latency_ms": 536.33 }
```

### Turn detail

| Turn | TTFB (ms) | E2E (ms) | Reply |
|------|----------|----------|-------|
| rec (recommend) | 4006.75 | 5527.09 | "I recommend our fresh pomelo salad with shrimp and the savory lemongrass chicken..." |
| those-two | 4147.31 | 5574.03 | "I've added the pomelo salad and lemongrass chicken..." |
| place (order) | 5693.5 | 6459.54 | "Your order for the pomelo salad and lemongrass chicken has been sent to the kitchen!" |

Basket resolved correctly through real WS audio → STT → Gemini function-calling → TTS.

## Comparison vs legacy Da Nang realtime benchmark (reports/realtime_eval.json)

| Metric | Legacy Da Nang (RAG, cache warm) | Waiter (function-calling) |
|---|---|---|
| TTFB p50 | 2396 ms | 4147 ms (waiter) |
| E2E p50 | 3740 ms | 5574 ms (waiter) |
| Barge-in latency | 627 ms | 536 ms |

### Analysis — is realtime "stable enough"?

Functionality is fully stable: 4/4 accuracy, all 3 realtime turns succeed end-to-end, barge-in works (536 ms, faster than legacy). The ordering flow (recommend → resolve "those two" → place) is correct across both the logic layer and the real audio path.

Latency on the waiter path is **higher than the legacy RAG path** (TTFB +~1.75s, E2E +~1.8s). Root cause is architectural, not a bug: the waiter uses Gemini **function calling with a tool loop** (some turns require 2 model round-trips before the final spoken reply), whereas legacy Da Nang answers are a single RAG-grounded generation from a warm spoken-response cache.

### Latency budget check

Target from plan: E2E miss < 1000 ms, TTFB < 800 ms. Waiter measured p50 TTFB 4.1 s / E2E 5.6 s.

Verdict: **waiter path does NOT yet meet the sub-second latency budget** for the tool-loop turns. The extra latency comes from (1) Gemini free-tier cold responses, (2) multiple tool-call round-trips (recommend itself may call both search_menu + readback), and (3) no warm cache on the waiter path.

Recommended mitigations for P5/hackathon (next):
1. Cache stable tool-lookups / repeated recommendation answers on the waiter path (mirror spoken_cache for common "what's good?").
2. Stream the LLM reply to TTS in parallel with tool execution; emit filler audio while the tool loop runs so perceived TTFB drops.
3. Consider warm-up of Gemini connection / pin model for lower first-token latency.
