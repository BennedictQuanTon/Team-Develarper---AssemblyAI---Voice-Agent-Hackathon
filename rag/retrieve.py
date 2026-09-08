"""Hybrid retrieval: dense Chroma + BM25 fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rag.bm25_store import BM25Index
from rag.cache import RagCache, cache_key, normalize_query, timed_ms
from rag.store import get_bm25, get_collection

# Tuned for voice turns: smaller candidate set → faster embed+fuse + shorter LLM context
DEFAULT_TOP_N = 8
DEFAULT_TOP_K = 3


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any]
    sources: tuple[str, ...]


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must match ranked_lists length")

    scores: dict[str, float] = {}
    for weight, ranked in zip(weights, ranked_lists, strict=True):
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight * (1.0 / (k + rank + 1))

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _dense_search(collection, query: str, top_n: int) -> list[tuple[str, float, str, dict]]:
    result = collection.query(
        query_texts=[query],
        n_results=top_n,
        include=["documents", "metadatas", "distances"],
    )
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    rows: list[tuple[str, float, str, dict]] = []
    for doc_id, doc, meta, dist in zip(ids, docs, metas, dists, strict=False):
        # cosine distance → similarity-ish score for display
        score = 1.0 / (1.0 + float(dist))
        rows.append((doc_id, score, doc or "", meta or {}))
    return rows


def _bm25_search(index: BM25Index, query: str, top_n: int) -> list[tuple[str, float]]:
    return index.search(query, top_k=top_n)


def hybrid_retrieve(
    query: str,
    *,
    chroma_dir: Path,
    bm25_path: Path,
    top_n: int = DEFAULT_TOP_N,
    top_k: int = DEFAULT_TOP_K,
    dense_weight: float = 0.7,
    bm25_weight: float = 0.3,
    rrf_k: int = 60,
    cache: RagCache | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return fused top_k chunks with per-stage timing."""
    q_norm = normalize_query(query)
    key = cache_key("retrieval", q_norm, str(top_n), str(top_k), str(dense_weight), str(bm25_weight))

    t0 = time.perf_counter()
    if use_cache and cache is not None:
        cached = cache.retrieval.get(key)
        if cached is not None:
            payload = dict(cached)
            payload["cache_hit"] = True
            payload["timings_ms"] = {
                "total": timed_ms(t0),
                "dense": 0.0,
                "bm25": 0.0,
                "fusion": 0.0,
            }
            return payload

    collection = get_collection(chroma_dir)
    bm25_index = get_bm25(bm25_path)

    # Dense (ONNX embed) and BM25 run in parallel — both are CPU-bound and independent
    t_parallel = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        dense_fut = pool.submit(_dense_search, collection, query, top_n)
        bm25_fut = pool.submit(_bm25_search, bm25_index, query, top_n)
        dense_rows = dense_fut.result()
        bm25_rows = bm25_fut.result()
    parallel_ms = timed_ms(t_parallel)
    # Approximate split for metrics (wall clock is parallel_ms; report both under same wall)
    dense_ms = parallel_ms
    bm25_ms = parallel_ms

    t_fuse = time.perf_counter()
    dense_ids = [row[0] for row in dense_rows]
    bm25_ids = [row[0] for row in bm25_rows]
    fused = reciprocal_rank_fusion(
        [dense_ids, bm25_ids],
        weights=[dense_weight, bm25_weight],
        k=rrf_k,
    )[:top_k]
    fusion_ms = timed_ms(t_fuse)

    dense_map = {row[0]: row for row in dense_rows}
    # BM25-only hits need document text from Chroma get
    missing_ids = [doc_id for doc_id, _ in fused if doc_id not in dense_map]
    fetched: dict[str, tuple[str, dict]] = {}
    if missing_ids:
        got = collection.get(ids=missing_ids, include=["documents", "metadatas"])
        for doc_id, doc, meta in zip(
            got.get("ids") or [],
            got.get("documents") or [],
            got.get("metadatas") or [],
            strict=False,
        ):
            fetched[doc_id] = (doc or "", meta or {})

    bm25_score_map = {doc_id: score for doc_id, score in bm25_rows}
    chunks: list[RetrievedChunk] = []
    for doc_id, rrf_score in fused:
        sources: list[str] = []
        if doc_id in dense_map:
            sources.append("dense")
            _, _, text, meta = dense_map[doc_id]
        else:
            text, meta = fetched.get(doc_id, ("", {}))
        if doc_id in bm25_score_map:
            sources.append("bm25")
        chunks.append(
            RetrievedChunk(
                chunk_id=doc_id,
                text=text,
                score=float(rrf_score),
                metadata=meta,
                sources=tuple(sources),
            )
        )

    payload = {
        "query": query,
        "cache_hit": False,
        "chunks": [asdict(chunk) for chunk in chunks],
        "debug": {
            "dense_ids": dense_ids[:10],
            "bm25_ids": bm25_ids[:10],
            "dense_weight": dense_weight,
            "bm25_weight": bm25_weight,
        },
        "timings_ms": {
            "total": timed_ms(t0),
            "dense": dense_ms,
            "bm25": bm25_ms,
            "fusion": fusion_ms,
        },
    }

    if use_cache and cache is not None:
        cache.retrieval.set(key, {k: v for k, v in payload.items() if k != "timings_ms"})

    return payload


