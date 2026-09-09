"""Comprehensive 3-Scenario Evaluation & Benchmark for Da Nang Voice Agent.

Tests 3 distinct conversational scenarios:
- Case 1: 4 Da Nang tourism questions with graded difficulty (Easy -> Medium -> Hard 1 -> Hard 2)
- Case 2: 2 New practical planning questions (Weather season + Neighborhood comparison)
- Case 3: 2 Out-Of-Domain questions (Paris attractions + Python binary search tree script)

Measures:
- Per-query and per-case latency (Voice-to-Voice TTFB, E2E turn, RAG latency)
- ASR transcription fidelity
- Grounded factual accuracy (Case 1 & 2) vs Safe Refusal / Hallucination prevention (Case 3)
- System phase breakdown averages

Outputs structured JSON to reports/multi_case_benchmark.json.
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

BENCHMARK_SUITE = {
    "case_1_danang_graded": {
        "title": "Case 1: 4 Câu hỏi Nội bộ Đà Nẵng (Phân cấp Dễ -> Trung bình -> Khó)",
        "description": "Kiểm tra độ sâu của kho tri thức Đà Nẵng với 4 mức độ từ cơ bản đến quy định chi tiết.",
        "turns": [
            {
                "id": "c1_q1_easy",
                "difficulty": "Dễ (Easy)",
                "topic": "Địa danh & Lịch biểu show nổi tiếng",
                "text": "When does the Dragon Bridge fire and water show happen?",
                "expected_keywords": ["saturday", "sunday", "9", "weekend", "evening"],
                "is_ood": False,
            },
            {
                "id": "c1_q2_medium",
                "difficulty": "Trung bình (Medium)",
                "topic": "Di chuyển & Chuẩn bị hành lý Bà Nà Hills",
                "text": "How do I get to Ba Na Hills from the city, and what should I bring?",
                "expected_keywords": ["cable car", "jacket", "minutes", "road", "mountain", "ba na", "elevation"],
                "is_ood": False,
            },
            {
                "id": "c1_q3_hard1",
                "difficulty": "Khó 1 (Hard - Quy định & Trang phục)",
                "topic": "Giá vé & Quy định trang phục Tượng Phật Bà Linh Ứng",
                "text": "What is the dress code and entrance fee for visiting Lady Buddha at Linh Ung Pagoda?",
                "expected_keywords": ["free", "modest", "shoulders", "knees", "pagoda"],
                "is_ood": False,
            },
            {
                "id": "c1_q4_hard2",
                "difficulty": "Khó 2 (Hard - Ẩm thực & Chế độ ăn chay)",
                "topic": "Ẩm thực chay địa phương & Từ khóa gọi món",
                "text": "Can I find vegetarian food in Da Nang, and what local word should I say to ask for it?",
                "expected_keywords": ["chay", "vegetarian", "pagoda", "tofu"],
                "is_ood": False,
            },
        ],
    },
    "case_2_new_questions": {
        "title": "Case 2: Bộ câu hỏi thực tế mới (2 câu)",
        "description": "Kiểm tra khả năng tư vấn thời tiết theo mùa và so sánh khu vực lưu trú.",
        "turns": [
            {
                "id": "c2_q1_weather",
                "difficulty": "Trung bình",
                "topic": "Thời tiết mùa khô tắm biển",
                "text": "What is the best time of year to visit Da Nang for dry beach weather?",
                "expected_keywords": ["february", "august", "dry", "beach", "rain", "swimming"],
                "is_ood": False,
            },
            {
                "id": "c2_q2_hotel",
                "difficulty": "Trung bình",
                "topic": "So sánh khu vực ở Biển Mỹ Khê vs Trung tâm Sông Hàn",
                "text": "Should I stay near My Khe beach or near the Han River city center?",
                "expected_keywords": ["beach", "my khe", "river", "city", "resort", "center", "han"],
                "is_ood": False,
            },
        ],
    },
    "case_3_out_of_domain": {
        "title": "Case 3: 2 câu hỏi HOÀN TOÀN NGOÀI CHỦ ĐỀ (Out-Of-Domain)",
        "description": "Kiểm tra Guardrails: Agent phải từ chối an toàn, không được bịa đặt (không hallucinate).",
        "turns": [
            {
                "id": "c3_q1_paris",
                "difficulty": "Out-of-domain (Địa lý khác)",
                "topic": "Du lịch Paris (Eiffel, Louvre)",
                "text": "What are the best places to visit in Paris, like the Eiffel Tower and the Louvre Museum?",
                "refusal_keywords": ["not know", "do not know", "da nang", "cannot", "only"],
                "is_ood": True,
            },
            {
                "id": "c3_q2_coding",
                "difficulty": "Out-of-domain (Lập trình)",
                "topic": "Viết script Python giải thuật BST",
                "text": "Can you write me a Python script to balance a binary search tree?",
                "refusal_keywords": ["not know", "do not know", "da nang", "cannot", "only"],
                "is_ood": True,
            },
        ],
    },
}


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


async def _synthesize_voice_pcm(text: str) -> bytes:
    """Synthesize human voice audio using Cartesia."""
    tts = CartesiaTTSClient()
    res = await tts.synthesize(
        text=text,
        voice_id="db6b0ed5-d5d3-463d-ae85-518a07d3c2b4",
        speaking_rate=1.0,
        style_prompt="natural and clear spoken English",
    )
    raw = base64.b64decode(res.audio_b64)
    if len(raw) > 44 and raw[:4] == b"RIFF":
        return raw[44:]
    return raw


async def run_single_case(case_id: str, case_info: dict, ws_url: str) -> dict:
    chunk_size = 3200  # 100ms
    turn_results = []
    case_ttfb: list[float] = []
    case_e2e: list[float] = []
    case_rag: list[float] = []

    print(f"\n{'=' * 75}")
    print(f"🚀 RUNNING: {case_info['title']}")
    print(f"   {case_info['description']}")
    print(f"{'=' * 75}")

    async with websockets.connect(ws_url) as ws:
        init_msg = json.loads(await ws.recv())
        session_id = init_msg.get("session_id")
        logger.info("Connected to %s | Session: %s", ws_url, session_id)

        for item in case_info["turns"]:
            q_id = item["id"]
            q_text = item["text"]
            is_ood = item.get("is_ood", False)

            print(f"\n[QUERY {q_id}] ({item['difficulty']})")
            print(f"  User speaks: \"{q_text}\"")

            # 1. Synthesize audio
            t0 = time.perf_counter()
            pcm = await _synthesize_voice_pcm(q_text)
            dur_s = round(len(pcm) / 32000.0, 2)
            print(f"  Audio generated ({dur_s}s). Streaming to WebSocket...")

            # 2. Stream audio
            t_stream_start = time.perf_counter()
            for i in range(0, len(pcm), chunk_size):
                await ws.send(pcm[i : i + chunk_size])
                await asyncio.sleep(0.04)
            t_speech_end = time.perf_counter()

            # 3. Stream ambient background silence while waiting for agent
            silence_chunk = bytes(chunk_size)
            stop_silence = asyncio.Event()

            async def _silence_loop():
                while not stop_silence.is_set():
                    try:
                        await ws.send(silence_chunk)
                        await asyncio.sleep(0.05)
                    except Exception:
                        break

            silence_task = asyncio.create_task(_silence_loop())

            asr_final_text = ""
            rag_ms = None
            chunk_ids = []
            first_audio_time = None
            turn_complete_time = None
            agent_answer = ""
            audio_chunks_count = 0

            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=12.0)
                    msg = json.loads(raw)
                    mtype = msg.get("type")

                    if mtype == "final_transcript":
                        asr_final_text = msg.get("text", "")
                        asr_delay = round((time.perf_counter() - t_speech_end) * 1000, 2)
                        print(f"    🎯 ASR Final: \"{asr_final_text}\" (took {asr_delay} ms)")
                    elif mtype == "rag_done":
                        rag_ms = msg.get("rag_ms", 0.0)
                        chunk_ids = msg.get("chunk_ids", [])
                        case_rag.append(rag_ms)
                        print(f"    📚 RAG Retrieved: {chunk_ids} ({rag_ms} ms)")
                    elif mtype == "audio_chunk":
                        audio_chunks_count += 1
                        if first_audio_time is None:
                            first_audio_time = time.perf_counter()
                            ttfb = round((first_audio_time - t_speech_end) * 1000, 2)
                            case_ttfb.append(ttfb)
                            print(f"    ⚡ Voice-to-Voice TTFB: {ttfb} ms")
                    elif mtype == "turn_complete":
                        turn_complete_time = time.perf_counter()
                        agent_answer = msg.get("answer", "")
                        e2e = round((turn_complete_time - t_speech_end) * 1000, 2)
                        case_e2e.append(e2e)
                        print(f"    🏁 Turn Complete: E2E = {e2e} ms")
                        print(f"    🗣️ Agent Answer: \"{agent_answer}\"")
                        break
                    elif mtype == "error":
                        print(f"    ❌ Error: {msg}")
                        break
            finally:
                stop_silence.set()
                await silence_task

            # Check correctness
            ans_lower = agent_answer.lower()
            if is_ood:
                # For Out-Of-Domain, safe refusal is SUCCESS. Hallucinating details is FAILURE!
                refused = any(rk in ans_lower for rk in item["refusal_keywords"])
                passed = refused
                status_str = "SAFE_REFUSAL_PASS" if passed else "HALLUCINATION_FAIL"
            else:
                passed = any(kw in ans_lower for kw in item["expected_keywords"])
                status_str = "FACTUAL_PASS" if passed else "FACTUAL_WARN"

            print(f"    Status: {status_str}")

            turn_results.append(
                {
                    "id": q_id,
                    "difficulty": item["difficulty"],
                    "topic": item["topic"],
                    "user_text": q_text,
                    "asr_transcript": asr_final_text,
                    "agent_answer": agent_answer,
                    "is_out_of_domain": is_ood,
                    "evaluation_status": status_str,
                    "passed": passed,
                    "voice_to_voice_ttfb_ms": round((first_audio_time - t_speech_end) * 1000, 2) if first_audio_time else None,
                    "e2e_turn_ms": round((turn_complete_time - t_speech_end) * 1000, 2) if turn_complete_time else None,
                    "rag_latency_ms": rag_ms,
                    "rag_chunks": chunk_ids,
                    "audio_chunks": audio_chunks_count,
                }
            )

    return {
        "case_id": case_id,
        "title": case_info["title"],
        "description": case_info["description"],
        "total_queries": len(turn_results),
        "passed_count": sum(1 for t in turn_results if t["passed"]),
        "pass_rate": round(sum(1 for t in turn_results if t["passed"]) / len(turn_results), 2),
        "metrics": {
            "voice_to_voice_ttfb_ms": _calc_stats(case_ttfb),
            "e2e_turn_ms": _calc_stats(case_e2e),
            "rag_latency_ms": _calc_stats(case_rag),
        },
        "turns": turn_results,
    }


async def main():
    settings = get_settings()
    out_file = ROOT_DIR / "reports" / "multi_case_benchmark.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ws_url = f"ws://{settings.app_host}:{settings.app_port}/ws/realtime"
    if settings.app_host == "0.0.0.0":
        ws_url = f"ws://127.0.0.1:{settings.app_port}/ws/realtime"

    all_cases = []
    for case_id, case_info in BENCHMARK_SUITE.items():
        case_res = await run_single_case(case_id, case_info, ws_url)
        all_cases.append(case_res)

    # Compute overall statistics
    all_ttfb = []
    all_e2e = []
    all_rag = []
    for c in all_cases:
        for t in c["turns"]:
            if t["voice_to_voice_ttfb_ms"]:
                all_ttfb.append(t["voice_to_voice_ttfb_ms"])
            if t["e2e_turn_ms"]:
                all_e2e.append(t["e2e_turn_ms"])
            if t["rag_latency_ms"]:
                all_rag.append(t["rag_latency_ms"])

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": all_cases,
        "overall": {
            "total_queries": sum(c["total_queries"] for c in all_cases),
            "total_passed": sum(c["passed_count"] for c in all_cases),
            "overall_pass_rate": round(sum(c["passed_count"] for c in all_cases) / sum(c["total_queries"] for c in all_cases), 3),
            "voice_to_voice_ttfb_ms": _calc_stats(all_ttfb),
            "e2e_turn_ms": _calc_stats(all_e2e),
            "rag_latency_ms": _calc_stats(all_rag),
        },
    }

    out_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'=' * 75}")
    print("🎯 BENCHMARK COMPLETED SUCCESSFULLY!")
    print(f"Results written to: {out_file}")
    print(f"Overall Pass Rate: {summary['overall']['total_passed']}/{summary['overall']['total_queries']} ({summary['overall']['overall_pass_rate'] * 100}%)")
    print(f"{'=' * 75}")


if __name__ == "__main__":
    asyncio.run(main())
