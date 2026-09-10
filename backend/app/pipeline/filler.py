"""Rule-based backchannel / filler selection (no LLM, zero API cost)."""

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


def is_backchannel(text: str) -> bool:
    """True for short utterances that should NOT interrupt an agent mid-speech.

    Backchannels (affirmations, minimal acknowledgements, fillers) are the opposite
    of a barge-in: the user is agreeing/listening, not taking the turn. Cutting TTS
    on these is exactly the UX bug we must avoid.
    """
    t = (text or "").strip().lower()
    if not t or len(t.split()) > 3:
        return False
    if _FAREWELL.search(t) and len(t.split()) <= 2:
        # "no / yes" politeness shorthand while the waiter confirms — let it finish
        return True
    if _ACK.search(t) and not re.search(r"\b(add|remove|change|no[,.]?\b)", t):
        return True
    return t in {"uh", "uh-huh", "mm", "mhm", "right", "cool", "nice", "okay"}



def is_farewell(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    # Short farewell-ish utterances
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
        # Clear short fact questions → got_it; broader → thinking
        if len(t.split()) <= 10 and _WH.search(t):
            return "got_it"
        return "thinking"
    if _ACK.search(t):
        return "ack"
    return "thinking"


CLOSING_TEXT = "Anytime. Enjoy Da Nang."
CLOSING_AUDIO_PATH = "audio/backchannels/closing.wav"
