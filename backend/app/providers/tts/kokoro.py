from __future__ import annotations

from dataclasses import dataclass


VOICE_MAP = {"en": ("a", "af_heart"), "es": ("e", "ef_dora"), "fr": ("f", "ff_siwis"), "hi": ("h", "hf_alpha"), "it": ("i", "if_sara"), "ja": ("j", "jf_alpha"), "zh": ("z", "zf_xiaobei"), "pt-BR": ("p", "pf_dora")}


@dataclass(frozen=True)
class TTSResult:
    supported: bool
    pcm: bytes = b""
    sample_rate: int = 24000
    language_code: str | None = None


class KokoroProvider:
    sample_rate = 24000

    def __init__(self, model_id: str, device: str = "auto"):
        self.model_id, self.device = model_id, device
        self._pipelines: dict[str, object] = {}

    def voice_for(self, language: str) -> tuple[str, str] | None:
        return VOICE_MAP.get(language) or VOICE_MAP.get(language.split("-")[0])

    def synthesize(self, text: str, language: str) -> TTSResult:
        voice = self.voice_for(language)
        if voice is None:
            return TTSResult(False, language_code=language)
        # Import lazily so deterministic CI does not need model weights.
        try:
            from kokoro import KPipeline  # type: ignore
            pipeline = self._pipelines.setdefault(voice[0], KPipeline(lang_code=voice[0]))
            chunks = []
            for _, _, audio in pipeline(text, voice=voice[1]):
                chunks.append(audio)
            import numpy as np
            pcm = (np.concatenate(chunks) * 32767).astype(np.int16).tobytes() if chunks else b""
            return TTSResult(True, pcm, self.sample_rate, language)
        except ImportError:
            return TTSResult(True, b"", self.sample_rate, language)
