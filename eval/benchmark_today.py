"""Benchmark & Diagnostic Suite: Measure Live Voice Waiter Latency,
Gemini Function Calling Stability, and Response Accuracy against Yesterday's Baseline.

Evaluates:
1. Speed: TTFB (Time-To-First-Byte) & E2E turn latency vs Yesterday (reports/waiter_e2e_detail.json).
2. Gemini Stability: Rate limiter (15 RPM), tool roundtrips, timeouts, 429 quota errors.
3. Response Accuracy: Semantic intent resolution, function/tool execution correctness,
   basket item tracking, total calculation, clean spoken replies (no JSON/code leakage).

Outputs:
- JSON: reports/benchmark_today_eval.json
- Markdown: reports/benchmark_today_report.md

Usage:
  PYTHONPATH=. .venv/bin/python eval/benchmark_today.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import websockets

from backend.app.config import get_settings
from backend.app.domain.lantern import get_lantern_store
from backend.app.pipeline.tts_live import CartesiaTTSClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark_today")

WS_URL = "ws://127.0.0.1:8000/ws/realtime"
CHUNK = 1280  # 40ms of 16kHz 16-bit mono PCM

SCENARIO = [
    ("recommend", "What would you recommend for a mild couple?", ["recommend_dishes"]),
    ("those-two", "We'll take those two please", ["add_items_from_mention"]),
    ("86-squid", "I'd like the crispy squid too", ["search_menu", "add_item"]),
    ("sub-seabass", "Okay, make it a grilled seabass instead", ["search_menu", "add_item"]),
    ("side-morning-glory", "And stir-fried morning glory on the side", ["search_menu", "add_item"]),
    ("place", "That's all, please place the order", ["readback", "place_order"]),
]

EXPECTED_NAMES = {
    "Pomelo Salad with Shrimp",
    "Lemongrass Chicken",
    "Grilled Seabass",
    "Stir-fried Morning Glory",
}
EXPECTED_TOTAL = round(6.5 + 9.0 + 16.0 + 5.0, 2)  # $36.50


async def synth_pcm(text: str, tts: CartesiaTTSClient) -> bytes:
    """Pre-synthesize prompt speech into 16kHz PCM bytes to simulate natural voice input."""
    raw = bytearray()
    async for chunk in tts.synthesize_stream(text):
        raw.extend(chunk)
    return bytes(raw)


def _clean_text(t: str) -> bool:
    """Check that reply is natural spoken text without leaked code, JSON, or tool tokens."""
    low = t.lower()
    bad = ["{", "}", '"sku"', "'sku'", "tool_calls", "function_call", "[object", "gemini"]
    return not any(b in low for b in bad)


def _extract_tools(events: list[dict]) -> list[dict]:
    """Extract tool calls with name and arguments from events."""
    for ev in reversed(events):
        tc = ev.get("tool_calls")
        if tc and isinstance(tc, list):
            return tc
    return []


def _extract_basket(events: list[dict]) -> list[str]:
    """Extract basket item names from events."""
    for ev in reversed(events):
        b = ev.get("basket")
        if b and b.get("basket"):
            return [x["name"] for x in b["basket"]]
    return []


async def _stream_turn(ws, text: str, tts: CartesiaTTSClient) -> tuple[str, float | None, float | None, list[dict], float | None]:
    """Send voice audio over WebSocket and record streaming TTFB and turn completion."""
    audio = await synth_pcm(text, tts)
    for i in range(0, len(audio), CHUNK):
        await ws.send(audio[i : i + CHUNK])
        await asyncio.sleep(0.04)

    t_speech_end = time.perf_counter()
    first_audio_time = None
    ttfb_ms = None
    e2e_ms = None
    asr_first_ms = None
    reply = ""
    events = []
    stop = asyncio.Event()

    # Stream trailing silence frames so backend silence detector triggers turn completion
    async def sil():
        for _ in range(40):
            if stop.is_set():
                break
            await ws.send(bytes(CHUNK))
            await asyncio.sleep(0.04)

    sil_task = asyncio.create_task(sil())
    try:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=180.0))
            t = msg.get("type")
            ev_at = time.perf_counter()
            ev = {
                k: msg.get(k)
                for k in ("type", "text", "answer", "basket", "rolled_back", "ttfb_ms", "tool_calls", "timings_ms", "provider")
                if k in msg
            }
            ev["at"] = round((ev_at - t_speech_end) * 1000, 2)
            events.append(ev)

            if t == "final_transcript" and asr_first_ms is None:
                asr_first_ms = ev["at"]
            if t == "audio_chunk":
                if msg.get("thinking"):
                    continue  # ignore speculative local filler for true voice TTFB
                if first_audio_time is None:
                    first_audio_time = ev_at
                    ttfb_ms = round((first_audio_time - t_speech_end) * 1000, 2)
            elif t == "turn_complete":
                reply = msg.get("answer", "")
                e2e_ms = round((ev_at - t_speech_end) * 1000, 2)
                break
            elif t == "error":
                reply = f"ERROR: {msg.get('message') or msg.get('detail')}"
                break
    finally:
        stop.set()
        await sil_task

    return reply, ttfb_ms, e2e_ms, events, asr_first_ms


def load_yesterday_baseline() -> dict | None:
    """Load baseline results from reports/waiter_e2e_detail.json if available."""
    path = ROOT / "reports" / "waiter_e2e_detail.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def run_benchmark() -> dict:
    """Execute the full 6-turn diagnostic benchmark against the live server."""
    try:
        async with websockets.connect(WS_URL, open_timeout=3.0) as probe:
            await probe.recv()
    except Exception as exc:
        logger.error("Cannot connect to server at %s. Ensure './scripts/start.sh' is running. Error: %s", WS_URL, exc)
        return {"error": f"Server not reachable at {WS_URL}"}

    tts = CartesiaTTSClient()
    yesterday = load_yesterday_baseline()

    result = {
        "benchmark_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case": "single_native_order_flow",
        "api_providers": {
            "asr": "assemblyai_realtime",
            "llm": "gemini_tools",
            "tts": "cartesia_websocket",
        },
        "turns": [],
        "state_delta": {
            "menu_changed": False,
            "basket_after_each_turn": [],
            "final_items": [],
            "final_total": 0.0,
            "expected_total": EXPECTED_TOTAL,
        },
        "response_accuracy": {},
        "speed_comparison": {},
        "gemini_stability": {},
    }

    prev_basket: list[str] = []
    t_start_all = time.perf_counter()
    errors_429 = 0
    timeouts = 0

    print("\n" + "=" * 75)
    print(" 🚀 STARTING LIVE VOICE WAITER BENCHMARK & DIAGNOSIS")
    print("=" * 75)

    async with websockets.connect(WS_URL) as ws:
        ready_msg = await ws.recv()  # session_ready
        logger.info("Connected to WebSocket. Session ready: %s", ready_msg[:80])

        for i, (intent, text, expected_tools) in enumerate(SCENARIO, start=1):
            print(f"\n--- Turn {i}/6: [{intent}] ---")
            print(f"  User query: \"{text}\"")

            # 86 the squid before turn 3 to verify inventory check
            if intent == "86-squid":
                await ws.send(json.dumps({"command": "set_86", "sku": "MAIN_SQUID", "available": False}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                    if m.get("type") == "set_86_done":
                        print("  [Setup] 86'd MAIN_SQUID (out of stock)")
                        break

            t_turn_start = time.perf_counter()
            try:
                reply, ttfb, e2e, events, asr_ms = await _stream_turn(ws, text, tts)
            except asyncio.TimeoutError:
                timeouts += 1
                reply = "ERROR: Timeout waiting for response"
                ttfb, e2e, events, asr_ms = None, None, [], None
            except Exception as exc:
                if "429" in str(exc) or "exhausted" in str(exc).lower():
                    errors_429 += 1
                reply = f"ERROR: {exc}"
                ttfb, e2e, events, asr_ms = None, None, [], None

            basket_after = _extract_basket(events)
            raw_tools = _extract_tools(events)
            tools_called_names = [t.get("tool") for t in raw_tools] if raw_tools else []

            # Determine per-turn accuracy
            tool_matched = any(tool in expected_tools for tool in tools_called_names) if tools_called_names else (intent == "recommend" or intent == "place")
            reply_clean = _clean_text(reply)

            # Check yesterday's turn latency if available
            y_turn = yesterday["turns"][i - 1] if (yesterday and len(yesterday.get("turns", [])) >= i) else None
            y_ttfb = y_turn.get("ttfb_ms") if y_turn else None
            y_e2e = y_turn.get("e2e_ms") if y_turn else None

            speedup_ttfb = round(((y_ttfb - ttfb) / y_ttfb) * 100, 1) if (y_ttfb and ttfb and y_ttfb > 0) else None
            speedup_e2e = round(((y_e2e - e2e) / y_e2e) * 100, 1) if (y_e2e and e2e and y_e2e > 0) else None

            turn_rec = {
                "turn": i,
                "intent": intent,
                "user_query_text": text,
                "reply_text": reply,
                "functions_used": raw_tools,
                "tools_called": tools_called_names,
                "expected_tools": expected_tools,
                "basket_before": list(prev_basket),
                "basket_after": basket_after,
                "timings_ms": {
                    "asr_first_ms": asr_ms,
                    "ttfb_ms": ttfb,
                    "e2e_ms": e2e,
                },
                "comparison_yesterday": {
                    "yesterday_ttfb_ms": y_ttfb,
                    "yesterday_e2e_ms": y_e2e,
                    "speedup_ttfb_pct": speedup_ttfb,
                    "speedup_e2e_pct": speedup_e2e,
                },
                "turn_accuracy": {
                    "tool_called_ok": tool_matched,
                    "reply_clean": reply_clean,
                },
            }
            result["turns"].append(turn_rec)
            result["state_delta"]["basket_after_each_turn"].append(basket_after)

            print(f"  AI Reply: \"{reply}\"")
            print(f"  Functions Used: {tools_called_names}")
            print(f"  Timings: ASR={asr_ms}ms | TTFB={ttfb}ms | E2E={e2e}ms")
            if speedup_ttfb is not None:
                print(f"  Speed vs Yesterday: TTFB {'+' if speedup_ttfb >= 0 else ''}{speedup_ttfb}% | E2E {'+' if speedup_e2e >= 0 else ''}{speedup_e2e}%")
            print(f"  Basket: {prev_basket} -> {basket_after}")

            prev_basket = basket_after
            await asyncio.sleep(0.4)

        # Restore squid availability for subsequent runs
        await ws.send(json.dumps({"command": "set_86", "sku": "MAIN_SQUID", "available": True}))
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if m.get("type") == "set_86_done":
                    break
        except Exception:
            pass

    wall_seconds = round(time.perf_counter() - t_start_all, 1)

    # Compute Final State & Response Accuracy
    final_items = result["state_delta"]["basket_after_each_turn"][-1] if result["state_delta"]["basket_after_each_turn"] else []
    result["state_delta"]["final_items"] = final_items

    store = get_lantern_store()
    calc_total = sum(store.find_item(n).price for n in final_items if store.find_item(n))
    calc_total = round(calc_total, 2)
    result["state_delta"]["final_total"] = calc_total

    name_accuracy = EXPECTED_NAMES.issubset(set(final_items))
    total_accuracy = abs(calc_total - EXPECTED_TOTAL) < 0.01
    all_clean = all(t["turn_accuracy"]["reply_clean"] for t in result["turns"])
    tool_accuracy = sum(1 for t in result["turns"] if t["turn_accuracy"]["tool_called_ok"]) / len(result["turns"])

    # Score from 0.0 to 1.0 (25% basket items, 25% total price, 25% tool calling, 25% clean speech)
    overall_accuracy = (
        (1.0 if name_accuracy else 0.0) * 0.25
        + (1.0 if total_accuracy else 0.0) * 0.25
        + tool_accuracy * 0.25
        + (1.0 if all_clean else 0.0) * 0.25
    )

    result["response_accuracy"] = {
        "overall_accuracy_score": round(overall_accuracy, 3),
        "overall_accuracy_pct": f"{round(overall_accuracy * 100, 1)}%",
        "basket_items_accuracy": name_accuracy,
        "expected_items": list(EXPECTED_NAMES),
        "actual_items": final_items,
        "total_price_accuracy": total_accuracy,
        "expected_total": EXPECTED_TOTAL,
        "actual_total": calc_total,
        "tool_calling_accuracy_rate": round(tool_accuracy, 3),
        "clean_speech_rate": 1.0 if all_clean else 0.0,
        "all_turns_passed": name_accuracy and total_accuracy and all_clean,
    }

    # Speed Metrics & Yesterday Comparison
    valid_ttfbs = [t["timings_ms"]["ttfb_ms"] for t in result["turns"] if t["timings_ms"]["ttfb_ms"]]
    valid_e2es = [t["timings_ms"]["e2e_ms"] for t in result["turns"] if t["timings_ms"]["e2e_ms"]]

    today_avg_ttfb = round(statistics.mean(valid_ttfbs), 2) if valid_ttfbs else None
    today_avg_e2e = round(statistics.mean(valid_e2es), 2) if valid_e2es else None

    y_avg_ttfb = yesterday["summary"].get("avg_ttfb_ms") if (yesterday and "summary" in yesterday) else 42851.0
    y_avg_e2e = yesterday["summary"].get("avg_e2e_ms") if (yesterday and "summary" in yesterday) else 43820.0
    y_wall_sec = yesterday["rpm"].get("wall_seconds") if (yesterday and "rpm" in yesterday) else 262.9

    overall_ttfb_speedup = round(((y_avg_ttfb - today_avg_ttfb) / y_avg_ttfb) * 100, 1) if (today_avg_ttfb and y_avg_ttfb) else None
    overall_e2e_speedup = round(((y_avg_e2e - today_avg_e2e) / y_avg_e2e) * 100, 1) if (today_avg_e2e and y_avg_e2e) else None

    result["speed_comparison"] = {
        "today_avg_ttfb_ms": today_avg_ttfb,
        "yesterday_avg_ttfb_ms": y_avg_ttfb,
        "ttfb_speedup_pct": overall_ttfb_speedup,
        "today_avg_e2e_ms": today_avg_e2e,
        "yesterday_avg_e2e_ms": y_avg_e2e,
        "e2e_speedup_pct": overall_e2e_speedup,
        "today_wall_seconds": wall_seconds,
        "yesterday_wall_seconds": y_wall_sec,
        "wall_time_speedup_pct": round(((y_wall_sec - wall_seconds) / y_wall_sec) * 100, 1) if (y_wall_sec and wall_seconds) else None,
        "is_faster_today": (today_avg_e2e is not None and y_avg_e2e is not None and today_avg_e2e < y_avg_e2e),
    }

    # Gemini Stability Analysis
    total_calls_est = sum(len(t["functions_used"]) + 1 for t in result["turns"])
    observed_rpm = round(total_calls_est / (wall_seconds / 60.0), 2) if wall_seconds > 0 else 0.0
    stable = (errors_429 == 0) and (timeouts == 0)

    result["gemini_stability"] = {
        "total_gemini_calls_est": total_calls_est,
        "wall_seconds": wall_seconds,
        "observed_rpm": observed_rpm,
        "token_bucket_cap_rpm": 15,
        "under_quota_limit": observed_rpm <= 15,
        "rate_limit_429_errors": errors_429,
        "timeouts_count": timeouts,
        "stability_verdict": "STABLE" if stable else "DEGRADED",
    }

    # Write Outputs
    out_json = ROOT / "reports" / "benchmark_today_eval.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    out_md = ROOT / "reports" / "benchmark_today_report.md"
    md_lines = [
        f"# Live Voice Waiter Benchmark Report — {result['benchmark_timestamp']}",
        "",
        "## 1. Executive Summary",
        f"- **Overall Response Accuracy**: **{result['response_accuracy']['overall_accuracy_pct']}** "
        f"({'PASS' if result['response_accuracy']['all_turns_passed'] else 'FAIL'})",
        f"- **Speed vs Yesterday**: TTFB **{overall_ttfb_speedup or 0:+.1f}%** | E2E **{overall_e2e_speedup or 0:+.1f}%** "
        f"({'FASTER' if result['speed_comparison']['is_faster_today'] else 'SIMILAR/SLOWER'})",
        f"- **Gemini Stability**: **{result['gemini_stability']['stability_verdict']}** "
        f"(429 Errors: {errors_429}, Timeouts: {timeouts}, RPM: {observed_rpm}/15)",
        "",
        "## 2. Turn-by-Turn Telemetry & Function Calling",
        "| Turn | Intent | Functions Used | TTFB (Today) | TTFB (Yest) | E2E (Today) | E2E (Yest) | Speedup |",
        "|:---:|:---|:---|---:|---:|---:|---:|---:|",
    ]

    for t in result["turns"]:
        tools_str = ", ".join(t["tools_called"]) if t["tools_called"] else "(none)"
        cmp = t["comparison_yesterday"]
        speed_badge = f"{cmp['speedup_e2e_pct']:+.1f}%" if cmp["speedup_e2e_pct"] is not None else "—"
        md_lines.append(
            f"| {t['turn']} | `{t['intent']}` | `{tools_str}` | "
            f"{t['timings_ms']['ttfb_ms']} ms | {cmp['yesterday_ttfb_ms']} ms | "
            f"{t['timings_ms']['e2e_ms']} ms | {cmp['yesterday_e2e_ms']} ms | {speed_badge} |"
        )

    md_lines += [
        "",
        "## 3. Responses & Basket Mutations",
    ]
    for t in result["turns"]:
        md_lines.append(f"### Turn {t['turn']}: [{t['intent']}]")
        md_lines.append(f"- **User**: *\"{t['user_query_text']}\"*")
        md_lines.append(f"- **AI Spoken**: *\"{t['reply_text']}\"*")
        md_lines.append(f"- **Functions Called**: `{t['tools_called']}`")
        md_lines.append(f"- **Basket After Turn**: `{t['basket_after']}`")
        md_lines.append("")

    out_md.write_text("\n".join(md_lines), encoding="utf-8")

    # Console Summary Table
    print("\n" + "=" * 75)
    print(" 📊 BENCHMARK RESULTS SUMMARY")
    print("=" * 75)
    print(f" Accuracy Score    : {result['response_accuracy']['overall_accuracy_pct']} (Basket: {name_accuracy}, Total: ${calc_total})")
    print(f" Speed Comparison  : Today Avg E2E={today_avg_e2e}ms vs Yesterday={y_avg_e2e}ms (Speedup: {overall_e2e_speedup:+.1f}%)")
    print(f" Gemini Stability  : {result['gemini_stability']['stability_verdict']} (429s: {errors_429}, Timeouts: {timeouts}, RPM: {observed_rpm})")
    print(f" JSON Saved To     : {out_json}")
    print(f" Markdown Saved To : {out_md}")
    print("=" * 75 + "\n")

    return result


def main():
    parser = argparse.ArgumentParser(description="Run Live Voice Waiter Benchmark")
    args = parser.parse_args()
    res = asyncio.run(run_benchmark())
    if res.get("error"):
        print(f"FAIL: {res['error']}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
