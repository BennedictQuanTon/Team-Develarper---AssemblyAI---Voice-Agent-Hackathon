from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.domain.lantern import get_lantern_store
from backend.app.metrics.spans import summarize_jsonl
from backend.app.pipeline.orchestrator import Orchestrator
from backend.app.pipeline.realtime_session import RealtimeSessionController
from backend.app.pipeline.session import SessionStore
from rag.cache import RagCache
from rag.retrieve import DEFAULT_TOP_K, ask, hybrid_retrieve, warmup_retriever
from rag.store import get_collection

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"


@lru_cache
def get_rag_cache() -> RagCache:
    return RagCache()


@lru_cache
def get_sessions() -> SessionStore:
    return SessionStore()


@lru_cache
def get_orchestrator() -> Orchestrator:
    get_settings.cache_clear()
    return Orchestrator(
        rag_cache=get_rag_cache(),
        settings=get_settings(),
        sessions=get_sessions(),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    try:
        warm = warmup_retriever(
            chroma_dir=settings.chroma_persist_dir,
            bm25_path=settings.bm25_index_path,
        )
        print(f"[startup] RAG warmup ok: {warm}")
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] RAG warmup skipped: {exc}")
    get_orchestrator()
    yield


app = FastAPI(
    title="Da Nang Realtime Voice Agent",
    description="AssemblyAI Realtime STT + custom RAG/LLM/TTS orchestration",
    version="0.5.0-realtime",
    lifespan=lifespan,
)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=20)
    use_cache: bool = True


class TurnRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    audio_b64: str | None = None
    profile: str | None = None
    session_id: str = "default"
    use_cache: bool = True
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=20)


@app.get("/health")
def health() -> JSONResponse:
    settings = get_settings()
    chroma_count: int | None = None
    try:
        chroma_count = get_collection(settings.chroma_persist_dir).count()
    except Exception:
        chroma_count = None

    metrics_path = settings.metrics_dir / "turns.jsonl"
    return JSONResponse(
        {
            "status": "ok",
            "phase": 5,
            "service": "danang-realtime-voice-agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "keys_configured": settings.keys_configured,
            "client_mode": get_orchestrator().client_mode,
            "voice_profile": settings.voice_profile,
            "assemblyai_mode": settings.assemblyai_mode,
            "gemini_model": settings.gemini_model,
            "chroma_chunk_count": chroma_count,
            "cache": get_rag_cache().snapshot(),
            "metrics_file": str(metrics_path),
            "ui": "/",
        }
    )


@app.get("/")
def root_page() -> FileResponse:
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html missing")
    return FileResponse(index)


@app.get("/api")
def api_root() -> dict[str, str]:
    return {
        "message": "The Lantern — voice waiter (realtime ordering + recommendations)",
        "health": "/health",
        "ui": "/",
        "guest": "/r/lantern",
        "menu": "GET /menu",
        "floor": "GET /floor",
        "ws": "WS /ws/realtime",
        "docs": "/docs",
    }


class MenuItemOut(BaseModel):
    sku: str
    name: str
    category: str
    price: float
    spicy_level: int
    available: bool
    description: str
    allergens: list[str]


@app.get("/menu")
def menu_list() -> dict[str, Any]:
    store = get_lantern_store()
    items = store.list_menu(available_only=False)
    return {
        "restaurant": "The Lantern",
        "count": len(items),
        "items": [i.as_dict() for i in items],
    }


@app.get("/menu/available")
def menu_available() -> dict[str, Any]:
    store = get_lantern_store()
    items = store.list_menu(available_only=True)
    return {"count": len(items), "items": [i.as_dict() for i in items]}


@app.get("/floor")
def floor() -> dict[str, Any]:
    store = get_lantern_store()
    tables = store.list_tables()
    return {
        "tables": [{"id": t.id, "name": t.name, "seats": t.seats, "status": t.status} for t in tables],
        "status_counts": store.free_table_statuses(),
    }


class SetAvailableRequest(BaseModel):
    sku: str
    available: bool


@app.post("/menu/set-available")
def menu_set_available(body: SetAvailableRequest) -> dict[str, Any]:
    store = get_lantern_store()
    item = store.set_available(body.sku, body.available)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Unknown SKU {body.sku}")
    return item.as_dict()


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
    except Exception as exc:  # noqa: BLE001
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


@app.post("/session/reset")
def session_reset(session_id: str = "default") -> dict[str, Any]:
    state = get_sessions().reset(session_id)
    return {"reset": True, "session": state.snapshot()}


@app.post("/turn")
async def turn(body: TurnRequest) -> dict[str, Any]:
    if not (body.text and body.text.strip()) and not body.audio_b64:
        raise HTTPException(status_code=400, detail="Provide text or audio_b64")
    try:
        turn_text = None if body.audio_b64 else body.text
        return await get_orchestrator().run_turn(
            text=turn_text,
            audio_b64=body.audio_b64,
            profile_name=body.profile,
            session_id=body.session_id,
            use_cache=body.use_cache,
            top_k=body.top_k,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/metrics/summary")
def metrics_summary() -> dict[str, Any]:
    settings = get_settings()
    return summarize_jsonl(settings.metrics_dir / "turns.jsonl")


@app.websocket("/ws/turn")
async def ws_turn(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            text = payload.get("text")
            audio_b64 = payload.get("audio_b64")
            if not (text and str(text).strip()) and not audio_b64:
                await websocket.send_json({"type": "error", "detail": "Provide text or audio_b64"})
                continue

            async def on_event(event: dict[str, Any]) -> None:
                await websocket.send_json(event)

            await websocket.send_json({"type": "status", "stage": "started"})
            # Prefer audio when both are present — text may be a stale caption.
            turn_text = None if audio_b64 else text
            result = await get_orchestrator().run_turn(
                text=turn_text,
                audio_b64=audio_b64,
                profile_name=payload.get("profile"),
                session_id=str(payload.get("session_id") or "ws"),
                use_cache=bool(payload.get("use_cache", True)),
                top_k=int(payload.get("top_k") or DEFAULT_TOP_K),
                on_event=on_event,
            )
            await websocket.send_json({"type": "final", **result})
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            return


@app.websocket("/ws/realtime")
async def ws_realtime(websocket: WebSocket) -> None:
    """Full-duplex real-time streaming endpoint: bi-directional audio + live barge-in."""
    await websocket.accept()
    controller = RealtimeSessionController(
        websocket,
        settings=get_settings(),
        rag_cache=get_rag_cache(),
        sessions=get_sessions(),
    )
    try:
        await controller.start()
        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"]:
                await controller.handle_pcm_audio(message["bytes"])
            elif "text" in message and message["text"]:
                try:
                    import json
                    payload = json.loads(message["text"])
                    if isinstance(payload, dict):
                        await controller.handle_text_command(payload)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[ws/realtime error] {exc}")
    finally:
        await controller.close()
