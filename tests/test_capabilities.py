"""The catalogue of what Aethelark can actually do.

Before this, capability lived as prose in 30 tool descriptions — ~210 actions,
one of which used a machine-readable enum. A user's utterance cannot be matched
against prose, so an intent decoder had nothing to decode against, and nothing
could answer basic questions mechanically: what can this software do, which of
those are safe to start speculatively, what does each one need first.

Two failures this week came from exactly that gap. 59 working actions in
computer_settings were invisible to the model because a hand-written list
drifted. `wait_for_element` and `scroll_into_view` were dispatchable and
undocumented. Neither is possible once capability is data with a test behind it.

The catalogue is deliberately hand-written where machines cannot help (what a
person might SAY, whether an action can be undone) and checked against the code
where they can (does this tool exist, is this action really dispatched).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from core.capabilities import (CATALOGUE, EFFECTS, IRREVERSIBLE,  # noqa: E402
                               READ_ONLY, REVERSIBLE, Capability,
                               speculatable, find_by_phrase)


def test_every_capability_points_at_a_real_tool():
    """A catalogue entry naming a tool that does not exist would route the
    decoder into nothing."""
    declared = {d["name"] for d in main.TOOL_DECLARATIONS if isinstance(d, dict)}
    unknown = sorted({c.tool for c in CATALOGUE} - declared)
    assert unknown == [], f"catalogue names tools that are not declared: {unknown}"


def test_every_capability_has_something_a_person_would_say():
    """The whole point. An entry with no phrasings can never be matched."""
    mute = [c.id for c in CATALOGUE if not c.says]
    assert mute == [], f"no trigger phrases: {mute}"


def test_every_capability_declares_its_effect():
    bad = [c.id for c in CATALOGUE if c.effect not in EFFECTS]
    assert bad == [], f"unclassified effect: {bad}"


def test_only_read_only_work_is_speculatable():
    """The safety link to the intent layer. It may PRE-WARM on its own
    authority and may only ACT once the model commits — so the set it is
    allowed to touch must contain nothing that changes the world."""
    unsafe = [c.id for c in speculatable() if c.effect != READ_ONLY]
    assert unsafe == [], (
        f"these would let a guess change something: {unsafe}")


def test_nothing_irreversible_is_ever_speculatable():
    """Stated separately and bluntly. Buying, deleting and sending must never
    be startable on a prediction, however confident."""
    ids = {c.id for c in speculatable()}
    for c in CATALOGUE:
        if c.effect == IRREVERSIBLE:
            assert c.id not in ids, f"{c.id} is irreversible and speculatable"


def test_ids_are_unique():
    ids = [c.id for c in CATALOGUE]
    assert len(ids) == len(set(ids)), "duplicate capability ids"


def test_a_plain_utterance_finds_the_right_capability():
    """End to end, on the sentences that actually failed this week."""
    assert find_by_phrase("show me my liked videos on youtube").tool == "youtube_api"
    assert find_by_phrase("go to emag.ro and search for headphones").tool == "web_agency"
    assert find_by_phrase("turn the volume up").tool == "computer_settings"
    assert find_by_phrase("what is on my screen").tool == "screen_process"


def test_a_general_question_routes_to_search():
    """This used to assert None, back when web_search was not catalogued.
    "What is the airspeed velocity of a swallow" IS a search — the old
    assertion was describing a gap, not a requirement."""
    assert find_by_phrase("what is the airspeed velocity of a swallow").tool == "web_search"


def test_an_unrecognised_utterance_returns_nothing_rather_than_guessing():
    """A decoder that always answers is a decoder that is often wrong. No
    match means "let the model decide", which is today's behaviour and safe."""
    for said in ("mhm", "yeah okay sure", "hold on a second", "thanks pal"):
        assert find_by_phrase(said) is None, said


def test_the_catalogue_states_how_complete_it_is():
    """Honest coverage, so nobody assumes it is finished. This number should
    go up deliberately, not be discovered later."""
    from core.capabilities import coverage
    got = coverage()
    assert 0.0 < got <= 1.0


def test_a_compound_request_still_offers_safe_work_to_start(monkeypatch):
    """"Go to emag.ro and search for headphones" resolves to web.type — the
    right final intent, and reversible, so nothing may be started on it.

    But the utterance also plainly implies opening the site, which is
    read-only. Speculation must look at everything an utterance mentions, not
    just the winner, or the commonest shape of request (navigate THEN act)
    never gets to pre-warm at all."""
    from core.capabilities import find_by_phrase, prewarm_for
    said = "go to emag.ro and search for wireless headphones"
    assert find_by_phrase(said).action == "type"
    ids = {c.id for c in prewarm_for(said)}
    assert "web.open" in ids, "the safe half of a compound request was dropped"


def test_prewarm_never_offers_anything_that_changes_the_world():
    """The invariant, checked on utterances that mix safe and unsafe."""
    from core.capabilities import prewarm_for, READ_ONLY
    for said in ("go to the shop and buy it now",
                 "open my messages and send one to mama",
                 "read the file then delete it"):
        for cap in prewarm_for(said):
            assert cap.effect == READ_ONLY, f"{cap.id} on '{said}'"
            assert cap.speculative


def test_prewarm_is_empty_when_nothing_is_recognised():
    from core.capabilities import prewarm_for
    assert prewarm_for("tell me a joke about penguins") == ()


# ── Bare TLDs matched inside ordinary words ────────────────────────────────
# ".ro" was a trigger phrase for web.open. `_normalise` strips punctuation, so
# it became "ro" — which matches inside "euro", "from", "printer", "store".
# In a live session the pre-warm fired on nearly every utterance:
#     [Intent] web.open (conf=0.8, matched='ro') → pre-warming: web.open
# on "tell me about euro prices". A predictor that fires constantly is not a
# predictor, and it started a browser nobody asked for on almost every turn.

def test_a_tld_does_not_match_inside_an_ordinary_word():
    from core.capabilities import find_by_phrase
    for said in ("tell me about euro prices", "from the store",
                 "the printer is broken", "european weather"):
        got = find_by_phrase(said)
        assert got is None or got.id != "web.open", f"{said!r} matched web.open"


def test_a_real_domain_is_still_recognised():
    from core.capabilities import find_by_phrase, prewarm_for
    for said in ("go to emag.ro and search", "open bambulab.com",
                 "check us.store.bambulab.com", "pull up olx.ro"):
        ids = {c.id for c in prewarm_for(said)}
        assert "web.open" in ids, f"{said!r} did not look web-shaped"
