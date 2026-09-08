"""Live Cartesia Sonic TTS client (bytes endpoint, short utterances)."""

from __future__ import annotations

import base64
import time

import httpx

from backend.app.config import get_settings
from backend.app.pipeline.base import TTSResult

# Documented example English voice from Cartesia API docs
DEFAULT_VOICE_ID = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
CARTESIA_VERSION = "2026-03-01"


class CartesiaTTSClient:
    def __init__(self, api_key: str | None = None, voice_id: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.cartesia_api_key).strip()
        self.voice_id = (voice_id or settings.cartesia_voice_id or DEFAULT_VOICE_ID).strip()
        if not self.api_key:
            raise ValueError("CARTESIA_API_KEY is required for CartesiaTTSClient")

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        speaking_rate: float,
        style_prompt: str,
    ) -> TTSResult:
        voice = (voice_id or self.voice_id or DEFAULT_VOICE_ID).strip()
        # Cap length hard to control free-tier credit burn
        words = text.split()
        if len(words) > 30:
            text = " ".join(words[:30])

        payload = {
            "model_id": "sonic-3",
            "transcript": text,
            "voice": {"id": voice},
            "language": "en",
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
            },
            "generation_config": {
                "speed": max(0.7, min(1.5, float(speaking_rate) or 1.0)),
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Cartesia-Version": CARTESIA_VERSION,
            "Content-Type": "application/json",
        }

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers=headers,
                json=payload,
            )
        ttfb = round((time.perf_counter() - t0) * 1000, 3)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Cartesia TTS failed ({response.status_code}): {response.text[:300]}"
            )
        audio_b64 = base64.b64encode(response.content).decode("ascii")
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        return TTSResult(
            audio_b64=audio_b64,
            mime_type="audio/wav",
            latency_ms=elapsed,
            ttfb_ms=ttfb,
            provider="cartesia_sonic",
            sample_rate=16000,
        )
