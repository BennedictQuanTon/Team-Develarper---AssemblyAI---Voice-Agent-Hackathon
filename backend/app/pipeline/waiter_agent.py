"""Waiter agents — LLM function calling over the deterministic toolkit.

The LLM decides tools, the toolkit computes truth. `OllamaWaiterAgent` (a local model through
LangChain) is the default; `WaiterAgent` (Gemini) is the fallback, and both share `TOOL_SCHEMAS`.

If GEMINI_API_KEY is absent, a deterministic `RuleBasedWaiterAgent` keeps the
golden path working locally (no API) so the UI/playbook can be tested offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from backend.app.domain.lantern import get_lantern_store
from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline.llm_live import _gemini_limiter
from backend.app.pipeline.llm_live import AsyncTokenBucket  # re-export for typing convenience

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are the friendly voice waiter at The Lantern, a Vietnamese seafood & grill restaurant in Da Nang. "
    "You take orders and recommend dishes over the phone/browser, one short spoken sentence at a time.\n"
    "House Specialties & Signature Dishes:\n"
    "- Grilled Seabass ($16.00): Whole char-grilled seabass with lemongrass and lime leaf.\n"
    "- Grilled River Prawns ($18.00): Large river prawns over charcoal with tamarind glaze.\n"
    "- Pomelo Salad with Shrimp ($6.50): Crisp pomelo, poached shrimp, roasted peanut.\n"
    "- Lemongrass Chicken ($9.00): Grilled chicken thigh with jasmine rice.\n"
    "Rules:\n"
    "- When guests ask for house specialties or recommendations, recommend 1-2 of our signature dishes directly without needing extra tool calls.\n"
    "- Never invent a price, availability, or allergen not listed above. Only use tool results for unknown items.\n"
    "- If a menu item is sold out (86), do not add it; offer the suggested substitute.\n"
    "- Add only dishes the guest actually asked for. Never add one just because it appeared in a\n"
    "  lookup result, and never re-add a dish that order_lines already shows in the order.\n"
    "- Only remove a dish the guest actually names. Never remove one to make room for another.\n"
    "- After you refused a sold-out dish, 'make it X instead' replaces that sold-out dish. It was never\n"
    "  added, so just add X and remove nothing.\n"
    "- Use order_items to order: it adds dishes, applies modifiers and can place the order in one call.\n"
    "- Pass pronouns like 'those two' / 'that one' / 'the first' straight through as the item ref.\n"
    "- Set place=true only when the guest has asked to finalise; otherwise leave it false and read the order back.\n"
    "- For allergens not on the menu, say you must check the kitchen and do not guess.\n"
    "- Keep every spoken reply under ~25 words, conversational, no markdown, no emoji.\n"
    "- Order tool results already carry order_lines and total - read those back in your reply; never call readback separately.\n"
    "- Never give medical or diagnostic advice."
)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_menu",
        "description": "Find dishes on the menu by a word or category.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or category."},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "recommend_dishes",
        "description": "Recommend 1-2 available dishes matching guest tags and party size.",
        "parameters": {
            "type": "object",
            "properties": {
                "tags": {"type": "string", "description": "comma-separated tags e.g. couple,mild,kids,vegetarian,spicy_ok"},
                "party_size": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 2},
            },
            "required": ["tags", "party_size"],
        },
    },
    {
        "name": "check_availability",
        "description": "Check whether a dish is available (not 86).",
        "parameters": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "order_items",
        "description": (
            "Preferred way to order: add one or more dishes with quantities and modifiers, "
            "and optionally place the order, in a single call. Use this instead of separate "
            "add_item / add_items_from_mention / set_modifier / place_order calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "The dishes to add.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref": {
                                "type": "string",
                                "description": "Dish sku or name, or a pronoun such as 'the first' / 'those two'.",
                            },
                            "qty": {"type": "integer", "default": 1},
                            "modifiers": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["ref"],
                    },
                },
                "place": {
                    "type": "boolean",
                    "description": "True only when the guest has asked to finalise the order.",
                    "default": False,
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "add_item",
        "description": "Add a single dish by sku or name. Prefer order_items.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer", "default": 1},
                "modifiers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["sku"],
        },
    },
    {
        "name": "add_items_from_mention",
        "description": "Add previously-mentioned dishes using a pronoun reference.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "enum": ["those two", "both", "that one", "the first", "the second", "the last one"]},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "remove_item",
        "description": "Remove a dish from the order by name or sku.",
        "parameters": {
            "type": "object",
            "properties": {"sku_or_name": {"type": "string"}},
            "required": ["sku_or_name"],
        },
    },
    {
        "name": "set_modifier",
        "description": "Set modifiers on the last-added or named line (e.g. no coriander).",
        "parameters": {
            "type": "object",
            "properties": {
                "line_or_sku": {"type": "string"},
                "modifiers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["line_or_sku", "modifiers"],
        },
    },
    {
        "name": "get_floor",
        "description": "Get free tables and floor status.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "seat_party",
        "description": "Seat a party at a table (dine-in).",
        "parameters": {
            "type": "object",
            "properties": {"table_id": {"type": "string"}, "party_size": {"type": "integer"}},
            "required": ["table_id", "party_size"],
        },
    },
    {
        "name": "readback",
        "description": "Read back the current order and total.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "place_order",
        "description": "Finalize and place the order to the kitchen.",
        "parameters": {"type": "object", "properties": {}},
    },
]


MAX_ROUNDS = 6
# Headroom for a compound tool call: a nested item array does not fit in 90 tokens
# and would truncate silently. The spoken reply is held short by SYSTEM_INSTRUCTION.
MAX_OUTPUT_TOKENS = 256


def _format_prefetch(prefetch: dict[str, Any]) -> str:
    """Render locally computed read-only lookups for the first round.

    These come straight from the deterministic toolkit, so they carry the same
    authority as a tool response and save the model the round trip it would
    otherwise spend asking for them.
    """
    lines = [
        "Reference only - lookups already run for you, so you do not need the tool. "
        "These are candidates, NOT an order: add nothing the guest did not actually ask for."
    ]
    for name, result in prefetch.items():
        lines.append(f"- {name}: {json.dumps(result, ensure_ascii=False)}")
    return "\n".join(lines)


class WaiterAgent:
    """Gemini-backed function-calling loop."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from backend.app.config import get_settings

        settings = get_settings()
        self.api_key = (api_key or settings.gemini_api_key).strip()
        self.model = (model or settings.gemini_model or "gemini-3.5-flash-lite").strip()
        self.available = bool(self.api_key)
        # Built once: a fresh Client per turn pays a TLS handshake, and a tool spec
        # rebuilt per turn is a new object for no reason. Both stay byte-identical
        # across turns so the prompt prefix remains cacheable.
        self._client: Any = None
        self._tools: list[Any] | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _tool_spec(self) -> list[Any]:
        if self._tools is None:
            from google.genai import types

            self._tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=s["name"],
                            description=s["description"],
                            parameters=types.Schema.model_validate(s["parameters"]),
                        )
                        for s in TOOL_SCHEMAS
                    ]
                )
            ]
        return self._tools

    async def respond(
        self,
        ss: WaiterSession,
        user_text: str,
        prefetch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one conversational turn. Returns {
           reply: str, tool_calls: [...], basket: snapshot, timings: {...}
        }"""
        if not self.available:
            raise RuntimeError("Gemini not available (no API key)")

        import asyncio as _a
        import time as _time

        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.3,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            tools=self._tool_spec(),
        )

        first_parts = []
        if prefetch:
            first_parts.append(types.Part.from_text(text=_format_prefetch(prefetch)))
        first_parts.append(types.Part.from_text(text=user_text))
        contents: list[Any] = [types.Content(role="user", parts=first_parts)]

        tool_log: list[dict[str, Any]] = []
        text = ""
        rounds = 0
        bucket_wait_ms = 0.0
        llm_ms = 0.0
        tool_ms = 0.0

        def _result(reply: str) -> dict[str, Any]:
            return {
                "reply": reply,
                "tool_calls": tool_log,
                "basket": ss.snapshot(),
                "timings": {
                    "llm_rounds": rounds,
                    "llm_total_ms": round(llm_ms, 1),
                    "bucket_wait_ms": round(bucket_wait_ms, 1),
                    "tool_ms": round(tool_ms, 1),
                    "prefetch_used": bool(prefetch),
                },
            }

        for _ in range(MAX_ROUNDS):
            rounds += 1
            r = None
            for attempt in range(3):
                lim = _gemini_limiter()
                if lim is not None:
                    bucket_wait_ms += await lim.acquire() or 0.0
                t_call = _time.perf_counter()
                try:
                    r = await _a.to_thread(
                        lambda: client.models.generate_content(
                            model=self.model, contents=contents, config=config
                        )
                    )
                    llm_ms += (_time.perf_counter() - t_call) * 1000.0
                    break
                except Exception as exc:  # noqa: BLE001
                    llm_ms += (_time.perf_counter() - t_call) * 1000.0
                    err = str(exc)
                    is_ratelimit = ("429" in err) or ("RESOURCE_EXHAUSTED" in err) or ("quota" in err.lower())
                    if not is_ratelimit or attempt == 2:
                        raise
                    logger.warning("Gemini rate-limited, retry %d/2", attempt + 1)
                    await _a.sleep(2.0 * (2**attempt))

            if r is not None and r.candidates and r.candidates[0].content:
                parts = r.candidates[0].content.parts or []
            else:
                parts = []

            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            text = "".join(getattr(p, "text", "") or "" for p in parts).strip()

            if not calls:
                if text:
                    for it in ss.store.list_menu():
                        if it.name.lower() in text.lower():
                            ss._record_mentions([it])
                return _result(text)

            # execute calls
            function_parts: list[Any] = []
            t_tools = _time.perf_counter()
            for call in calls:
                name = call.name
                args = call.args or {}
                result = _exec_waiter_tool(ss, name, args)
                tool_log.append({"tool": name, "args": args, "result": result})
                function_parts.append(
                    types.Part.from_function_response(
                        name=name, response={"result": result}
                    )
                )
            tool_ms += (_time.perf_counter() - t_tools) * 1000.0
            # append model's tool-call turn and feed results
            contents.append(r.candidates[0].content)
            contents.append(types.Content(role="user", parts=function_parts))

        return _result(text if text else "Let me read that back for you.")

def _exec_waiter_tool(ss: WaiterSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "search_menu":
            return ss.search_menu(args.get("query", ""), int(args.get("limit", 5) or 5))
        if name == "recommend_dishes":
            return ss.recommend_dishes(args.get("tags", ""), int(args.get("party_size", 2) or 2), int(args.get("limit", 2) or 2))
        if name == "check_availability":
            return ss.check_availability(args.get("sku", ""))
        if name == "order_items":
            return ss.order_items(args.get("items") or [], _as_bool(args.get("place", False)))
        if name == "add_item":
            return ss.add_item(args.get("sku", ""), int(args.get("qty", 1) or 1), args.get("modifiers"))
        if name == "add_items_from_mention":
            return ss.add_items_from_mention(args.get("ref", ""))
        if name == "remove_item":
            return ss.remove_item(args.get("sku_or_name", ""))
        if name == "set_modifier":
            return ss.set_modifier(args.get("line_or_sku", ""), args.get("modifiers"))
        if name == "get_floor":
            return ss.get_floor()
        if name == "seat_party":
            return ss.seat_party(args.get("table_id", ""), int(args.get("party_size", 2) or 2))
        if name == "readback":
            return ss.readback()
        if name == "place_order":
            return ss.place_order()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool %s error: %s", name, exc)
        return {"error": str(exc)}
    return {"error": f"unknown tool {name}"}


_STOPWORDS = frozenset(
    "a an the and or of for to i we you my our is are do does can could would like "
    "want get have has had it its that this these those please just some any with "
    "me us them there here what whats when where how much many on in at be am".split()
)

# Read-only tools only. A wrong guess costs a few unused input tokens; a mutating
# guess would corrupt the order, so nothing here may write.
PREFETCH_TOOLS: dict[str, tuple[str, ...]] = {
    "case_specialty_rec": ("recommend_dishes",),
    "case_dish_check": ("search_menu",),
    "case_table_check": ("get_floor",),
    # Deliberately no prefetch for case_order_process: those turns always call a
    # mutating tool, whose result already carries order_lines/total, so prefetching
    # readback saves no round trip and puts the same total in context twice. The
    # model summed the two copies and quoted $31.00 on a $15.50 order.
}


def _query_terms(query: str) -> str:
    """Content words from an utterance, so search_menu is not fed 'the' and 'i'."""
    words = [w.strip(".,!?;:'\"") for w in (query or "").lower().split()]
    return " ".join(w for w in words if len(w) >= 3 and w not in _STOPWORDS)


def prefetch_for_case(ss: WaiterSession, case: str, query: str) -> dict[str, Any]:
    """Run the read-only lookups this intent almost always needs.

    These are in-memory dict lookups costing no API call and no rate-limit budget,
    and handing the results over saves the model the round trip it would otherwise
    spend asking for them. A wrong guess is simply unused.

    search_menu and recommend_dishes both push onto the mention stack, so the stack
    is restored afterwards: what the waiter actually said is recorded from the reply
    text instead, and a prefetch the model ignored must not shift 'the first'.
    """
    names = PREFETCH_TOOLS.get(case, ())
    if not names:
        return {}
    mentions_before = list(ss.mentioned)
    out: dict[str, Any] = {}
    try:
        for name in names:
            try:
                if name == "recommend_dishes":
                    tags = ",".join(ss.guest_tags) or "couple,mild"
                    out[name] = ss.recommend_dishes(tags, ss.party_size, 2)
                elif name == "search_menu":
                    terms = _query_terms(query)
                    if terms:
                        out[name] = ss.search_menu(terms, 3)
                elif name == "get_floor":
                    out[name] = ss.get_floor()
            except Exception:  # noqa: BLE001
                continue  # a prefetch is an optimisation; it must never break a turn
    finally:
        ss.mentioned = mentions_before
    return out


_TOOL_NAMES = frozenset(schema["name"] for schema in TOOL_SCHEMAS)
_FALLBACK_REPLY = "Sorry, could you say that again?"
_PARSE_NUDGE = "Your last tool call had invalid JSON arguments. Call the tool again with valid JSON arguments."
_EMPTY_NUDGE = "Reply to the guest, or call one of the tools by its exact name."


def _as_bool(value: Any) -> bool:
    """A small model may send `"false"` for a boolean, and `bool("false")` is True."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def _coerce_tool_args(name: str, args: Any) -> dict[str, Any]:
    """Normalise the argument shapes a small model gets wrong before they reach the toolkit."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        return {}
    args = dict(args)
    if name == "order_items":
        items = args.get("items")
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except ValueError:
                items = []
        if isinstance(items, dict):
            items = [items]
        args["items"] = items if isinstance(items, list) else []
        args["place"] = _as_bool(args.get("place", False))
    return args


def _record_reply_mentions(ss: WaiterSession, text: str) -> None:
    """Dishes the waiter named become the targets of "that one" / "the first" in the next turn."""
    lowered = text.lower()
    for item in ss.store.list_menu():
        if item.name.lower() in lowered:
            ss._record_mentions([item])


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [part if isinstance(part, str) else part.get("text", "") for part in content if isinstance(part, (str, dict))]
        return "".join(parts).strip()
    return ""


def ollama_params(settings: Any = None, *, model: str | None = None, base_url: str | None = None) -> Any:
    from backend.app.config import get_settings
    from backend.app.pipeline.llm_ollama import OllamaParams, parse_keep_alive, parse_optional_bool

    settings = settings or get_settings()
    return OllamaParams(
        model=(model or settings.ollama_model or "qwen2.5:3b").strip(),
        base_url=(base_url or settings.ollama_base_url or "http://localhost:11434").rstrip("/"),
        temperature=float(settings.ollama_temperature),
        num_ctx=int(settings.ollama_num_ctx),
        num_predict=int(settings.ollama_num_predict),
        keep_alive=parse_keep_alive(settings.ollama_keep_alive),
        reasoning=parse_optional_bool(settings.ollama_reasoning),
        request_timeout_s=float(settings.ollama_request_timeout_s),
    )


@dataclass
class _TurnStats:
    prefetch_used: bool = False
    rounds: int = 0
    llm_total_ms: float = 0.0
    tool_ms: float = 0.0
    parse_errors: int = 0
    empty_responses: int = 0
    salvaged_tool_calls: int = 0
    template_reply: int = 0
    server: dict[str, float] = field(default_factory=dict)
    first_prompt_tokens: int | None = None

    def add_server(self, timings: dict[str, float]) -> None:
        if self.first_prompt_tokens is None and "prompt_tokens" in timings:
            self.first_prompt_tokens = int(timings["prompt_tokens"])
        for key, value in timings.items():
            self.server[key] = self.server.get(key, 0) + value

    def as_timings(self) -> dict[str, Any]:
        timings: dict[str, Any] = {
            "llm_rounds": self.rounds,
            "llm_total_ms": round(self.llm_total_ms, 1),
            "tool_ms": round(self.tool_ms, 1),
            "bucket_wait_ms": 0.0,  # no quota locally; kept so rows line up with Gemini turns
            "prefetch_used": self.prefetch_used,
            "template_reply": self.template_reply,
            "parse_errors": self.parse_errors,
            "empty_responses": self.empty_responses,
            "salvaged_tool_calls": self.salvaged_tool_calls,
        }
        for key in ("load_ms", "prompt_eval_ms", "eval_ms", "server_total_ms", "prompt_tokens", "output_tokens"):
            if key in self.server:
                name = "ollama_server_ms" if key == "server_total_ms" else f"ollama_{key}"
                value = self.server[key]
                timings[name] = int(value) if key.endswith("tokens") else round(value, 1)
        if self.first_prompt_tokens is not None:
            timings["ollama_first_prompt_tokens"] = self.first_prompt_tokens
        if "server_total_ms" in self.server:
            # What HTTP, streaming and LangChain cost on top of Ollama's own work.
            timings["llm_overhead_ms"] = round(self.llm_total_ms - self.server["server_total_ms"], 1)
        return timings


class OllamaWaiterAgent:
    """The default waiter agent: a local model on Ollama, called through LangChain.

    Round trips are the dominant cost on a local model, so a turn is built to need as few as possible:
    read-only lookups arrive prefetched, order changes go through the compound `order_items` tool, and a
    clean order change can be confirmed from the tool result instead of a second model call.
    """

    provider_label = "ollama_langchain_tools"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        *,
        chat_model: Any = None,
        template_replies: bool | None = None,
        max_rounds: int = MAX_ROUNDS,
        settings: Any = None,
    ) -> None:
        from backend.app.config import get_settings
        from backend.app.pipeline import llm_ollama

        settings = settings or get_settings()
        self.params = ollama_params(settings, model=model, base_url=base_url)
        self.model = self.params.model
        self.base_url = self.params.base_url
        self.available = True
        self.template_replies = (
            bool(settings.waiter_template_replies) if template_replies is None else bool(template_replies)
        )
        self.max_rounds = max(1, int(max_rounds))
        self._tools = llm_ollama.to_openai_tools(TOOL_SCHEMAS)
        if chat_model is not None:
            self._runnable = chat_model.bind_tools(self._tools)
        else:
            self._runnable = llm_ollama.get_bound_model(self.params, self._tools)

    def _tool_spec(self) -> list[dict[str, Any]]:
        return self._tools

    async def respond(
        self,
        ss: WaiterSession,
        user_text: str,
        prefetch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import time as _time

        import httpx
        import ollama
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from backend.app.pipeline import llm_ollama
        from backend.app.pipeline.waiter_templates import render_confirmation

        stats = _TurnStats(prefetch_used=bool(prefetch))
        context = _format_prefetch(prefetch) if prefetch else None
        messages: list[Any] = llm_ollama.build_messages(SYSTEM_INSTRUCTION, user_text, context)
        tool_log: list[dict[str, Any]] = []
        consecutive_parse_errors = 0
        nudged_empty = False
        overflow_warned = False

        for _round in range(self.max_rounds):
            stats.rounds += 1
            started = _time.perf_counter()
            try:
                ai = await self._runnable.ainvoke(messages)
            except llm_ollama.OutputParserException as exc:
                stats.llm_total_ms += (_time.perf_counter() - started) * 1000
                stats.parse_errors += 1
                consecutive_parse_errors += 1
                logger.warning("Ollama returned unparseable tool arguments: %s", exc)
                if consecutive_parse_errors >= 2:
                    return self._result(ss, _FALLBACK_REPLY, tool_log, stats)
                messages.append(HumanMessage(content=_PARSE_NUDGE))
                continue
            except (ConnectionError, httpx.TransportError, ollama.ResponseError) as exc:
                # Mark Ollama down so the next session falls back; realtime_session apologises for this turn.
                llm_ollama.set_last_probe(
                    llm_ollama.OllamaProbe(False, False, f"request failed: {type(exc).__name__}", _time.time())
                )
                raise
            stats.llm_total_ms += (_time.perf_counter() - started) * 1000
            consecutive_parse_errors = 0

            server = llm_ollama.ollama_timings_ms(getattr(ai, "response_metadata", None))
            stats.add_server(server)
            prompt_tokens = server.get("prompt_tokens")
            if (
                not overflow_warned
                and prompt_tokens is not None
                and prompt_tokens + self.params.num_predict > 0.9 * self.params.num_ctx
            ):
                overflow_warned = True
                logger.warning(
                    "Prompt is %d tokens of a %d-token context; Ollama truncates from the front, which drops "
                    "the system prompt. Raise OLLAMA_NUM_CTX.",
                    prompt_tokens,
                    self.params.num_ctx,
                )

            calls = list(ai.tool_calls or [])
            text = _message_text(ai.content)
            if not calls and text:
                salvaged = llm_ollama.salvage_tool_calls(text, _TOOL_NAMES)
                if salvaged:
                    stats.salvaged_tool_calls += len(salvaged)
                    calls, text = salvaged, ""
                    ai = AIMessage(content="", tool_calls=salvaged)

            if not calls:
                if text:
                    _record_reply_mentions(ss, text)
                    return self._result(ss, text, tool_log, stats)
                if getattr(ai, "invalid_tool_calls", None):
                    stats.parse_errors += 1
                    consecutive_parse_errors += 1
                    if consecutive_parse_errors >= 2:
                        return self._result(ss, _FALLBACK_REPLY, tool_log, stats)
                    messages.append(HumanMessage(content=_PARSE_NUDGE))
                    continue
                # Small Qwen models sometimes answer with nothing at all.
                stats.empty_responses += 1
                if nudged_empty:
                    return self._result(ss, _FALLBACK_REPLY, tool_log, stats)
                nudged_empty = True
                messages.append(HumanMessage(content=_EMPTY_NUDGE))
                continue

            messages.append(ai)
            round_log: list[dict[str, Any]] = []
            tools_started = _time.perf_counter()
            for index, call in enumerate(calls):
                name = call.get("name") or ""
                args = _coerce_tool_args(name, call.get("args"))
                result = _exec_waiter_tool(ss, name, args)
                entry = {"tool": name, "args": args, "result": result}
                tool_log.append(entry)
                round_log.append(entry)
                messages.append(
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False, default=str),
                        tool_call_id=call.get("id") or f"call-{_round}-{index}",
                        name=name,
                    )
                )
            stats.tool_ms += (_time.perf_counter() - tools_started) * 1000

            if self.template_replies:
                reply = render_confirmation(round_log)
                if reply:
                    stats.template_reply = 1
                    _record_reply_mentions(ss, reply)
                    return self._result(ss, reply, tool_log, stats)

        return self._result(ss, self._rounds_exhausted_reply(ss), tool_log, stats)

    @staticmethod
    def _rounds_exhausted_reply(ss: WaiterSession) -> str:
        from backend.app.pipeline.waiter_templates import speak_order_lines

        readback = ss.readback()
        if readback.get("empty"):
            return _FALLBACK_REPLY
        return f"So far I have {speak_order_lines(readback['lines'])}, ${readback['total']:.2f}. Anything else?"

    @staticmethod
    def _result(ss: WaiterSession, reply: str, tool_log: list[dict[str, Any]], stats: _TurnStats) -> dict[str, Any]:
        return {"reply": reply, "tool_calls": tool_log, "basket": ss.snapshot(), "timings": stats.as_timings()}


class RuleBasedWaiterAgent:
    """Offline fallback: small keyword router over the toolkit (no LLM).

    Covers the golden path deterministically so the UI and playbook work without
    an API key. Not a substitute for the LLM in the real demo, but proves the
    tools/state layer end-to-end."""

    def __init__(self) -> None:
        self.available = False

    async def respond(
        self, ss: WaiterSession, user_text: str, prefetch: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        t = (user_text or "").lower()
        log: list[dict[str, Any]] = []

        def rec(tags: list[str], size: int):
            r = ss.recommend_dishes(",".join(tags), size, 2)
            log.append({"tool": "recommend_dishes", "result": r})
            names = ", ".join(i["name"] for i in r["items"])
            return f"I'd suggest {names}. Should I add those two?"

        # intent: what's good / recommend
        if any(w in t for w in ("what's good", "whats good", "recommend", "suggest", "not too spicy", "not spicy")):
            size = self._party(t)
            tags = ["couple", "mild"] if "not spicy" in t or "mild" in t else ["couple"]
            if "kids" in t:
                tags = ["kids", "mild"]
            return {"reply": rec(tags, size), "tool_calls": log, "basket": ss.snapshot()}

        # explicit order: "those two" / "both"
        if any(w in t for w in ("those two", "both", "that one", "the first", "the second", "hai món đó", "món đó")):
            r = ss.add_items_from_mention("those two" if "two" in t or "both" in t or "hai" in t else "that one")
            log.append({"tool": "add_items_from_mention", "result": r})
            if r.get("clarify"):
                return {"reply": r["clarify"], "tool_calls": log, "basket": ss.snapshot()}
            names = ", ".join(a.get("name", a.get("sku", "?")) for a in r.get("added", []))
            return {"reply": f"Adding {names}. Total ${ss.total():.2f}. Anything else?", "tool_calls": log, "basket": ss.snapshot()}

        # place order
        if any(w in t for w in ("that's all", "thats all", "that's it", "place the order", "check out", "order that")):
            r = ss.readback()
            p = ss.place_order()
            log.append({"tool": "place_order", "result": p})
            lines = ", ".join(r["lines"]) if r.get("lines") else ""
            total = r.get("total", 0)
            return {"reply": f"Order placed: {lines}. Total ${total:.2f}.", "tool_calls": log, "basket": ss.snapshot()}

        # add named item
        for it in ss.store.list_menu():
            if it.name.lower() in t:
                r = ss.add_item(it.name)
                log.append({"tool": "add_item", "result": r})
                if r.get("not_added"):
                    sub = (r.get("suggested_substitute") or {}).get("name", "")
                    return {"reply": f"{it.name} is sold out. May I suggest {sub} instead?", "tool_calls": log, "basket": ss.snapshot()}
                return {"reply": f"Added {it.name}. Want anything else?", "tool_calls": log, "basket": ss.snapshot()}

        # allergen question
        if "gluten" in t or "allerg" in t or "peanut" in t or "shellfish" in t:
            return {"reply": "I can only confirm allergens listed on the menu. I'll ask the kitchen before ordering.", "tool_calls": log, "basket": ss.snapshot()}

        # fallback: search
        r = ss.search_menu(user_text)
        log.append({"tool": "search_menu", "result": r})
        names = ", ".join(i["name"] for i in r["items"][:2])
        return {"reply": f"We serve {names}. What would you like?", "tool_calls": log, "basket": ss.snapshot()}

    def _party(self, text: str) -> int:
        import re

        m = re.search(r"\b(one|two|three|four|five|six|1|2|3|4|5|6)\b", text)
        w = m.group(1) if m else "2"
        nums = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        try:
            return int(w)
        except ValueError:
            return nums.get(w, 2)


_KNOWN_PROVIDERS = ("ollama", "gemini")
_last_fallback_reason: str | None = None


def requested_provider(settings: Any = None, provider: str | None = None) -> str:
    from backend.app.config import get_settings

    settings = settings or get_settings()
    chosen = (provider or getattr(settings, "llm_provider", "") or "ollama").strip().lower()
    if chosen not in _KNOWN_PROVIDERS:
        logger.warning("Unknown LLM_PROVIDER %r; using ollama", chosen)
        return "ollama"
    return chosen


def _ollama_unavailable_reason() -> str | None:
    """Why the Ollama agent can't be used right now, or None if it can.

    Reads only the cached startup probe, so building an agent never blocks the event loop. A server that
    was never probed (scripts, benchmarks) gets the Ollama agent and finds out on its first request.
    """
    try:
        import langchain_ollama  # noqa: F401

        from backend.app.pipeline import llm_ollama
    except ImportError as exc:
        return f"langchain-ollama is not installed ({exc})"
    probe = llm_ollama.get_last_probe()
    if probe is None or probe.healthy:
        return None
    return probe.detail


def build_waiter_agent(provider: str | None = None) -> WaiterAgent | OllamaWaiterAgent | RuleBasedWaiterAgent:
    """Local Ollama by default; Gemini when asked for or when Ollama isn't usable; rule-based without keys.

    A missing provider never breaks a session, and /health reports which agent is actually active.
    """
    from backend.app.config import get_settings

    global _last_fallback_reason
    settings = get_settings()
    if requested_provider(settings, provider) == "ollama":
        reason = _ollama_unavailable_reason()
        if reason is None:
            _last_fallback_reason = None
            return OllamaWaiterAgent()
        if reason != _last_fallback_reason:
            _last_fallback_reason = reason
            fallback = "Gemini" if settings.keys_configured.get("gemini") else "the rule-based agent"
            logger.warning("Ollama unavailable (%s); falling back to %s", reason, fallback)
    if settings.keys_configured.get("gemini"):
        return WaiterAgent()
    return RuleBasedWaiterAgent()


class WaiterLLMRuntime:
    """What the server started for the waiter model: the warm-up task and its outcome."""

    def __init__(self) -> None:
        self.warmup_task: asyncio.Task[None] | None = None
        self.warm = False
        self.warmup_ms: float | None = None
        self.warmup_error: str | None = None

    async def _warm_up(self, settings: Any) -> None:
        from backend.app.pipeline import llm_ollama

        runnable = llm_ollama.get_bound_model(ollama_params(settings), llm_ollama.to_openai_tools(TOOL_SCHEMAS))
        try:
            timings = await llm_ollama.warm_up(runnable, SYSTEM_INSTRUCTION)
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort; the first real turn tries again
            self.warmup_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Ollama warm-up failed: %s", self.warmup_error)
            return
        self.warm = True
        self.warmup_ms = timings.get("warmup_ms")
        logger.info("Ollama warm-up done in %s ms (load %s ms)", self.warmup_ms, timings.get("load_ms"))

    async def aclose(self) -> None:
        task, self.warmup_task = self.warmup_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


_runtime = WaiterLLMRuntime()


def get_waiter_llm_runtime() -> WaiterLLMRuntime:
    return _runtime


async def start_waiter_llm(settings: Any = None) -> WaiterLLMRuntime:
    """Probe Ollama at startup and, if it is healthy, warm the model up in the background.

    The warm-up is not awaited, so the server answers /health straight away. The first guest then gets a
    model that is already loaded with the same num_ctx and has the system prompt and tools cached.
    """
    from backend.app.config import get_settings

    global _runtime
    settings = settings or get_settings()
    await _runtime.aclose()
    _runtime = WaiterLLMRuntime()
    if settings.agent_mode != "waiter" or requested_provider(settings) != "ollama":
        return _runtime
    try:
        from backend.app.pipeline import llm_ollama
    except ImportError as exc:
        logger.warning("Ollama agent unavailable: %s", exc)
        return _runtime
    probe = await llm_ollama.probe_ollama(
        settings.ollama_base_url, settings.ollama_model, timeout_s=settings.ollama_probe_timeout_s
    )
    llm_ollama.set_last_probe(probe)
    if not probe.healthy:
        logger.warning("Ollama not ready at startup: %s", probe.detail)
        return _runtime
    if settings.ollama_warmup:
        _runtime.warmup_task = asyncio.create_task(_runtime._warm_up(settings))
    return _runtime


async def refresh_ollama_probe(settings: Any = None, *, retry_after_s: float = 5.0) -> Any:
    """Re-probe a failed Ollama before a new session, so an Ollama started after the server is picked up.

    A healthy probe is kept (a failing request marks Ollama down by itself), and a server that never
    probed is left alone.
    """
    from backend.app.config import get_settings

    settings = settings or get_settings()
    if settings.agent_mode != "waiter" or requested_provider(settings) != "ollama":
        return None
    try:
        from backend.app.pipeline import llm_ollama
    except ImportError:
        return None
    probe = llm_ollama.get_last_probe()
    if probe is None or probe.healthy or time.time() - probe.checked_at < retry_after_s:
        return probe
    probe = await llm_ollama.probe_ollama(
        settings.ollama_base_url, settings.ollama_model, timeout_s=settings.ollama_probe_timeout_s
    )
    llm_ollama.set_last_probe(probe)
    if probe.healthy:
        logger.info("Ollama is reachable again; new sessions use the local model")
    return probe


def waiter_llm_status(settings: Any = None) -> dict[str, Any]:
    """The waiter model as /health reports it: what was asked for and what is actually answering."""
    from backend.app.config import get_settings

    settings = settings or get_settings()
    agent = build_waiter_agent()
    status: dict[str, Any] = {
        "provider_requested": requested_provider(settings),
        "provider_active": waiter_provider_label(agent),
        "model": getattr(agent, "model", None),
        "template_replies": bool(settings.waiter_template_replies),
    }
    if status["provider_requested"] == "ollama":
        probe = None
        try:
            from backend.app.pipeline import llm_ollama

            probe = llm_ollama.get_last_probe()
        except ImportError:
            pass
        runtime = get_waiter_llm_runtime()
        status["ollama"] = {
            "base_url": settings.ollama_base_url,
            "probed": probe is not None,
            "reachable": probe.reachable if probe else None,
            "model_present": probe.model_present if probe else None,
            "detail": probe.detail if probe else "not probed",
            "checked_at": probe.checked_at if probe else None,
            "warm": runtime.warm,
            "warmup_ms": runtime.warmup_ms,
            "warmup_error": runtime.warmup_error,
        }
    return status


def waiter_provider_label(agent: Any) -> str:
    """The label turn metrics and events report for whichever agent actually handled the turn."""
    if isinstance(agent, WaiterAgent):
        return "gemini_tools"
    if isinstance(agent, RuleBasedWaiterAgent):
        return "rulebased"
    return str(getattr(agent, "provider_label", type(agent).__name__))
