from __future__ import annotations

import uuid
from typing import Any

from .models import IntentProposal
from .repository import SQLiteOrderRepository
from .store import LanternStore
from .validation import validate_intent


class OrderWorkflow:
    def __init__(self, repository: SQLiteOrderRepository, store: LanternStore):
        self.repository, self.store = repository, store

    def describe_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """Add verified menu facts to a persisted order for clients and speech."""
        basket = []
        for line in order["items"]:
            menu_item = self.store.get_item(line["sku"])
            if menu_item is None:
                continue
            quantity = int(line["quantity"])
            basket.append({
                **line,
                "name": menu_item.name,
                "unit_price": menu_item.price,
                "line_total": round(menu_item.price * quantity, 2),
            })
        return {**order, "basket": basket, "total": round(sum(line["line_total"] for line in basket), 2), "currency": "USD"}

    def _current(self, order_id: str | None, table_id: str, guest_session_id: str) -> dict[str, Any] | None:
        order = self.repository.get_order(order_id) if order_id else None
        if order and (order["table_id"] != table_id or order["guest_session_id"] != guest_session_id):
            raise ValueError("order belongs to another guest session")
        return order

    def alternatives(self, sku: str, allergies: list[str] | None = None) -> list[dict[str, Any]]:
        requested = self.store.get_item(sku)
        if requested is None:
            return []
        avoided = {allergy.lower() for allergy in (allergies or [])}
        candidates = [
            item for item in self.store.list_menu(available_only=True)
            if item.sku != sku and item.category == requested.category
            and not avoided.intersection(set(item.allergens) - {"none"})
        ]
        candidates.sort(key=lambda item: (-item.ordered_count, item.name))
        return [item.as_dict() for item in candidates[:2]]

    def validate_kitchen_decision(self, order_id: str, decision: dict[str, Any]) -> None:
        order = self.repository.get_order(order_id)
        if not order:
            raise KeyError(order_id)
        if not order["placed"]:
            raise ValueError("this order has not been placed yet")
        if decision["action"] != "propose_substitute":
            return
        substitutions = decision.get("substitutions") or []
        if len(substitutions) != 1:
            raise ValueError("propose_substitute requires exactly one replacement")
        proposal = substitutions[0]
        from_sku, to_sku = proposal.get("from_sku"), proposal.get("to_sku")
        if not any(line["sku"] == from_sku for line in order["items"]):
            raise ValueError("replacement item is not in the current order")
        replacement = self.store.get_item(to_sku)
        if not replacement or not replacement.available:
            raise ValueError("replacement is unavailable")
        if {a.lower() for a in order["allergies"]} & (set(replacement.allergens) - {"none"}):
            raise ValueError("replacement conflicts with guest allergies")

    def submit(self, table_id: str, guest_session_id: str, transcript: str, intent: IntentProposal, order_id: str | None = None) -> dict[str, Any]:
        order = self._current(order_id, table_id, guest_session_id)
        action = intent.action
        if action not in {"create_or_update_order", "replace_item", "remove_item", "cancel_order", "accept_substitute", "reject_substitute"}:
            return {"status": "clarification_required", "errors": [f"unsupported order action: {action}"]}

        effective_allergies = list(dict.fromkeys((order["allergies"] if order else []) + intent.allergies))
        checked_intent = intent.model_copy(update={"allergies": effective_allergies}) if action in {"create_or_update_order", "replace_item"} else intent
        errors = validate_intent(checked_intent, self.store)
        if errors:
            unavailable = [item.sku for item in intent.items if not self.store.is_available(item.sku) and self.store.get_item(item.sku)] if action in {"create_or_update_order", "replace_item"} else []
            return {
                "status": "clarification_required", "errors": errors, "intent": intent.model_dump(),
                "unavailable_items": unavailable,
                "alternatives": self.alternatives(unavailable[0], effective_allergies) if unavailable else [],
            }

        if action != "create_or_update_order" and order is None:
            return {"status": "clarification_required", "errors": ["there is no current order"]}
        if order and order["status"] in {"cancelled", "ready", "rejected"}:
            return {"status": "clarification_required", "errors": ["this order is closed; start a new order"]}

        lines = [dict(line) for line in order["items"]] if order else []
        allergies = effective_allergies
        # Changes stay a draft until the guest places the order; after that they go to the kitchen again.
        status = "pending_kitchen" if order and order["placed"] else "draft"
        if action == "create_or_update_order":
            for item in intent.items:
                match = next((line for line in lines if line["sku"] == item.sku and line.get("modifiers", []) == item.modifiers), None)
                if match:
                    match["quantity"] += item.quantity
                else:
                    lines.append(item.model_dump())
        elif action == "replace_item":
            match = next((line for line in lines if line["sku"] == intent.replaces_sku), None)
            if match is None:
                return {"status": "clarification_required", "errors": [f"{intent.replaces_sku} is not in the order"]}
            replacement = intent.items[0]
            if replacement.sku == match["sku"] and replacement.modifiers == match.get("modifiers", []):
                return {"status": "clarification_required", "errors": ["the replacement is identical to the current item"]}
            lines.remove(match)
            lines.append(replacement.model_dump())
        elif action == "remove_item":
            for item in intent.items:
                match = next((line for line in lines if line["sku"] == item.sku), None)
                if match is None:
                    return {"status": "clarification_required", "errors": [f"{item.sku} is not in the order"]}
                match["quantity"] -= item.quantity
            lines = [line for line in lines if line["quantity"] > 0]
            if not lines:
                status = "cancelled"
        elif action == "cancel_order":
            lines, status = [], "cancelled"
        elif action in {"accept_substitute", "reject_substitute"}:
            decision = order.get("latest_decision")
            if order["status"] != "substitution_proposed" or not decision or decision["action"] != "propose_substitute":
                return {"status": "clarification_required", "errors": ["there is no pending substitute"]}
            proposals = decision["substitutions"]
            if len(proposals) != 1:
                return {"status": "clarification_required", "errors": ["please choose one substitute"]}
            proposal = proposals[0]
            if action == "accept_substitute":
                from_sku, to_sku = proposal["from_sku"], proposal["to_sku"]
                replacement = self.store.get_item(to_sku)
                if not replacement or not replacement.available:
                    return {"status": "clarification_required", "errors": ["the proposed substitute is unavailable"]}
                if {a.lower() for a in allergies} & (set(replacement.allergens) - {"none"}):
                    return {"status": "clarification_required", "errors": ["the proposed substitute conflicts with an allergy"]}
                match = next((line for line in lines if line["sku"] == from_sku), None)
                if match is None:
                    return {"status": "clarification_required", "errors": ["the original item is no longer in the order"]}
                match.update({"sku": to_sku, "modifiers": []})
            else:
                status = "clarification_required"

        saved = self.repository.create_revision(
            order["order_id"] if order else str(uuid.uuid4()), table_id, guest_session_id,
            intent.source_language, transcript, lines, allergies, status,
            expected_revision=order["current_revision"] if order else 0,
        )
        return self.describe_order(saved)

    def place(self, table_id: str, guest_session_id: str, transcript: str, order_id: str | None, language: str = "en") -> dict[str, Any]:
        """Send the draft to the kitchen: a new revision with the same lines, status pending_kitchen."""
        order = self._current(order_id, table_id, guest_session_id)
        if not order or order["status"] in {"cancelled", "ready", "rejected"} or not order["items"]:
            return {"status": "clarification_required", "errors": ["there is no order to place"]}
        if order["placed"]:
            return {**self.describe_order(order), "already_placed": True}
        saved = self.repository.create_revision(
            order["order_id"], table_id, guest_session_id, language, transcript,
            [dict(line) for line in order["items"]], order["allergies"], "pending_kitchen",
            expected_revision=order["current_revision"],
        )
        return self.describe_order(saved)
