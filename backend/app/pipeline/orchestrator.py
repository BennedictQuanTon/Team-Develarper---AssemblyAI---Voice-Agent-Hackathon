"""Turn orchestrator: ASR → hybrid RAG → LLM → TTS + metrics."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.metrics.spans import MetricsWriter, TurnSpans, new_turn_id
from backend.app.pipeline.base import ASRClient, LLMClient, TTSClient
from backend.app.pipeline.factory import build_clients
from backend.app.pipeline.profiles import get_voice_profile
from rag.cache import RagCache, cache_key, normalize_query
from rag.retrieve import DEFAULT_TOP_K, hybrid_retrieve


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
    ) -> None:
        self.settings = settings or get_settings()
        built = build_clients(self.settings)
        self.asr = asr or built["asr"]
        self.llm = llm or built["llm"]
        self.tts = tts or built["tts"]
        self.client_mode = built["mode"]
        self.rag_cache = rag_cache if rag_cache is not None else RagCache()
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
    ) -> dict[str, Any]:
        t_e2e = time.perf_counter()
        profile = get_voice_profile(profile_name)
        turn_id = new_turn_id()

        # 1) ASR — live only when audio is sent; typed text skips live ASR (saves cost)
        stt = await self.asr.transcribe_turn(
            audio_b64=audio_b64,
            mock_transcript=text,
        )
        query = stt.text

        # Spoken-response cache: skip Gemini + Cartesia on exact (query, profile) repeats
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
                result["timings_ms"] = timings
                result["cache"] = {
                    "retrieval_cache_hit": True,
                    "spoken_cache_hit": True,
                    "stats": self.rag_cache.snapshot(),
                }
                spans = TurnSpans(
                    turn_id=turn_id,
                    session_id=session_id,
                    query=query,
                    transcript=query,
                    answer=result.get("answer") or "",
                    profile=profile["name"],
                    provider={
                        "asr": stt.provider,
                        "llm": "spoken_cache",
                        "tts": "spoken_cache",
                    },
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
                )
                self.metrics.write_turn(spans)
                result["providers"] = spans.provider
                return result

        # 2) RAG (local hybrid) — off event loop so ONNX/BM25 do not block WS
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

        # 3) LLM (Gemini when key present)
        llm = await self.llm.generate(
            user_text=query,
            context_chunks=retrieval["chunks"],
            style_prompt=profile["style_prompt"],
            answer_template=profile["answer_template"],
        )

        # 4) TTS (Cartesia when key present)
        tts = await self.tts.synthesize(
            text=llm.text,
            voice_id=profile["cartesia_voice_id"],
            speaking_rate=profile["speaking_rate"],
            style_prompt=profile["style_prompt"],
        )

        e2e_ms = round((time.perf_counter() - t_e2e) * 1000, 3)
        timings = {
            "stt_finalize_ms": stt.latency_ms,
            "rag_ms": rag_ms,
            "rag_internal_ms": retrieval.get("timings_ms", {}),
            "llm_ttft_ms": llm.ttft_ms,
            "llm_total_ms": llm.latency_ms,
            "tts_ttfb_ms": tts.ttfb_ms,
            "tts_total_ms": tts.latency_ms,
            "e2e_turn_ms": e2e_ms,
            "spoken_cache_hit": False,
        }

        spans = TurnSpans(
            turn_id=turn_id,
            session_id=session_id,
            query=query,
            transcript=query,
            answer=llm.text,
            profile=profile["name"],
            provider={
                "asr": stt.provider,
                "llm": llm.provider,
                "tts": tts.provider,
            },
            timings_ms={
                "stt_finalize_ms": timings["stt_finalize_ms"],
                "rag_ms": timings["rag_ms"],
                "llm_ttft_ms": timings["llm_ttft_ms"],
                "llm_total_ms": timings["llm_total_ms"],
                "tts_ttfb_ms": timings["tts_ttfb_ms"],
                "tts_total_ms": timings["tts_total_ms"],
                "e2e_turn_ms": timings["e2e_turn_ms"],
            },
            cache={
                "retrieval_cache_hit": bool(retrieval.get("cache_hit")),
                "spoken_cache_hit": False,
                "stats": self.rag_cache.snapshot(),
            },
            chunk_ids=[c["chunk_id"] for c in retrieval["chunks"]],
        )
        self.metrics.write_turn(spans)

        result = {
            "turn_id": turn_id,
            "session_id": session_id,
            "phase": 4,
            "profile": profile,
            "transcript": query,
            "answer": llm.text,
            "chunks": retrieval["chunks"],
            "audio": {
                "b64": tts.audio_b64,
                "mime_type": tts.mime_type,
                "sample_rate": tts.sample_rate,
            },
            "timings_ms": timings,
            "providers": spans.provider,
            "client_mode": self.client_mode,
            "cache": spans.cache,
            "metrics_file": str(self.metrics.path),
        }

        if use_cache:
            # Store a lean copy for spoken replay (no turn_id / session_id)
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
                "providers": spans.provider,
                "client_mode": self.client_mode,
                "metrics_file": str(self.metrics.path),
            }
            self.rag_cache.spoken.set(spoken_key, to_store)

        return result
