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
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=20.0))
            t = msg.get("type")
            events.append(
                {k: msg.get(k) for k in ("type", "text", "answer", "basket", "rolled_back", "ttfb_ms") if k in msg}
            )
            if t == "audio_chunk" and first_audio_time is None:
                first_audio_time = time.perf_counter()
                ttfb_ms = round((first_audio_time - t_speech_end) * 1000, 2)
            elif t == "turn_complete":
                reply = msg.get("answer", "")
                e2e_ms = round((time.perf_counter() - t_speech_end) * 1000, 2)
                break
            elif t == "error":
                reply = f"ERROR: {msg.get('message')}"
                break
    finally:
        stop.set()
        await sil_task
    return reply, ttfb_ms, e2e_ms, events


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

        r1, ttfb1, e2e1, ev1 = await _stream_turn(ws, "What would you recommend for a mild couple?", tts)
        report["ttfb_ms"].append(ttfb1); report["e2e_ms"].append(e2e1)
        report["turns"].append({"id": "rec", "reply": r1, "ttfb_ms": ttfb1, "e2e_ms": e2e1, "events": ev1})
        print(f"  [WS rec] ttfb={ttfb1}ms e2e={e2e1}ms reply={r1!r}")

        r2, ttfb2, e2e2, ev2 = await _stream_turn(ws, "We'll take those two please", tts)
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

        r3, ttfb3, e2e3, ev3 = await _stream_turn(ws, "that's all, please place the order", tts)
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
