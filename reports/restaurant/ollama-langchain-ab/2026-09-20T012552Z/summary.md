# Local Ollama waiter — same-machine A/B, 2026-09-20

**Headline: on `qwen2.5:3b` neither the current agent nor the rebuilt one can complete the 6-turn order.
All 12 order runs failed. Latency is not the problem any more; the model is.**

This measures the archived V1 LLM path only (`legacy/lantern_v1/eval/benchmark_ollama_waiter.py`): it calls `OllamaWaiterAgent.respond()`
in-process, with no speech recognition, no speech synthesis, no server and no API keys, so the numbers
isolate what differs between the branches. Both sides ran on this machine, back to back, against the same
loaded model.

## Environment

| | |
| --- | --- |
| GPU | NVIDIA RTX 5060 Laptop, 8,151 MiB, driver 616.92, CUDA runner `cuda_v13`, compute 12.0 |
| Ollama | 0.34.1 (server reported 0.34.2), models on `D:\Ollama\models` |
| Models | `qwen2.5:3b` (digest `357c53fb…`, 2.01 GB in VRAM), `qwen3:4b` (2.96 GB in VRAM) |
| Context | 4096 for both sides — Ollama's VRAM-based default equals the value our branch sends, so switching sides never reloaded the model |
| Packages | langchain-core 1.6.3, langchain-ollama 1.1.0, ollama 0.6.2, httpx 0.28.1, Python 3.12.13 |
| Branches | A = `origin/main` @ `6482fbf` · B = `perf/local-ollama-langchain` @ `969d828` |
| Load | one model at a time, each verified `100% GPU` (`size_vram == size`) before timing; another GPU application was running throughout |

Reps: 3 per variant. Warm (the model stayed loaded); cold-load cost measured separately at 29.4 s for
`qwen2.5:3b` and 16.5 s for `qwen3:4b` on first load from disk.

## Results

| Variant | 2-turn median | 6-turn median | 6-turn p90 | Order accuracy | Rounds/turn | Template replies |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A — main, `qwen2.5:3b` | 1,715 ms | 4,806 ms | 5,667 ms | **0/3** | 1.67 | — |
| B — ours, templates off, `qwen2.5:3b` | 2,095 ms | **4,343 ms** | 5,363 ms | **0/3** | 1.83 | 0 |
| B — ours, templates on, `qwen2.5:3b` | 2,378 ms | 5,682 ms | 5,952 ms | **0/3** | 1.61 | 5 |
| B — ours, templates off, `qwen3:4b` | 7,179 ms | 21,766 ms | 21,949 ms | **0/3** | 1.00 | 0 |

With n=3 and a different (wrong) basket every run, the ±10% spread between the `qwen2.5:3b` rows is not a
result. Nothing here supports a latency claim in either direction.

## Why every run failed

The gates are: exact dishes, total $36.50, the sold-out dish refused, the order placed, no JSON spoken.

| Variant | Failing gates (of 3 runs) | Baskets produced |
| --- | --- | --- |
| A main | dishes 3, total 3, placed 1 | $34.00, $50.00 (seabass twice), $41.00 |
| B tpl off | dishes 3, total 3, **placed 3** | $25.00, $34.00, $39.00 |
| B tpl on | dishes 3, total 3, **placed 3** | $31.50, $15.50, $18.00 |
| B qwen3:4b | dishes 3, total 3, placed 3, json 1 | empty, empty, empty |

**`qwen2.5:3b` does not follow the ordering flow.** It recommends dishes that aren't the ones it then adds,
adds the same dish twice, drops the side, and answers turns with no tool call at all (9 of 18 turns on main,
3 of 18 on ours). One example from main, turn 3: it said *"Got it, adding crispy squid to your order"* while
calling **no tool** — the dish was never added, and the sold-out check never ran. The model narrates actions
it has not taken.

