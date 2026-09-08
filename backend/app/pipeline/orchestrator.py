"""Turn orchestrator: stub ASR → real RAG → stub LLM → stub TTS + metrics."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.metrics.spans import MetricsWriter, TurnSpans, new_turn_id
from backend.app.pipeline.asr import StubASRClient
from backend.app.pipeline.base import ASRClient, LLMClient, TTSClient
from backend.app.pipeline.llm import StubLLMClient
from backend.app.pipeline.profiles import get_voice_profile
from backend.app.pipeline.tts import StubTTSClient
from rag.cache import RagCache
from rag.retrieve import hybrid_retrieve


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
        self.asr = asr or StubASRClient()
        self.llm = llm or StubLLMClient()
        self.tts = tts or StubTTSClient()
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
        top_k: int = 5,
    ) -> dict[str, Any]:
        t_e2e = time.perf_counter()
        profile = get_voice_profile(profile_name)
        turn_id = new_turn_id()

        # 1) ASR (stub)
        stt = await self.asr.transcribe_turn(
            audio_b64=audio_b64,
            mock_transcript=text,
        )
        query = stt.text

        # 2) RAG (real local hybrid)
        t_rag = time.perf_counter()
        retrieval = hybrid_retrieve(
            query,
            chroma_dir=self.settings.chroma_persist_dir,
            bm25_path=self.settings.bm25_index_path,
            top_k=top_k,
            cache=self.rag_cache if use_cache else None,
            use_cache=use_cache,
        )
        rag_ms = round((time.perf_counter() - t_rag) * 1000, 3)

        # 3) LLM (stub grounded)
        llm = await self.llm.generate(
            user_text=query,
            context_chunks=retrieval["chunks"],
            style_prompt=profile["style_prompt"],
            answer_template=profile["answer_template"],
        )

        # 4) TTS (stub beep)
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
                "stats": self.rag_cache.snapshot(),
            },
            chunk_ids=[c["chunk_id"] for c in retrieval["chunks"]],
        )
        self.metrics.write_turn(spans)

        return {
            "turn_id": turn_id,
            "session_id": session_id,
            "phase": 3,
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
            "cache": spans.cache,
            "metrics_file": str(self.metrics.path),
        }
