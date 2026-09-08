from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.app.config import get_settings

app = FastAPI(
    title="Da Nang Realtime Voice Agent",
    description="AssemblyAI Realtime STT + custom RAG/LLM/TTS orchestration",
    version="0.0.1-phase0",
)


@app.get("/health")
def health() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "phase": 0,
            "service": "danang-realtime-voice-agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "keys_configured": settings.keys_configured,
            "voice_profile": settings.voice_profile,
            "assemblyai_mode": settings.assemblyai_mode,
            "gemini_model": settings.gemini_model,
        }
    )


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "Da Nang Realtime Voice Agent — Phase 0 scaffold",
        "health": "/health",
        "docs": "/docs",
    }
