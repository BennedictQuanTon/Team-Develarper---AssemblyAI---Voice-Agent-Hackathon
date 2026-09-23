"""Pipeline client interfaces — Phase 3 stubs, Phase 4 live implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class TranscriptResult:
    text: str
    latency_ms: float
    provider: str = "stub"
    is_final: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str
    latency_ms: float
    ttft_ms: float
    provider: str = "stub"
    streamed: bool = False


@dataclass
class TTSResult:
    audio_b64: str
    mime_type: str
    latency_ms: float
    ttfb_ms: float
    provider: str = "stub"
    sample_rate: int = 16000


class ASRClient(Protocol):
    async def transcribe_turn(
        self,
        *,
        audio_b64: str | None = None,
        mock_transcript: str | None = None,
    ) -> TranscriptResult: ...


class LLMClient(Protocol):
    async def generate(
        self,
        *,
        user_text: str,
        context_chunks: list[dict[str, Any]],
        style_prompt: str,
        answer_template: str,
        history: list[dict[str, str]] | None = None,
        last_turn: bool = False,
    ) -> LLMResult: ...

    def stream(
        self,
        *,
        user_text: str,
        context_chunks: list[dict[str, Any]],
        style_prompt: str,
        answer_template: str,
        history: list[dict[str, str]] | None = None,
        last_turn: bool = False,
    ) -> AsyncIterator[str]: ...


class TTSClient(Protocol):
    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        speaking_rate: float,
        style_prompt: str,
    ) -> TTSResult: ...
