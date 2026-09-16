"""Live Gemini LLM client for short grounded spoken answers."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from backend.app.config import get_settings
from backend.app.pipeline.base import LLMResult


class AsyncTokenBucket:
    """Sliding-window limiter for a provider's per-minute request quota.

    A provider RPM cap is a count inside a rolling window, not a minimum gap
    between calls, so a turn's whole tool loop may fire back to back; we only
    block once `rate` requests already sit inside the trailing window. `burst`
    is a second, shorter window that keeps a runaway tool loop from draining the
    whole minute in a couple of seconds.
    """

    BURST_WINDOW = 10.0

    def __init__(self, rate: int, window_seconds: int = 60, burst: int = 0) -> None:
        self.rate = max(1, int(rate))
        self.window = max(1, int(window_seconds))
        self.interval = self.window / self.rate  # nominal spacing, kept for reporting
        self.burst = min(self.rate, int(burst)) if int(burst or 0) > 0 else self.rate
        self._lock = asyncio.Lock()
        self.acquired_count = 0
        self.waited_ms = 0.0
        self._slot_times: list[float] = []

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        if self._slot_times and self._slot_times[0] <= cutoff:
            self._slot_times = [t for t in self._slot_times if t > cutoff]

    def _delay_for(self, now: float, limit: int, window: float) -> float:
        """Seconds until a `limit`-per-`window` budget has room for one more."""
        recent = [t for t in self._slot_times if t > now - window]
        if len(recent) < limit:
            return 0.0
        return max(0.0, recent[len(recent) - limit] + window - now)

    async def acquire(self) -> float:
        """Take one slot, sleeping only if a budget is full. Returns the wait in ms."""
        async with self._lock:
            waited = 0.0
            for _ in range(2):
                now = time.monotonic()
                self._prune(now)
                delay = max(
                    self._delay_for(now, self.rate, float(self.window)),
                    self._delay_for(now, self.burst, self.BURST_WINDOW),
                )
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
                waited += delay
            now = time.monotonic()
            self._prune(now)
            self._slot_times.append(now)
            self.acquired_count += 1
            self.waited_ms += waited * 1000.0
            return waited * 1000.0

    def rate_per_minute(self, window_seconds: float = 60.0) -> float:
        """Observed sustained request rate (requests/min) over the last window."""
        if not self._slot_times:
            return 0.0
        now = time.monotonic()
        cutoff = now - window_seconds
        recent = [t for t in self._slot_times if t >= cutoff]
        if not recent:
            return 0.0
        return round(len(recent) / (min(window_seconds, now - recent[0]) / 60.0), 3)


def _gemini_rpm() -> int:
    try:
        return int(get_settings().gemini_rpm or 0)
    except Exception:  # noqa: BLE001
        return 0


def _gemini_burst() -> int:
    try:
        return int(getattr(get_settings(), "gemini_burst", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _gemini_limiter() -> AsyncTokenBucket | None:
    rpm = _gemini_rpm()
    if rpm <= 0:
        return None
    # Singleton shared across all client instances so the whole process stays under quota.
    if not hasattr(_gemini_limiter, "_bucket"):
        _gemini_limiter._bucket = AsyncTokenBucket(rate=rpm, burst=_gemini_burst())
    return _gemini_limiter._bucket


class GeminiLLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.gemini_api_key).strip()
        self.model = (model or settings.gemini_model).strip() or "gemini-3.5-flash-lite"
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiLLMClient")

    def _build_prompt(
        self,
        *,
        user_text: str,
        context_chunks: list[dict[str, Any]],
        style_prompt: str,
        answer_template: str,
        history: list[dict[str, str]] | None = None,
        last_turn: bool = False,
    ) -> str:
        context_blocks: list[str] = []
        for i, chunk in enumerate(context_chunks[:3], start=1):
            meta = chunk.get("metadata") or {}
            title = meta.get("title") or chunk.get("chunk_id")
            context_blocks.append(f"[{i}] {title}: {chunk.get('text') or ''}")
        context = "\n".join(context_blocks) if context_blocks else "(no context)"

        history_block = ""
        if history:
            lines: list[str] = []
            for turn in history[-2:]:
                u = (turn.get("user") or "").strip()
                a = (turn.get("agent") or "").strip()
                if u:
                    lines.append(f"User: {u}")
                if a:
                    lines.append(f"Assistant: {a}")
            if lines:
                history_block = "Recent conversation:\n" + "\n".join(lines) + "\n\n"

        closing = ""
        if last_turn:
            closing = (
                "This is the last turn of the conversation. "
                "End with one warm closing sentence inviting them to enjoy Da Nang.\n"
            )

        return (
            "You are a concise English-speaking Da Nang travel voice assistant.\n"
            f"Style: {style_prompt}\n"
            f"Format rules: {answer_template}\n"
            "Answer ONLY using the context below. If unsupported, say you do not know.\n"
            "Keep the reply under 25 words, spoken English, no markdown.\n"
            f"{closing}\n"
            f"Context:\n{context}\n\n"
            f"{history_block}"
            f"User: {user_text}\n"
            "Assistant:"
        )

    async def generate(
        self,
        *,
        user_text: str,
        context_chunks: list[dict[str, Any]],
        style_prompt: str,
        answer_template: str,
        history: list[dict[str, str]] | None = None,
        last_turn: bool = False,
    ) -> LLMResult:
        lim = _gemini_limiter()
        if lim is not None:
            await lim.acquire()
        prompt = self._build_prompt(
            user_text=user_text,
            context_chunks=context_chunks,
            style_prompt=style_prompt,
            answer_template=answer_template,
            history=history,
            last_turn=last_turn,
        )
        t0 = time.perf_counter()
        text = await asyncio.to_thread(self._generate_sync, prompt)
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        return LLMResult(
            text=text,
            latency_ms=elapsed,
            ttft_ms=elapsed,  # non-streaming path: TTFT ~= total
            provider="gemini",
            streamed=False,
        )

    def _generate_sync(self, prompt: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        config_kwargs: dict[str, Any] = {
            "temperature": 0.3,
            "max_output_tokens": 80,
        }
        try:
            config = types.GenerateContentConfig(
                **config_kwargs,
                thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            )
        except Exception:
            config = types.GenerateContentConfig(**config_kwargs)

        # Retry a sporadic rate-limit (429/RESOURCE_EXHAUSTED) with backoff; the
        # token-bucket normally keeps us under the RPM quota so this is just a safety net.
        import time as _t

        deadline = _t.monotonic() + 90.0
        while True:
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                text = (getattr(response, "text", None) or "").strip()
                if not text:
                    raise RuntimeError("Gemini returned empty text")
                return text
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                is_ratelimit = ("429" in err) or ("RESOURCE_EXHAUSTED" in err) or ("quota" in err.lower())
                if not is_ratelimit or _t.monotonic() >= deadline:
                    raise
                _t.sleep(4.0)

    async def stream(
        self,
        *,
        user_text: str,
        context_chunks: list[dict[str, Any]],
        style_prompt: str,
        answer_template: str,
        history: list[dict[str, str]] | None = None,
        last_turn: bool = False,
    ) -> AsyncIterator[str]:
        """Yield text chunks from Gemini streaming when available; else one full string."""
        lim = _gemini_limiter()
        if lim is not None:
            await lim.acquire()
        prompt = self._build_prompt(
            user_text=user_text,
            context_chunks=context_chunks,
            style_prompt=style_prompt,
            answer_template=answer_template,
            history=history,
            last_turn=last_turn,
        )
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _produce() -> None:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.api_key)
                config_kwargs: dict[str, Any] = {
                    "temperature": 0.3,
                    "max_output_tokens": 80,
                }
                try:
                    config = types.GenerateContentConfig(
                        **config_kwargs,
                        thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
                    )
                except Exception:
                    config = types.GenerateContentConfig(**config_kwargs)

                stream = client.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                for chunk in stream:
                    piece = (getattr(chunk, "text", None) or "").strip()
                    if piece:
                        loop.call_soon_threadsafe(queue.put_nowait, piece)
            except Exception:
                try:
                    full = self._generate_sync(prompt)
                    if full:
                        loop.call_soon_threadsafe(queue.put_nowait, full)
                except Exception:
                    pass
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _produce)
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
