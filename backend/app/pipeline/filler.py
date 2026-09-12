"""Rule-based backchannel / filler selection (no LLM, zero API cost).

Includes:
1. Classic conversational backchannels (ack, thinking, got_it, checking, closing).
2. Context-Aware Audio Fillers for Voice Waiter:
   - case_specialty_rec: "Let me check our house specialties for you right now."
   - case_dish_check:    "Let me check the kitchen if that dish is available."
   - case_table_check:   "Checking our floor plan for an open table for you."
   - case_order_process: "Sure thing, putting that into the system for you."
   - case_general:       "Uh, let me see right now."
"""

from __future__ import annotations

import re

_CHECK = re.compile(
    r"\b(when|what time|schedule|price|cost|how much|how long|hours?|open|close)\b",
    re.I,
)
_WH = re.compile(r"\b(what|where|how|why|which|who|tell me|is there|are there)\b", re.I)
_ACK = re.compile(r"\b(ok|okay|yes|yeah|sure|got it|thanks|thank you)\b", re.I)
_FAREWELL = re.compile(
    r"\b(thanks|thank you|that'?s all|that is all|bye|goodbye|i'?m good|see you|all set|nothing else)\b",
    re.I,
)

# --- Context-Aware Voice Waiter Intent Classifiers (< 1ms execution) ---
_SPECIALTY_REC = re.compile(
    r"\b(specialt(y|ies)|recommend(ation)?s?|signature|what'?s good|whats good|suggest|popular|favorite|house specials?|best)\b",
    re.I,
)
_ORDER_PROCESS = re.compile(
    r"\b(take (the|those|that)|i'?ll take|we'?ll take|place (the|my)? ?order|order (that|it|the)|"
    r"check out|put (that|it) in|add (the|a|an|to)|no (green onion|scallion|onion|cilantro|peanut|fish sauce|chili)|"
    r"make it (a|an)?|that'?s all)\b",
    re.I,
)
_TABLE_CHECK = re.compile(
    r"\b(table(s)?|seat(s|ed|ing)?|party (of|size)|booth|floor(plan)?|reservation|sit (inside|outside)|dine(-|\s)?in)\b",
    re.I,
)
_DISH_CHECK = re.compile(
    r"\b(available|availability|in stock|sold out|86|have any|is there any|do you have|"
    r"contain|allerg(y|ies|en)|ingredient|squid|seabass|prawn|pho|spring roll|wing|pork|chicken|morning glory|beef)\b",
    re.I,
)

CONTEXT_FILLER_WAVS: dict[str, str] = {
    "case_dish_check": "dish_check.wav",
    "case_table_check": "table_check.wav",
    "case_order_process": "order_process.wav",
    "case_specialty_rec": "specialty_rec.wav",
    "case_general": "thinking.wav",
}


def classify_context_filler(text: str) -> str:
    """Classify user query into a context-aware filler case in < 1ms.

    Order of priority:
    1. Specialty / Recommendation queries -> case_specialty_rec
    2. Order actions / modifications -> case_order_process
    3. Seating / Table queries -> case_table_check
    4. Menu availability / Dish queries -> case_dish_check
    5. Fallback -> case_general
    """
    t = (text or "").strip()
    if not t:
        return "case_general"

    # Priority 1: Recommendation & Specialties
    if _SPECIALTY_REC.search(t):
        return "case_specialty_rec"

    # Priority 2: Placing orders, choosing dishes, adding modifiers
    if _ORDER_PROCESS.search(t):
        return "case_order_process"

    # Priority 3: Table / Seating inquiries
    if _TABLE_CHECK.search(t):
        return "case_table_check"

    # Priority 4: Dish availability / menu inquiries
    if _DISH_CHECK.search(t):
        return "case_dish_check"

    return "case_general"


def is_backchannel(text: str) -> bool:
    """True for short utterances that should NOT interrupt an agent mid-speech."""
    t = (text or "").strip().lower()
    if not t or len(t.split()) > 3:
        return False
    if _FAREWELL.search(t) and len(t.split()) <= 2:
        return True
    if _ACK.search(t) and not re.search(r"\b(add|remove|change|no[,.]?\b)", t):
        return True
    return t in {"uh", "uh-huh", "mm", "mhm", "right", "cool", "nice", "okay"}


def is_farewell(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _FAREWELL.search(t) and len(t.split()) <= 12:
        return True
    return False


def choose_filler_id(text: str) -> str:
    """Pick a local backchannel clip id for the processing gap."""
    t = (text or "").strip()
    if not t:
        return "thinking"
    if is_farewell(t):
        return "ack"
    if _CHECK.search(t):
        return "checking"
    if "?" in t or _WH.search(t):
        if len(t.split()) <= 10 and _WH.search(t):
            return "got_it"
        return "thinking"
    if _ACK.search(t):
        return "ack"
    return "thinking"


CLOSING_TEXT = "Anytime. Enjoy Da Nang."
CLOSING_AUDIO_PATH = "audio/backchannels/closing.wav"
