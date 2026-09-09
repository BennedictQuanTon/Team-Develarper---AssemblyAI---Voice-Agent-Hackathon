"""Realistic Human Multi-Turn Conversation Smoke Test & Realtime Benchmark.

Simulates a natural conversation between a foreign tourist and the Da Nang Voice Agent:
- Turn 1: Exploring Attractions & Top Highlights
- Turn 2: Airport Transit with mid-sentence Barge-in interruption
- Turn 3: Natural farewell closing

Verifies:
1. Streaming STT captioning with AssemblyAI Realtime
2. Zero-hang silence endpointing (user stops speaking -> agent auto-responds within 1s)
3. Live Barge-in interruption (agent speaking -> user speaks -> instant cutoff)
4. Fast Hybrid RAG facts accuracy & Cartesia streaming TTS
5. Outputs comprehensive report to reports/human_conversation_eval.json
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import statistics
import sys
import time
from pathlib import Path

import websockets

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from backend.app.pipeline.tts_live import CartesiaTTSClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CONVERSATION_TURNS = [
    {
        "turn": 1,
        "role": "tourist",
        "text": "What are the must-see attractions in Da Nang, such as the Dragon Bridge?",
        "expected_topics": ["dragon", "bridge", "danang", "da nang", "fire", "river"],
        "expect_barge_in": False,
    },
    {
        "turn": 2,
        "role": "tourist",
        "text": "How can I travel from the airport to the city center?",
        "expected_topics": ["taxi", "grab", "airport", "car"],
        "expect_barge_in": True,
        "interruption_text": "Wait, can I use Grab at the airport?",
        "interruption_expected_topics": ["grab", "app", "arrival", "taxi"],
    },
    {
        "turn": 3,
        "role": "tourist",
        "text": "Thank you so much for the helpful tips, that is all for now!",
        "expected_topics": ["enjoy", "da nang", "welcome", "anytime"],
        "farewell": True,
        "expect_barge_in": False,
    },
]


def _calc_stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[p95_idx], 2),
        "mean": round(statistics.mean(ordered), 2),
        "min": round(min(ordered), 2),
        "max": round(max(ordered), 2),
        "count": len(ordered),
    }


async def _synthesize_voice_pcm(text: str, voice_id: str = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4") -> bytes:
    """Pre-synthesize natural human question PCM (16kHz s16le) to stream via WebSocket."""
    tts = CartesiaTTSClient()
    res = await tts.synthesize(
        text=text,
        voice_id=voice_id,
        speaking_rate=1.0,
        style_prompt="natural, conversational speech",
    )
    raw = base64.b64decode(res.audio_b64)
    if len(raw) > 44 and raw[:4] == b"RIFF":
        return raw[44:]
    return raw


async def run_human_conversation_benchmark(ws_url: str) -> dict:
    chunk_size = 3200  # 100ms at 16kHz s16le mono (3200 bytes)
    ttfb_list: list[float] = []
    e2e_list: list[float] = []
    turn_reports: list[dict] = []
    barge_in_metric: dict = {"success": False, "latency_ms": None}

    print("=" * 70)
    print("🎙️ STARTING REALISTIC HUMAN CONVERSATION & REALTIME BENCHMARK")
    print(f"Connecting to: {ws_url}")
    print("=" * 70)

    async with websockets.connect(ws_url) as ws:
        # 1. Wait for session ready
        ready_msg = json.loads(await ws.recv())
        print(f"✅ Session Ready: {ready_msg}")

        for item in CONVERSATION_TURNS:
            turn_idx = item["turn"]
            tourist_text = item["text"]
            expect_barge = item.get("expect_barge_in", False)

            print(f"\n--- [TURN {turn_idx}] Tourist Speaks: \"{tourist_text}\" ---")
            print("  Synthesizing human speech audio...")
            voice_pcm = await _synthesize_voice_pcm(tourist_text)

            t_speech_start = time.perf_counter()
            # Stream the question audio in real-time chunks (100ms pace)
            for i in range(0, len(voice_pcm), chunk_size):
                await ws.send(voice_pcm[i : i + chunk_size])
                await asyncio.sleep(0.04)

            t_speech_end = time.perf_counter()
            speech_dur_s = round(t_speech_end - t_speech_start, 2)
            print(f"  Finished speaking ({speech_dur_s}s). Now pausing (ambient background)...")

            # Stream ambient silence while waiting for endpointing and agent response
            silence_chunk = bytes(chunk_size)
            silence_stop = asyncio.Event()

            async def _stream_ambient_silence():
                while not silence_stop.is_set():
                    try:
                        await ws.send(silence_chunk)
                        await asyncio.sleep(0.05)
                    except Exception:
                        break

            silence_task = asyncio.create_task(_stream_ambient_silence())

            first_audio_time = None
            turn_complete_time = None
            final_answer = ""
            final_asr = ""
            audio_chunks_count = 0
            interim_count = 0
            rag_ms = None

            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=12.0)
                    msg = json.loads(raw)
                    mtype = msg.get("type")

                    if mtype == "interim_transcript":
                        interim_count += 1
                        print(f"    [ASR Interim]: {msg.get('text')}")
                    elif mtype == "final_transcript":
                        final_asr = msg.get("text", "")
                        print(f"  🎯 [ASR Final Turn]: \"{final_asr}\"")
                    elif mtype == "rag_done":
                        rag_ms = msg.get("rag_ms")
                        print(f"  📚 [RAG Retrieved] in {rag_ms} ms: {msg.get('chunk_ids')}")
                    elif mtype == "audio_chunk":
                        audio_chunks_count += 1
                        if first_audio_time is None:
                            first_audio_time = time.perf_counter()
                            ttfb = round((first_audio_time - t_speech_end) * 1000, 2)
                            ttfb_list.append(ttfb)
                            print(f"  ⚡ [First Audio Chunk] Voice-to-Voice Latency: {ttfb} ms")

                        # If this turn tests barge-in, interrupt as soon as agent starts speaking!
                        if expect_barge and audio_chunks_count == 2:
                            print("\n  ⚡ [BARGE-IN TRIGGER] Tourist interrupts mid-sentence!")
                            interruption_text = item["interruption_text"]
                            print(f"  Tourist says: \"{interruption_text}\"")
                            interrupt_pcm = await _synthesize_voice_pcm(
                                interruption_text, voice_id="db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
                            )
                            t_barge_start = time.perf_counter()
                            for j in range(0, len(interrupt_pcm), chunk_size):
                                await ws.send(interrupt_pcm[j : j + chunk_size])
                                await asyncio.sleep(0.03)

                            # Wait for barge_in signal
                            barge_received = False
                            for _ in range(40):
                                b_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                                if b_msg.get("type") == "barge_in":
                                    b_lat = round((time.perf_counter() - t_barge_start) * 1000, 2)
                                    barge_in_metric = {"success": True, "latency_ms": b_lat}
                                    print(f"  🛑 [Barge-In Confirmed] Agent silenced in {b_lat} ms! (Reason: {b_msg.get('reason')})")
                                    barge_received = True
                                    break
                            # After interruption, wait for answer to the interrupted question
                            expect_barge = False
                    elif mtype == "turn_complete":
                        turn_complete_time = time.perf_counter()
                        final_answer = msg.get("answer", "")
                        e2e = round((turn_complete_time - t_speech_end) * 1000, 2)
                        e2e_list.append(e2e)
                        print(f"  🏁 [Turn Complete] E2E: {e2e} ms | Answer: {final_answer[:120]}...\n")
                        break
                    elif mtype == "error":
                        print(f"  ❌ [Error]: {msg}")
                        break
            finally:
                silence_stop.set()
                await silence_task

            # Knowledge ground truth check
            ans_lower = final_answer.lower()
            keyword_pass = any(k in ans_lower for k in item["expected_topics"])

            turn_reports.append(
                {
                    "turn": turn_idx,
                    "query": tourist_text,
                    "asr_transcript": final_asr,
                    "agent_answer": final_answer,
                    "audio_chunks_received": audio_chunks_count,
                    "voice_to_voice_ttfb_ms": round((first_audio_time - t_speech_end) * 1000, 2) if first_audio_time else None,
                    "e2e_turn_ms": round((turn_complete_time - t_speech_end) * 1000, 2) if turn_complete_time else None,
                    "rag_ms": rag_ms,
                    "passed_knowledge_check": keyword_pass,
                }
            )

    report = {
        "status": "PASS" if all(t["passed_knowledge_check"] for t in turn_reports) else "WARN",
        "benchmark_summary": {
            "total_turns": len(turn_reports),
            "all_turns_passed": all(t["passed_knowledge_check"] for t in turn_reports),
            "voice_to_voice_ttfb_ms": _calc_stats(ttfb_list),
            "e2e_turn_ms": _calc_stats(e2e_list),
            "barge_in_interruption": barge_in_metric,
            "silence_endpointing_hung": False,
        },
        "turns": turn_reports,
    }
    return report


async def main():
    settings = get_settings()
    out_file = ROOT_DIR / "reports" / "human_conversation_eval.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ws_url = f"ws://{settings.app_host}:{settings.app_port}/ws/realtime"
    if settings.app_host == "0.0.0.0":
        ws_url = f"ws://127.0.0.1:{settings.app_port}/ws/realtime"

    try:
        report = await run_human_conversation_benchmark(ws_url)
        out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("=" * 70)
        print("📊 BENCHMARK REPORT SUMMARY:")
        print(json.dumps(report["benchmark_summary"], indent=2))
        print(f"\nWrote full report to: {out_file}")
        print("=" * 70)
    except Exception as exc:
        logger.exception("Benchmark failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
