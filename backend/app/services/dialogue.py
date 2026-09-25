"""Short-range dialogue memory and the resolver that turns references into SKUs.

The model reports what a sentence refers to (``IntentProposal.ref``); code decides which
dishes that means. ``DialogueState.to_dict``/``from_dict`` are the seam for persisting this
state per table session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..domain.restaurant.mentions import mentioned_skus
from ..domain.restaurant.models import IntentItem, IntentProposal
from ..domain.restaurant.store import LanternStore

CLOSED_STATUSES = {"cancelled", "ready", "rejected"}
OFFERED_REFS = {"offered_all", "offered_first", "offered_second"}
# Words that make a repeated dish a real addition rather than a restatement of the order.
ADD_WORDS = {"another", "more", "add", "extra", "again", "otra", "otro", "más", "mas"}
# How many guest turns each kind of pending question stays answerable.
PENDING_TURNS = {"offer_substitute": 2, "kitchen_substitute": 2, "confirm_cancel": 1, "confirm_place": 1}


@dataclass
class DialogueState:
    last_offered: list[str] = field(default_factory=list)
    last_added: list[str] = field(default_factory=list)
    pending: dict[str, Any] | None = None

    def set_pending(self, kind: str, **details: Any) -> None:
        # One slot: the newest question replaces any older one.
        self.pending = {"kind": kind, **details, "turns_left": PENDING_TURNS[kind]}

    def begin_turn(self) -> None:
        """Age the pending question; it expires once its turns are used up."""
        if self.pending is None:
            return
        if self.pending.get("turns_left", 0) <= 0:
            self.pending = None
        else:
            self.pending["turns_left"] -= 1

    def reset(self) -> None:
        self.last_offered, self.last_added, self.pending = [], [], None

    def to_prompt(self, store: LanternStore) -> dict[str, Any]:
        def named(skus: list[str]) -> list[dict[str, str]]:
            return [{"sku": sku, "name": item.name} for sku in skus if (item := store.get_item(sku))]

        pending = None
        if self.pending:
            pending = {key: value for key, value in self.pending.items() if key != "turns_left"}
            for key in ("refused", "offered"):
                item = store.get_item(pending.get(key) or "")
                if item:
                    pending[f"{key}_name"] = item.name
        return {"last_offered": named(self.last_offered), "last_added": named(self.last_added), "pending": pending}

    def to_dict(self) -> dict[str, Any]:
        return {"last_offered": list(self.last_offered), "last_added": list(self.last_added),
                "pending": dict(self.pending) if self.pending else None}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DialogueState":
        data = data or {}
        return cls(list(data.get("last_offered") or []), list(data.get("last_added") or []),
                   dict(data["pending"]) if data.get("pending") else None)


ResolutionKind = Literal["submit", "place", "ask", "readback", "clarify", "menu"]


@dataclass
class Resolution:
    """What the turn does. ``message`` is a renderer code for ``ask`` and ``clarify``."""

    kind: ResolutionKind
    intent: IntentProposal | None = None
    message: str | None = None
    clear_pending: bool = False
    new_pending: dict[str, Any] | None = None


def _items(skus: list[str]) -> list[IntentItem]:
    return [IntentItem(sku=sku, quantity=1) for sku in skus]


def _is_restatement(items: list[IntentItem], lines: list[dict[str, Any]], transcript: str) -> bool:
    """The model repeated the whole basket (e.g. to "place" it) instead of adding to it."""
    if len(lines) < 2 or len(items) != len(lines):
        return False
    if ADD_WORDS & set(re.findall(r"[^\W\d_]+", transcript.casefold())):
        return False
    key = lambda sku, quantity, modifiers: (sku, int(quantity), tuple(sorted(modifiers or [])))  # noqa: E731
    wanted = sorted(key(item.sku, item.quantity, item.modifiers) for item in items)
    current = sorted(key(line["sku"], line["quantity"], line.get("modifiers")) for line in lines)
    return wanted == current


def resolve(intent: IntentProposal, dialogue: DialogueState, order: dict[str, Any] | None,
            store: LanternStore, transcript: str) -> Resolution:
    """Map the model's intent onto the dialogue and the saved order. Pure: never writes."""
    pending = dialogue.pending or {}
    kind = pending.get("kind")
    action = intent.action
    lines = order["basket"] if order and order.get("status") not in CLOSED_STATUSES else []
    open_order = bool(order and order.get("status") not in CLOSED_STATUSES)
    confirming = kind in {"confirm_cancel", "confirm_place"}

    if action in {"confirm", "decline", "accept_substitute", "reject_substitute"}:
        yes = action in {"confirm", "accept_substitute"}
        if kind == "kitchen_substitute" or (order and order.get("status") == "substitution_proposed"):
            decided = intent.model_copy(update={"action": "accept_substitute" if yes else "reject_substitute", "items": []})
            return Resolution("submit", decided, clear_pending=True)
        if kind == "offer_substitute":
            if yes and pending.get("offered"):
                added = intent.model_copy(update={"action": "create_or_update_order", "items": _items([pending["offered"]])})
                return Resolution("submit", added, clear_pending=True)
            return Resolution("ask", message="declined_offer", clear_pending=True)
        if kind == "confirm_cancel":
            if yes:
                return Resolution("submit", intent.model_copy(update={"action": "cancel_order", "items": []}), clear_pending=True)
            return Resolution("ask", message="kept_order", clear_pending=True)
        if kind == "confirm_place":
            return (Resolution("place", intent, clear_pending=True) if yes
                    else Resolution("ask", message="anything_else", clear_pending=True))
        if yes and open_order and lines and not order.get("placed"):
            return Resolution("ask", message="confirm_place", new_pending={"kind": "confirm_place"})
        return Resolution("clarify", message="nothing_pending")

    if action == "clarify" or (intent.needs_clarification and action not in {"place_order"}):
        return Resolution("clarify", intent, clear_pending=confirming)
    if action in {"recommend", "menu_query"}:
        return Resolution("menu", intent, clear_pending=confirming)
    if action == "place_order":
        return Resolution("place", intent, clear_pending=True)
    if action == "cancel_order":
        if kind == "confirm_cancel":
            return Resolution("submit", intent, clear_pending=True)
        if not open_order:
            return Resolution("clarify", message="no_order", clear_pending=confirming)
        return Resolution("ask", message="confirm_cancel", new_pending={"kind": "confirm_cancel"})

    if action == "replace_item":
        in_order = {line["sku"] for line in lines}
        target = intent.replaces_sku
        if target is None and intent.ref == "last_added" and len(dialogue.last_added) == 1:
            target = dialogue.last_added[0]
        new_items = intent.items
        if not new_items and kind == "offer_substitute" and pending.get("offered"):
            new_items = _items([pending["offered"]])
        mentioned = mentioned_skus(transcript, store.list_menu())
        as_addition = intent.model_copy(update={"action": "create_or_update_order", "items": new_items, "replaces_sku": None})
        if target in in_order and target in mentioned:
            # The guest named the dish to swap out: a real correction.
            return Resolution("submit", intent.model_copy(update={"replaces_sku": target, "items": new_items}), clear_pending=True)
        if kind == "offer_substitute" and (intent.ref == "pending" or target in {None, pending.get("refused")} or target not in mentioned):
            # "Seabass instead" after the squid was refused: the squid never entered the order.
            return Resolution("submit", as_addition, clear_pending=True)
        if target not in in_order:
            return Resolution("submit", as_addition, clear_pending=confirming)
        return Resolution("submit", intent.model_copy(update={"replaces_sku": target, "items": new_items}), clear_pending=confirming)

    if action == "create_or_update_order":
        items = intent.items
        if intent.ref in OFFERED_REFS:
            offered = dialogue.last_offered
            picked = {"offered_all": offered, "offered_first": offered[:1], "offered_second": offered[1:2]}[intent.ref]
            if not picked:
                return Resolution("clarify", message="which_offered", clear_pending=confirming)
            items = _items(picked)
        elif intent.ref == "pending" and not items and kind == "offer_substitute" and pending.get("offered"):
            items = _items([pending["offered"]])
        if _is_restatement(items, lines, transcript):
            # Usually "that's all" worded as the whole basket: read it back and offer to place it.
            placing = {"kind": "confirm_place"} if not order.get("placed") else None
            return Resolution("readback", clear_pending=confirming, new_pending=placing)
        moved_on = kind == "offer_substitute" and any(item.sku != pending.get("refused") for item in items)
        return Resolution("submit", intent.model_copy(update={"items": items}), clear_pending=confirming or moved_on)

    if action == "remove_item" and not intent.items and intent.ref == "last_added" and dialogue.last_added:
        return Resolution("submit", intent.model_copy(update={"items": _items(dialogue.last_added)}), clear_pending=confirming)
    return Resolution("submit", intent, clear_pending=confirming)
