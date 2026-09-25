"""Generate the short "one moment" clips the guest hears while Qwen and Kokoro work.

Uses Kokoro with the same voices as live replies (``providers/tts/kokoro.py``), so the filler and
the answer sound like one waiter. Writes 24 kHz mono PCM16 WAVs and a manifest to
``frontend/public/audio/fillers/``. The guest view picks a clip from the final transcript.

    python tools/generate_fillers.py

Fillers make the wait feel shorter; they are not answer latency, and benchmarks must keep
measuring the first generated PCM.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.providers.tts.kokoro import VOICE_MAP  # noqa: E402

OUT_DIR = ROOT / "frontend" / "public" / "audio" / "fillers"
SAMPLE_RATE = 24000
# id -> (language, text). Keep each clip near one second: the answer queues behind it.
FILLERS = {
    "recommend_en": ("en", "Let me see what's good tonight."),
    "order_en": ("en", "Sure, one moment."),
    "check_en": ("en", "Let me check that for you."),
    "place_en": ("en", "Perfect, sending it through."),
    "generic_es": ("es", "Un momento, por favor."),
}


def trim_silence(audio: np.ndarray, threshold: float = 0.01, pad_seconds: float = 0.05) -> np.ndarray:
    """Cut Kokoro's leading and trailing silence; every millisecond here delays the real answer."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if loud.size == 0:
        return audio
    pad = int(pad_seconds * SAMPLE_RATE)
    return audio[max(0, loud[0] - pad): loud[-1] + pad]


def main() -> None:
    from kokoro import KPipeline

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pipelines: dict[str, KPipeline] = {}
    manifest = {}
    for clip_id, (language, text) in FILLERS.items():
        lang_code, voice = VOICE_MAP[language]
        pipeline = pipelines.setdefault(lang_code, KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M", device="cpu"))
        audio = trim_silence(np.concatenate([np.asarray(result.audio, dtype=np.float32) for result in pipeline(text, voice=voice)]))
        path = OUT_DIR / f"{clip_id}.wav"
        sf.write(path, audio, SAMPLE_RATE, subtype="PCM_16")
        manifest[clip_id] = {"language": language, "text": text, "file": path.name,
                             "voice": voice, "seconds": round(len(audio) / SAMPLE_RATE, 2)}
        print(f"{path.relative_to(ROOT)}  {manifest[clip_id]['seconds']:.2f}s  {text!r}")
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
