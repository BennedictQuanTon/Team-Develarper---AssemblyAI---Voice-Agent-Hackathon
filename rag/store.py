"""Shared local Chroma + BM25 accessors."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection

from rag.bm25_store import BM25Index, load_bm25_index
from rag.ingest_json import COLLECTION_NAME, get_embedding_function


@lru_cache
def _client(chroma_dir: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=chroma_dir)


def get_collection(chroma_dir: Path) -> Collection:
    client = _client(str(chroma_dir.resolve()))
    return client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=get_embedding_function(),
    )


@lru_cache
def _bm25(path: str) -> BM25Index:
    return load_bm25_index(Path(path))


def get_bm25(bm25_path: Path) -> BM25Index:
    return _bm25(str(bm25_path.resolve()))


def clear_store_caches() -> None:
    _client.cache_clear()
    _bm25.cache_clear()