**The compound tool is almost never chosen.** `order_items` was picked **once in 18 turns**. The whole
round-trip argument behind it depends on the model selecting it, so on this model the saving does not exist.
Observed tool choices instead: `add_item`, `add_items_from_mention`, `remove_item`, `readback`.

**Placement depends on the turn, and neither branch is reliable.** In the 6-turn scenario ours never placed
the order (0/3) and main placed it in 2/3. In the 2-turn scenario the result is the opposite: **ours places
it 3/3 and main never does (0/3)**, which matches main's own committed report, where turn 2 calls
`['add_item', 'readback']` and no `place_order`.

The difference is context, not the branch. The final 6-turn utterance, "That's all, please place the order",
arrives with **no conversation history** (issue #16), so the model has to call `readback` to discover the
basket and then stops there. In the 2-turn scenario the same utterance also names the dish, so the model
goes straight to `order_items`/`place_order`. Our rule "never call readback separately" removes the round
trip but gives the model nothing to do after the readback it makes anyway. Conversation memory, not prompt
wording, is the fix.

**`qwen3:4b` is unusable here, in both modes.** With thinking disabled it writes its reasoning into the reply
("Okay, let's see. The user is asking…"), hits the token cap every turn and never calls a tool — 0 dishes in
all 3 runs. With thinking enabled the reply is clean and correct ("For a mild couple, I'd recommend the Pomelo
Salad with Shrimp ($6.50)") but a single turn takes **18.6 s**. Raising `num_predict` to 1024 did not help:
it produced 830 tokens of narration and still called nothing.

## Why the existing local benchmark reports success

`eval/benchmark_local_qwen.py` runs 2 turns, and its turn-2 gates are `len(tools_used) > 0` and
`basket total > 0`. Any tool call plus a non-empty basket passes. It never checks which dishes were added,
the total, the sold-out refusal, or whether the order was placed — which is why it scored 100% on a run
whose turn 2 called `add_item` and `readback` and never placed the order. It is a smoke test, and a useful
one; it is not evidence that the ordering flow works.

On that same 2-turn scenario, this branch adds the dish **and** places the order in 3/3 runs, where main
adds the dish and places it in 0/3. Every failure in this report comes from the harder 6-turn scenario,
which has not been run against a local model before.

**One spoken-output bug worth noting:** with templates off, one reply quoted the total as `₫16,000.00` —
the model invented a currency. With templates on the same order read back as `$16.00`, because the number
and currency come from the tool result rather than the model. That is the argument for template replies,
independent of latency.

## What this means

1. **Accuracy, not latency, is what stands between us and a working local demo.** A voice waiter that says it
   added a dish and didn't is worse than a slow one. Any comparison of medians between these variants is
   premature while the basket is wrong every time.
2. **The PR #21 optimisations do not transfer to a 3B model as-is.** They assumed the model reliably picks the
   right tool, which Gemini did and `qwen2.5:3b` does not.
3. **Our "no separate readback" prompt rule should be reverted** for the local model; it costs the placement.
4. **Neither available local model clears the bar**: `qwen2.5:3b` calls tools but gets the order wrong;
   `qwen3:4b` is correct only in thinking mode, at ~18 s a turn.

## Suggested next step, in order of expected payoff

1. **Constrain the model instead of instructing it.** Ollama enforces a JSON schema with `format`, unlike tool
   definitions, which it does not enforce. One structured extraction per turn ("what does the guest want?")
   followed by deterministic execution removes tool selection from the model entirely. This is also the design
   on `refactor/new-architecture` and matches #23's node-per-slot proposal.
2. **Try `qwen2.5:7b`** (4.7 GB): it fits in 8 GB VRAM when nothing else is using the GPU, and scores markedly
   higher on function calling than the 3B. This is the cheapest test of "is it just the model?".
3. **Re-run this benchmark after either change.** The harness, gates and report are reusable as-is:
   `uv run python -m eval.benchmark_ollama_waiter --model <m> --reps 3 --out <path>`.

Raw per-run data, including every reply and tool call, is stored beside this summary.
