from __future__ import annotations

import time
import uuid
import re

from ..domain.restaurant.mentions import spoken_modifiers
from ..domain.restaurant.models import IntentProposal
from ..domain.restaurant.recommend import recommend
from ..domain.restaurant.workflow import OrderWorkflow
from ..providers.llm.ollama import OllamaClient
from .dialogue import CLOSED_STATUSES, DialogueState, resolve
from .language_router import resolve_language
from .response_renderer import SKU_PATTERN, render_clarification, render_kitchen_decision, render_order_response, render_prompt


class RealtimeSession:
    def __init__(
        self, table_id: str, workflow: OrderWorkflow, llm: OllamaClient | None = None,
        *, session_id: str | None = None, order_id: str | None = None,
    ):
        self.table_id = table_id
        self.session_id = session_id or str(uuid.uuid4())
        self.order_id = order_id
        self.workflow, self.llm = workflow, llm
        self.dialogue = DialogueState.from_dict(self._repository_call("load_dialogue", self.session_id, table_id))

    def _repository_call(self, name: str, *args):
        """Call an optional repository method; scripted test workflows may not have one."""
        method = getattr(getattr(self.workflow, "repository", None), name, None)
        return method(*args) if method else None

    def _save_dialogue(self) -> None:
        self._repository_call("save_dialogue", self.session_id, self.table_id, self.dialogue.to_dict())

    def _heard_modifiers_only(self, intent: IntentProposal, transcript: str) -> IntentProposal:
        """Drop modifiers the guest never said; a small model tends to add every allowed one."""
        if not intent.items:
            return intent
        items = []
        for item in intent.items:
            menu_item = self.workflow.store.get_item(item.sku)
            kept = spoken_modifiers(item.modifiers, transcript, menu_item.name if menu_item else "")
            items.append(item.model_copy(update={"modifiers": kept}))
        return intent.model_copy(update={"items": items})

    def current_order(self) -> dict | None:
        repository = getattr(self.workflow, "repository", None)
        if not repository or not self.order_id:
            return None
        order = repository.get_order(self.order_id)
        return self.workflow.describe_order(order) if order else None

    def _menu_answer(self, action: str, transcript: str, language: str, order: dict | None = None) -> dict:
        if action == "recommend":
            allergies = order.get("allergies", []) if order and order.get("status") not in CLOSED_STATUSES else []
            choices = recommend(self.workflow.store, transcript, allergies)
        else:
            choices = self.workflow.store.list_menu(available_only=True)[:5]
        self.dialogue.last_offered = [item.sku for item in choices]
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

    def _error_names(self, result: dict) -> dict[str, str]:
        """Dish names for the SKUs inside error strings, so the guest never hears a SKU."""
        names = {}
        for error in result.get("errors") or []:
            for sku in SKU_PATTERN.findall(str(error)):
                item = self.workflow.store.get_item(sku)
                if item:
                    names[sku] = item.name
        return names

    async def handle_transcript(self, transcript: str, language: str = "en") -> dict:
        started = time.perf_counter()
        current = self.current_order()
        self.dialogue.begin_turn()
        if self.llm:
            intent = await self.llm.extract_intent(transcript, {
                "menu": [item.as_dict() for item in self.workflow.store.list_menu()],
                "current_state": {
                    "order_id": current["order_id"] if current else None,
                    "status": current["status"] if current else None,
                    "items": current["basket"] if current else [],
                    "total": current["total"] if current else 0,
                    "latest_decision": current["latest_decision"] if current else None,
                    **self.dialogue.to_prompt(self.workflow.store),
                },
            })
        else:
            intent = IntentProposal(source_language=language, action="clarify", needs_clarification=True, clarification_question="Please confirm your order.")
        intent = self._heard_modifiers_only(intent, transcript)
        resolved, supported = resolve_language(intent.source_language or language)
        base = {"type": "workflow_update", "language_code": resolved, "tts_supported": supported}
        resolution = resolve(intent, self.dialogue, current, self.workflow.store, transcript)
        if resolution.clear_pending:
            self.dialogue.pending = None
        if resolution.new_pending:
            self.dialogue.set_pending(**resolution.new_pending)
        wrote_revision = False
        if resolution.kind == "clarify":
            question = None if resolution.message else intent.clarification_question
            result = {"status": "clarification_required", "response_text": question or render_prompt(resolution.message or "clarify", resolved)}
        elif resolution.kind == "menu":
            result = self._menu_answer(intent.action, transcript, resolved, current)
        elif resolution.kind == "ask":
            result = {"status": "awaiting_reply", "response_text": render_prompt(resolution.message, resolved)}
        elif resolution.kind == "readback":
            text = render_order_response(current, "readback", resolved)
            if resolution.new_pending:
                text = f"{text} {render_prompt(resolution.new_pending['kind'], resolved)}"
            result = {**current, "response_text": text}
        elif resolution.kind == "place":
            result = self.workflow.place(self.table_id, self.session_id, transcript, self.order_id, intent.source_language)
            if result.get("order_id"):
                wrote_revision = not result.get("already_placed")
                result["response_text"] = render_order_response(result, "place_order", resolved)
            else:
                result["response_text"] = render_clarification(result, resolved, self._error_names(result))
        else:
            submitted = resolution.intent
            if current and current["status"] in CLOSED_STATUSES and submitted.action == "create_or_update_order":
                self.order_id = None
                self.dialogue.last_added = []
            result = self.workflow.submit(self.table_id, self.session_id, transcript, submitted, self.order_id)
            if result.get("order_id"):
                self.order_id = result["order_id"]
                wrote_revision = True
                if submitted.action in {"create_or_update_order", "replace_item"}:
                    self.dialogue.last_added = [item.sku for item in submitted.items]
                result["response_text"] = render_order_response(result, submitted.action, resolved)
            else:
                unavailable = result.get("unavailable_items") or []
                if unavailable:
                    item = self.workflow.store.get_item(unavailable[0])
                    result["requested_name"] = item.name if item else unavailable[0]
                    alternatives = result.get("alternatives") or []
                    # Remember the refusal so "X instead" next turn adds X and removes nothing.
                    self.dialogue.set_pending("offer_substitute", refused=unavailable[0],
                                              offered=alternatives[0]["sku"] if alternatives else None)
                result["response_text"] = render_clarification(result, resolved, self._error_names(result))
        result["wrote_revision"] = wrote_revision
        self._save_dialogue()
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
        if decision.get("action") == "propose_substitute":
            self.dialogue.set_pending("kitchen_substitute")
            self._save_dialogue()
        result = {
            "type": "workflow_update", "source": "kitchen", "status": order["status"],
            "order_id": order["order_id"], "revision": order["current_revision"],
            "basket": described["basket"], "total": described["total"], "currency": "USD",
            "decision": enriched, "language_code": language, "tts_supported": supported,
            "response_text": render_kitchen_decision(described, enriched, language),
        }
        await self._localize_verified(result, language)
        return result
