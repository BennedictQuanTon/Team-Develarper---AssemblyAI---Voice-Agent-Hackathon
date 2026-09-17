from __future__ import annotations

import json
import httpx
from ...domain.restaurant.models import IntentProposal


class OllamaClient:
    def __init__(self, base_url: str, model: str, thinking: bool = False):
        self.base_url, self.model, self.thinking = base_url.rstrip('/'), model, thinking

    async def _chat(self, prompt: str) -> str:
        payload = {"model": self.model, "prompt": prompt, "stream": False, "think": self.thinking, "options": {"temperature": 0}}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            return response.json().get("response", "")

    async def extract_intent(self, transcript: str, context: dict) -> IntentProposal:
        prompt = "Return JSON only matching this schema: {source_language,action,items:[{sku,quantity,modifiers}],allergies,dietary_constraints,substitution_response,needs_clarification,clarification_question}. Menu SKUs: %s\nTranscript: %s" % (json.dumps(context.get("menu", [])), transcript)
        raw = await self._chat(prompt)
        try:
            return IntentProposal.model_validate_json(raw)
        except Exception:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                return IntentProposal.model_validate_json(raw[start:end + 1])
            raise ValueError("Ollama returned malformed intent JSON")

    async def localize_verified_response(self, facts: dict, language: str) -> str:
        return await self._chat(f"Localize this verified restaurant response into {language}. Preserve facts and numbers. Return text only.\n{json.dumps(facts)}")
