from __future__ import annotations

import time
import uuid
import re

from ..domain.restaurant.models import IntentProposal
from ..domain.restaurant.workflow import OrderWorkflow
from ..providers.llm.ollama import OllamaClient
from .language_router import resolve_language
from .response_renderer import render_clarification, render_kitchen_decision, render_order_response


class RealtimeSession:
    def __init__(
        self, table_id: str, workflow: OrderWorkflow, llm: OllamaClient | None = None,
        *, session_id: str | None = None, order_id: str | None = None,
    ):
        self.table_id = table_id
        self.session_id = session_id or str(uuid.uuid4())
        self.order_id = order_id
        self.workflow, self.llm = workflow, llm
        self.last_recommendations: list[str] = []

    def current_order(self) -> dict | None:
        repository = getattr(self.workflow, "repository", None)
        if not repository or not self.order_id:
            return None
        order = repository.get_order(self.order_id)
        return self.workflow.describe_order(order) if order else None

    def _menu_answer(self, action: str, transcript: str, language: str) -> dict:
        available = self.workflow.store.list_menu(available_only=True)
        if action == "recommend":
            mild = "mild" in transcript.lower() or "suave" in transcript.lower()
            choices = [item for item in available if item.spicy_level <= 1] if mild else available
            choices.sort(key=lambda item: (-item.ordered_count, item.name))
            choices = choices[:2]
            self.last_recommendations = [item.sku for item in choices]
        else:
            choices = available[:5]
        names = ", ".join(f"{item.name} (${item.price:.2f})" for item in choices)
        if action == "recommend":
            response = f"I recommend {names}." if language != "es" else f"Le recomiendo {names}."
        else:
            response = f"Available dishes include {names}." if language != "es" else f"Los platos disponibles incluyen {names}."
        return {"type": "workflow_update", "status": action, "response_text": response, "recommendations": [item.as_dict() for item in choices]}

    async def _localize_verified(self, result: dict, language: str) -> None:
        """Localize speech without allowing the model to change menu names or prices."""
        if language in {"en", "es"} or not self.llm or not hasattr(self.llm, "localize_verified_response"):
            return
        original = result.get("response_text", "")
        names = [line["name"] for line in result.get("basket", [])]
        names += [item["name"] for item in result.get("recommendations", [])]
        names += [item["name"] for item in result.get("alternatives", [])]
        if result.get("requested_name"):
            names.append(result["requested_name"])
        decision = result.get("decision") or {}
        for proposal in decision.get("substitutions") or []:
            names.extend([proposal.get("from_name", ""), proposal.get("to_name", "")])
        try:
            localized = await self.llm.localize_verified_response(
                {"text": original, "preserve_names_exactly": names}, language,
            )
            amounts = re.findall(r"\$\d+(?:\.\d{2})?", original)
            if all(name.casefold() in localized.casefold() for name in names if name) and all(amount in localized for amount in amounts):
                result["response_text"] = localized
        except Exception:  # noqa: BLE001
            pass  # English verified response is safer than a failed localization.

    async def handle_transcript(self, transcript: str, language: str = "en") -> dict:
        started = time.perf_counter()
        current = self.current_order()
        if self.llm:
            intent = await self.llm.extract_intent(transcript, {
                "menu": [item.as_dict() for item in self.workflow.store.list_menu()],
                "current_state": {
                    "order_id": current["order_id"] if current else None,
                    "status": current["status"] if current else None,
                    "items": current["basket"] if current else [],
                    "total": current["total"] if current else 0,
                    "latest_decision": current["latest_decision"] if current else None,
                    "last_recommendations": self.last_recommendations,
                },
            })
        else:
            intent = IntentProposal(source_language=language, action="clarify", needs_clarification=True, clarification_question="Please confirm your order.")
        resolved, supported = resolve_language(intent.source_language or language)
        base = {"type": "workflow_update", "language_code": resolved, "tts_supported": supported}
        if intent.action == "clarify" or intent.needs_clarification:
            result = {"status": "clarification_required", "response_text": intent.clarification_question or "Please clarify your order."}
        elif intent.action in {"recommend", "menu_query"}:
            result = self._menu_answer(intent.action, transcript, resolved)
        else:
            if current and current["status"] in {"cancelled", "ready", "rejected"} and intent.action == "create_or_update_order":
                self.order_id = None
            result = self.workflow.submit(self.table_id, self.session_id, transcript, intent, self.order_id)
            if result.get("order_id"):
                self.order_id = result["order_id"]
                result["response_text"] = render_order_response(result, intent.action, resolved)
            else:
                unavailable = result.get("unavailable_items") or []
                if unavailable:
                    item = self.workflow.store.get_item(unavailable[0])
                    result["requested_name"] = item.name if item else unavailable[0]
                result["response_text"] = render_clarification(result, resolved)
        await self._localize_verified(result, resolved)
        return {**result, **base, "pipeline_ms": round((time.perf_counter() - started) * 1000, 2)}

    async def handle_kitchen_decision(self, order: dict, decision: dict) -> dict | None:
        if order["order_id"] != self.order_id:
            return None
        described = self.workflow.describe_order(order)
        language, supported = resolve_language(order["response_language"])
        substitutions = []
        for proposal in decision.get("substitutions") or []:
            original = self.workflow.store.get_item(proposal.get("from_sku", ""))
            replacement = self.workflow.store.get_item(proposal.get("to_sku", ""))
            substitutions.append({
                **proposal,
                "from_name": original.name if original else proposal.get("from_sku"),
                "to_name": replacement.name if replacement else proposal.get("to_sku"),
            })
        enriched = {**decision, "substitutions": substitutions}
        result = {
            "type": "workflow_update", "source": "kitchen", "status": order["status"],
            "order_id": order["order_id"], "revision": order["current_revision"],
            "basket": described["basket"], "total": described["total"], "currency": "USD",
            "decision": enriched, "language_code": language, "tts_supported": supported,
            "response_text": render_kitchen_decision(described, enriched, language),
        }
        await self._localize_verified(result, language)
        return result
