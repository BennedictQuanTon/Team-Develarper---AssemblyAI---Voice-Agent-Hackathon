from __future__ import annotations

import asyncio
import base64
import json
import time
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .config import settings
from .domain.restaurant.repository import SQLiteOrderRepository
from .domain.restaurant.models import KitchenDecision
from .domain.restaurant.store import get_lantern_store
from .domain.restaurant.workflow import OrderWorkflow
from .services.kitchen_events import KitchenEventBroker
from .services.realtime_session import RealtimeSession
from .providers.asr.assemblyai_stream import AssemblyAIRealtimeProvider, TranscriptEvent
from .providers.llm.ollama import OllamaClient
from .providers.tts.kokoro import KokoroProvider

app = FastAPI(title="The Lantern — Multilingual Voice Service")
store = get_lantern_store()
repository = SQLiteOrderRepository(settings.database_path)
workflow = OrderWorkflow(repository, store)
broker = KitchenEventBroker()
llm = OllamaClient(settings.ollama_base_url, settings.ollama_model, settings.ollama_thinking) if settings.llm_provider == "ollama" else None
tts = KokoroProvider(settings.kokoro_model_id, settings.kokoro_device)
provider_state = {"llm_warm": False, "tts_warm": False, "llm_error": None, "tts_error": None}


@app.on_event("startup")
async def warm_local_providers() -> None:
    if llm:
        try:
            await llm.warmup()
            provider_state["llm_warm"] = True
        except Exception as exc:  # noqa: BLE001
            provider_state["llm_error"] = str(exc)
    if settings.tts_provider == "kokoro":
        try:
            await asyncio.to_thread(tts.warmup, "en")
            provider_state["tts_warm"] = True
        except Exception as exc:  # noqa: BLE001
            provider_state["tts_error"] = str(exc)


@app.on_event("shutdown")
async def close_local_providers() -> None:
    if llm:
        await llm.close()
    repository.close()


class AvailabilityRequest(BaseModel):
    sku: str
    available: bool


@app.get("/health")
def health():
    return {"status": "ok", "service": "lantern"}


@app.get("/ready")
def ready():
    return {
        "ready": bool(provider_state["llm_warm"] and provider_state["tts_warm"] and settings.assemblyai_api_key),
        "providers": {"asr": settings.assemblyai_speech_model, "llm": settings.ollama_model, "tts": settings.kokoro_model_id},
        "provider_state": provider_state,
        "tts_device": tts.resolved_device,
    }


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
    return workflow.describe_order(order)


@app.get("/api/kitchen/orders")
def kitchen_orders():
    return {"orders": [workflow.describe_order(order) for order in repository.list_orders() if order["placed"]]}


@app.post("/api/kitchen/orders/{order_id}/decisions")
async def kitchen_decision(order_id: str, request: KitchenDecision):
    try:
        workflow.validate_kitchen_decision(order_id, request.model_dump())
        order = repository.decide(order_id, request.model_dump())
    except KeyError:
        raise HTTPException(404, "order not found")
    except ValueError as exc:
        current = repository.get_order(order_id)
        code = 409 if "stale revision" in str(exc) else 400
        raise HTTPException(code, {"detail": str(exc), "current_revision": current["current_revision"] if current else None})
    described = workflow.describe_order(order)
    broker.publish({"type": "kitchen_decision", "order": described, "decision": request.model_dump()})
    return described


def agent_turn_event(table_id: str, transcript: str, result: dict, first_audio_ms: float | None) -> dict:
    """One guest turn as the operations view shows it: what was heard, what the agent did, how fast.

    Drafts stay off the kitchen tickets, but the activity feed shows every turn so staff can
    follow a table while the guest is still ordering.
    """
    return {
        "type": "agent_turn",
        "table_id": table_id,
        "order_id": result.get("order_id"),
        "transcript": transcript,
        "language_code": result.get("language_code"),
        "status": result.get("status"),
        "response_text": result.get("response_text"),
        "items": [f"{line['quantity']} × {line['name']}" for line in result.get("basket") or []],
        "total": result.get("total"),
        "placed": bool(result.get("placed")),
        "pipeline_ms": result.get("pipeline_ms"),
        "voice_ttfb_ms": first_audio_ms,
        "at": time.time(),
    }


