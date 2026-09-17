from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    language_code: str | None = None
    language_confidence: float | None = None


class AssemblyAIRealtimeProvider:
    """AssemblyAI U3.5 adapter boundary; network session wiring is injected by the app."""

    model = "universal-3-5-pro"

    def __init__(self, api_key: str, keyterms: list[str] | None = None):
        self.api_key, self.keyterms = api_key, keyterms or []
        if not api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY is required for realtime transcription")

    @property
    def config(self) -> dict:
        return {"speech_model": self.model, "sample_rate": 16000, "keyterms": self.keyterms}
