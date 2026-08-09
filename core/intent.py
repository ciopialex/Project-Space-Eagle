"""A hypothesis about what the user wants, formed before the model answers.

This does NOT route. The model still chooses the tool. What this adds is two
things the model cannot give, because by the time it speaks the decision is
already made and committed:

  * a CONFIDENCE, so the eagle can tell a near-certain request from a coin
    flip and behave differently — hedge, confirm, or prepare a fallback.
  * a HYPOTHESIS EARLY, while the user is still talking, so the safe half of
    the work can already be underway when they stop.

Removing the model from the decision would trade a well-tuned chooser for a
keyword matcher. That is a bad trade nobody asked for. Narrowing what it
chooses between — from the nine tools that used to claim "open a website" down
to two — is the good half of the same idea.

AUTHORITY, INVERTED FROM prompt_reflex
--------------------------------------
`core/prompt_reflex.py` solves the same shape of problem for a different input
and is the model for this. Its rule: it may STOP on its own authority, and may
only GO from an explicit proven-safe list, because blocking is cheap and
reversible while allowing is neither.

This is the mirror:

    It may PRE-WARM on its own authority.
    It may only ACT once the model has committed.

Speculative navigation is the feature. Speculative action is a bug with a nice
name. The `prewarm` list is filtered to READ_ONLY inside `capabilities.py`, not
here, so a future caller cannot forget to.

COST
----
It runs on the event loop inside the 350ms end-of-turn window. A hypothesis
that costs more than the work it saves is a net loss, so this is substring
matching over a fixed catalogue — no inference, no network, no allocation of
consequence. Pinned by a test at well under a millisecond.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.capabilities import (Capability, CATALOGUE, find_by_phrase,
                               prewarm_for, _normalise)

#: Confidence bands. Deliberately coarse — three meaningful states, not a
#: false-precision float. Anything finer would imply a calibration this has
#: not earned yet.
HIGH = 0.8      # one capability matched, and matched distinctively
MEDIUM = 0.5    # matched, but something else also plausibly fits
LOW = 0.2       # a weak signal; the model should not be influenced much
NONE = 0.0      # nothing recognised: today's behaviour, model decides alone


@dataclass(frozen=True)
class Intent:
    """What the eagle thinks is being asked, and what it may do about it."""

    #: Best single guess at the request, or None when nothing is recognised.
    capability: Capability | None
    confidence: float
    #: Tools worth putting in front of the model. Never narrower than the
    #: answer — a narrowing that excludes the right tool is worse than none.
    candidates: tuple[str, ...] = ()
    #: Read-only work that may begin NOW, before the model has said anything.
    prewarm: tuple[Capability, ...] = ()
    #: What matched, for the trace. Diagnosing a bad hypothesis is impossible
    #: without knowing which phrase caused it.
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
    """Form a hypothesis. Never raises; silence is a valid answer.

    Works on a PARTIAL utterance on purpose - the value is in being early, so
    it has to be useful halfway through a sentence.
    """
    try:
        return _decode(utterance)
    except Exception:
        # A broken hypothesis must never take down a turn. Falling back to
        # "no idea" costs the speculative work and nothing else.
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

    # Confidence is about DISTINCTIVENESS, not about how sure the phrase felt.
    # Two capabilities matching equally well is a genuine ambiguity, and
    # reporting high confidence on it would waste the pre-warm most of the
    # time - the cost of being confidently wrong is paid every turn.
    # MARGIN, not mere presence of a rival. An incidental short match ("message"
    # inside "what did i miss in my messages") is not competition with a long
    # distinctive one, and treating it as such made every real request look
    # ambiguous - which would waste the pre-warm on exactly the requests it
    # should serve best.
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

    # A short match inside a long sentence is weak evidence: "playlist" in a
    # forty-word request is probably not what the request is about.
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
