"""Generate 100% English Context-Aware Audio Fillers using Cartesia Streaming TTS.

Saves 16kHz s16le Mono WAV files into frontend/audio/backchannels/:
- dish_check.wav: "Let me check the kitchen if that dish is available."
- table_check.wav: "Checking our floor plan for an open table for you."
- order_process.wav: "Sure thing, putting that into the system for you."
- specialty_rec.wav: "Let me check our house specialties for you right now."
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid
import wave

from backend.app.config import get_settings
from backend.app.pipeline.tts_stream import CartesiaStreamingTTS

AUDIO_DIR = Path(__file__).resolve().parents[1] / "frontend" / "audio" / "backchannels"

CLIPS = [
    {
        "id": "dish_check",
        "filename": "dish_check.wav",
        "text": "Let me check the kitchen if that dish is available.",
        "label": "check dish availability",
        "when": "guest asking if dish/squid/item is available",
    },
    {
        "id": "table_check",
        "filename": "table_check.wav",
        "text": "Checking our floor plan for an open table for you.",
        "label": "check table availability",
        "when": "guest asking for table/seating/party size",
    },
    {
        "id": "order_process",
        "filename": "order_process.wav",
        "text": "Sure thing, putting that into the system for you.",
        "label": "processing order",
        "when": "guest adding items, modifying, or placing order",
    },
    {
        "id": "specialty_rec",
        "filename": "specialty_rec.wav",
        "text": "Let me check our house specialties for you right now.",
        "label": "house specialties",
        "when": "guest asking for recommendations or specialties",
    },
]


async def generate_clip(tts: CartesiaStreamingTTS, text: str, output_path: Path) -> float:
    async def _token_gen():
        for word in text.split():
            yield word + " "

    context_id = f"filler-{uuid.uuid4().hex[:6]}"
    pcm_chunks: list[bytes] = []

    async for chunk in tts.stream_utterance(_token_gen(), context_id=context_id):
        pcm_chunks.append(chunk)

    raw_pcm = b"".join(pcm_chunks)
    if not raw_pcm:
        raise RuntimeError(f"No audio returned for: {text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(raw_pcm)

    duration = len(raw_pcm) / (16000 * 2)
    return duration


async def main() -> None:
    settings = get_settings()
    tts = CartesiaStreamingTTS(
        api_key=settings.cartesia_api_key,
        voice_id=settings.cartesia_voice_id,
        sample_rate=16000,
    )

    print("Generating Context-Aware Audio Fillers...")
    for clip in CLIPS:
        dest = AUDIO_DIR / clip["filename"]
        try:
            dur = await generate_clip(tts, clip["text"], dest)
            print(f"  [OK] {clip['filename']} ({dur:.2f}s) -> {dest}")
        except Exception as e:
            print(f"  [ERROR] Failed to generate {clip['filename']}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
