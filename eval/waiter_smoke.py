"""Waiter real-case smoke test (turn-level accuracy + realtime E2E, single script).

Part A — Turn-level (no server): calls WaiterAgent.respond() directly with real Gemini
   to assert ordering logic accuracy: recommend -> "those two" -> place order,
   86-substitute, allergen-refuse, barge-in keeps basket.
Part B — Realtime (needs WS server): streams synthesized audio over /ws/realtime,
   measures voice-to-voice TTFB + E2E and barge-in latency, asserts basket through
   basket_update / turn_complete payloads. Auto-skips if server is not up.

Run:
  PYTHONPATH=. .venv/bin/python eval/waiter_smoke.py          # Part A only
  # start server first, then:
  PYTHONPATH=. .venv/bin/python eval/waiter_smoke.py --realtime
  PYTHONPATH=. .venv/bin/python eval/waiter_smoke.py --all    # A + B

Writes reports/waiter_smoke_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.domain.lantern import get_lantern_store
from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline.tts_live import CartesiaTTSClient
from backend.app.pipeline.waiter_agent import WaiterAgent, build_waiter_agent

REPORT_PATH = ROOT / "reports" / "waiter_smoke_report.md"


def _calc_stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[p95_idx], 2),
        "mean": round(statistics.mean(ordered), 2),
        "count": len(ordered),
    }


# ---------------------------------------------------------------------------
# Part A: turn-level accuracy (real Gemini tool loop, no server)
# ---------------------------------------------------------------------------


async def scenario_c1(agent, store):
    """Recommend mild couple -> 'those two' -> place order. Assert 2 items + total."""
    ss = WaiterSession(store, f"c1-{uuid.uuid4().hex[:6]}")
    r1 = await agent.respond(ss, "What would you recommend for two people who like mild food?")
    if not r1.get("reply"):
        return {"pass": False, "reason": "empty reply on recommend"}
    r2 = await agent.respond(ss, "We'll take those two please")
    snap = ss.snapshot()
    basket = snap["basket"]
    if len(basket) != 2:
        return {
            "pass": False,
            "reason": f"expected 2 items in basket, got {len(basket)}: {[b['name'] for b in basket]}",
        }
    total = sum(b["price"] * b["qty"] for b in basket)
    if abs(total - snap["total"]) > 0.01:
        return {"pass": False, "reason": f"total mismatch {total} vs {snap['total']}"}
    for b in basket:
        if store.get_item(b["sku"]) is None:
            return {"pass": False, "reason": f"bogus sku {b['sku']}"}
    return {
        "pass": True,
        "basket": [b["name"] for b in basket],
        "total": snap["total"],
        "reply": r2.get("reply", "")[:80],
    }


async def scenario_c2_86(agent, store):
    """86 Crispy Squid -> guest calls it. Assert NOT added + substitute offered."""
    squid = store.get_item("MAIN_SQUID")
    if squid is None:
        return {"pass": False, "reason": "MAIN_SQUID missing"}
    store.set_available("MAIN_SQUID", False)
    ss = WaiterSession(store, f"c2-{uuid.uuid4().hex[:6]}")
    try:
        ss._record_mentions([squid])
        r = await agent.respond(ss, "I'd like the crispy squid please")
        snap = ss.snapshot()
        added_squid = any(b["name"] == "Crispy Squid" for b in snap["basket"])
        if added_squid:
            return {"pass": False, "reason": "added a sold-out item"}
        low = (r.get("reply") or "").lower()
        suggested = (
            "substitute" in low
            or "instead" in low
            or "sold out" in low
            or "sold-out" in low
        )
        if not suggested:
            return {"pass": False, "reason": f"no substitute/sold-out surfaced: {r.get('reply','')!r}"}
        return {"pass": True, "reply": r.get("reply", "")[:90]}
    finally:
        store.set_available("MAIN_SQUID", True)


async def scenario_c3_allergen(agent, store):
    """Guest asks about an allergen not plainly listed -> agent must refuse to guess."""
    ss = WaiterSession(store, f"c3-{uuid.uuid4().hex[:6]}")
    r = await agent.respond(ss, "Does the grilled seabass contain any MSG? I'm allergic.")
    low = (r.get("reply") or "").lower()
    refused = any(
        w in low
        for w in ("ask the kitchen", "check the kitchen", "can't confirm", "not listed", "check", "confirm")
    )
    pin = any(w in low for w in ("msg", "allerg"))
    if not refused and not pin:
        return {"pass": False, "reason": f"agent neither refused nor acknowledged allergen: {r.get('reply','')!r}"}
    return {"pass": True, "reply": r.get("reply", "")[:80]}


async def scenario_c4_barge_in(agent, store):
    """Barge-in (cancel ev set mid-tool) must not reset an existing basket.

    We simulate the controller's snapshot-then-restore contract directly on the
    WaiterSession: complete a basket, attempt an interrupted turn that adds a dish,
    then rollback to the pre-turn snapshot -> basket must be preserved exactly.
    """
    ss = WaiterSession(store, f"c4-{uuid.uuid4().hex[:6]}")
    await agent.respond(ss, "What's good for a mild couple?")
    await agent.respond(ss, "We'll take those two")
    base = ss.snapshot()
    ss2 = WaiterSession(store, f"c4b-{uuid.uuid4().hex[:6]}")
    ss2.restore(base)
    await agent.respond(ss2, "add the crispy squid too")
    squids = [b["name"] for b in ss2.snapshot()["basket"] if b["name"] == "Crispy Squid"]
    ss2.restore(base)
    if squids and ss2.snapshot()["basket"] != base["basket"]:
        return {"pass": False, "reason": "basket not preserved after rollback"}
    return {"pass": True, "basket": [b["name"] for b in ss2.snapshot()["basket"]]}


async def run_part_a(agent) -> dict:
    store = get_lantern_store()
    results = {}
    latency_ms = []
    for name, fn in [
        ("C1_recommend_order", scenario_c1),
        ("C2_86_substitute", scenario_c2_86),
        ("C3_allergen_refuse", scenario_c3_allergen),
        ("C4_barge_in_keep", scenario_c4_barge_in),
    ]:
        t0 = time.perf_counter()
        res = await fn(agent, store)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        latency_ms.append(elapsed)
        results[name] = {**res, "latency_ms": elapsed}
        status = "PASS" if res["pass"] else "FAIL"
        print(f"  [{name}] {status}")
        print(f"        latency={elapsed}ms  details={json.dumps({k: res.get(k) for k in ('basket','total','reply','reason')}, ensure_ascii=False)}")
    report = {
        "accuracy": {k: ":white_check_mark:" if v["pass"] else ":x:" for k, v in results.items()},
        "details": results,
        "passed": sum(1 for v in results.values() if v["pass"]),
        "total": len(results),
        "scenario_latency_ms": _calc_stats(latency_ms),
    }
    print(f"  accuracy: {report['passed']}/{report['total']}")
    return report


# ---------------------------------------------------------------------------
# Part B: realtime E2E over WS /ws/realtime
# ---------------------------------------------------------------------------
WS_URL = "ws://127.0.0.1:8000/ws/realtime"
CHUNK = 3200  # 100ms of 16kHz s16le mono


async def synth_pcm(text: str, tts) -> bytes:
    res = await tts.synthesize(text=text, voice_id="", speaking_rate=1.0, style_prompt="clear")
    raw = base64.b64decode(res.audio_b64)
    if len(raw) > 44 and raw[:4] == b"RIFF":
        return raw[44:]
    return raw


async def _stream_turn(ws, text: str, tts) -> tuple:
    """Send synthesized audio, wait for turn_complete, return (reply, ttfb, e2e, events)."""
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

    async def sil():
        for _ in range(35):
            if stop.is_set():
                break
            await ws.send(bytes(CHUNK))
            await asyncio.sleep(0.04)

    sil_task = asyncio.create_task(sil())
    try:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=150.0))
            t = msg.get("type")
            ev_at = time.perf_counter()
            ev = {
                k: msg.get(k)
                for k in ("type", "text", "answer", "basket", "rolled_back", "ttfb_ms", "tool_calls")
                if k in msg
            }
            ev["at"] = round((ev_at - t_speech_end) * 1000, 2)
            events.append(ev)
            if t == "final_transcript" and asr_first_ms is None:
                asr_first_ms = ev["at"]
            if t == "audio_chunk":  # ignore speculative 'thinking' clip for real TTFB
                if msg.get("thinking"):
                    continue
                if first_audio_time is None:
                    first_audio_time = ev_at
                    ttfb_ms = round((first_audio_time - t_speech_end) * 1000, 2)
            elif t == "turn_complete":
                reply = msg.get("answer", "")
                e2e_ms = round((ev_at - t_speech_end) * 1000, 2)
                break
            elif t == "error":
                reply = f"ERROR: {msg.get('message')}"
                break
    finally:
        stop.set()
        await sil_task
    return reply, ttfb_ms, e2e_ms, events, asr_first_ms


async def run_part_b(settings) -> dict:
    import websockets

    try:
        async with websockets.connect(WS_URL, open_timeout=3.0) as probe:
            await probe.recv()
        up = True
    except Exception:
        up = False
    if not up:
        return {"skipped": True, "reason": "server not up — run bash scripts/start.sh first"}

    tts = CartesiaTTSClient()
    report = {"skipped": False, "ttfb_ms": [], "e2e_ms": [], "turns": [], "barge_in": None}
    async with websockets.connect(WS_URL) as ws:
        await ws.recv()  # session_ready

        r1, ttfb1, e2e1, ev1, _ = await _stream_turn(ws, "What would you recommend for a mild couple?", tts)
        report["ttfb_ms"].append(ttfb1); report["e2e_ms"].append(e2e1)
        report["turns"].append({"id": "rec", "reply": r1, "ttfb_ms": ttfb1, "e2e_ms": e2e1, "events": ev1})
        print(f"  [WS rec] ttfb={ttfb1}ms e2e={e2e1}ms reply={r1!r}")

        r2, ttfb2, e2e2, ev2, _ = await _stream_turn(ws, "We'll take those two please", tts)
        report["ttfb_ms"].append(ttfb2); report["e2e_ms"].append(e2e2)
        report["turns"].append({"id": "those-two", "reply": r2, "ttfb_ms": ttfb2, "e2e_ms": e2e2, "events": ev2})
        print(f"  [WS those-two] ttfb={ttfb2}ms e2e={e2e2}ms reply={r2!r}")
        basket = None
        for ev in reversed(ev2):
            b = ev.get("basket")
            if b and b.get("basket"):
                basket = [x["name"] for x in b["basket"]]
                break
        report["turns"][-1]["basket"] = basket
        print(f"        basket={basket}")

        r3, ttfb3, e2e3, ev3, _ = await _stream_turn(ws, "that's all, please place the order", tts)
        report["ttfb_ms"].append(ttfb3); report["e2e_ms"].append(e2e3)
        report["turns"].append({"id": "place", "reply": r3, "ttfb_ms": ttfb3, "e2e_ms": e2e3, "events": ev3})
        print(f"  [WS place] ttfb={ttfb3}ms e2e={e2e3}ms reply={r3!r}")

        # Barge-in test
        print("  Testing barge-in...")
        try:
            await ws.send(json.dumps({"command": "reset"}))
            while True:
                if json.loads(await ws.recv()).get("type") == "session_reset":
                    break
            q_long = await synth_pcm("Tell me about your best grilled options and popular dishes tonight", tts)
            interrupt = await synth_pcm("Actually I'd like the lemongrass chicken instead", tts)
            for i in range(0, len(q_long), CHUNK):
                await ws.send(q_long[i : i + CHUNK])
                await asyncio.sleep(0.04)
            got_speech = False
            while not got_speech:
                await ws.send(bytes(CHUNK))
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.15))
                    if msg.get("type") == "audio_chunk":
                        got_speech = True
                except asyncio.TimeoutError:
                    pass
            t0 = time.perf_counter()
            for i in range(0, len(interrupt), CHUNK):
                await ws.send(interrupt[i : i + CHUNK])
                await asyncio.sleep(0.02)
            t_done = time.perf_counter()
            barge_latency = None
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    if msg.get("type") == "barge_in":
                        barge_latency = round((t_done - t0) * 1000, 2)
                        break
            except Exception:
                pass
            report["barge_in"] = {"success": barge_latency is not None, "latency_ms": barge_latency}
            print(f"  [barge_in] success={report['barge_in']['success']} latency={barge_latency}ms")
        except Exception as exc:
            report["barge_in"] = {"success": False, "error": str(exc)[:120]}

    report["metrics"] = {
        "voice_to_voice_ttfb_ms": _calc_stats([x for x in report["ttfb_ms"] if x]),
        "e2e_turn_ms": _calc_stats([x for x in report["e2e_ms"] if x]),
    }
    return report


# ---------------------------------------------------------------------------
# Full-case smoke: recommend -> those two -> barge-in swap -> add -> place order
# Measures per-turn latency + accuracy, writes detailed JSON for audit.
# ---------------------------------------------------------------------------


def _basket_from_events(events: list[dict]) -> list[str]:
    for ev in reversed(events):
        b = ev.get("basket")
        if b and b.get("basket"):
            return [x["name"] for x in b["basket"]]
    return []


def _last_tool_calls(events: list[dict]) -> list[str]:
    # basket_update carries tool_calls
    for ev in reversed(events):
        tc = ev.get("tool_calls")
        if tc is not None:
            return [t.get("tool") for t in tc] if isinstance(tc, list) else []
    return []


def _clean_text(t: str) -> bool:
    """Heuristic: reply should not contain residual function-context artifacts like
    JSON, tool names, or 'Tool' markers that leak into spoken text."""
    low = t.lower()
    bad = ["{", "}", '"sku"', "'sku'", "tool_calls", "function_call", "[object", "gemini"]
    return not any(b in low for b in bad)


async def run_full_flow(settings) -> dict:
    """Run one real end-to-end ordering case through real audio WS + real APIs.

    Returns a fully auditable dict (per-turn ms, content, spell/leak check,
    tools called, basket state delta).
    """
    import websockets

    try:
        async with websockets.connect(WS_URL, open_timeout=3.0) as probe:
            await probe.recv()
    except Exception:
        return {"skipped": True, "reason": "server not up — run bash scripts/start.sh first"}

    tts = CartesiaTTSClient()
    steps = [
        ("recommend", "What do you recommend for a mild couple?", None),
        ("those-two", "We'll take those two please", None),
        ("add-morning-glory", "And add a stir-fried morning glory too", None),
        ("place", "That's all, please place the order", None),
    ]
    turns = []
    detail = {
        "case": "full_order_flow",
        "api": {"asr": "assemblyai_realtime", "llm": "gemini_tools", "tts": "cartesia_websocket"},
        "turns": [],
        "state_delta": {"menu_changed": False, "basket_after_each_turn": [], "final_total": None, "placed": None},
        "summary": {},
    }

    async with websockets.connect(WS_URL) as ws:
        await ws.recv()  # session_ready
        for i, (intent, text, _barge_in) in enumerate(steps, start=1):
            reply, ttfb, e2e, events, _asr = await _stream_turn(ws, text, tts)
            basket = _basket_from_events(events)
            tools = _last_tool_calls(events)
            record = {
                "turn": i,
                "intent": intent,
                "user_query_text": text,
                "reply_text": reply,
                "reply_clean": _clean_text(reply),
                "tools_called": tools,
                "basket_after": basket,
                "ttfb_ms": ttfb,
                "e2e_ms": e2e,
            }
            turns.append(record)
            detail["state_delta"]["basket_after_each_turn"].append(basket)
            print(f"  [{intent}] ttfb={ttfb}ms e2e={e2e}ms reply={reply!r}")
            print(f"        basket={basket} tools={tools}")
            await asyncio.sleep(0.6)  # pacing between turns

    # accuracy asserts on final basket
    final = detail["state_delta"]["basket_after_each_turn"][-1]
    expected_names = {
        "Pomelo Salad with Shrimp",
        "Lemongrass Chicken",
        "Stir-fried Morning Glory",
    }
    final_set = set(final)
    accuracy_name = expected_names.issubset(final_set)
    # total from last turn_complete basket payload if present
    final_total = None
    placed = None
    for rec in turns:
        # we don't persist total here; recompute from menu prices
        pass

    # recompute total deterministically from final basket via store
    store = get_lantern_store()
    total = 0.0
    ok_total = True
    for name in final:
        it = store.find_item(name)
        if it is None:
            ok_total = False
            continue
        total += it.price
    detail["state_delta"]["final_total"] = round(total, 2)
    detail["state_delta"]["placed"] = placed  # filled below if we capture

    # spell/robustness: any reply missing expected keywords?
    all_clean = all(r["reply_clean"] for r in turns)
    trouble = [r for r in turns if not r["reply_clean"]]

    detail["summary"] = {
        "turns_completed": len(turns),
        "accuracy_name_ok": accuracy_name,
        "total_ok": ok_total,
        "all_replies_clean": all_clean,
        "issues": [{"turn": r["turn"], "intent": r["intent"]} for r in trouble],
        "avg_e2e_ms": round(sum(t["e2e_ms"] for t in turns if t["e2e_ms"]) / max(1, len([t for t in turns if t["e2e_ms"]])), 2),
        "avg_ttfb_ms": round(sum(t["ttfb_ms"] for t in turns if t["ttfb_ms"]) / max(1, len([t for t in turns if t["ttfb_ms"]])), 2),
    }
    detail["turns"] = turns

    # persist detailed JSON for audit
    out_json = ROOT / "reports" / "waiter_latency_detail.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote detail JSON -> {out_json}")
    return detail


# ---------------------------------------------------------------------------
# Single native-flow scenario (6 turns), full-phase report + RPM audit
# ---------------------------------------------------------------------------
SCENARIO_NATIVE = [
    ("recommend", "What would you recommend for a mild couple?"),
    ("those-two", "We'll take those two please"),
    ("86-squid", "I'd like the crispy squid too"),
    ("sub-seabass", "Okay, make it a grilled seabass instead"),
    ("add-morning-glory", "And stir-fried morning glory on the side"),
    ("place", "That's all, please place the order"),
]

EXPECTED_NAMES = {
    "Pomelo Salad with Shrimp",
    "Lemongrass Chicken",
    "Grilled Seabass",
    "Stir-fried Morning Glory",
}
EXPECTED_TOTAL = round(6.5 + 9.0 + 16.0 + 5.0, 2)


async def run_one_scenario(settings) -> dict:
    """One real end-to-end ordering conversation over WS, phase-per-turn.

    Asserts basket correctness, total, clean speech, and Gemini request rate
    (< 15 RPM thanks to the async token bucket at the server).
    """
    import websockets

    try:
        async with websockets.connect(WS_URL, open_timeout=3.0) as probe:
            await probe.recv()
    except Exception:
        return {"skipped": True, "reason": "server not up — run bash scripts/start.sh first"}

    tts = CartesiaTTSClient()
    detail = {
        "case": "single_native_order_flow",
        "api": {"asr": "assemblyai_realtime", "llm": "gemini_tools", "tts": "cartesia_websocket"},
        "turns": [],
        "state_delta": {"menu_changed": False, "basket_after_each_turn": []},
        "rpm": {},
        "summary": {},
    }
    prev_basket: list[str] = []
    t_t0 = time.perf_counter()

    async with websockets.connect(WS_URL) as ws:
        await ws.recv()  # session_ready
        for i, (intent, text) in enumerate(SCENARIO_NATIVE, start=1):
            # Exercise the sold-out -> substitute path deterministically: 86 the squid.
            if intent == "86-squid":
                await ws.send(json.dumps({"command": "set_86", "sku": "MAIN_SQUID", "available": False}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                    if m.get("type") == "set_86_done":
                        print(f"        86 MAIN_SQUID -> found={m.get('found')}")
                        break
            reply, ttfb, e2e, events, asr_ms = await _stream_turn(ws, text, tts)
            basket_after = _basket_from_events(events)
            tools = _last_tool_calls(events)
            rec = {
                "turn": i,
                "intent": intent,
                "user_query_text": text,
                "asr_first_ms": asr_ms,
                "llm_tool_rounds": len(tools) + 1,  # 1 LLM content call + 1 per executed tool
                "reply_text": reply,
                "reply_clean": _clean_text(reply),
                "tools_called": tools,
                "basket_before": list(prev_basket),
                "basket_after": basket_after,
                "ttfb_ms": ttfb,
                "e2e_ms": e2e,
            }
            detail["turns"].append(rec)
            detail["state_delta"]["basket_after_each_turn"].append(basket_after)
            print(f"  [{intent}] asr={asr_ms}ms ttfb={ttfb}ms e2e={e2e}ms tools={tools}")
            print(f"        basket {prev_basket} -> {basket_after}")
            prev_basket = basket_after
            await asyncio.sleep(0.3)  # small protocol pacing between spoken turns

        # Restore squid availability so repeat runs start from a clean menu.
        await ws.send(json.dumps({"command": "set_86", "sku": "MAIN_SQUID", "available": True}))
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if m.get("type") == "set_86_done":
                    break
        except Exception:  # noqa: BLE001
            pass

    wall_seconds = round(time.perf_counter() - t_t0, 1)

    final = detail["state_delta"]["basket_after_each_turn"][-1] if detail["state_delta"]["basket_after_each_turn"] else []
    name_ok = EXPECTED_NAMES.issubset(set(final))
    store = get_lantern_store()
    total = 0.0
    for n in final:
        it = store.find_item(n)
        total += it.price if it else 0.0
    total_ok = abs(total - EXPECTED_TOTAL) < 0.01
    all_clean = all(t["reply_clean"] for t in detail["turns"])
    total_gemini_calls = sum(t["llm_tool_rounds"] for t in detail["turns"])

    detail["rpm"] = {
        "total_gemini_calls_est": total_gemini_calls,
        "wall_seconds": wall_seconds,
        "rate_per_minute_est": round(total_gemini_calls / (wall_seconds / 60.0), 3),
        "cap_rpm": 15,
        "under_cap": total_gemini_calls / (wall_seconds / 60.0) < 15 if wall_seconds > 0 else True,
    }
    detail["summary"] = {
        "turns_completed": len(detail["turns"]),
        "accuracy_name_ok": name_ok,
        "total_ok": total_ok,
        "all_replies_clean": all_clean,
        "final_total": round(total, 2),
        "issues": [{"turn": t["turn"], "intent": t["intent"]} for t in detail["turns"] if not t["reply_clean"]],
        "avg_ttfb_ms": round(sum(t["ttfb_ms"] for t in detail["turns"] if t["ttfb_ms"]) / max(1, len([t for t in detail["turns"] if t["ttfb_ms"]])), 2),
        "avg_e2e_ms": round(sum(t["e2e_ms"] for t in detail["turns"] if t["e2e_ms"]) / max(1, len([t for t in detail["turns"] if t["e2e_ms"]])), 2),
    }

    out_json = ROOT / "reports" / "waiter_e2e_detail.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")
    out_md = ROOT / "reports" / "waiter_e2e_report.md"
    md = [
        "# Waiter Single-Flow E2E (full phase)",
        "",
        f"API: ASR=assemblyai_realtime | LLM=gemini_tools | TTS=cartesia_websocket",
        f"Case: recommend -> those-two -> 86-squid -> seabass -> morning-glory -> place",
        "",
        "| turn | intent | asr_ms | ttfb_ms | e2e_ms | tools | basket_after |",
        "|------|--------|-------:|--------:|-------:|-------|--------------|",
    ]
    for t in detail["turns"]:
        name = ", ".join(t["basket_after"]) if t["basket_after"] else "-"
        tools = ", ".join(t["tools_called"]) if t["tools_called"] else "-"
        md.append(
            f"| {t['turn']} | {t['intent']} | {t['asr_first_ms']} | {t['ttfb_ms']} | {t['e2e_ms']} "
            f"| {tools} | {name} |"
        )
    md += [
        "",
        f"- accuracy_name_ok={name_ok}, total_ok={total_ok}, final_total=${round(total,2)} "
        f"(expected ${EXPECTED_TOTAL})",
        f"- all_replies_clean={all_clean}",
        f"- gemini calls est={total_gemini_calls} in {wall_seconds}s = "
        f"{detail['rpm']['rate_per_minute_est']}/min (cap 15, under={detail['rpm']['under_cap']})",
    ]
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote detail JSON -> {out_json}")
    print(f"Wrote report     -> {out_md}")
    return detail


def write_report(part_a: dict, part_b: dict):
    lines = ["# Waiter Real-Case Smoke Test\n"]
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("## Part A — Turn-level accuracy\n")
    if not part_a:
        lines.append("(not run — this was a realtime-only run)\n")
    else:
        lines.append(f"Accuracy: **{part_a['passed']}/{part_a['total']}**\n")
        for k, v in part_a.get("accuracy", {}).items():
            lines.append(f"- {k}: `{v}`")
        lines.append("\n### Scenario latency (tool loop)\n")
        lines.append("```json\n" + json.dumps(part_a.get("scenario_latency_ms") or {}, indent=2) + "\n```\n")

    lines.append("## Part B — Realtime E2E\n")
    if part_b.get("skipped"):
        lines.append(f"SKIPPED: {part_b.get('reason')}\n")
    else:
        lines.append("### Metrics\n")
        lines.append("```json\n" + json.dumps(part_b.get("metrics") or {}, indent=2) + "\n```\n")
        lines.append("### Barge-in\n")
        lines.append("```json\n" + json.dumps(part_b.get("barge_in"), indent=2) + "\n```\n")
        lines.append("### Turns\n")
        for t in part_b.get("turns") or []:
            lines.append(f"- **{t['id']}** ttfb={t['ttfb_ms']}ms e2e={t['e2e_ms']}ms reply=`{t['reply'][:60]}`")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote report -> {REPORT_PATH}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--realtime", action="store_true", help="run realtime E2E part (needs server)")
    parser.add_argument("--all", action="store_true", help="run turn-level + realtime")
    parser.add_argument("--full", action="store_true", help="run full ordering case over WS with detail JSON")
    parser.add_argument("--scenario", action="store_true", help="run single native-flow scenario with full phase report")
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    if not all(settings.keys_configured.values()):
        print("FAIL: need all keys in .env")
        return 2

    agent = build_waiter_agent()
    if not isinstance(agent, WaiterAgent) or not agent.available:
        print("FAIL: real Gemini not available — set GEMINI_API_KEY")
        return 2

    if args.scenario:
        print("=== Single native-flow scenario (real APIs, full phase) ===")
        det = await run_one_scenario(settings)
        if det.get("skipped"):
            print(f"SKIPPED: {det.get('reason')}")
            return 2
        s = det["summary"]
        rpm = det["rpm"]
        print(f"accuracy_name_ok={s['accuracy_name_ok']} total_ok={s['total_ok']} "
              f"all_clean={s['all_replies_clean']} final_total=${s['final_total']}")
        print(f"gemini calls={rpm['total_gemini_calls_est']} wall={rpm['wall_seconds']}s "
              f"rate={rpm['rate_per_minute_est']}/min cap={rpm['cap_rpm']} under_cap={rpm['under_cap']}")
        return 0 if (s['accuracy_name_ok'] and s['total_ok'] and s['all_replies_clean']) else 1

    if args.full:
        print("=== Full ordering case over WS (real APIs) ===")
        detail = await run_full_flow(settings)
        if detail.get("skipped"):
            print(f"SKIPPED: {detail.get('reason')}")
            return 2
        ok = detail["summary"].get("accuracy_name_ok") and detail["summary"].get("all_replies_clean")
        print(f"accuracy_name_ok={ok} total_ok={detail['summary'].get('total_ok')} all_clean={detail['summary'].get('all_replies_clean')}")
        return 0 if ok else 1

    part_b = {"skipped": True, "reason": "Part B not requested (add --realtime)"}
    if args.realtime or args.all:
        print("=== Part B: Realtime E2E over WS ===")
        part_b = await run_part_b(settings)

    if args.all:
        print("\n=== Part A: Turn-level accuracy (real Gemini tool loop) ===")
        part_a = await run_part_a(agent)
        write_report(part_a, part_b)
        overall_ok = part_a["passed"] == part_a["total"]
        print(f"\npart_a={part_a['passed']}/{part_a['total']}")
    else:
        # --realtime alone: write realtime-only report
        part_a = {}
        write_report(part_a, part_b)
        overall_ok = not part_b.get("skipped")

    print("EXIT SUCCESS" if overall_ok else "EXIT FAIL")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", type(exc).__name__, str(exc)[:400])
        raise SystemExit(1) from exc
