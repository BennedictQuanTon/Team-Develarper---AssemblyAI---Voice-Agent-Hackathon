"""Cache hit vs miss micro-benchmark for hybrid RAG."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from rag.cache import RagCache
from rag.retrieve import ask

DEFAULT_QUERIES = [
    "What is Ba Na Hills and the Golden Bridge?",
    "Best beaches in Da Nang My Khe",
    "How do I get from Da Nang airport to the city?",
    "Day trip to Hoi An from Da Nang",
    "When is the Dragon Bridge fire show?",
    "What food should I try mi Quang",
    "Is Da Nang good for families?",
    "Weather and best time to visit Da Nang",
]


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
    }


def run_bench(queries: list[str], rounds: int = 1) -> dict:
    settings = get_settings()

    cold_totals: list[float] = []
    warm_totals: list[float] = []
    cold_retrieval: list[float] = []
    warm_retrieval: list[float] = []
    answer_hits = 0
    answer_misses = 0
    details = []

    for query in queries:
        for _ in range(rounds):
            cache = RagCache()

            cold = ask(
                query,
                chroma_dir=settings.chroma_persist_dir,
                bm25_path=settings.bm25_index_path,
                cache=cache,
                use_cache=True,
            )
            warm = ask(
                query,
                chroma_dir=settings.chroma_persist_dir,
                bm25_path=settings.bm25_index_path,
                cache=cache,
                use_cache=True,
            )

            cold_totals.append(cold["timings_ms"]["total"])
            warm_totals.append(warm["timings_ms"]["total"])
            cold_retrieval.append(cold["timings_ms"]["retrieval"])
            warm_retrieval.append(warm["timings_ms"]["retrieval"])
            answer_misses += 1
            if warm.get("answer_cache_hit"):
                answer_hits += 1
            else:
                answer_misses += 1

            details.append(
                {
                    "query": query,
                    "cold_total_ms": cold["timings_ms"]["total"],
                    "warm_total_ms": warm["timings_ms"]["total"],
                    "cold_retrieval_ms": cold["timings_ms"]["retrieval"],
                    "warm_retrieval_ms": warm["timings_ms"]["retrieval"],
                    "warm_answer_cache_hit": warm.get("answer_cache_hit"),
                    "top_chunk": (cold["chunks"][0]["chunk_id"] if cold["chunks"] else None),
                }
            )

    cold = _stats(cold_totals)
    warm = _stats(warm_totals)
    return {
        "phase": 2,
        "query_count": len(queries),
        "rounds": rounds,
        "cold_total_ms": cold,
        "warm_total_ms": warm,
        "cold_retrieval_ms": _stats(cold_retrieval),
        "warm_retrieval_ms": _stats(warm_retrieval),
        "speedup_total_p50": (
            round(cold["p50"] / max(warm["p50"], 0.001), 2) if cold and warm else None
        ),
        "answer_cache": {
            "hits": answer_hits,
            "misses": answer_misses,
            "hit_rate": round(answer_hits / max(answer_hits + answer_misses, 1), 4),
        },
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG cache hit/miss benchmark")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    out_path = args.out or (settings.metrics_dir / "cache_bench_phase2.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = run_bench(DEFAULT_QUERIES, rounds=args.rounds)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "details"}, indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
