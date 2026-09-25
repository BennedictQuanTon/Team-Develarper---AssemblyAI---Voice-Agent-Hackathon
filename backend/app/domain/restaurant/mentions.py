from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

KNOWN_ALIASES = {
    "スズキ": "MAIN_SEABASS",
    "sea bass": "MAIN_SEABASS",
    "seabass": "MAIN_SEABASS",
}

_WORD = re.compile(r"[^\W\d_]+")
# Words that occur in one menu name but are ordinary speech ("something sweet", "hot tea").
_COMMON = {"and", "the", "with", "hot", "iced", "fresh", "local", "sweet", "craft"}


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _name_and_sku(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("name") or ""), str(item.get("sku") or "")
    return str(item.name), str(item.sku)


# Words that shape a modifier but don't say which one it is ("no chili" is about chili).
_MODIFIER_FUNCTION_WORDS = {"no", "with", "without", "extra", "less", "more", "added", "and", "the", "of", "on", "side"}


def spoken_modifiers(modifiers: Iterable[str], transcript: str, dish_name: str = "") -> list[str]:
    """The modifiers the guest actually said.

    A modifier counts only when one of its content words ("chili" in "no chili") is in the
    transcript. Words of the dish's own name don't count, so "pomelo salad with shrimp" doesn't
    select "extra shrimp".
    """
    spoken = set(_words(transcript)) - set(_words(dish_name))

    def heard(word: str) -> bool:
        # "peanuts" and "chilies" still name "peanut" and "chili".
        return any(said == word or (len(word) >= 4 and said.startswith(word) and len(said) - len(word) <= 2)
                   for said in spoken)

    kept = []
    for modifier in modifiers:
        content = [word for word in _words(modifier) if word not in _MODIFIER_FUNCTION_WORDS]
        if content and any(heard(word) for word in content):
            kept.append(modifier)
    return kept


def mentioned_skus(transcript: str, menu: Iterable[Any], *, distinctive_words: bool = True) -> set[str]:
    """SKUs the guest named: full dish name, a known alias, or a word only one dish name uses.

    ``menu`` holds menu dicts or ``MenuItem``s. A word shared by several dishes ("chicken")
    names none of them, so an ambiguous sentence mentions nothing.
    """
    lowered = transcript.casefold()
    entries = [_name_and_sku(item) for item in menu]
    known = {sku for _name, sku in entries}
    found = {sku for phrase, sku in KNOWN_ALIASES.items() if phrase.casefold() in lowered and sku in known}
    found |= {sku for name, sku in entries if name and name.casefold() in lowered}
    if distinctive_words:
        spoken = set(_words(transcript))
        counts = Counter(word for name, _sku in entries for word in set(_words(name)))
        for name, sku in entries:
            if any(len(word) >= 3 and word not in _COMMON and counts[word] == 1 and word in spoken
                   for word in _words(name)):
                found.add(sku)
    return found
