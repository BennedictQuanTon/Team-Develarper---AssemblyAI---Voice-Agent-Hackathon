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
    "- Resolve 'those two' / 'that one' / 'both' / 'the first' via add_items_from_mention with the exact ref word.\n"
    "- For allergens not on the menu, say you must check the kitchen and do not guess.\n"
    "- Keep every spoken reply under ~25 words, conversational, no markdown, no emoji.\n"
    "- Confirm by reading back the order before place_order.\n"
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
        "name": "add_item",
        "description": "Add a dish to the order by sku or name.",
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


class WaiterAgent:
    """Gemini-backed function-calling loop."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from backend.app.config import get_settings

        settings = get_settings()
        self.api_key = (api_key or settings.gemini_api_key).strip()
        self.model = (model or settings.gemini_model or "gemini-3.5-flash-lite").strip()
        self.available = bool(self.api_key)

    def _tool_spec(self) -> list[Any]:
        from google.genai import types

        return [
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

    async def respond(
        self,
        ss: WaiterSession,
        user_text: str,
    ) -> dict[str, Any]:
        """Run one conversational turn. Returns {
           reply: str, tool_calls: [...], basket: snapshot, mentions: [...]
        }"""
        if not self.available:
            raise RuntimeError("Gemini not available (no API key)")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.3,
            max_output_tokens=90,
            tools=self._tool_spec(),
        )

        contents: list[Any] = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
        tool_log: list[dict[str, Any]] = []

        for _ in range(6):
            lim = _gemini_limiter()
            if lim is not None:
                await lim.acquire()
            import asyncio as _a

            while True:
                try:
                    r = await _a.to_thread(
                        lambda: client.models.generate_content(
                            model=self.model, contents=contents, config=config
                        )
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)
                    is_ratelimit = ("429" in err) or ("RESOURCE_EXHAUSTED" in err) or ("quota" in err.lower())
                    if not is_ratelimit:
                        raise
                    await _a.sleep(5.0)
                    continue
            if r.candidates and r.candidates[0].content:
                parts = r.candidates[0].content.parts
            else:
                parts = []

            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            text = "".join(getattr(p, "text", "") or "" for p in parts).strip()

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

            # execute calls
            function_parts: list[Any] = []
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
            # append model's tool-call turn and feed results
            contents.append(r.candidates[0].content)
            contents.append(types.Content(role="user", parts=function_parts))

        return {
            "reply": text if text else "Let me read that back for you.",
            "tool_calls": tool_log,
            "basket": ss.snapshot(),
        }

def _exec_waiter_tool(ss: WaiterSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "search_menu":
            return ss.search_menu(args.get("query", ""), int(args.get("limit", 5) or 5))
        if name == "recommend_dishes":
            return ss.recommend_dishes(args.get("tags", ""), int(args.get("party_size", 2) or 2), int(args.get("limit", 2) or 2))
        if name == "check_availability":
            return ss.check_availability(args.get("sku", ""))
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
    ) -> dict[str, Any]:
        import httpx

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

    async def respond(self, ss: WaiterSession, user_text: str) -> dict[str, Any]:
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

