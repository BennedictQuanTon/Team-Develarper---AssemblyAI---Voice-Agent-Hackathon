"""Local LLM access for the waiter: Ollama through LangChain.

This module is self-contained on purpose. It imports nothing from `backend.app`; prompts, tool schemas and
settings are passed in, so it can move to a providers package unchanged.

The latency rules it encodes:

- The chat model and its tool binding are built once per process and reused. ChatOllama creates its
  async HTTP client with the model, so a model rebuilt per turn would also rebuild the connection.
- The system prompt and the tool list must be byte-identical on every request. Ollama renders tools into
  the system block and reuses a matching prompt prefix from the previous request; any change to either
  reprocesses the whole prompt.
- `num_ctx` must never vary between requests: a different value makes Ollama reload the model.
- `keep_alive=-1` keeps the model loaded. The default unloads it after five idle minutes, and the next
  guest pays the full load.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

__all__ = [
    "OllamaParams",
    "OllamaProbe",
    "OutputParserException",
    "build_chat_model",
    "build_messages",
    "get_bound_model",
    "get_last_probe",
    "model_matches",
    "ollama_timings_ms",
    "parse_keep_alive",
    "parse_optional_bool",
    "probe_ollama",
    "reset_model_cache",
    "salvage_tool_calls",
    "set_last_probe",
    "to_openai_tools",
    "tools_fingerprint",
    "warm_up",
]


@dataclass(frozen=True)
class OllamaParams:
    """Everything that defines a chat model. Hashable, so it keys the process-wide model cache."""

    model: str
    base_url: str = "http://localhost:11434"
    temperature: float = 0.3
    num_ctx: int = 4096
    num_predict: int = 256
    keep_alive: int | str = -1
    reasoning: bool | None = None
    request_timeout_s: float = 45.0


def parse_keep_alive(raw: int | str | None) -> int | str:
    """`-1` / `"300"` become seconds as an int; a duration such as `"5m"` passes through."""
    if raw is None:
        return -1
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    text = str(raw).strip()
    if not text:
        return -1
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse_optional_bool(raw: bool | str | None) -> bool | None:
    """`""` / None mean "not set", which is different from False for Ollama's `think` flag."""
    if raw is None or isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def to_openai_tools(schemas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Wrap `{name, description, parameters}` schemas as OpenAI-style tools, preserving their order."""
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": copy.deepcopy(schema["parameters"]),
            },
        }
        for schema in schemas
    ]


def tools_fingerprint(tools: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha1(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_chat_model(params: OllamaParams, *, async_client_kwargs: Mapping[str, Any] | None = None) -> Any:
    """A ChatOllama for `params`. Makes no request: model validation is deferred to the first call."""
    from langchain_ollama import ChatOllama

    kwargs: dict[str, Any] = {
        "model": params.model,
        "base_url": params.base_url,
        "temperature": params.temperature,
        "num_ctx": params.num_ctx,
        "num_predict": params.num_predict,
        "keep_alive": params.keep_alive,
        "validate_model_on_init": False,
        # A fresh dict each time: ChatOllama merges auth headers into it in place.
        "client_kwargs": {"timeout": params.request_timeout_s},
    }
    # Only send `think` when it was asked for; a model without a thinking mode may reject it.
    if params.reasoning is not None:
        kwargs["reasoning"] = params.reasoning
    if async_client_kwargs:
        kwargs["async_client_kwargs"] = dict(async_client_kwargs)
    return ChatOllama(**kwargs)


_model_cache: dict[tuple[OllamaParams, str], Any] = {}
_model_cache_lock = threading.Lock()


def get_bound_model(params: OllamaParams, tools: Sequence[Mapping[str, Any]]) -> Any:
    """The process-wide model with `tools` bound, built on first use.

    The waiter agent is created per WebSocket connection; sharing one binding keeps a single HTTP client
    and the exact same tool payload across every session.
    """
    key = (params, tools_fingerprint(tools))
    with _model_cache_lock:
        runnable = _model_cache.get(key)
        if runnable is None:
            runnable = build_chat_model(params).bind_tools(list(tools))
            _model_cache[key] = runnable
        return runnable


def reset_model_cache() -> None:
    """Drop cached models. A cached model's HTTP client belongs to the event loop that first used it."""
    with _model_cache_lock:
        _model_cache.clear()


def build_messages(system: str, user_text: str, context_block: str | None = None) -> list[BaseMessage]:
    """System prompt first and unchanged; anything that varies per turn goes after it, in the human turn."""
    human = f"{context_block}\n\n{user_text}" if context_block else user_text
    return [SystemMessage(content=system), HumanMessage(content=human)]


_DURATION_FIELDS = {
    "load_duration": "load_ms",
    "prompt_eval_duration": "prompt_eval_ms",
    "eval_duration": "eval_ms",
    "total_duration": "server_total_ms",
}
_COUNT_FIELDS = {"prompt_eval_count": "prompt_tokens", "eval_count": "output_tokens"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def ollama_timings_ms(metadata: Mapping[str, Any] | None) -> dict[str, float]:
    """Ollama's server-side durations (nanoseconds) as milliseconds, plus token counts.

    `load_ms` exposes a cold model load; a low `prompt_tokens` on a later turn is the prefix cache working.
    """
    metadata = metadata or {}
    timings: dict[str, float] = {}
    for source, target in _DURATION_FIELDS.items():
        value = metadata.get(source)
        if _is_number(value):
            timings[target] = round(value / 1_000_000, 3)
    for source, target in _COUNT_FIELDS.items():
        value = metadata.get(source)
        if _is_number(value):
            timings[target] = int(value)
    return timings


_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _as_call(candidate: Any, known_tools: Collection[str]) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    name = candidate.get("name")
    if name not in known_tools:
        return None
    args = candidate.get("arguments", candidate.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    return {"name": name, "args": args, "id": f"salvaged-{uuid.uuid4().hex[:12]}", "type": "tool_call"}


def salvage_tool_calls(text: str | None, known_tools: Collection[str]) -> list[dict[str, Any]]:
    """Recover a tool call that a small model wrote into its reply text instead of the tool_calls field.

    Accepts `<tool_call>{...}</tool_call>` blocks, or a reply that is nothing but a JSON call (or list of
    calls), optionally fenced. JSON inside ordinary prose is left alone, and unknown tool names are never
    returned. Without this the call would be spoken to the guest as JSON.
    """
    if not text or not known_tools:
        return []
    tagged = _TOOL_CALL_TAG.findall(text)
    if tagged:
        blocks: list[Any] = []
        for block in tagged:
            try:
                blocks.append(json.loads(block))
            except ValueError:
                return []
    else:
        body = text.strip()
        fence = _CODE_FENCE.match(body)
        if fence:
            body = fence.group(1).strip()
        if not body.startswith(("{", "[")):
            return []
        try:
            parsed = json.loads(body)
        except ValueError:
            return []
        blocks = parsed if isinstance(parsed, list) else [parsed]
    calls = [_as_call(block, known_tools) for block in blocks]
    if not calls or any(call is None for call in calls):
        return []
    return calls  # type: ignore[return-value]


@dataclass(frozen=True)
class OllamaProbe:
    reachable: bool
    model_present: bool
    detail: str
    checked_at: float

    @property
    def healthy(self) -> bool:
        return self.reachable and self.model_present


def model_matches(model: str, available: Collection[str]) -> bool:
    """`qwen2.5:3b` must be pulled exactly; an untagged name also matches its `:latest` tag."""
    wanted = {model}
    if ":" not in model:
        wanted.add(f"{model}:latest")
    return any(name in wanted for name in available)


async def probe_ollama(
    base_url: str,
    model: str,
    *,
    timeout_s: float = 1.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OllamaProbe:
    """Is Ollama up, and has `model` been pulled? Never raises for an unreachable server."""
    url = base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return OllamaProbe(False, False, f"Ollama not reachable at {base_url} ({type(exc).__name__})", time.time())
    names = {entry.get("name") or entry.get("model") for entry in payload.get("models") or []}
    if model_matches(model, {name for name in names if name}):
        return OllamaProbe(True, True, "ok", time.time())
    return OllamaProbe(True, False, f"model {model!r} is not pulled; run `ollama pull {model}`", time.time())


_last_probe: OllamaProbe | None = None


def get_last_probe() -> OllamaProbe | None:
    return _last_probe


def set_last_probe(probe: OllamaProbe | None) -> None:
    global _last_probe
    _last_probe = probe


async def warm_up(runnable: Any, system: str, *, prompt: str = "Hello.") -> dict[str, float]:
    """Load the model into memory ahead of the first guest.

    It goes through the same bound runnable as real turns, so the model loads with the same `num_ctx` and
    the system prompt plus tools are already in Ollama's prefix cache.
    """
    started = time.perf_counter()
    message = await runnable.ainvoke(build_messages(system, prompt))
    timings = ollama_timings_ms(getattr(message, "response_metadata", None))
    timings["warmup_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return timings
