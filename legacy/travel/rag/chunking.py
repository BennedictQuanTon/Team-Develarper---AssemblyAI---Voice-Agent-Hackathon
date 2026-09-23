"""Chunk English Da Nang documents for RAG ingest."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    parent_doc_id: str
    text: str
    metadata: dict[str, Any]


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]

    sentences = _SENTENCE_SPLIT.split(text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(sentence) <= max_chars:
            current = sentence
        else:
            start = 0
            while start < len(sentence):
                end = min(start + max_chars, len(sentence))
                chunks.append(sentence[start:end].strip())
                if end >= len(sentence):
                    break
                start = max(0, end - overlap_chars)
            current = ""

    if current:
        chunks.append(current)

    if overlap_chars > 0 and len(chunks) > 1:
        overlapped: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap_chars:]
            piece = f"{prev_tail} {chunks[i]}".strip()
            overlapped.append(piece[: max_chars + overlap_chars])
        return overlapped

    return chunks


def documents_to_chunks(
    documents: list[dict[str, Any]],
    *,
    max_chars: int = 900,
    overlap_chars: int = 120,
) -> list[Chunk]:
    """Prefer one document → one chunk; split only when content exceeds max_chars."""
    chunks: list[Chunk] = []

    for doc in documents:
        doc_id = str(doc["id"])
        title = str(doc.get("title") or "")
        content = str(doc.get("content") or "").strip()
        if not content:
            continue

        body = f"{title}. {content}".strip() if title else content
        parts = _split_long_text(body, max_chars=max_chars, overlap_chars=overlap_chars)

        base_meta = {
            "parent_doc_id": doc_id,
            "category": str(doc.get("category") or ""),
            "place_name": str(doc.get("place_name") or ""),
            "title": title,
            "source": str(doc.get("source") or "curated"),
            "updated_at": str(doc.get("updated_at") or ""),
            "tags": ",".join(doc.get("tags") or []),
        }

        for index, part in enumerate(parts):
            chunk_id = doc_id if len(parts) == 1 else f"{doc_id}__{index}"
            meta = {**base_meta, "chunk_index": index, "chunk_count": len(parts)}
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    parent_doc_id=doc_id,
                    text=part,
                    metadata=meta,
                )
            )

    return chunks
