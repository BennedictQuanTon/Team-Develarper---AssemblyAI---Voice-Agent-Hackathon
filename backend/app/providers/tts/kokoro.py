from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading
from collections.abc import AsyncIterator, Iterator


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
        self._model: object | None = None
        self._resolved_device: str | None = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def voice_for(self, language: str) -> tuple[str, str] | None:
        return VOICE_MAP.get(language) or VOICE_MAP.get(language.split("-")[0])

    @property
    def resolved_device(self) -> str | None:
        return self._resolved_device

    def _device(self) -> str:
        import torch

        requested = self.device.lower()
        if requested == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "KOKORO_DEVICE=cuda but the installed PyTorch build has no CUDA support. "
                "Install a CUDA-enabled PyTorch build or set KOKORO_DEVICE=cpu/auto."
            )
        return requested

    def _pipeline(self, language: str):
        voice = self.voice_for(language)
        if voice is None:
            return None, None
        lang_code, voice_name = voice
        with self._load_lock:
            if lang_code not in self._pipelines:
                from kokoro import KModel, KPipeline  # type: ignore

                device = self._device()
                if self._model is None:
                    self._model = KModel(repo_id=self.model_id).to(device).eval()
                    self._resolved_device = device
                self._pipelines[lang_code] = KPipeline(
                    lang_code=lang_code,
                    repo_id=self.model_id,
                    model=self._model,
                    device=device,
                )
        return self._pipelines[lang_code], voice_name

    def iter_pcm(self, text: str, language: str, *, chunk_ms: int = 100) -> Iterator[bytes]:
        pipeline, voice_name = self._pipeline(language)
        if pipeline is None:
            return
        import numpy as np

        chunk_samples = max(1, int(self.sample_rate * chunk_ms / 1000))
        with self._inference_lock:
            for result in pipeline(text, voice=voice_name, split_pattern=r"(?<=[.!?。！？])\s+"):
                audio = result.audio if hasattr(result, "audio") else result[2]
                pcm = (np.asarray(audio) * 32767).clip(-32768, 32767).astype(np.int16)
                for start in range(0, len(pcm), chunk_samples):
                    yield pcm[start : start + chunk_samples].tobytes()

    def warmup(self, language: str = "en") -> None:
        # Consume a minimal utterance once so model download/load and graph setup
        # are excluded from guest-turn latency.
        next(self.iter_pcm("Ready.", language), None)

    def synthesize(self, text: str, language: str) -> TTSResult:
        voice = self.voice_for(language)
        if voice is None:
            return TTSResult(False, language_code=language)
        try:
            pcm = b"".join(self.iter_pcm(text, language))
            return TTSResult(True, pcm, self.sample_rate, language)
        except ImportError:
            return TTSResult(False, b"", self.sample_rate, language)

    async def synthesize_async(self, text: str, language: str) -> TTSResult:
        return await asyncio.to_thread(self.synthesize, text, language)

    async def stream_async(self, text: str, language: str, cancel_event: asyncio.Event | None = None) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

        def produce() -> None:
            try:
                for chunk in self.iter_pcm(text, language):
                    if cancel_event and cancel_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        worker = asyncio.create_task(asyncio.to_thread(produce))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if cancel_event:
                cancel_event.set()
            await worker
