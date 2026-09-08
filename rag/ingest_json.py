"""Ingest English Da Nang JSON into local Chroma + BM25."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from rag.bm25_store import build_bm25_index, save_bm25_index
from rag.chunking import documents_to_chunks

COLLECTION_NAME = "danang_en"


def load_documents(json_path: Path) -> list[dict[str, Any]]:
    with json_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError(f"No documents found in {json_path}")
    return documents


def get_embedding_function():
    """Local English embeddings via Chroma's default ONNX model (no API key, no torch)."""
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()


def ingest(
    *,
    json_path: Path,
    chroma_dir: Path,
    bm25_path: Path,
    reset: bool = True,
) -> dict[str, Any]:
    import chromadb

    documents = load_documents(json_path)
    chunks = documents_to_chunks(documents)

    if reset and chroma_dir.exists():
        shutil.rmtree(chroma_dir)
    chroma_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_dir))
    embedding_fn = get_embedding_function()

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [chunk.chunk_id for chunk in chunks]
    texts = [chunk.text for chunk in chunks]
    metadatas = [chunk.metadata for chunk in chunks]

    # Chroma prefers modest batches
    batch_size = 64
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        collection.upsert(
            ids=ids[start:end],
            documents=texts[start:end],
            metadatas=metadatas[start:end],
        )

    bm25_index = build_bm25_index(ids, texts)
    save_bm25_index(bm25_index, bm25_path)

    categories: dict[str, int] = {}
    for doc in documents:
        cat = str(doc.get("category") or "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "json_path": str(json_path),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "collection": COLLECTION_NAME,
        "chroma_persist_dir": str(chroma_dir),
        "bm25_index_path": str(bm25_path),
        "categories": dict(sorted(categories.items())),
        "peek_ids": ids[:5],
    }


def peek_collection(chroma_dir: Path, limit: int = 3) -> dict[str, Any]:
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=get_embedding_function(),
    )
    result = collection.peek(limit=limit)
    return {
        "count": collection.count(),
        "ids": result.get("ids", []),
        "documents": result.get("documents", []),
        "metadatas": result.get("metadatas", []),
    }


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Ingest Da Nang JSON into Chroma + BM25")
    parser.add_argument("--json", type=Path, default=settings.danang_json_path)
    parser.add_argument("--chroma-dir", type=Path, default=settings.chroma_persist_dir)
    parser.add_argument("--bm25-path", type=Path, default=settings.bm25_index_path)
    parser.add_argument("--no-reset", action="store_true", help="Do not wipe existing Chroma dir")
    parser.add_argument("--peek-only", action="store_true", help="Only peek existing collection")
    args = parser.parse_args()

    if args.peek_only:
        summary = peek_collection(args.chroma_dir)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    summary = ingest(
        json_path=args.json,
        chroma_dir=args.chroma_dir,
        bm25_path=args.bm25_path,
        reset=not args.no_reset,
    )
    peek = peek_collection(args.chroma_dir, limit=3)
    summary["peek"] = {
        "count": peek["count"],
        "sample": [
            {"id": i, "title": (m or {}).get("title"), "preview": (d or "")[:120]}
            for i, d, m in zip(
                peek["ids"],
                peek["documents"],
                peek["metadatas"],
                strict=False,
            )
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
