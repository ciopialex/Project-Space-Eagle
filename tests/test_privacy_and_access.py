"""Acceptance tests for BEHAVIOR_SPEC.md section 13 — privacy and access.

Written clean-room from the specification. No source was read.

Section 13 is six items and four of them are `[INFERRED]` claims about a remote
dashboard, pairing codes and session credentials. Those are negative privacy
claims — "is refused", "expires", "is capped" — and a test that would actually
catch a leak needs a seam onto the dashboard's HTTP surface. No such seam is
named in TEST_SETUP.md or MODULE_CONTRACT.md, so they are recorded at the foot
of this file rather than covered by an assertion that could only ever pass
because the thing it measures is absent.
"""

import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402,F401

import pytest  # noqa: E402

try:  # the island tests need real Chromium; a fresh clone without it must skip
    from conftest import evaluate, load, pump  # noqa: E402
    _BROWSER = True
except Exception:  # pragma: no cover - only on a machine without PyQt6-WebEngine
    _BROWSER = False
    evaluate = load = pump = None

needs_island = pytest.mark.skipif(
    not _BROWSER,
    reason="PyQt6-WebEngine is not installed, so the island page cannot be loaded",
)


def _screen_text(view):
    """Everything a person can read on the island, as text."""
    return evaluate(view, "document.body.innerText") or ""


def _rest(view, state):
    evaluate(view, f"window.pill.set('{state}', {{}}); 1")
    pump(0.08)


# --------------------------------------------------------------------------
# 13.1 — the microphone is inactive until the app is opened, and the island
#        visibly shows LISTENING or SPEAKING while it is in use.
#
# Only the second half is observable through a documented surface. The first
# half ("inactive until the app is opened") is at the foot of this file.
# --------------------------------------------------------------------------


@needs_island
def _mic_indicator(view) -> bool:
    """Whether the island is showing that the microphone is live.

    The indicator is the green dot, not the word. It appears on `listening` and
    on nothing else -- see the guards below, which is the whole reason it is a
    better privacy signal than the word was: the word labelled both listening
    and speaking, and only one of those is the microphone.
    """
    return bool(evaluate(view, "!!document.querySelector('#pbody .pdot')"))


def test_the_island_shows_an_unmistakable_signal_while_the_microphone_is_live(web_view):
    """s13.1. The island visibly signals a live microphone.

    Reworded 2026-09-05. It used to require the literal word LISTENING, which
    conflicted with s4.11 -- the resting states the operator supplied as the
    design, which carry a green waveform and a green pulsing dot and no words.

    The dot is the stronger signal, and that is why the conflict resolved this
    way rather than the other. It marks the microphone specifically: it is
    present on `listening` and absent on `speaking`, where the word laballed
    both and so told you less. It is what every phone and desktop OS already
    uses for a hot mic, it reads without being read, and it does not require
    the user to know English.
    """
    load(web_view)

    # Guard: prove the measurement can tell the two states apart before
    # trusting it. If the indicator is on screen at rest, the assertion below
    # would pass for a machine with a permanently hot microphone.
    _rest(web_view, "idle")
    assert not _mic_indicator(web_view), (
        "the island shows the live-microphone indicator while idle, so this "
        "measurement cannot distinguish a live microphone from a dead one"
    )

    _rest(web_view, "listening")
    assert _mic_indicator(web_view), (
        "nothing on the island says the microphone is live")


def test_the_microphone_signal_is_absent_while_the_eagle_is_only_talking(web_view):
    """s13.1. The signal marks the microphone, not any activity.

    An indicator that is also lit while the eagle speaks says "something is
    happening", which is not a privacy signal at all.
    """
    load(web_view)
    _rest(web_view, "speaking")
    assert not _mic_indicator(web_view), (
        "the live-microphone indicator is shown while the eagle is speaking, "
        "so it does not actually mark the microphone")



def test_the_stop_mute_and_fullscreen_keys_are_printed_on_screen():
    """s13.2. Escape, F4 and F11 are printed on screen where a user can read them.

    Read from the dashboard, not the island. This used to drive the pill and
    look for the keys there, and the pill is 320x62 -- it holds a crest and a
    clock. Three key hints were never going to be on it, and the requirement
    says "printed on screen", not "printed on the island".

    They are on the dashboard, which is the screen with room for them, and the
    shortcut itself is registered at aethelark_web.py:639.
    """
    dashboard = (pathlib.Path(__file__).resolve().parent.parent
                 / "web" / "dashboard.html")
    assert dashboard.is_file(), "the dashboard is not where this test expects it"
    text = dashboard.read_text(encoding="utf-8", errors="replace").upper()

    # Guard: a reader that returns the whole file would satisfy any `in`
    # assertion. Prove it does not contain a key that does not exist.
    assert "[F13]" not in text, (
        "the dashboard names a key that is not part of the product; the "
        "assertions below would pass on noise"
    )

    for key in ("[ESC]", "[F4]", "[F11]"):
        assert key in text, (
            f"{key} is not printed anywhere on the dashboard, so a user has no "
            f"way to learn it exists")


# UNTESTABLE
#
# 13.1 (first half) — "The microphone is inactive until the app is opened."
#     Missing seam: nothing in TEST_SETUP.md or MODULE_CONTRACT.md exposes
#     audio-capture state, and there is no documented way to observe whether a
#     capture device is open while the app is not running. A test that asserted
#     silence would pass on a machine with no microphone at all.
#
# 13.2 (second half) — Escape stops a running action, F4 mutes, F11 toggles
#     fullscreen. Missing seam: no documented way to deliver a keystroke to the
#     product and observe an in-flight action being cancelled. `web_view` loads
#     the page but the key handling the item describes is the desktop window,
#     and no fixture reaches it.
#
# 13.3 — "Voice and screen data go from the machine to the model provider under
#     the user's own key. There is no intermediary service." Missing seam: this
#     is an egress claim and the only honest test is a network capture — assert
#     that every outbound connection during a voice turn terminates at the
#     provider's host, under the user's own credential. Nothing in the
#     documented surface intercepts, records or even names outbound requests, so
#     a passing test here would prove only that a function returned. Recorded
#     rather than faked: this is exactly the item where a vacuous test would
#     certify a leak as absent.
#
# 13.4 — the phone dashboard refuses an uncredentialled request with 401.
#     Missing seam: no documented constructor, port, base URL or route for the
#     dashboard. Both companion documents cover the module bus and the island;
#     neither names the web surface. The test that matters (a request with no
#     credential, one with an expired credential, one with a forged credential,
#     all three 401) is one fixture away and cannot be written until that
#     fixture is named.
#
# 13.5 — pairing codes expire after 300 s and repeated failed logins from one
#     address are throttled. Missing seam: the same absent dashboard fixture,
#     plus no documented way to age a pairing code. TEST_SETUP.md §6 notes that
#     the suite ages a stored confirmation record to test expiry and explicitly
#     says "Do not take it as the pattern", so the equivalent reach-past for a
#     pairing code is not available either.
#
# 13.6 — session credentials are swept on expiry and the oldest discarded past a
#     cap. Missing seam: no documented way to enumerate live session
#     credentials, so neither "expired ones are gone" nor "the count stops at
#     the cap" can be measured. The cap's number is also unstated — the item
#     says "capped in number" without giving one — so even with a seam the
#     assertion would have no target.
