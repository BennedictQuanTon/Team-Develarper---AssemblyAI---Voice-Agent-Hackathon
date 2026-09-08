from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from rag.cache import RagCache
from rag.retrieve import ask, hybrid_retrieve
from rag.store import get_collection

app = FastAPI(
    title="Da Nang Realtime Voice Agent",
    description="AssemblyAI Realtime STT + custom RAG/LLM/TTS orchestration",
    version="0.2.0-phase2",
)


@lru_cache
def get_rag_cache() -> RagCache:
    return RagCache()


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    use_cache: bool = True


@app.get("/health")
def health() -> JSONResponse:
    settings = get_settings()
    chroma_count: int | None = None
    try:
        chroma_count = get_collection(settings.chroma_persist_dir).count()
    except Exception:
        chroma_count = None

    return JSONResponse(
        {
            "status": "ok",
            "phase": 2,
            "service": "danang-realtime-voice-agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "keys_configured": settings.keys_configured,
            "voice_profile": settings.voice_profile,
            "assemblyai_mode": settings.assemblyai_mode,
            "gemini_model": settings.gemini_model,
            "chroma_chunk_count": chroma_count,
            "cache": get_rag_cache().snapshot(),
        }
    )


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "Da Nang Realtime Voice Agent — Phase 2 hybrid RAG",
        "health": "/health",
        "ask": "POST /rag/ask",
        "retrieve": "POST /rag/retrieve",
        "docs": "/docs",
    }


@app.post("/rag/ask")
def rag_ask(body: AskRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        return ask(
            body.query,
            chroma_dir=settings.chroma_persist_dir,
            bm25_path=settings.bm25_index_path,
            cache=get_rag_cache() if body.use_cache else None,
            use_cache=body.use_cache,
            top_k=body.top_k,
        )
    except Exception as exc:  # noqa: BLE001 — surface ingest/missing-index errors clearly
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/rag/retrieve")
def rag_retrieve(body: AskRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        return hybrid_retrieve(
            body.query,
            chroma_dir=settings.chroma_persist_dir,
            bm25_path=settings.bm25_index_path,
            top_k=body.top_k,
            cache=get_rag_cache() if body.use_cache else None,
            use_cache=body.use_cache,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/rag/cache/clear")
def rag_cache_clear() -> dict[str, Any]:
    cache = get_rag_cache()
    cache.clear()
    return {"cleared": True, "cache": cache.snapshot()}
