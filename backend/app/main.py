from __future__ import annotations

import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .config import settings
from .domain.restaurant.repository import SQLiteOrderRepository
from .domain.restaurant.store import get_lantern_store
from .domain.restaurant.workflow import OrderWorkflow
from .services.kitchen_events import KitchenEventBroker
from .services.realtime_session import RealtimeSession
from .providers.llm.ollama import OllamaClient

app = FastAPI(title="The Lantern — Multilingual Voice Service")
store = get_lantern_store()
repository = SQLiteOrderRepository(settings.database_path)
workflow = OrderWorkflow(repository, store)
broker = KitchenEventBroker()
llm = OllamaClient(settings.ollama_base_url, settings.ollama_model, settings.ollama_thinking) if settings.llm_provider == "ollama" else None


class DecisionRequest(BaseModel):
    expected_revision: int
    action: str
    eta_minutes: int | None = None
    substitutions: list[dict] = []
    reason: str | None = None
    actor: str = "kitchen"


class AvailabilityRequest(BaseModel):
    sku: str
    available: bool


@app.get("/health")
def health():
    return {"status": "ok", "service": "lantern"}


@app.get("/ready")
def ready():
    return {"ready": True, "providers": {"asr": settings.assemblyai_speech_model, "llm": settings.ollama_model, "tts": settings.kokoro_model_id}}


@app.get("/api")
def api_info():
    return {"name": "The Lantern", "architecture": "multilingual-local-voice", "version": "1.0"}


@app.get("/menu")
def menu(available_only: bool = False):
    return {"items": [i.as_dict() for i in store.list_menu(available_only=available_only)]}


@app.get("/menu/available")
def available_menu():
    return menu(True)


@app.get("/floor")
def floor():
    return {"tables": [t.__dict__ for t in store.list_tables()]}


@app.post("/menu/set-available")
def set_available(request: AvailabilityRequest):
    item = store.set_available(request.sku, request.available)
    if item is None:
        raise HTTPException(404, "menu item not found")
    return item.as_dict()


@app.get("/metrics")
def metrics():
    return {"service": "lantern", "orders": len(repository.list_orders()), "provider_model": settings.assemblyai_speech_model}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    order = repository.get_order(order_id)
    if not order:
        raise HTTPException(404, "order not found")
    return order


@app.get("/api/kitchen/orders")
def kitchen_orders():
    return {"orders": repository.list_orders()}


@app.post("/api/kitchen/orders/{order_id}/decisions")
def kitchen_decision(order_id: str, request: DecisionRequest):
    try:
        order = repository.decide(order_id, request.model_dump())
    except KeyError:
        raise HTTPException(404, "order not found")
    except ValueError as exc:
        current = repository.get_order(order_id)
        raise HTTPException(409, {"detail": str(exc), "current_revision": current["current_revision"] if current else None})
    broker.publish({"type": "workflow_update", "order": order})
    return order


@app.websocket("/ws/realtime")
async def realtime(websocket: WebSocket):
    table_id = websocket.query_params.get("table_id")
    if not table_id or not store.get_table(table_id):
        await websocket.close(code=1008, reason="valid table_id is required")
        return
    await websocket.accept()
    session = RealtimeSession(table_id, workflow, llm)
    await websocket.send_json({"type": "session_ready", "table_id": table_id, "providers": {"asr": settings.assemblyai_speech_model, "llm": settings.ollama_model, "tts": settings.kokoro_model_id}, "has_tts": settings.tts_provider == "kokoro", "has_cartesia": False})
    try:
        while True:
            message = await websocket.receive()
            if message.get("text"):
                payload = json.loads(message["text"])
                if payload.get("type") == "transcript":
                    result = await session.handle_transcript(payload.get("text", ""), payload.get("language_code", "en"))
                    await websocket.send_json(result)
            elif message.get("bytes"):
                await websocket.send_json({"type": "audio_received", "sample_rate": 16000})
    except WebSocketDisconnect:
        return


@app.websocket("/ws/ops")
async def operations(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "kitchen_snapshot", "orders": repository.list_orders()})
    queue = broker.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        broker.unsubscribe(queue)