@app.websocket("/ws/realtime")
async def realtime(websocket: WebSocket):
    table_id = websocket.query_params.get("table_id")
    if not table_id or not store.get_table(table_id):
        await websocket.close(code=1008, reason="valid table_id is required")
        return
    resume_id = websocket.query_params.get("order_id")
    resumed = repository.get_order(resume_id) if resume_id else None
    if resumed and (resumed["table_id"] != table_id or resumed["status"] in {"cancelled", "ready", "rejected"}):
        resumed = None
    await websocket.accept()
    session = RealtimeSession(
        table_id, workflow, llm,
        session_id=resumed["guest_session_id"] if resumed else None,
        order_id=resumed["order_id"] if resumed else None,
    )
    send_lock = asyncio.Lock()
    cancel_event = asyncio.Event()
    response_task: asyncio.Task | None = None
    guest_queue = broker.subscribe()

    async def send(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def speak(result: dict, turn_cancel: asyncio.Event, started: float) -> float | None:
        response_text = result.get("response_text")
        if not response_text or not result.get("tts_supported"):
            return None
        first_audio_ms: float | None = None
        async for pcm_chunk in tts.stream_async(response_text, result.get("language_code", "en"), turn_cancel):
            if turn_cancel.is_set():
                break
            if first_audio_ms is None:
                first_audio_ms = round((time.perf_counter() - started) * 1000, 2)
            await send({
                "type": "audio_chunk", "pcm_b64": base64.b64encode(pcm_chunk).decode("ascii"),
                "sample_rate": tts.sample_rate, "language_code": result.get("language_code", "en"),
                "ttfb_ms": first_audio_ms,
            })
        return first_audio_ms

    async def deliver_transcript(text: str, language_code: str, turn_cancel: asyncio.Event) -> None:
        started = time.perf_counter()
        await send({"type": "final_transcript", "text": text, "language_code": language_code})
        await send({"type": "workflow_update", "status": "interpreting"})
        try:
            result = await session.handle_transcript(text, language_code)
            await send(result)
            # Drafts stay off the kitchen board, and readbacks that wrote nothing aren't re-published.
            if result.get("order_id") and result.get("wrote_revision") and result.get("placed"):
                broker.publish({"type": "order_update", "order": workflow.describe_order(repository.get_order(result["order_id"]))})
            first_audio_ms = await speak(result, turn_cancel, started)
            await send(
                {
                    "type": "turn_complete",
                    "pipeline_ms": result.get("pipeline_ms"),
                    "voice_ttfb_ms": first_audio_ms,
                }
            )
            broker.publish(agent_turn_event(table_id, text, result, first_audio_ms))
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            await send({"type": "error", "message": str(exc)})

    def start_response(text: str, language_code: str = "en") -> None:
        nonlocal response_task, cancel_event
        if response_task and not response_task.done():
            cancel_event.set()
            response_task.cancel()
        cancel_event = asyncio.Event()
        response_task = asyncio.create_task(deliver_transcript(text, language_code, cancel_event))

    async def on_transcript(event: TranscriptEvent) -> None:
        if not event.is_final:
            await send({"type": "interim_transcript", "text": event.text, "language_code": event.language_code})
            return
        start_response(event.text, event.language_code or "en")

    async def on_speech_started() -> None:
        cancel_event.set()
        if response_task and not response_task.done():
            response_task.cancel()
        await send({"type": "barge_in", "reason": "assemblyai_vad"})

    async def on_asr_error(message: str) -> None:
        await send({"type": "provider_unavailable", "provider": "assemblyai", "message": message})

    async def deliver_kitchen_events() -> None:
        nonlocal response_task, cancel_event
        while True:
            event = await guest_queue.get()
            if event.get("type") != "kitchen_decision":
                continue
            update = await session.handle_kitchen_decision(event["order"], event["decision"])
            if update is None:
                continue
            if response_task and not response_task.done():
                cancel_event.set()
                response_task.cancel()
            cancel_event = asyncio.Event()
            await send(update)
            try:
                await speak(update, cancel_event, time.perf_counter())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await send({"type": "provider_unavailable", "provider": "tts", "message": str(exc)})

    asr = None
    asr_ready = asyncio.Event()
    asr_connect_task: asyncio.Task | None = None
    if settings.assemblyai_api_key:
        asr = AssemblyAIRealtimeProvider(
            settings.assemblyai_api_key,
            store.keyterms(),
            on_transcript=on_transcript,
            on_speech_started=on_speech_started,
            on_error=on_asr_error,
        )

        async def connect_asr() -> None:
            try:
                # Room for the SDK's own retries: 3 attempts x CONNECT_TIMEOUT_S + 2 x 0.5 s.
                await asyncio.wait_for(asr.connect(), timeout=20)
                asr_ready.set()
                await send({"type": "provider_ready", "provider": "assemblyai"})
            except Exception as exc:  # noqa: BLE001
                await send({"type": "provider_unavailable", "provider": "assemblyai", "message": str(exc)})

        asr_connect_task = asyncio.create_task(connect_asr())
    guest_events_task = asyncio.create_task(deliver_kitchen_events())
    await send({"type": "session_ready", "table_id": table_id, "order": workflow.describe_order(resumed) if resumed else None, "providers": {"asr": settings.assemblyai_speech_model, "llm": settings.ollama_model, "tts": settings.kokoro_model_id}, "provider_state": provider_state, "asr_status": "connecting" if asr else "unavailable", "has_tts": provider_state["tts_warm"], "has_cartesia": False})
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("text"):
                payload = json.loads(message["text"])
                if payload.get("type") == "transcript":
                    start_response(payload.get("text", ""), payload.get("language_code", "en"))
                elif payload.get("type") in {"barge_in", "interrupt"}:
                    await on_speech_started()
            elif message.get("bytes"):
                if asr and asr_ready.is_set():
                    await asr.send_audio(message["bytes"])
                else:
                    await send({"type": "provider_unavailable", "provider": "assemblyai", "message": "speech recognition is not connected"})
    except WebSocketDisconnect:
        pass
    finally:
        cancel_event.set()
        if response_task and not response_task.done():
            response_task.cancel()
        if asr_connect_task and not asr_connect_task.done():
            asr_connect_task.cancel()
        guest_events_task.cancel()
        broker.unsubscribe(guest_queue)
        if asr and asr_ready.is_set():
            await asr.close()


@app.websocket("/ws/ops")
async def operations(websocket: WebSocket):
    await websocket.accept()
    queue = broker.subscribe()
    try:
        await websocket.send_json({"type": "kitchen_snapshot", "orders": [workflow.describe_order(order) for order in repository.list_orders() if order["placed"]]})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        broker.unsubscribe(queue)
