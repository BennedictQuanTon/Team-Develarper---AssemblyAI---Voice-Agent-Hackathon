from __future__ import annotations

import json
import httpx
from ...domain.restaurant.mentions import mentioned_skus
from ...domain.restaurant.models import IntentProposal

# Required so the model always commits to an action and a reference before listing items.
INTENT_SCHEMA = {**IntentProposal.model_json_schema(), "required": ["action", "ref", "items"]}


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        thinking: bool = False,
        *,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url, self.model, self.thinking = base_url.rstrip('/'), model, thinking
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=3.0))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _chat(self, prompt: str, *, schema: dict | str = "json", max_tokens: int = 192) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": self.thinking,
            "format": schema,
            "keep_alive": -1,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096},
        }
        response = await self._client.post(f"{self.base_url}/api/generate", json=payload)
        response.raise_for_status()
        body = response.json()
        return body.get("response", ""), body

    @staticmethod
    def _compact_menu(menu: list[dict]) -> list[dict]:
        return [
            {
                "sku": item.get("sku"),
                "name": item.get("name"),
                "available": item.get("available", True),
                "modifiers": item.get("modifiers", []),
                "allergens": item.get("allergens", []),
            }
            for item in menu
        ]

    @staticmethod
    def _entity_hints(transcript: str, menu: list[dict]) -> list[dict]:
        hinted_skus = mentioned_skus(transcript, menu, distinctive_words=False)
        return [item for item in menu if item.get("sku") in hinted_skus]

    @staticmethod
    def _script_language(transcript: str) -> str | None:
        codepoints = [ord(char) for char in transcript]
        if any(0x3040 <= value <= 0x30FF for value in codepoints):
            return "ja"
        if any(0x0900 <= value <= 0x097F for value in codepoints):
            return "hi"
        if any(0x4E00 <= value <= 0x9FFF for value in codepoints):
            return "zh"
        return None

    async def warmup(self) -> None:
        await self._chat(
            'Return exactly {"ready":true}.',
            schema={"type": "object", "properties": {"ready": {"type": "boolean"}}, "required": ["ready"]},
            max_tokens=16,
        )

    async def extract_intent(self, transcript: str, context: dict) -> IntentProposal:
        menu = self._compact_menu(context.get("menu", []))
        hints = self._entity_hints(transcript, menu)
        current = context.get("current_state") or {}
        prompt = (
            "Extract one restaurant action as schema-valid JSON. "
            "Use menu SKUs and allowed modifiers exactly; ENTITY_HINTS are deterministic matches. "
            "For a new/additional dish, emit create_or_update_order with only the requested NEW items, not the full basket. "
            "For dishes the waiter just offered (STATE.last_offered), such as 'those two' or 'the first one', emit create_or_update_order with ref offered_all, offered_first or offered_second and no items. "
            "When STATE.pending shows a refused dish, 'X instead' means create_or_update_order with item X and ref pending. "
            "Only when the guest names both dishes, 'X instead of Y', emit replace_item with one new item X and replaces_sku=Y. "
            "For a removal, emit remove_item with the SKU from STATE.items. "
            "When the guest is finished or asks to place, send or confirm the order, emit place_order with no items. "
            "For cancellation, emit cancel_order with no items. "
            "For yes or no to the waiter's question in STATE.pending, emit confirm or decline with no items. "
            "For menu questions or recommendations, emit menu_query or recommend with no items. "
            "Otherwise set ref to none. "
            "If a known menu item is sold out, still return its SKU so deterministic code can explain and suggest an alternative. "
            "Unknown dishes or unsupported modifiers require clarify with no items. "
            "Set source_language to USER's language. Do not explain outside the JSON fields.\n"
            f"ENTITY_HINTS={json.dumps(hints, ensure_ascii=False, separators=(',', ':'))}\n"
            f"MENU={json.dumps(menu, ensure_ascii=False, separators=(',', ':'))}\n"
            f"STATE={json.dumps(current, ensure_ascii=False, separators=(',', ':'))}\n"
            f"USER={transcript}"
        )
        raw, _ = await self._chat(prompt, schema=INTENT_SCHEMA, max_tokens=224)
        try:
            intent = IntentProposal.model_validate_json(raw)
        except Exception:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                intent = IntentProposal.model_validate_json(raw[start:end + 1])
            else:
                raise ValueError("Ollama returned malformed intent JSON")
        script_language = self._script_language(transcript)
        if script_language:
            intent = intent.model_copy(update={"source_language": script_language})
        return intent

    async def localize_verified_response(self, facts: dict, language: str) -> str:
        schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        raw, _ = await self._chat(
            f"Translate text into {language}. Keep every preserve_names_exactly entry, currency symbol and number unchanged. Do not add facts. Return JSON only.\n{json.dumps(facts, ensure_ascii=False)}",
            schema=schema,
            max_tokens=96,
        )
        return str(json.loads(raw)["text"])
