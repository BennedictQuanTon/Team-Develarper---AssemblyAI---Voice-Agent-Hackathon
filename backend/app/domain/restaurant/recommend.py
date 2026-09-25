"""Deterministic recommendations: score available dishes by the menu's ``fits`` tags.

Ported from V1's ``recommend_dishes``: each guest tag a dish fits is worth 3 points, a party of
four or more favours dishes for sharing, and popularity breaks ties. The guest's tags come from
their words, so the model never picks the dishes.
"""
from __future__ import annotations

import re

from .store import LanternStore, MenuItem

# Guest words -> menu ``fits`` tags, in the demo languages (English, Spanish).
TAG_WORDS = {
    "mild": ("mild", "not spicy", "not too spicy", "no spice", "suave", "sin picante", "poco picante"),
    "spicy_ok": ("spicy", "hot and spicy", "picante"),
    "couple": ("couple", "two of us", "for two", "date", "pareja", "para dos"),
    "family": ("family", "familia"),
    "kids": ("kid", "kids", "child", "children", "niño", "niños", "niña", "niñas"),
    "vegetarian": ("vegetarian", "vegan", "veggie", "no meat", "vegetariano", "vegetariana", "sin carne"),
    "light": ("light", "healthy", "ligero", "ligera", "saludable"),
    "hearty": ("hearty", "filling", "hungry", "big appetite", "abundante", "hambre"),
    "solo": ("just me", "for one", "by myself", "alone", "solo", "sola"),
    "share_table": ("share", "sharing", "group", "compartir"),
}
# "spicy" inside "not spicy" must not also count as spicy_ok.
NEGATED_SPICE = re.compile(r"\b(?:not|no|without|sin)\s+(?:too\s+|very\s+|muy\s+)?(?:spicy|spice|picante)\b", re.IGNORECASE)
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8}
PARTY = re.compile(r"\b(?:for|of|para|somos)\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)
# Recommendations are dishes unless the guest asks for a drink or a dessert.
DRINK_WORDS = ("drink", "beverage", "tea", "beer", "juice", "bebida", "té", "cerveza", "jugo")
DESSERT_WORDS = ("dessert", "sweet", "postre", "dulce")


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def guest_tags(transcript: str) -> tuple[list[str], int]:
    """The ``fits`` tags and party size a guest's request implies."""
    text = transcript.casefold()
    spice_negated = NEGATED_SPICE.search(text) is not None
    tags = [tag for tag, phrases in TAG_WORDS.items()
            if any(_has(text, phrase) for phrase in phrases) and not (tag == "spicy_ok" and spice_negated)]
    if spice_negated and "mild" not in tags:
        tags.append("mild")
    if "hearty" in tags:
        tags.append("heartier")
    match = PARTY.search(text)
    party = 1 if "solo" in tags and "couple" not in tags else 2
    if match:
        value = match.group(1).casefold()
        party = int(value) if value.isdigit() else NUMBER_WORDS[value]
    return tags, party


def recommend(store: LanternStore, transcript: str, allergies: list[str] | None = None, limit: int = 2) -> list[MenuItem]:
    """Up to ``limit`` available dishes that best fit the request, never one with a declared allergen."""
    tags, party = guest_tags(transcript)
    text = transcript.casefold()
    categories = set()
    if any(_has(text, word) for word in DRINK_WORDS):
        categories.add("Drink")
    if any(_has(text, word) for word in DESSERT_WORDS):
        categories.add("Dessert")
    avoided = {allergy.casefold() for allergy in allergies or []}

    def allowed(item: MenuItem) -> bool:
        if avoided & {allergen.casefold() for allergen in item.allergens}:
            return False
        if categories:
            return item.category in categories
        return item.category not in {"Drink", "Dessert"}

    def score(item: MenuItem) -> int:
        points = 3 * sum(tag in item.fits for tag in tags)
        points += min(item.ordered_count, 250) // 250
        if party >= 4 and "share_table" in item.fits:
            points += 1
        return points

    candidates = [item for item in store.list_menu(available_only=True) if allowed(item)]
    candidates.sort(key=lambda item: (-score(item), -item.ordered_count, item.name))
    return candidates[:limit]
