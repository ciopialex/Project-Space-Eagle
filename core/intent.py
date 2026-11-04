"""A hypothesis about what the user wants, formed while they are still talking. It
does not route: the model still chooses the tool. It adds a confidence and an
early guess, so read-only work can start before they stop.

The mirror of prompt_reflex: it may pre-warm on its own authority and may only act
once the model has committed. `prewarm` is filtered to READ_ONLY in
capabilities.py.

Runs inside the 350 ms end-of-turn window, so it is substring matching over a
fixed catalogue: well under a millisecond (pinned by a test).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.capabilities import (Capability, CATALOGUE, find_by_phrase,
                               prewarm_for, _normalise)

# Sites common enough that naming one names a destination.
_KNOWN_SITES = ("makerworld", "youtube", "wikipedia", "github", "gmail",
                "amazon", "spotify", "reddit", "linkedin", "whatsapp",
                "bambulab", "printables", "thingiverse", "google")

# A domain-looking token: "makerworld.com", not "0.2mm" or "file.stl".
_LOOKS_LIKE_HOST = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|dev|app|co|ai|me|uk|de|ro|eu)\b",
    re.I)

_GO_TO_SITE = re.compile(
    r"\b(?:go(?:ing)? (?:to|on)|open|visit|navigate to|surf to|pull up)\s+"
    r"(?:the\s+)?([a-z0-9][a-z0-9.\-]{2,})", re.I)


def worth_warming(utterance: str | None) -> bool:
    """Is there a destination here, or just a verb? Starting a browser is worth it
    only when someone is going somewhere.
    """
    text = (utterance or "").strip().lower()
    if not text:
        return False
    if "http://" in text or "https://" in text:
        return True
    if _LOOKS_LIKE_HOST.search(text):
        return True
    if any(site in text for site in _KNOWN_SITES):
        return True
    m = _GO_TO_SITE.search(text)
    # "go to makerworld" qualifies; "go to the next thing" does not.
    return bool(m and m.group(1) not in
                ("the", "it", "that", "this", "next", "there", "back", "him",
                 "her", "them", "bed", "sleep", "work"))


# Confidence bands: deliberately coarse.
HIGH = 0.8      # one capability matched, and matched distinctively
MEDIUM = 0.5    # matched, but something else also plausibly fits
LOW = 0.2       # a weak signal; the model should not be influenced much
NONE = 0.0      # nothing recognised: today's behaviour, model decides alone


@dataclass(frozen=True)
class Intent:
    """What the eagle thinks is being asked, and what it may do about it."""

    # Best single guess, or None.
    capability: Capability | None
    confidence: float
    # Tools worth putting in front of the model. Never narrower than the answer.
    candidates: tuple[str, ...] = ()
    # Read-only work that may begin now.
    prewarm: tuple[Capability, ...] = ()
    # What matched, for the trace.
    matched: str = ""


_EMPTY = Intent(capability=None, confidence=NONE)


def _all_matches(text: str) -> list[tuple[int, Capability, str]]:
    """Every catalogue entry the text mentions, longest phrase first."""
    hits: list[tuple[int, Capability, str]] = []
    for cap in CATALOGUE:
        for phrase in cap.says:
            needle = _normalise(phrase).strip()
            if needle and needle in text:
                hits.append((len(needle), cap, needle))
    hits.sort(key=lambda h: -h[0])
    return hits


def decode(utterance) -> Intent:
    """Form a hypothesis. Works on a partial utterance. Never raises."""
    try:
        return _decode(utterance)
    except Exception:
        # A broken hypothesis costs only the speculative work.
        return _EMPTY


def _decode(utterance) -> Intent:
    if not isinstance(utterance, str):
        return _EMPTY
    text = _normalise(utterance)
    if not text.strip():
        return _EMPTY

    hits = _all_matches(text)
    if not hits:
        return _EMPTY

    best_len, best_cap, matched = hits[0]

    # Confidence is about distinctiveness, measured as a margin: a short incidental
    # match does not compete with a long distinctive one.
    runner_up = next((l for l, c, _m in hits if c.id != best_cap.id), 0)
    margin = best_len - runner_up

    if runner_up == 0:
        confidence = HIGH                 # nothing else matched at all
    elif margin >= 4:
        confidence = HIGH                 # clearly more specific than the rest
    elif margin > 0:
        confidence = MEDIUM               # better, but not by much
    else:
        confidence = LOW                  # a genuine tie

    # A short match in a long sentence is weak evidence.
    if best_len <= 4 and len(text) > 40:
        confidence = min(confidence, LOW)

    candidates = tuple(dict.fromkeys(
        [best_cap.tool] + [c.tool for _l, c, _m in hits]))

    return Intent(
        capability=best_cap,
        confidence=confidence,
        candidates=candidates,
        prewarm=prewarm_for(utterance),
        matched=matched,
    )
