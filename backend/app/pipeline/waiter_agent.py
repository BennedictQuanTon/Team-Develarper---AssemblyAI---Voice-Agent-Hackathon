"""Waiter agent — Gemini function-calling loop over the deterministic toolkit.

The LLM decides tools, the toolkit computes truth. Tool schema (JSON Schema)
is mirrored for plumbing into google.genai `FunctionDeclaration` / `Tool`.

If GEMINI_API_KEY is absent, a deterministic `RuleBasedWaiterAgent` keeps the
golden path working locally (no API) so the UI/playbook can be tested offline.
"""

from __future__ import annotations

import json
import logging
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
            return ss.order_items(args.get("items") or [], bool(args.get("place", False)))
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


class OllamaWaiterAgent:
    """Local Qwen / Ollama-backed function-calling loop with zero cloud latency and no rate limits."""

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        from backend.app.config import get_settings

        settings = get_settings()
        self.model = (model or getattr(settings, "ollama_model", "") or "qwen2.5:3b").strip()
        self.base_url = (base_url or getattr(settings, "ollama_base_url", "") or "http://localhost:11434").rstrip("/")
        self.available = True

    def _tool_spec(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s["description"],
                    "parameters": s["parameters"],
                },
            }
            for s in TOOL_SCHEMAS
        ]

    async def respond(
        self,
        ss: WaiterSession,
        user_text: str,
        prefetch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import httpx

        if prefetch:
            user_text = _format_prefetch(prefetch) + "\n\n" + user_text

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": user_text},
        ]
        tool_log: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=45.0) as client:
            for _ in range(6):
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "tools": self._tool_spec(),
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 90,
                    },
                }
                res = await client.post(f"{self.base_url}/api/chat", json=payload)
                if res.status_code != 200:
                    raise RuntimeError(f"Ollama API error {res.status_code}: {res.text[:200]}")
                data = res.json()
                msg = data.get("message", {})
                text = (msg.get("content") or "").strip()
                calls = msg.get("tool_calls") or []

                if not calls:
                    if text:
                        for it in ss.store.list_menu():
                            if it.name.lower() in text.lower():
                                ss._record_mentions([it])
                    return {
                        "reply": text,
                        "tool_calls": tool_log,
                        "basket": ss.snapshot(),
                    }

                # Record assistant tool call turn
                messages.append(msg)

                # Execute calls and feed results back into context
                for call in calls:
                    fn = call.get("function", {})
                    name = fn.get("name")
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    result = _exec_waiter_tool(ss, name, args)
                    tool_log.append({"tool": name, "args": args, "result": result})
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(result),
                    })

        return {
            "reply": text if text else "Let me read that back for you.",
            "tool_calls": tool_log,
            "basket": ss.snapshot(),
        }


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


def build_waiter_agent(provider: str | None = None) -> WaiterAgent | OllamaWaiterAgent | RuleBasedWaiterAgent:
    from backend.app.config import get_settings

    settings = get_settings()
    p = (provider or getattr(settings, "llm_provider", "") or "gemini").lower()
    if p == "ollama":
        return OllamaWaiterAgent()
    if settings.keys_configured.get("gemini"):
        return WaiterAgent()
    return RuleBasedWaiterAgent()

