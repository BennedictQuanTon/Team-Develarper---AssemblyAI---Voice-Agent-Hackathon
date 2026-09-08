"""Live AssemblyAI ASR — used when audio is provided; text turns skip this."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time
from pathlib import Path

from backend.app.config import get_settings
from backend.app.pipeline.base import TranscriptResult
from backend.app.pipeline.asr import StubASRClient


class AssemblyAIASRClient:
    """Prerecorded transcription for a single audio turn (cost-controlled smoke path)."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.assemblyai_api_key).strip()
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is required for AssemblyAIASRClient")
        self.speech_model = settings.assemblyai_speech_model
        self._fallback = StubASRClient(simulated_latency_ms=5.0)

    async def transcribe_turn(
        self,
        *,
        audio_b64: str | None = None,
        mock_transcript: str | None = None,
    ) -> TranscriptResult:
        # Prefer explicit text to avoid burning ASR minutes on typed turns.
        if mock_transcript and mock_transcript.strip():
            result = await self._fallback.transcribe_turn(
                audio_b64=None,
                mock_transcript=mock_transcript,
            )
            result.provider = "text_passthrough"
            result.raw = {"skipped_live_asr": True, "reason": "text_provided"}
            return result

        if not audio_b64:
            raise ValueError("AssemblyAIASRClient requires audio_b64 or mock_transcript")

        t0 = time.perf_counter()
        text = await asyncio.to_thread(self._transcribe_b64, audio_b64)
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        return TranscriptResult(
            text=text,
            latency_ms=elapsed,
            provider="assemblyai_prerecorded",
            is_final=True,
            raw={"speech_model": self.speech_model},
        )

    def _transcribe_b64(self, audio_b64: str) -> str:
        import assemblyai as aai

        aai.settings.api_key = self.api_key
        raw = base64.b64decode(audio_b64)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(raw)
            path = Path(tmp.name)
        try:
            config = aai.TranscriptionConfig(
                speech_model=self.speech_model,
                language_code="en",
            )
            # Keyterms for Da Nang domain (best-effort; SDK versions vary)
            try:
                config = aai.TranscriptionConfig(
                    speech_model=self.speech_model,
                    language_code="en",
                    keyterms_prompt=[
                        "Da Nang",
                        "Ba Na Hills",
                        "Golden Bridge",
                        "Marble Mountains",
                        "My Khe",
                        "Hoi An",
                        "Dragon Bridge",
                    ],
                )
            except TypeError:
                pass
            transcript = aai.Transcriber().transcribe(str(path), config=config)
            if transcript.status == aai.TranscriptStatus.error:
                raise RuntimeError(transcript.error or "AssemblyAI transcription failed")
            text = (transcript.text or "").strip()
            if not text:
                raise RuntimeError("AssemblyAI returned empty transcript")
            return text
        finally:
            path.unlink(missing_ok=True)
