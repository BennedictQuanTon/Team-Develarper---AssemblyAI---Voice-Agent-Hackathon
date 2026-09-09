"""AssemblyAI Realtime Streaming STT client using assemblyai.streaming.v3."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

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
    "Han River",
]

SpeechCallback = Callable[[], Awaitable[None] | None]
TurnCallback = Callable[[str, bool], Awaitable[None] | None]  # (transcript, is_final)
ErrorCallback = Callable[[str], Awaitable[None] | None]


class AssemblyAIRealtimeStream:
    """Manages an active real-time streaming STT session with AssemblyAI."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        sample_rate: int = 16000,
        on_speech_started: SpeechCallback | None = None,
        on_turn: TurnCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.assemblyai_api_key).strip()
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is required for AssemblyAIRealtimeStream")

        self.sample_rate = sample_rate
        self.on_speech_started = on_speech_started
        self.on_turn = on_turn
        self.on_error = on_error

        self._transcriber = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._audio_buffer = bytearray()

    async def connect(self) -> None:
        from assemblyai.streaming.v3 import (
            AsyncRealTimeTranscriber,
            Encoding,
            RealTimeEvents,
            RealTimeParameters,
            SpeechModel,
        )

        async with self._lock:
            if self._connected:
                return

            self._audio_buffer.clear()
            self._transcriber = AsyncRealTimeTranscriber(api_key=self.api_key)

            # Register event handlers
            self._transcriber.on(RealTimeEvents.SpeechStarted, self._handle_speech_started)
            self._transcriber.on(RealTimeEvents.Turn, self._handle_turn)
            self._transcriber.on(RealTimeEvents.Error, self._handle_error)

            params = RealTimeParameters(
                speech_model=SpeechModel.universal_streaming_english,
                encoding=Encoding.pcm_s16le,
                sample_rate=self.sample_rate,
                format_turns=True,
                include_partial_turns=True,
                keyterms_prompt=DANANG_KEYTERMS,
            )

            await self._transcriber.connect(params)
            self._connected = True
            logger.info("AssemblyAI Realtime Stream connected (sample_rate=%d)", self.sample_rate)

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if not self._connected or not self._transcriber:
            raise RuntimeError("Realtime stream not connected")
        self._audio_buffer.extend(pcm_chunk)
        # AssemblyAI v3 requires chunks between 50ms (1600 bytes) and 1000ms (32000 bytes)
        chunk_size = 3200
        while len(self._audio_buffer) >= chunk_size:
            chunk_to_send = bytes(self._audio_buffer[:chunk_size])
            del self._audio_buffer[:chunk_size]
            await self._transcriber.stream(chunk_to_send)

    async def flush_buffer(self) -> None:
        if not self._connected or not self._transcriber or not self._audio_buffer:
            return
        if len(self._audio_buffer) >= 1600:
            chunk_to_send = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            await self._transcriber.stream(chunk_to_send)
        elif len(self._audio_buffer) > 0:
            pad = 1600 - len(self._audio_buffer)
            chunk_to_send = bytes(self._audio_buffer) + bytes(pad)
            self._audio_buffer.clear()
            await self._transcriber.stream(chunk_to_send)

    async def force_endpoint(self) -> None:
        if self._connected and self._transcriber:
            await self.flush_buffer()
            try:
                await self._transcriber.force_endpoint()
            except Exception as exc:  # noqa: BLE001
                logger.warning("force_endpoint error: %s", exc)

    async def close(self) -> None:
        async with self._lock:
            if not self._connected or not self._transcriber:
                return
            try:
                await self._transcriber.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing AssemblyAI stream: %s", exc)
            finally:
                self._connected = False
                self._transcriber = None
                logger.info("AssemblyAI Realtime Stream disconnected")

    async def _handle_speech_started(self, _transcriber: Any, _event: Any) -> None:
        if self.on_speech_started:
            res = self.on_speech_started()
            if inspect.isawaitable(res):
                await res

    async def _handle_turn(self, _transcriber: Any, event: Any) -> None:
        transcript = (getattr(event, "transcript", "") or "").strip()
        end_of_turn = bool(getattr(event, "end_of_turn", False))
        if transcript and self.on_turn:
            res = self.on_turn(transcript, end_of_turn)
            if inspect.isawaitable(res):
                await res

    async def _handle_error(self, _transcriber: Any, event: Any) -> None:
        msg = str(getattr(event, "message", "") or getattr(event, "error", "") or event)
        logger.error("AssemblyAI Realtime Error: %s", msg)
        if self.on_error:
            res = self.on_error(msg)
            if inspect.isawaitable(res):
                await res


class StubRealtimeStream:
    """Mock stream for local testing without AssemblyAI key."""

    def __init__(
        self,
        *,
        on_speech_started: SpeechCallback | None = None,
        on_turn: TurnCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.on_speech_started = on_speech_started
        self.on_turn = on_turn
        self.on_error = on_error
        self._connected = False
        self._audio_received = 0

    async def connect(self) -> None:
        self._connected = True

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if not self._connected:
            return
        self._audio_received += len(pcm_chunk)
        # After receiving enough audio (~1 sec = 32000 bytes for 16kHz s16le), simulate turn
        if self._audio_received >= 32000:
            self._audio_received = 0
            if self.on_speech_started:
                res = self.on_speech_started()
                if inspect.isawaitable(res):
                    await res
            await asyncio.sleep(0.05)
            if self.on_turn:
                res = self.on_turn("When is the Dragon Bridge fire show?", True)
                if inspect.isawaitable(res):
                    await res

    async def close(self) -> None:
        self._connected = False
