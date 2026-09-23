"""Cartesia Sonic WebSocket Streaming TTS client for ultra-low latency audio chunks."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import struct
from typing import AsyncIterator

import websockets

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_VOICE_ID = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"
CARTESIA_VERSION = "2026-03-01"


class CartesiaStreamingTTS:
    """Streams text to Cartesia WebSocket and yields raw PCM 16kHz audio chunks."""

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        sample_rate: int = 16000,
    ) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.cartesia_api_key).strip()
        self.voice_id = (voice_id or settings.cartesia_voice_id or DEFAULT_VOICE_ID).strip()
        self.sample_rate = sample_rate

        if not self.api_key:
            raise ValueError("CARTESIA_API_KEY is required for CartesiaStreamingTTS")

    def _get_url(self) -> str:
        return f"{CARTESIA_WS_URL}?api_key={self.api_key}&cartesia_version={CARTESIA_VERSION}"

    async def stream_utterance(
        self,
        text_stream: AsyncIterator[str],
        *,
        context_id: str,
        voice_id: str | None = None,
        speaking_rate: float = 1.0,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        """Send streamed text tokens into Cartesia and yield PCM 16kHz audio bytes."""
        url = self._get_url()
        voice = (voice_id or self.voice_id).strip()

        async with websockets.connect(url) as ws:
            send_done = asyncio.Event()

            async def _sender() -> None:
                try:
                    buffered_words: list[str] = []
                    async for token in text_stream:
                        if cancel_event and cancel_event.is_set():
                            break
                        buffered_words.append(token)
                        # Send in small chunks of 2-4 words or sentence endings for optimal TTS pacing
                        combined = "".join(buffered_words)
                        if len(buffered_words) >= 3 or any(p in combined for p in ".!?"):
                            chunk_text = combined
                            buffered_words.clear()
                            req = {
                                "context_id": context_id,
                                "model_id": "sonic-3",
                                "transcript": chunk_text,
                                "voice": {"mode": "id", "id": voice},
                                "output_format": {
                                    "container": "raw",
                                    "encoding": "pcm_s16le",
                                    "sample_rate": self.sample_rate,
                                },
                                "language": "en",
                                "continue": True,
                            }
                            await ws.send(json.dumps(req))

                    # Send any remaining words with continue=False to signal end
                    remaining = "".join(buffered_words)
                    if not (cancel_event and cancel_event.is_set()):
                        final_req = {
                            "context_id": context_id,
                            "model_id": "sonic-3",
                            "transcript": remaining or " ",
                            "voice": {"mode": "id", "id": voice},
                            "output_format": {
                                "container": "raw",
                                "encoding": "pcm_s16le",
                                "sample_rate": self.sample_rate,
                            },
                            "language": "en",
                            "continue": False,
                        }
                        await ws.send(json.dumps(final_req))
                    else:
                        # Cancel context
                        await ws.send(json.dumps({"context_id": context_id, "cancel": True}))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error in Cartesia TTS sender: %s", exc)
                finally:
                    send_done.set()

            sender_task = asyncio.create_task(_sender())

            try:
                while True:
                    if cancel_event and cancel_event.is_set():
                        try:
                            await ws.send(json.dumps({"context_id": context_id, "cancel": True}))
                        except Exception:
                            pass
                        break

                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError:
                        if send_done.is_set():
                            break
                        continue

                    msg = json.loads(raw_msg)
                    if msg.get("context_id") == context_id:
                        b64_data = msg.get("data")
                        if b64_data:
                            pcm_bytes = base64.b64decode(b64_data)
                            yield pcm_bytes

                        if msg.get("done"):
                            break
            finally:
                if not sender_task.done():
                    sender_task.cancel()
                    try:
                        await sender_task
                    except asyncio.CancelledError:
                        pass


class StubStreamingTTS:
    """Mock streaming TTS yielding sine-wave PCM audio for testing without Cartesia key."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate

    async def stream_utterance(
        self,
        text_stream: AsyncIterator[str],
        *,
        context_id: str,
        voice_id: str | None = None,
        speaking_rate: float = 1.0,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        del voice_id, speaking_rate, context_id
        full_text = []
        async for token in text_stream:
            if cancel_event and cancel_event.is_set():
                return
            full_text.append(token)

        # Yield 3 small sine wave PCM chunks (16kHz s16le mono)
        chunk_samples = int(0.1 * self.sample_rate)  # 100ms chunk
        for step in range(3):
            if cancel_event and cancel_event.is_set():
                return
            frames = bytearray()
            for i in range(chunk_samples):
                val = int(0.2 * 32767 * math.sin(2 * math.pi * 440.0 * (step * chunk_samples + i) / self.sample_rate))
                frames.extend(struct.pack("<h", val))
            yield bytes(frames)
            await asyncio.sleep(0.08)
