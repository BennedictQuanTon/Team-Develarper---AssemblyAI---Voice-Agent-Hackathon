from __future__ import annotations

from .models import IntentProposal
from .store import LanternStore


def validate_intent(intent: IntentProposal, store: LanternStore) -> list[str]:
    errors: list[str] = []
    for item in intent.items:
        menu_item = store.get_item(item.sku)
        if menu_item is None:
            errors.append(f"unknown sku: {item.sku}")
            continue
        if not menu_item.available:
            errors.append(f"unavailable item: {item.sku}")
        allowed = {m.lower() for m in menu_item.modifiers}
        for modifier in item.modifiers:
            if modifier.lower() not in allowed:
                errors.append(f"unsupported modifier for {item.sku}: {modifier}")
    if intent.allergies and any(not a.strip() for a in intent.allergies):
        errors.append("allergy must not be empty")
    return errors
