"""Turn orchestrator: ASR → hybrid RAG → LLM → TTS + session + metrics."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.metrics.spans import MetricsWriter, TurnSpans, new_turn_id
from backend.app.pipeline.base import ASRClient, LLMClient, TTSClient
from backend.app.pipeline.factory import build_clients
from backend.app.pipeline.filler import CLOSING_TEXT, choose_filler_id, is_farewell
from backend.app.pipeline.profiles import get_voice_profile
from backend.app.pipeline.session import MAX_TURNS, SessionStore
from rag.cache import RagCache, cache_key, normalize_query
from rag.retrieve import DEFAULT_TOP_K, hybrid_retrieve

ROOT_DIR = Path(__file__).resolve().parents[3]
CLOSING_WAV = ROOT_DIR / "frontend" / "audio" / "backchannels" / "closing.wav"

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def first_utterance(text: str, *, max_words: int = 12) -> tuple[str, str]:
    """Split answer into first speakable chunk and remainder."""
    clean = " ".join((text or "").split()).strip()
    if not clean:
        return "", ""
    parts = _SENTENCE_END.split(clean, maxsplit=1)
    if len(parts) == 2 and parts[0].strip():
        first, rest = parts[0].strip(), parts[1].strip()
    else:
        words = clean.split()
        if len(words) <= max_words:
            return clean, ""
        first = " ".join(words[:max_words])
        rest = " ".join(words[max_words:])
    return first, rest


def _load_closing_audio() -> dict[str, Any]:
    raw = CLOSING_WAV.read_bytes() if CLOSING_WAV.exists() else b""
    return {
        "b64": base64.b64encode(raw).decode("ascii") if raw else "",
        "mime_type": "audio/wav",
        "sample_rate": 16000,
        "local": True,
    }


async def _emit(on_event: EventCallback | None, payload: dict[str, Any]) -> None:
    if on_event is None:
        return
    result = on_event(payload)
    if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
        await result  # type: ignore[arg-type]


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        asr: ASRClient | None = None,
        llm: LLMClient | None = None,
        tts: TTSClient | None = None,
        rag_cache: RagCache | None = None,
        metrics_path: Path | None = None,
        sessions: SessionStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        built = build_clients(self.settings)
        self.asr = asr or built["asr"]
        self.llm = llm or built["llm"]
        self.tts = tts or built["tts"]
        self.client_mode = built["mode"]
        self.rag_cache = rag_cache if rag_cache is not None else RagCache()
        self.sessions = sessions if sessions is not None else SessionStore()
        path = metrics_path or (self.settings.metrics_dir / "turns.jsonl")
        self.metrics = MetricsWriter(path)

    async def run_turn(
        self,
        *,
        text: str | None = None,
        audio_b64: str | None = None,
        profile_name: str | None = None,
        session_id: str = "default",
        use_cache: bool = True,
        top_k: int = DEFAULT_TOP_K,
        history: list[dict[str, str]] | None = None,
        last_turn: bool = False,
        on_event: EventCallback | None = None,
        manage_session: bool = True,
    ) -> dict[str, Any]:
        t_e2e = time.perf_counter()
        t_perceived = t_e2e
        profile = get_voice_profile(profile_name)
        turn_id = new_turn_id()
        session = self.sessions.get(session_id) if manage_session else None

        if manage_session and session is not None and session.ended:
            return {
                "turn_id": turn_id,
                "session_id": session_id,
                "phase": 5,
                "ended": True,
                "session": session.snapshot(),
                "transcript": (text or "").strip(),
                "answer": "This conversation has ended. Start a new one to keep chatting.",
                "chunks": [],
                "audio": {"b64": "", "mime_type": "audio/wav", "sample_rate": 16000},
                "timings_ms": {"e2e_turn_ms": 0.0},
                "providers": {"asr": "none", "llm": "none", "tts": "none"},
                "client_mode": self.client_mode,
                "cache": {"retrieval_cache_hit": False, "spoken_cache_hit": False},
                "filler_id": "ack",
            }

        # 1) ASR
        stt = await self.asr.transcribe_turn(
            audio_b64=audio_b64,
            mock_transcript=text,
        )
        query = stt.text

        filler_id = choose_filler_id(query)
        await _emit(
            on_event,
            {"type": "filler", "filler_id": filler_id, "stage": "processing"},
        )

        # Farewell early exit — local closer, no Gemini/Cartesia
        if manage_session and is_farewell(query):
            audio = _load_closing_audio()
            e2e_ms = round((time.perf_counter() - t_e2e) * 1000, 3)
            if session is not None:
                session = self.sessions.end(session_id)
            result = {
                "turn_id": turn_id,
                "session_id": session_id,
                "phase": 5,
                "profile": profile,
                "transcript": query,
                "answer": CLOSING_TEXT,
                "chunks": [],
                "audio": audio,
                "timings_ms": {
                    "stt_finalize_ms": stt.latency_ms,
                    "rag_ms": 0.0,
                    "llm_ttft_ms": 0.0,
                    "llm_total_ms": 0.0,
                    "tts_ttfb_ms": 0.0,
                    "tts_total_ms": 0.0,
                    "e2e_turn_ms": e2e_ms,
                    "perceived_ttfb_ms": round((time.perf_counter() - t_perceived) * 1000, 3),
                    "spoken_cache_hit": False,
                    "farewell": True,
                },
                "providers": {"asr": stt.provider, "llm": "local_farewell", "tts": "local_closing"},
                "client_mode": self.client_mode,
                "cache": {
                    "retrieval_cache_hit": False,
                    "spoken_cache_hit": False,
                    "stats": self.rag_cache.snapshot(),
                },
                "metrics_file": str(self.metrics.path),
                "filler_id": filler_id,
                "session": session.snapshot() if session else None,
                "session_ended": True,
                "turn_index": (session.turn_count if session else 0),
            }
            await _emit(on_event, {"type": "audio_chunk", "audio": audio, "partial": False})
            await _emit(
                on_event,
                {"type": "session", "session": result["session"], "ended": True},
            )
            spans = TurnSpans(
                turn_id=turn_id,
                session_id=session_id,
                query=query,
                transcript=query,
                answer=CLOSING_TEXT,
                profile=profile["name"],
                provider=result["providers"],
                timings_ms={
                    "stt_finalize_ms": stt.latency_ms,
                    "rag_ms": 0.0,
                    "llm_ttft_ms": 0.0,
                    "llm_total_ms": 0.0,
                    "tts_ttfb_ms": 0.0,
                    "tts_total_ms": 0.0,
                    "e2e_turn_ms": e2e_ms,
                },
                cache=result["cache"],
                chunk_ids=[],
                phase=5,
            )
            self.metrics.write_turn(spans)
            return result

        # Spoken-response cache
        spoken_key = cache_key("spoken", normalize_query(query), profile["name"], str(top_k))
        if use_cache:
            cached_spoken = self.rag_cache.spoken.get(spoken_key)
            if cached_spoken is not None:
                e2e_ms = round((time.perf_counter() - t_e2e) * 1000, 3)
                result = dict(cached_spoken)
                result["turn_id"] = turn_id
                result["session_id"] = session_id
                timings = dict(result.get("timings_ms") or {})
                timings["stt_finalize_ms"] = stt.latency_ms
                timings["e2e_turn_ms"] = e2e_ms
                timings["spoken_cache_hit"] = True
                timings["perceived_ttfb_ms"] = round((time.perf_counter() - t_perceived) * 1000, 3)
                result["timings_ms"] = timings
                result["cache"] = {
                    "retrieval_cache_hit": True,
                    "spoken_cache_hit": True,
                    "stats": self.rag_cache.snapshot(),
                }
                result["filler_id"] = filler_id
                result["providers"] = {
                    "asr": stt.provider,
                    "llm": "spoken_cache",
                    "tts": "spoken_cache",
                }
                # Session bookkeeping
                if manage_session and session is not None:
                    hist = list(session.history)
                    next_count = session.turn_count + 1
                    is_last = next_count >= MAX_TURNS
                    session = self.sessions.append_turn(
                        session_id, query, result.get("answer") or ""
                    )
                    result["session"] = session.snapshot()
                    result["session_ended"] = session.ended
                    result["turn_index"] = session.turn_count
                    result["last_turn"] = is_last
                await _emit(
                    on_event,
                    {"type": "audio_chunk", "audio": result.get("audio"), "partial": False},
                )
                spans = TurnSpans(
                    turn_id=turn_id,
                    session_id=session_id,
                    query=query,
                    transcript=query,
                    answer=result.get("answer") or "",
                    profile=profile["name"],
                    provider=result["providers"],
                    timings_ms={
                        "stt_finalize_ms": timings["stt_finalize_ms"],
                        "rag_ms": timings.get("rag_ms", 0.0),
                        "llm_ttft_ms": 0.0,
                        "llm_total_ms": 0.0,
                        "tts_ttfb_ms": 0.0,
                        "tts_total_ms": 0.0,
                        "e2e_turn_ms": e2e_ms,
                    },
                    cache=result["cache"],
                    chunk_ids=[c["chunk_id"] for c in result.get("chunks") or []],
                    phase=5,
                )
                self.metrics.write_turn(spans)
                return result

        # Session history / last-turn flags
        hist = history
        is_last = last_turn
        if manage_session and session is not None:
            hist = list(session.history[-2:])
            next_count = session.turn_count + 1
            is_last = next_count >= MAX_TURNS

        # 2) RAG
        t_rag = time.perf_counter()
        retrieval = await asyncio.to_thread(
            hybrid_retrieve,
            query,
            chroma_dir=self.settings.chroma_persist_dir,
            bm25_path=self.settings.bm25_index_path,
            top_k=top_k,
            cache=self.rag_cache if use_cache else None,
            use_cache=use_cache,
        )
        rag_ms = round((time.perf_counter() - t_rag) * 1000, 3)
        await _emit(
            on_event,
            {
                "type": "rag_done",
                "rag_ms": rag_ms,
                "chunk_ids": [c["chunk_id"] for c in retrieval["chunks"]],
                "cache_hit": bool(retrieval.get("cache_hit")),
            },
        )

        # 3) LLM — stream when event callback present, else one-shot
        t_llm = time.perf_counter()
        llm_text = ""
        llm_ttft = 0.0
        llm_provider = "gemini"
        streamed = False

        if on_event is not None and hasattr(self.llm, "stream"):
            streamed = True
            first_token = True
            async for piece in self.llm.stream(
                user_text=query,
                context_chunks=retrieval["chunks"],
                style_prompt=profile["style_prompt"],
                answer_template=profile["answer_template"],
                history=hist,
                last_turn=is_last,
            ):
                if first_token:
                    llm_ttft = round((time.perf_counter() - t_llm) * 1000, 3)
                    first_token = False
                llm_text += piece
            llm_provider = getattr(self.llm, "model", None) and "gemini" or "llm"
            # Detect stub
            if getattr(self.llm, "simulated_ttft_ms", None) is not None:
                llm_provider = "stub_llm"
        else:
            llm = await self.llm.generate(
                user_text=query,
                context_chunks=retrieval["chunks"],
                style_prompt=profile["style_prompt"],
                answer_template=profile["answer_template"],
                history=hist,
                last_turn=is_last,
            )
            llm_text = llm.text
            llm_ttft = llm.ttft_ms
            llm_provider = llm.provider
        llm_total = round((time.perf_counter() - t_llm) * 1000, 3)
        if not llm_ttft:
            llm_ttft = llm_total

        # 4) TTS — first utterance ASAP, optional second chunk
        first, rest = first_utterance(llm_text)
        t_tts = time.perf_counter()
        tts_first = await self.tts.synthesize(
            text=first or llm_text,
            voice_id=profile["cartesia_voice_id"],
            speaking_rate=profile["speaking_rate"],
            style_prompt=profile["style_prompt"],
        )
        perceived_ttfb = round((time.perf_counter() - t_perceived) * 1000, 3)
        await _emit(
            on_event,
            {
                "type": "audio_chunk",
                "audio": {
                    "b64": tts_first.audio_b64,
                    "mime_type": tts_first.mime_type,
                    "sample_rate": tts_first.sample_rate,
                },
                "partial": bool(rest),
                "text": first or llm_text,
            },
        )

        audio_b64_final = tts_first.audio_b64
        tts_provider = tts_first.provider
        tts_ttfb = tts_first.ttfb_ms
        # Optional second synthesize for remainder (max 2 calls)
        if rest:
            tts_rest = await self.tts.synthesize(
                text=rest,
                voice_id=profile["cartesia_voice_id"],
                speaking_rate=profile["speaking_rate"],
                style_prompt=profile["style_prompt"],
            )
            await _emit(
                on_event,
                {
                    "type": "audio_chunk",
                    "audio": {
                        "b64": tts_rest.audio_b64,
                        "mime_type": tts_rest.mime_type,
                        "sample_rate": tts_rest.sample_rate,
                    },
                    "partial": False,
                    "text": rest,
                },
            )
            # Keep first chunk as primary for HTTP clients; UI already played both
            audio_b64_final = tts_first.audio_b64
            tts_provider = tts_first.provider

        tts_total = round((time.perf_counter() - t_tts) * 1000, 3)
        e2e_ms = round((time.perf_counter() - t_e2e) * 1000, 3)

        if manage_session and session is not None:
            session = self.sessions.append_turn(session_id, query, llm_text)

        timings = {
            "stt_finalize_ms": stt.latency_ms,
            "rag_ms": rag_ms,
            "rag_internal_ms": retrieval.get("timings_ms", {}),
            "llm_ttft_ms": llm_ttft,
            "llm_total_ms": llm_total,
            "tts_ttfb_ms": tts_ttfb,
            "tts_total_ms": tts_total,
            "e2e_turn_ms": e2e_ms,
            "perceived_ttfb_ms": perceived_ttfb,
            "spoken_cache_hit": False,
            "streamed_llm": streamed,
        }

        providers = {
            "asr": stt.provider,
            "llm": llm_provider,
            "tts": tts_provider,
        }
        cache_info = {
            "retrieval_cache_hit": bool(retrieval.get("cache_hit")),
            "spoken_cache_hit": False,
            "stats": self.rag_cache.snapshot(),
        }

        spans = TurnSpans(
            turn_id=turn_id,
            session_id=session_id,
            query=query,
            transcript=query,
            answer=llm_text,
            profile=profile["name"],
            provider=providers,
            timings_ms={
                "stt_finalize_ms": timings["stt_finalize_ms"],
                "rag_ms": timings["rag_ms"],
                "llm_ttft_ms": timings["llm_ttft_ms"],
                "llm_total_ms": timings["llm_total_ms"],
                "tts_ttfb_ms": timings["tts_ttfb_ms"],
                "tts_total_ms": timings["tts_total_ms"],
                "e2e_turn_ms": timings["e2e_turn_ms"],
            },
            cache=cache_info,
            chunk_ids=[c["chunk_id"] for c in retrieval["chunks"]],
            phase=5,
        )
        self.metrics.write_turn(spans)

        result = {
            "turn_id": turn_id,
            "session_id": session_id,
            "phase": 5,
            "profile": profile,
            "transcript": query,
            "answer": llm_text,
            "chunks": retrieval["chunks"],
            "audio": {
                "b64": audio_b64_final,
                "mime_type": tts_first.mime_type,
                "sample_rate": tts_first.sample_rate,
            },
            "timings_ms": timings,
            "providers": providers,
            "client_mode": self.client_mode,
            "cache": cache_info,
            "metrics_file": str(self.metrics.path),
            "filler_id": filler_id,
            "session": session.snapshot() if session else None,
            "session_ended": bool(session.ended) if session else False,
            "turn_index": session.turn_count if session else None,
            "last_turn": is_last,
        }

        if use_cache:
            to_store = {
                "phase": result["phase"],
                "profile": result["profile"],
                "transcript": result["transcript"],
                "answer": result["answer"],
                "chunks": result["chunks"],
                "audio": result["audio"],
                "timings_ms": {
                    "rag_ms": rag_ms,
                    "rag_internal_ms": timings.get("rag_internal_ms", {}),
                },
                "providers": providers,
                "client_mode": self.client_mode,
                "metrics_file": str(self.metrics.path),
            }
            self.rag_cache.spoken.set(spoken_key, to_store)

        await _emit(
            on_event,
            {
                "type": "session",
                "session": result["session"],
                "ended": result["session_ended"],
            },
        )
        return result
