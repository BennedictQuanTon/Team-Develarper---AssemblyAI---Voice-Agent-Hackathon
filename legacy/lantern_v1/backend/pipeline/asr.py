"""Stub ASR — no AssemblyAI key. Uses mock/client transcript + simulated latency."""

from __future__ import annotations

import asyncio
import time

from backend.app.pipeline.base import TranscriptResult


class StubASRClient:
    def __init__(self, simulated_latency_ms: float = 45.0) -> None:
        self.simulated_latency_ms = simulated_latency_ms

    async def transcribe_turn(
        self,
        *,
        audio_b64: str | None = None,
        mock_transcript: str | None = None,
    ) -> TranscriptResult:
        t0 = time.perf_counter()
        await asyncio.sleep(self.simulated_latency_ms / 1000.0)
        text = (mock_transcript or "").strip()
        if not text:
            text = (
                "Tell me about Ba Na Hills and the Golden Bridge"
                if audio_b64
                else ""
            )
        if not text:
            raise ValueError("StubASR requires mock_transcript or audio (demo fallback)")
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        return TranscriptResult(
            text=text,
            latency_ms=elapsed,
            provider="stub_asr",
            is_final=True,
            raw={"had_audio": bool(audio_b64), "phase": 3},
        )
