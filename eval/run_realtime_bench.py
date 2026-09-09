"""Real-time Voice-to-Voice E2E Benchmark: Streaming STT (AssemblyAI) -> Hybrid RAG -> Gemini Stream -> Cartesia WS Stream -> Barge-in test.

Outputs report to reports/realtime_eval.json.
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

logger = logging.getLogger(__name__)

BENCHMARK_QUERIES = [
    {
        "id": "q1",
        "text": "When is the Dragon Bridge fire show?",
        "expected_keywords": ["saturday", "sunday", "9"],
    },
    {
        "id": "q2",
        "text": "How do I get from the airport to the city?",
        "expected_keywords": ["taxi", "grab", "airport"],
    },
    {
        "id": "q3",
        "text": "Thanks, that's all.",
        "expected_keywords": ["enjoy", "da nang", "anytime"],
        "farewell": True,
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


async def _synthesize_question_pcm(text: str) -> bytes:
    """Synthesize question text into 16kHz s16le PCM audio using Cartesia."""
    tts = CartesiaTTSClient()
    res = await tts.synthesize(
        text=text,
        voice_id="db6b0ed5-d5d3-463d-ae85-518a07d3c2b4",
        speaking_rate=1.0,
        style_prompt="clear",
    )
    raw = base64.b64decode(res.audio_b64)
    # Strip 44-byte WAV header to get raw PCM bytes
    if len(raw) > 44 and raw[:4] == b"RIFF":
        return raw[44:]
    return raw


async def run_benchmark(ws_url: str) -> dict:
    results = []
    ttfb_list: list[float] = []
    e2e_list: list[float] = []

    print(f"Connecting to {ws_url}...")
    async with websockets.connect(ws_url) as ws:
        # Wait for session_ready
        init_msg = json.loads(await ws.recv())
        print(f"Connected: {init_msg}")

        for item in BENCHMARK_QUERIES:
            print(f"\n--- Testing query: {item['text']} ---")
            pcm_audio = await _synthesize_question_pcm(item["text"])
            t_send_start = time.perf_counter()

            # Stream PCM in 100ms chunks (3200 bytes for 16kHz 16-bit mono)
            chunk_size = 3200
            for i in range(0, len(pcm_audio), chunk_size):
                chunk = pcm_audio[i : i + chunk_size]
                await ws.send(chunk)
                await asyncio.sleep(0.04)  # real-time simulated pacing

            t_speech_end = time.perf_counter()
            first_audio_time = None
            turn_complete_time = None
            final_answer = ""
            audio_chunks_received = 0
            interim_transcripts = []

            silence_stop = asyncio.Event()

            async def _stream_trailing_silence():
                silence_chunk = bytes(chunk_size)
                # Stream up to 1.5s of silence frames to trigger AssemblyAI end of turn
                for _ in range(35):
                    if silence_stop.is_set():
                        break
                    try:
                        await ws.send(silence_chunk)
                        await asyncio.sleep(0.04)
                    except Exception:
                        break

            silence_task = asyncio.create_task(_stream_trailing_silence())

            try:
                while True:
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                    msg = json.loads(msg_raw)
                    mtype = msg.get("type")

                    if mtype == "interim_transcript":
                        interim_transcripts.append(msg.get("text"))
                    elif mtype == "final_transcript":
                        print(f"  [ASR Final]: {msg.get('text')}")
                    elif mtype == "audio_chunk":
                        audio_chunks_received += 1
                        if first_audio_time is None:
                            first_audio_time = time.perf_counter()
                            ttfb_ms = round((first_audio_time - t_speech_end) * 1000, 2)
                            print(f"  ⚡ [First Audio Chunk] Voice-to-Voice TTFB: {ttfb_ms} ms")
                            ttfb_list.append(ttfb_ms)
                    elif mtype == "turn_complete":
                        turn_complete_time = time.perf_counter()
                        final_answer = msg.get("answer", "")
                        e2e_ms = round((turn_complete_time - t_speech_end) * 1000, 2)
                        print(f"  [Turn Complete] E2E: {e2e_ms} ms | Answer: {final_answer[:80]}...")
                        e2e_list.append(e2e_ms)
                        break
                    elif mtype == "error":
                        print(f"  [Error]: {msg}")
                        break
            finally:
                silence_stop.set()
                await silence_task

            # Keyword validation
            ans_lower = final_answer.lower()
            keyword_pass = any(kw in ans_lower for kw in item["expected_keywords"])

            results.append(
                {
                    "id": item["id"],
                    "query": item["text"],
                    "answer": final_answer,
                    "keyword_pass": keyword_pass,
                    "audio_chunks": audio_chunks_received,
                    "voice_to_voice_ttfb_ms": ttfb_ms if first_audio_time else None,
                    "e2e_turn_ms": e2e_ms if turn_complete_time else None,
                    "farewell": item.get("farewell", False),
                }
            )

        # Barge-in Interruption Test
        print("\n--- Testing Live Barge-In Interruption ---")
        await ws.send(json.dumps({"command": "reset"}))
        while True:
            init_reset = json.loads(await ws.recv())
            if init_reset.get("type") == "session_reset":
                break

        # Pre-synthesize BOTH questions so sending is instantaneous
        print("  Pre-synthesizing long question and interruption audio...")
        q_long = await _synthesize_question_pcm("Tell me about Ba Na Hills and the Golden Bridge")
        interruption_audio = await _synthesize_question_pcm("Wait, what about Dragon Bridge?")

        print("  Streaming question audio...")
        for i in range(0, len(q_long), chunk_size):
            await ws.send(q_long[i : i + chunk_size])
            await asyncio.sleep(0.04)

        barge_in_triggered = False
        t_barge_start = 0.0
        barge_in_latency_ms = None
        silence_chunk = bytes(chunk_size)

        # Stream silence until agent starts speaking
        while True:
            await ws.send(silence_chunk)
            try:
                msg_raw = await asyncio.wait_for(ws.recv(), timeout=0.1)
                msg = json.loads(msg_raw)
                if msg.get("type") == "audio_chunk":
                    print("  ⚡ Agent speaking! Immediately streaming interruption audio...")
                    t_barge_start = time.perf_counter()
                    for j in range(0, len(interruption_audio), chunk_size):
                        await ws.send(interruption_audio[j : j + chunk_size])
                        await asyncio.sleep(0.03)
                    break
            except asyncio.TimeoutError:
                pass

        # Wait for barge_in event
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if msg.get("type") == "barge_in":
                    barge_in_latency_ms = round((time.perf_counter() - t_barge_start) * 1000, 2)
                    barge_in_triggered = True
                    print(f"  ⚡ Barge-In Confirmed in {barge_in_latency_ms} ms!")
                    break
        except Exception:
            pass

    return {
        "status": "PASS" if all(r["keyword_pass"] for r in results) else "WARN",
        "turns": results,
        "metrics": {
            "voice_to_voice_ttfb_ms": _calc_stats(ttfb_list),
            "e2e_turn_ms": _calc_stats(e2e_list),
            "barge_in_test": {
                "success": barge_in_triggered,
                "latency_ms": barge_in_latency_ms,
            },
        },
    }


async def main():
    settings = get_settings()
    out_file = ROOT_DIR / "reports" / "realtime_eval.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ws_url = f"ws://{settings.app_host}:{settings.app_port}/ws/realtime"
    if settings.app_host == "0.0.0.0":
        ws_url = f"ws://127.0.0.1:{settings.app_port}/ws/realtime"

    try:
        report = await run_benchmark(ws_url)
        out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote benchmark report to: {out_file}")
        print(json.dumps(report["metrics"], indent=2))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
