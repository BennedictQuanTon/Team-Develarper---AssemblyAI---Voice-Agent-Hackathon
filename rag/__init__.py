"""RAG package: ingest (Phase 1), hybrid retrieve + cache (Phase 2)."""

from rag.cache import RagCache, normalize_query
from rag.chunking import Chunk, documents_to_chunks
from rag.retrieve import ask, extractive_answer, hybrid_retrieve, reciprocal_rank_fusion

__all__ = [
    "Chunk",
    "RagCache",
    "ask",
    "documents_to_chunks",
    "extractive_answer",
    "hybrid_retrieve",
    "normalize_query",
    "reciprocal_rank_fusion",
]
