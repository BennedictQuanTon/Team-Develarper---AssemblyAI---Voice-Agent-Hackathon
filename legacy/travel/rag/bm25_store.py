"""Persist and query a simple BM25 index aligned with Chroma chunk ids."""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class BM25Index:
    chunk_ids: list[str]
    corpus_tokens: list[list[str]]
    bm25: BM25Okapi

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        tokens = tokenize(query)
        if not tokens or not self.chunk_ids:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(
            zip(self.chunk_ids, scores, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        return [(chunk_id, float(score)) for chunk_id, score in ranked[:top_k] if score > 0]


def build_bm25_index(chunk_ids: list[str], texts: list[str]) -> BM25Index:
    if len(chunk_ids) != len(texts):
        raise ValueError("chunk_ids and texts must be the same length")
    corpus_tokens = [tokenize(text) for text in texts]
    return BM25Index(
        chunk_ids=list(chunk_ids),
        corpus_tokens=corpus_tokens,
        bm25=BM25Okapi(corpus_tokens),
    )


def save_bm25_index(index: BM25Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "chunk_ids": index.chunk_ids,
        "corpus_tokens": index.corpus_tokens,
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def load_bm25_index(path: Path) -> BM25Index:
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    return BM25Index(
        chunk_ids=payload["chunk_ids"],
        corpus_tokens=payload["corpus_tokens"],
        bm25=BM25Okapi(payload["corpus_tokens"]),
    )
