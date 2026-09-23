"""Stub TTS — tiny WAV beep (no Cartesia key)."""

from __future__ import annotations

import asyncio
import base64
import io
import math
import struct
import time
import wave

from backend.app.pipeline.base import TTSResult


def _sine_wav_b64(
    *,
    duration_s: float = 0.18,
    freq_hz: float = 440.0,
    sample_rate: int = 16000,
    volume: float = 0.25,
) -> str:
    n_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            # short fade in/out to avoid click
            env = 1.0
            fade = int(0.02 * sample_rate)
            if i < fade:
                env = i / fade
            elif i > n_samples - fade:
                env = max(0.0, (n_samples - i) / fade)
            sample = int(volume * env * 32767 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(bytes(frames))
    return base64.b64encode(buf.getvalue()).decode("ascii")


class StubTTSClient:
    def __init__(self, simulated_ttfb_ms: float = 20.0) -> None:
        self.simulated_ttfb_ms = simulated_ttfb_ms

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        speaking_rate: float,
        style_prompt: str,
    ) -> TTSResult:
        t0 = time.perf_counter()
        await asyncio.sleep(self.simulated_ttfb_ms / 1000.0)
        ttfb = round((time.perf_counter() - t0) * 1000, 3)
        # Longer text → slightly longer stub tone (capped)
        duration = min(0.45, 0.12 + 0.004 * len(text.split()))
        freq = 523.25 if "warm" in style_prompt.lower() else 392.0
        audio = _sine_wav_b64(duration_s=duration, freq_hz=freq)
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        return TTSResult(
            audio_b64=audio,
            mime_type="audio/wav",
            latency_ms=elapsed,
            ttfb_ms=ttfb,
            provider="stub_tts",
            sample_rate=16000,
        )
