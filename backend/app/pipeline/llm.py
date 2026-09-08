"""Stub LLM — grounded extractive answer from RAG chunks (no Gemini key)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from backend.app.pipeline.base import LLMResult
from rag.retrieve import extractive_answer


class StubLLMClient:
    def __init__(self, simulated_ttft_ms: float = 25.0) -> None:
        self.simulated_ttft_ms = simulated_ttft_ms

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
        del history  # unused in stub; kept for interface parity
        t0 = time.perf_counter()
        await asyncio.sleep(self.simulated_ttft_ms / 1000.0)
        ttft = round((time.perf_counter() - t0) * 1000, 3)

        body = extractive_answer(context_chunks, max_chars=280)
        words = body.split()
        if len(words) > 25:
            body = " ".join(words[:25]) + "..."

        prefix = ""
        if "warm" in style_prompt.lower():
            prefix = "Sure — "
        elif "calm" in style_prompt.lower() or "concierge" in style_prompt.lower():
            prefix = "Certainly. "

        text = f"{prefix}{body}".strip()
        if last_turn:
            text = f"{text} Enjoy Da Nang!"
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        return LLMResult(
            text=text,
            latency_ms=elapsed,
            ttft_ms=ttft,
            provider="stub_llm",
            streamed=False,
        )

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
        result = await self.generate(
            user_text=user_text,
            context_chunks=context_chunks,
            style_prompt=style_prompt,
            answer_template=answer_template,
            history=history,
            last_turn=last_turn,
        )
        for word in result.text.split(" "):
            yield word + " "
            await asyncio.sleep(0.01)
