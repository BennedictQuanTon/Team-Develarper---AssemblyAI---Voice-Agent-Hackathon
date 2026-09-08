"""RAG package: Phase 1 ingest; Phase 2 hybrid retrieve + cache."""

from rag.chunking import Chunk, documents_to_chunks

__all__ = ["Chunk", "documents_to_chunks"]
