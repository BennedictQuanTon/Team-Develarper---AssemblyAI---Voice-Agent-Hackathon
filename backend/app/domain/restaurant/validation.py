from __future__ import annotations

from .models import IntentProposal
from .store import LanternStore


def validate_intent(intent: IntentProposal, store: LanternStore) -> list[str]:
    errors: list[str] = []
    if intent.action in {"create_or_update_order", "replace_item", "remove_item"} and not intent.items:
        errors.append(f"{intent.action} requires at least one item")
    if intent.action == "replace_item" and (len(intent.items) != 1 or not intent.replaces_sku):
        errors.append("replace_item requires one new item and replaces_sku")
    for item in intent.items:
        menu_item = store.get_item(item.sku)
        if menu_item is None:
            errors.append(f"unknown sku: {item.sku}")
            continue
        if intent.action in {"create_or_update_order", "replace_item"} and not menu_item.available:
            errors.append(f"unavailable item: {item.sku}")
        declared_allergies = {allergy.strip().lower() for allergy in intent.allergies if allergy.strip()}
        item_allergens = {allergen.lower() for allergen in menu_item.allergens if allergen.lower() != "none"}
        conflicts = sorted(declared_allergies & item_allergens)
        if conflicts and intent.action in {"create_or_update_order", "replace_item"}:
            errors.append(f"allergen conflict for {item.sku}: {', '.join(conflicts)}")
        allowed = {m.lower() for m in menu_item.modifiers}
        for modifier in item.modifiers:
            if intent.action in {"create_or_update_order", "replace_item"} and modifier.lower() not in allowed:
                errors.append(f"unsupported modifier for {item.sku}: {modifier}")
    if intent.allergies and any(not a.strip() for a in intent.allergies):
        errors.append("allergy must not be empty")
    return errors