def extractive_answer(chunks: list[dict[str, Any]], *, max_chars: int = 420) -> str:
    """Phase 2 text demo without LLM: concatenate top chunks into a short spoken-style blurb."""
    if not chunks:
        return "I don't have enough information about that in the Da Nang guide yet."

    parts: list[str] = []
    used = 0
    for chunk in chunks[:3]:
        text = " ".join(str(chunk.get("text") or "").split())
        title = (chunk.get("metadata") or {}).get("title") or chunk.get("chunk_id")
        piece = f"{title}: {text}"
        if used + len(piece) > max_chars and parts:
            break
        parts.append(piece)
        used += len(piece)

    joined = " ".join(parts)
    if len(joined) > max_chars:
        joined = joined[: max_chars - 3].rstrip() + "..."
    return joined


def warmup_retriever(*, chroma_dir: Path, bm25_path: Path) -> dict[str, float]:
    """Load Chroma/ONNX + BM25 and run one dummy query to avoid cold first-turn latency."""
    t0 = time.perf_counter()
    collection = get_collection(chroma_dir)
    get_bm25(bm25_path)
    # Force embedding model load
    _ = collection.query(query_texts=["Da Nang travel"], n_results=1, include=["documents"])
    return {"warmup_ms": timed_ms(t0)}


def ask(
    query: str,
    *,
    chroma_dir: Path,
    bm25_path: Path,
    cache: RagCache | None = None,
    use_cache: bool = True,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Retrieve + extractive answer with answer-level cache."""
    q_norm = normalize_query(query)
    answer_key = cache_key("answer", q_norm, str(top_k))
    t0 = time.perf_counter()

    if use_cache and cache is not None:
        cached = cache.answer.get(answer_key)
        if cached is not None:
            out = dict(cached)
            out["cache_hit"] = True
            out["answer_cache_hit"] = True
            out["timings_ms"] = {"total": timed_ms(t0), "retrieval": 0.0, "answer": 0.0}
            return out

    retrieval = hybrid_retrieve(
        query,
        chroma_dir=chroma_dir,
        bm25_path=bm25_path,
        top_k=top_k,
        cache=cache,
        use_cache=use_cache,
    )
    t_ans = time.perf_counter()
    answer = extractive_answer(retrieval["chunks"])
    answer_ms = timed_ms(t_ans)

    out = {
        "query": query,
        "answer": answer,
        "cache_hit": False,
        "answer_cache_hit": False,
        "retrieval_cache_hit": bool(retrieval.get("cache_hit")),
        "chunks": retrieval["chunks"],
        "debug": retrieval.get("debug", {}),
        "timings_ms": {
            "total": timed_ms(t0),
            "retrieval": retrieval["timings_ms"]["total"],
            "dense": retrieval["timings_ms"]["dense"],
            "bm25": retrieval["timings_ms"]["bm25"],
            "fusion": retrieval["timings_ms"]["fusion"],
            "answer": answer_ms,
        },
        "cache_stats": cache.snapshot() if cache is not None else None,
    }

    if use_cache and cache is not None:
        to_store = {
            "query": out["query"],
            "answer": out["answer"],
            "chunks": out["chunks"],
            "debug": out["debug"],
        }
        cache.answer.set(answer_key, to_store)

    return out
