"""Tiny API smoke test: AssemblyAI (short WAV) + Gemini + Cartesia.

Does not print secrets. Exit 0 only if all three succeed.

  PYTHONPATH=. .venv/bin/python eval/smoke_apis.py
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.pipeline.asr_live import AssemblyAIASRClient
from backend.app.pipeline.llm_live import GeminiLLMClient
from backend.app.pipeline.tts_live import CartesiaTTSClient


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    keys = settings.keys_configured
    print("keys:", {k: bool(v) for k, v in keys.items()})
    if not all(keys.values()):
        print("FAIL: need ASSEMBLYAI + GEMINI + CARTESIA in .env")
        return 2

    wav_path = ROOT / "frontend" / "audio" / "backchannels" / "got_it.wav"
    audio_b64 = base64.b64encode(wav_path.read_bytes()).decode("ascii")

    # 1) AssemblyAI
    t0 = time.perf_counter()
    asr = AssemblyAIASRClient()
    asr_result = await asr.transcribe_turn(audio_b64=audio_b64)
    asr_ms = round((time.perf_counter() - t0) * 1000, 1)
    print(f"ASR  OK  {asr_ms}ms  provider={asr_result.provider}  text={asr_result.text!r}")
    print(f"     models={asr_result.raw.get('speech_models')} used={asr_result.raw.get('speech_model_used')}")

    # 2) Gemini (tiny grounded stub context)
    t1 = time.perf_counter()
    llm = GeminiLLMClient()
    llm_result = await llm.generate(
        user_text="When is the Dragon Bridge fire show?",
        context_chunks=[
            {
                "chunk_id": "events_dragon_show",
                "text": "Dragon Bridge fire show on Saturday and Sunday around 9:00 PM.",
                "metadata": {"title": "Dragon Bridge"},
            }
        ],
        style_prompt="Brief travel guide.",
        answer_template="Under 20 words.",
    )
    llm_ms = round((time.perf_counter() - t1) * 1000, 1)
    print(f"LLM  OK  {llm_ms}ms  provider={llm_result.provider}  text={llm_result.text!r}")

    # 3) Cartesia
    t2 = time.perf_counter()
    tts = CartesiaTTSClient()
    tts_result = await tts.synthesize(
        text="Got it.",
        voice_id="",
        speaking_rate=1.0,
        style_prompt="clear",
    )
    tts_ms = round((time.perf_counter() - t2) * 1000, 1)
    audio_len = len(tts_result.audio_b64 or "")
    print(f"TTS  OK  {tts_ms}ms  provider={tts_result.provider}  b64_chars={audio_len}")

    print("ALL APIs OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", type(exc).__name__, str(exc)[:300])
        raise SystemExit(1) from exc
