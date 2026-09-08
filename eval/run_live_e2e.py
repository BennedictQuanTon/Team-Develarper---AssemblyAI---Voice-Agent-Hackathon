"""Live E2E eval: text in → real Gemini + Cartesia audio out + RAG accuracy.

Usage (server optional — calls Orchestrator in-process):
  PYTHONPATH=. .venv/bin/python eval/run_live_e2e.py

Requires GEMINI_API_KEY + CARTESIA_API_KEY in .env. Does not print secrets.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import statistics
import sys
import wave
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from backend.app.pipeline.orchestrator import Orchestrator
from backend.app.pipeline.session import SessionStore
from rag.cache import RagCache
from rag.retrieve import hybrid_retrieve

QRELS_PATH = ROOT_DIR / "eval" / "datasets" / "rag_qrels.json"
LIVE_QUERY_IDS = ("q002", "q003", "q004")

GOLD_KEYWORDS: dict[str, list[str]] = {
    "q002": ["sunday", "saturday", "9"],
    "q003": ["taxi", "grab", "airport"],
    "q004": ["hoi an", "45", "30"],
}


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_idx], 3),
        "mean": round(statistics.mean(ordered), 3),
        "min": round(min(ordered), 3),
        "max": round(max(ordered), 3),
        "count": len(ordered),
    }


def _wav_duration_ms(b64: str) -> float:
    if not b64:
        return 0.0
    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate() or 1
        return round(1000.0 * frames / rate, 1)


def _token_overlap(answer: str, context: str) -> float:
    a = set(re.findall(r"[a-z0-9]+", (answer or "").lower()))
    c = set(re.findall(r"[a-z0-9]+", (context or "").lower()))
    if not a:
        return 0.0
    return round(len(a & c) / len(a), 4)


def _gold_hit(qid: str, answer: str) -> bool:
    text = (answer or "").lower()
    keys = GOLD_KEYWORDS.get(qid) or []
    return any(k in text for k in keys)


def run_rag_accuracy(settings, top_k: int = 3) -> dict:
    qrels = json.loads(QRELS_PATH.read_text(encoding="utf-8"))
    hits = 0
    details = []
    rag_ms: list[float] = []
    for row in qrels["queries"]:
        retrieval = hybrid_retrieve(
            row["question"],
            chroma_dir=settings.chroma_persist_dir,
            bm25_path=settings.bm25_index_path,
            top_k=top_k,
            use_cache=False,
        )
        ids = [c["chunk_id"] for c in retrieval["chunks"]]
        relevant = set(row["relevant_chunk_ids"])
        overlap = relevant.intersection(ids)
        ok = len(overlap) >= 1
        hits += int(ok)
        rag_ms.append(float(retrieval["timings_ms"]["total"]))
        details.append(
            {
                "id": row["id"],
                "hit": ok,
                "chunk_ids": ids,
                "overlap": sorted(overlap),
                "rag_ms": retrieval["timings_ms"]["total"],
            }
        )
    return {
        "recall_at_3_hits": hits,
        "recall_at_3_total": len(details),
        "recall_at_3_rate": round(hits / max(1, len(details)), 4),
        "rag_ms": _stats(rag_ms),
        "details": details,
    }


async def run_live_turns(settings, audio_dir: Path, use_cache: bool) -> dict:
    qrels = {q["id"]: q for q in json.loads(QRELS_PATH.read_text(encoding="utf-8"))["queries"]}
    orch = Orchestrator(
        settings=settings,
        rag_cache=RagCache(),
        sessions=SessionStore(),
    )
    session_id = "e2e-live"
    turns = []
    audio_dir.mkdir(parents=True, exist_ok=True)

    for qid in LIVE_QUERY_IDS:
        q = qrels[qid]
        result = await orch.run_turn(
            text=q["question"],
            session_id=session_id,
            use_cache=use_cache,
            manage_session=True,
        )
        dur = _wav_duration_ms((result.get("audio") or {}).get("b64") or "")
        path = audio_dir / f"{result['turn_id']}.wav"
        b64 = (result.get("audio") or {}).get("b64") or ""
        if b64:
            path.write_bytes(base64.b64decode(b64))
        ctx = " ".join(c.get("text") or "" for c in result.get("chunks") or [])
        turns.append(
            {
                "id": qid,
                "question": q["question"],
                "answer": result.get("answer"),
                "chunk_ids": [c["chunk_id"] for c in result.get("chunks") or []],
                "relevant_overlap": sorted(
                    set(q["relevant_chunk_ids"]).intersection(
                        c["chunk_id"] for c in result.get("chunks") or []
                    )
                ),
                "grounded_overlap": _token_overlap(result.get("answer") or "", ctx),
                "gold_keyword_hit": _gold_hit(qid, result.get("answer") or ""),
                "audio_duration_ms": dur,
                "audio_file": str(path.relative_to(ROOT_DIR)) if b64 else None,
                "audio_ok": dur > 400,
                "timings_ms": result.get("timings_ms"),
                "providers": result.get("providers"),
                "filler_id": result.get("filler_id"),
            }
        )

    farewell = await orch.run_turn(
        text="Thanks, that's all.",
        session_id=session_id,
        use_cache=False,
        manage_session=True,
    )
    fare = {
        "answer": farewell.get("answer"),
        "session_ended": farewell.get("session_ended"),
        "providers": farewell.get("providers"),
        "used_paid_apis": farewell.get("providers", {}).get("llm") not in {
            "local_farewell",
            "spoken_cache",
            "none",
        },
        "audio_duration_ms": _wav_duration_ms((farewell.get("audio") or {}).get("b64") or ""),
    }

    e2e = [float((t.get("timings_ms") or {}).get("e2e_turn_ms") or 0) for t in turns]
    rag = [float((t.get("timings_ms") or {}).get("rag_ms") or 0) for t in turns]
    llm = [float((t.get("timings_ms") or {}).get("llm_total_ms") or 0) for t in turns]
    tts = [float((t.get("timings_ms") or {}).get("tts_total_ms") or 0) for t in turns]
    perceived = [float((t.get("timings_ms") or {}).get("perceived_ttfb_ms") or 0) for t in turns]

    return {
        "turns": turns,
        "farewell": fare,
        "latency": {
            "e2e_turn_ms": _stats(e2e),
            "rag_ms": _stats(rag),
            "llm_total_ms": _stats(llm),
            "tts_total_ms": _stats(tts),
            "perceived_ttfb_ms": _stats(perceived),
        },
        "pass": {
            "audio_3_of_3": all(t["audio_ok"] for t in turns),
            "gold_keywords": all(t["gold_keyword_hit"] for t in turns),
            "grounded": all(t["grounded_overlap"] > 0 for t in turns),
            "farewell_local": fare["session_ended"] and not fare["used_paid_apis"],
        },
    }


def main() -> None:
    import asyncio

    parser = argparse.ArgumentParser(description="Live E2E latency + accuracy eval")
    parser.add_argument("--out", type=Path, default=ROOT_DIR / "reports" / "e2e_live.json")
    parser.add_argument("--audio-dir", type=Path, default=ROOT_DIR / "reports" / "audio")
    parser.add_argument("--use-cache", action="store_true", help="Allow spoken/retrieval cache")
    args = parser.parse_args()

    settings = get_settings()
    keys = settings.keys_configured
    if not (keys.get("gemini") and keys.get("cartesia")):
        print("ERROR: GEMINI_API_KEY and CARTESIA_API_KEY required in .env", file=sys.stderr)
        sys.exit(2)

    from rag.retrieve import warmup_retriever

    warm = warmup_retriever(
        chroma_dir=settings.chroma_persist_dir,
        bm25_path=settings.bm25_index_path,
    )
    rag_acc = run_rag_accuracy(settings)
    live = asyncio.run(run_live_turns(settings, args.audio_dir, use_cache=args.use_cache))

    report = {
        "phase": "e2e_live",
        "warmup_ms": warm,
        "rag_accuracy": rag_acc,
        "live": live,
        "pass_bar": {
            "rag_recall_at_least_6_of_8": rag_acc["recall_at_3_hits"] >= 6,
            **live["pass"],
        },
    }
    report["pass_bar"]["all"] = all(report["pass_bar"].values())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")
    sys.exit(0 if report["pass_bar"]["all"] else 1)


if __name__ == "__main__":
    main()
