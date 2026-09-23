"""Build live or stub pipeline clients from configured API keys."""

from __future__ import annotations

from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.pipeline.asr import StubASRClient
from backend.app.pipeline.asr_live import AssemblyAIASRClient
from backend.app.pipeline.base import ASRClient, LLMClient, TTSClient
from backend.app.pipeline.llm import StubLLMClient
from backend.app.pipeline.llm_live import GeminiLLMClient
from backend.app.pipeline.tts import StubTTSClient
from backend.app.pipeline.tts_live import CartesiaTTSClient


def build_clients(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    keys = settings.keys_configured

    asr: ASRClient
    llm: LLMClient
    tts: TTSClient
    mode = {
        "asr": "stub",
        "llm": "stub",
        "tts": "stub",
    }

    if keys["assemblyai"]:
        asr = AssemblyAIASRClient()
        mode["asr"] = "assemblyai_or_text_passthrough"
    else:
        asr = StubASRClient()

    if keys["gemini"]:
        llm = GeminiLLMClient()
        mode["llm"] = "gemini"
    else:
        llm = StubLLMClient()

    if keys["cartesia"]:
        tts = CartesiaTTSClient()
        mode["tts"] = "cartesia"
    else:
        tts = StubTTSClient()

    return {"asr": asr, "llm": llm, "tts": tts, "mode": mode}
