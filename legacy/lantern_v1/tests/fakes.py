"""Test doubles for the LLM, the Ollama HTTP API, speech recognition and speech synthesis."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, PrivateAttr

# What Ollama reports on a warm turn, in nanoseconds.
DEFAULT_METADATA: dict[str, int] = {
    "load_duration": 5_000_000,
    "prompt_eval_count": 40,
    "prompt_eval_duration": 30_000_000,
    "eval_count": 15,
    "eval_duration": 150_000_000,
    "total_duration": 200_000_000,
}


def ai_text(text: str, **metadata: int) -> AIMessage:
    return AIMessage(content=text, response_metadata={**DEFAULT_METADATA, **metadata})


def ai_tool(name: str, args: dict[str, Any], id: str | None = None, **metadata: int) -> AIMessage:
    call = {"name": name, "args": args, "id": id or f"call-{name}", "type": "tool_call"}
    return AIMessage(content="", tool_calls=[call], response_metadata={**DEFAULT_METADATA, **metadata})


def ai_tools(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    tool_calls = [
        {"name": name, "args": args, "id": f"call-{index}-{name}", "type": "tool_call"}
        for index, (name, args) in enumerate(calls)
    ]
    return AIMessage(content="", tool_calls=tool_calls, response_metadata=dict(DEFAULT_METADATA))


class ScriptedChatModel(BaseChatModel):
    """Replays a script of responses and records what it was asked.

    Each entry is an AIMessage, an exception to raise, or a callable that receives the messages and
    returns an AIMessage. Running out of script is a test failure, never a StopIteration (which breaks
    badly inside async code).
    """

    script: list[Any] = Field(default_factory=list)
    _cursor: int = PrivateAttr(default=0)
    _received: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _bound_tools: list[dict[str, Any]] = PrivateAttr(default_factory=list)
    _bind_calls: int = PrivateAttr(default=0)
    _gate: asyncio.Event | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    @property
    def calls(self) -> int:
        return self._cursor

    @property
    def received(self) -> list[list[BaseMessage]]:
        return self._received

    @property
    def bind_tools_calls(self) -> int:
        return self._bind_calls

    @property
    def bound_tools(self) -> list[dict[str, Any]]:
        return self._bound_tools

    def block_until(self, gate: asyncio.Event) -> None:
        """Make every call wait on `gate`, to test cancellation mid-request."""
        self._gate = gate

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:
        self._bind_calls += 1
        self._bound_tools = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=self._bound_tools, **kwargs)

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self._received.append(list(messages))
        if self._cursor >= len(self.script):
            raise AssertionError(f"the scripted model ran out of responses after {self._cursor} calls")
        entry = self.script[self._cursor]
        self._cursor += 1
        if isinstance(entry, BaseException):
            raise entry
        if callable(entry) and not isinstance(entry, BaseMessage):
            entry = entry(messages)
        return entry

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        if self._gate is not None:
            await self._gate.wait()
        return self._generate(messages, stop=stop, **kwargs)


def ndjson_chat_handler(responses: Sequence[dict[str, Any]], *, model: str = "qwen2.5:3b") -> tuple[
    Callable[[httpx.Request], httpx.Response], list[dict[str, Any]]
]:
    """An `httpx.MockTransport` handler that speaks Ollama's streaming `/api/chat` protocol.

    Each response is `{"content": str, "tool_calls": [{"name", "arguments"}]}`, answered in order. Returns
    the handler and the list every request body is appended to.
    """
    queue = deque(responses)
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        requests.append({"path": request.url.path, "body": body})
        if not queue:
            return httpx.Response(500, json={"error": "mock /api/chat ran out of scripted responses"})
        response = queue.popleft()
        message: dict[str, Any] = {"role": "assistant", "content": response.get("content", "")}
        if response.get("tool_calls"):
            message["tool_calls"] = [
                {"function": {"name": call["name"], "arguments": call["arguments"]}} for call in response["tool_calls"]
            ]
        chunks = [
            {"model": model, "created_at": "2026-09-17T00:00:00Z", "message": message, "done": False},
            {
                "model": model,
                "created_at": "2026-09-17T00:00:00Z",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                **{**DEFAULT_METADATA, **response.get("metadata", {})},
            },
        ]
        payload = "\n".join(json.dumps(chunk) for chunk in chunks) + "\n"
        return httpx.Response(200, content=payload.encode(), headers={"content-type": "application/x-ndjson"})

    return handler, requests


class ScriptedRealtimeStream:
    """Stands in for AssemblyAI streaming: each client `endpoint` command finalises the next transcript.

    The repo's `StubRealtimeStream` has no `force_endpoint` and always says the same sentence, so it can't
    drive a scripted conversation.
    """

    script: deque[str] = deque()

    def __init__(self, *, on_speech_started: Any = None, on_turn: Any = None, on_error: Any = None, **_: Any) -> None:
        self.on_speech_started = on_speech_started
        self.on_turn = on_turn
        self.on_error = on_error

    async def connect(self) -> None:
        return None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        return None

    async def force_endpoint(self) -> None:
        if not ScriptedRealtimeStream.script or self.on_turn is None:
            return
        result = self.on_turn(ScriptedRealtimeStream.script.popleft(), True)
        if inspect.isawaitable(result):
            await result

    async def close(self) -> None:
        return None


@dataclass
class FakeTTSResult:
    audio_b64: str = ""
    ttfb_ms: float = 12.0
    total_ms: float = 20.0
    provider: str = "fake"


class FakeTTS:
    voice_id = "fake-voice"

    async def synthesize(self, **_: Any) -> FakeTTSResult:
        return FakeTTSResult()
