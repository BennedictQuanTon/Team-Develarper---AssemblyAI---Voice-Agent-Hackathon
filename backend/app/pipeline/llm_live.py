"""Live Gemini LLM client for short grounded spoken answers."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from backend.app.config import get_settings
from backend.app.pipeline.base import LLMResult


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
    ) -> str:
        context_blocks: list[str] = []
        for i, chunk in enumerate(context_chunks[:5], start=1):
            meta = chunk.get("metadata") or {}
            title = meta.get("title") or chunk.get("chunk_id")
            context_blocks.append(f"[{i}] {title}: {chunk.get('text') or ''}")
        context = "\n".join(context_blocks) if context_blocks else "(no context)"
        return (
            "You are a concise English-speaking Da Nang travel voice assistant.\n"
            f"Style: {style_prompt}\n"
            f"Format rules: {answer_template}\n"
            "Answer ONLY using the context below. If unsupported, say you do not know.\n"
            "Keep the reply under 40 words, spoken English, no markdown.\n\n"
            f"Context:\n{context}\n\n"
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
    ) -> LLMResult:
        prompt = self._build_prompt(
            user_text=user_text,
            context_chunks=context_chunks,
            style_prompt=style_prompt,
            answer_template=answer_template,
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
            "max_output_tokens": 120,
        }
        # Best-effort minimal thinking for flash-lite variants
        try:
            config = types.GenerateContentConfig(
                **config_kwargs,
                thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            )
        except Exception:
            config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise RuntimeError("Gemini returned empty text")
        return text

    async def stream(
        self,
        *,
        user_text: str,
        context_chunks: list[dict[str, Any]],
        style_prompt: str,
        answer_template: str,
    ) -> AsyncIterator[str]:
        result = await self.generate(
            user_text=user_text,
            context_chunks=context_chunks,
            style_prompt=style_prompt,
            answer_template=answer_template,
        )
        yield result.text
