from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from typing import Any, Awaitable, Callable


TranscriptCallback = Callable[["TranscriptEvent"], Awaitable[None] | None]
SimpleCallback = Callable[[], Awaitable[None] | None]
ErrorCallback = Callable[[str], Awaitable[None] | None]

# The SDK waits 1.0 s for the WebSocket handshake by default and retries twice.
# The handshake measured 1.4-1.9 s from a Windows dev machine, so the first attempt
# timed out with "Connection failed" before a retry got through (#35).
CONNECT_TIMEOUT_S = 5.0
MAX_CONNECTION_RETRIES = 2
CONNECTION_RETRY_DELAY_S = 0.5
# Worst case for connect(): every attempt times out, plus the pauses between them and some slack.
CONNECT_BUDGET_S = CONNECT_TIMEOUT_S * (MAX_CONNECTION_RETRIES + 1) + CONNECTION_RETRY_DELAY_S * MAX_CONNECTION_RETRIES + 3.0


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    language_code: str | None = None
    language_confidence: float | None = None


class AssemblyAIRealtimeProvider:
    """AssemblyAI Universal-3.5 Pro streaming PCM16 adapter."""

    model = "universal-3-5-pro"

    def __init__(
        self,
        api_key: str,
        keyterms: list[str] | None = None,
        *,
        sample_rate: int = 16000,
        on_transcript: TranscriptCallback | None = None,
        on_speech_started: SimpleCallback | None = None,
        on_error: ErrorCallback | None = None,
        client_factory: Callable[..., Any] | None = None,
    ):
        self.api_key, self.keyterms = api_key, keyterms or []
        if not api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY is required for realtime transcription")
        self.sample_rate = sample_rate
        self.on_transcript = on_transcript
        self.on_speech_started = on_speech_started
        self.on_error = on_error
        self.client_factory = client_factory
        self._client: Any | None = None
        self._buffer = bytearray()
        self._connected = False
        self._lock = asyncio.Lock()

    @property
    def config(self) -> dict:
        return {"speech_model": self.model, "sample_rate": self.sample_rate, "keyterms": self.keyterms}

    async def connect(self) -> None:
        from assemblyai.streaming.v3 import (
            AsyncStreamingClient,
            Encoding,
            SpeechModel,
            StreamingClientOptions,
            StreamingEvents,
            StreamingParameters,
        )

        async with self._lock:
            if self._connected:
                return
            factory = self.client_factory or AsyncStreamingClient
            self._client = factory(
                options=StreamingClientOptions(
                    api_key=self.api_key,
                    connect_timeout=CONNECT_TIMEOUT_S,
                    max_connection_retries=MAX_CONNECTION_RETRIES,
                    connection_retry_delay=CONNECTION_RETRY_DELAY_S,
                ),
            )
            self._client.on(StreamingEvents.SpeechStarted, self._handle_speech_started)
            self._client.on(StreamingEvents.Turn, self._handle_turn)
            self._client.on(StreamingEvents.Error, self._handle_error)
            params = StreamingParameters(
                speech_model=SpeechModel.universal_3_5_pro,
                encoding=Encoding.pcm_s16le,
                sample_rate=self.sample_rate,
                format_turns=True,
                include_partial_turns=True,
                min_turn_silence=400,
                max_turn_silence=1000,
                end_of_turn_confidence_threshold=0.4,
                keyterms_prompt=self.keyterms or None,
            )
            await self._client.connect(params)
            self._connected = True

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if not self._connected or self._client is None:
            raise RuntimeError("AssemblyAI realtime stream is not connected")
        self._buffer.extend(pcm_chunk)
        chunk_bytes = 3200  # 100ms, mono PCM16 at 16kHz
        while len(self._buffer) >= chunk_bytes:
            chunk = bytes(self._buffer[:chunk_bytes])
            del self._buffer[:chunk_bytes]
            await self._client.stream(chunk)

    async def force_endpoint(self) -> None:
        if self._connected and self._client is not None:
            await self._flush()
            await self._client.force_endpoint()

    async def _flush(self) -> None:
        if not self._buffer or self._client is None:
            return
        chunk = bytes(self._buffer)
        self._buffer.clear()
        if len(chunk) < 1600:
            chunk += bytes(1600 - len(chunk))
        await self._client.stream(chunk)

    async def close(self) -> None:
        async with self._lock:
            if not self._connected or self._client is None:
                return
            try:
                await self._flush()
                await self._client.disconnect()
            finally:
                self._connected = False
                self._client = None
                self._buffer.clear()

    async def _dispatch(self, callback: Callable | None, *args: Any) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _handle_speech_started(self, _client: Any, _event: Any) -> None:
        await self._dispatch(self.on_speech_started)

    async def _handle_turn(self, _client: Any, event: Any) -> None:
        text = (getattr(event, "transcript", "") or "").strip()
        if not text:
            return
        await self._dispatch(
            self.on_transcript,
            TranscriptEvent(
                text=text,
                is_final=bool(getattr(event, "end_of_turn", False)),
                language_code=getattr(event, "language_code", None),
                language_confidence=getattr(event, "language_confidence", None),
            ),
        )

    async def _handle_error(self, _client: Any, event: Any) -> None:
        await self._dispatch(self.on_error, str(getattr(event, "message", None) or event))
