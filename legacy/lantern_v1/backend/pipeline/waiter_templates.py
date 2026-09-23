"""Spoken confirmations rendered straight from tool results.

When every tool call in a round is an order change that succeeded cleanly, the confirmation can be built
from the result instead of asking the model for a second reply. That removes a whole LLM call from an
ordering turn, and because every dish name and price comes from the tool result the reply cannot misquote
the order.

Anything that needs judgement falls back to the model: a sold-out dish and its substitute, a clarifying
question, an error, or a round that also looked something up.

Pure functions over plain dicts; nothing here imports from `backend.app`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# set_modifier is excluded because it silently edits the last line when it can't find its target, and
# seat_party because its result carries no order lines.
TEMPLATABLE_TOOLS = frozenset({"order_items", "add_item", "add_items_from_mention", "remove_item", "place_order"})

_FAILURE_KEYS = ("error", "clarify", "not_added", "suggested_substitute")
_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_ORDER_LINE = re.compile(r"^(\d+)x (.+?)(?: \((.+)\))?$")


def _failed(result: Mapping[str, Any]) -> bool:
    return any(result.get(key) for key in _FAILURE_KEYS)


def _clean_added(added: Any) -> bool:
    if isinstance(added, Mapping):
        return not _failed(added)
    if isinstance(added, list):
        return bool(added) and all(isinstance(entry, Mapping) and not _failed(entry) for entry in added)
    return False


def _clean_placed(place_result: Any) -> bool:
    return isinstance(place_result, Mapping) and place_result.get("placed") is True and not _failed(place_result)


def is_clean_success(tool: str, result: Any) -> bool:
    """True only when the tool did exactly what was asked, with nothing the guest needs to hear about."""
    if tool not in TEMPLATABLE_TOOLS or not isinstance(result, Mapping) or _failed(result):
        return False
    if tool == "add_item":
        return isinstance(result.get("added"), Mapping)
    if tool == "add_items_from_mention":
        return isinstance(result.get("added"), list) and _clean_added(result["added"])
    if tool == "remove_item":
        return isinstance(result.get("removed"), int) and result["removed"] >= 1
    if tool == "place_order":
        return _clean_placed(result)
    # order_items
    items = result.get("items") or []
    if not items and "place_result" not in result:
        return False
    for item in items:
        if not isinstance(item, Mapping) or _failed(item):
            return False
        if "already_in_order" not in item and not _clean_added(item.get("added")):
            return False
    return "place_result" not in result or _clean_placed(result["place_result"])


def _count_word(qty: int) -> str:
    return _NUMBER_WORDS[qty] if 0 <= qty < len(_NUMBER_WORDS) else str(qty)


def speak_line(line: str) -> str:
    """`"2x Grilled Seabass (no chili)"` -> `"two Grilled Seabass with no chili"`. Reformats, never adds."""
    match = _ORDER_LINE.match(line.strip())
    if not match:
        return line.strip()
    qty, name, modifiers = match.groups()
    spoken = f"{_count_word(int(qty))} {name}"
    if modifiers:
        spoken += " with " + " and ".join(part.strip() for part in modifiers.split(","))
    return spoken


def speak_order_lines(lines: Sequence[str]) -> str:
    spoken = [speak_line(line) for line in lines]
    if len(spoken) <= 2:
        return " and ".join(spoken)
    return ", ".join(spoken[:-1]) + ", and " + spoken[-1]


def _money(total: Any) -> str:
    return f"${float(total):.2f}"


def render_confirmation(round_log: Sequence[Mapping[str, Any]]) -> str | None:
    """The confirmation for a round of tool calls, or None when the model should reply itself.

    `round_log` holds this round's calls as `{"tool", "args", "result"}`. It renders only when every call
    is a clean order change, and it reads the order from the last result, which reflects the final basket.
    """
    if not round_log:
        return None
    if not all(is_clean_success(entry.get("tool", ""), entry.get("result")) for entry in round_log):
        return None

    last = round_log[-1]
    tool, result = last["tool"], last["result"]

    placed = result if tool == "place_order" else result.get("place_result")
    if placed:
        text = f"Your order is in: {speak_order_lines(placed.get('lines') or [])}. "
        text += f"Your total is {_money(placed.get('total', 0))}. Thank you!"
        if placed.get("ticket_type") == "takeout" and placed.get("eta_minutes"):
            text += f" It'll be ready in about {placed['eta_minutes']} minutes."
        return text

    lines = result.get("order_lines") or []
    total = _money(result.get("total", 0))
    if tool == "remove_item":
        if not lines:
            return "Removed. Your order is empty now."
        return f"Removed. Now you have {speak_order_lines(lines)}, {total}. Anything else?"
    if tool == "order_items" and all("already_in_order" in item for item in result.get("items") or []):
        return f"That's already in your order: {speak_order_lines(lines)}, {total}. Anything else?"
    return f"Got it. That's {speak_order_lines(lines)}, {total} so far. Anything else?"
