"""Live AssemblyAI ASR — prerecorded turn when audio_b64 is sent; text skips ASR."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time
from pathlib import Path

from backend.app.config import get_settings
from backend.app.pipeline.base import TranscriptResult
from backend.app.pipeline.asr import StubASRClient

# Current AssemblyAI API requires plural speech_models (singular speech_model is rejected).
# Default fallback chain from docs: U3.5 Pro for EN, else universal-2.
DEFAULT_SPEECH_MODELS = ["universal-3-5-pro", "universal-2"]

DANANG_KEYTERMS = [
    "Da Nang",
    "Ba Na Hills",
    "Golden Bridge",
    "Marble Mountains",
    "My Khe",
    "Hoi An",
    "Dragon Bridge",
    "Son Tra",
    "Grab",
]


def _audio_suffix(raw: bytes) -> str:
    if len(raw) >= 4 and raw[:4] == b"RIFF":
        return ".wav"
    if len(raw) >= 4 and raw[:4] == b"\x1aE\xdf\xa3":
        return ".webm"
    if len(raw) >= 8 and raw[4:8] == b"ftyp":
        return ".mp4"
    if len(raw) >= 4 and raw[:4] == b"OggS":
        return ".ogg"
    return ".wav"


def _parse_speech_models(raw: str) -> list[str]:
    """Accept comma-separated models or legacy single-model env values."""
    text = (raw or "").strip()
    if not text:
        return list(DEFAULT_SPEECH_MODELS)
    # Legacy singular names → modern list
    legacy = {
        "universal": DEFAULT_SPEECH_MODELS,
        "universal-3-5-pro": ["universal-3-5-pro", "universal-2"],
        "universal-3-pro": ["universal-3-pro", "universal-2"],
        "best": ["universal-3-5-pro", "universal-2"],
        "nano": ["universal-2"],
    }
    key = text.lower()
    if key in legacy:
        return list(legacy[key])
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


class AssemblyAIASRClient:
    """Prerecorded transcription for a single audio turn (cost-controlled)."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.assemblyai_api_key).strip()
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is required for AssemblyAIASRClient")
        self.speech_models = _parse_speech_models(settings.assemblyai_speech_model)
        self._fallback = StubASRClient(simulated_latency_ms=5.0)

    async def transcribe_turn(
        self,
        *,
        audio_b64: str | None = None,
        mock_transcript: str | None = None,
    ) -> TranscriptResult:
        # Voice turns: if audio is present, ALWAYS run ASR. Stale UI text must not
        # short-circuit into text_passthrough + spoken_cache (repeat-answer loop).
        if audio_b64:
            t0 = time.perf_counter()
            text, meta = await asyncio.to_thread(self._transcribe_b64, audio_b64)
            elapsed = round((time.perf_counter() - t0) * 1000, 3)
            return TranscriptResult(
                text=text,
                latency_ms=elapsed,
                provider="assemblyai_prerecorded",
                is_final=True,
                raw={
                    **meta,
                    "ignored_mock_transcript": bool(
                        mock_transcript and str(mock_transcript).strip()
                    ),
                },
            )

        # Typed / eval turns: text only, skip ASR minutes.
        if mock_transcript and mock_transcript.strip():
            result = await self._fallback.transcribe_turn(
                audio_b64=None,
                mock_transcript=mock_transcript,
            )
            result.provider = "text_passthrough"
            result.raw = {"skipped_live_asr": True, "reason": "text_provided"}
            return result

        raise ValueError("AssemblyAIASRClient requires audio_b64 or mock_transcript")

    def _build_config(self):
        import assemblyai as aai

        # Never pass speech_model (singular) — API returns:
        # "speech_model is deprecated. Use speech_models instead."
        kwargs: dict = {
            "speech_models": self.speech_models,
            "language_code": "en",
        }
        try:
            return aai.TranscriptionConfig(
                **kwargs,
                keyterms_prompt=DANANG_KEYTERMS,
            )
        except TypeError:
            return aai.TranscriptionConfig(**kwargs)

    def _transcribe_b64(self, audio_b64: str) -> tuple[str, dict]:
        import assemblyai as aai

        aai.settings.api_key = self.api_key
        raw = base64.b64decode(audio_b64)
        if len(raw) < 256:
            raise RuntimeError("Audio payload too small — speak longer and try again")

        suffix = _audio_suffix(raw)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            path = Path(tmp.name)

        try:
            config = self._build_config()
            transcript = aai.Transcriber().transcribe(str(path), config=config)
            if transcript.status == aai.TranscriptStatus.error:
                err = transcript.error or "AssemblyAI transcription failed"
                # Surface the real API message (often hidden behind "failed to transcribe url")
                raise RuntimeError(str(err))
            text = (transcript.text or "").strip()
            if not text:
                raise RuntimeError("AssemblyAI returned empty transcript (try speaking louder/closer)")
            used = None
            try:
                used = (transcript.json_response or {}).get("speech_model_used")
            except Exception:
                used = None
            return text, {
                "speech_models": self.speech_models,
                "speech_model_used": used,
                "audio_suffix": suffix,
                "audio_bytes": len(raw),
            }
        finally:
            path.unlink(missing_ok=True)
