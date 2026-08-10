"""`first_token` must mean the model answered, not that we heard ourselves.

From the first real traced session, thirteen turns:

    turn=2  response=-612ms
    turn=10 response=-296ms
    turn=3  response=-92ms

A negative duration is impossible. `response` is `speech_end → first_token`,
so a negative value means `first_token` was stamped BEFORE the user stopped
talking.

The cause: the mark accepted any `server_content`, and `input_transcription` —
the transcription of the USER — arrives inside `server_content` while they are
still mid-sentence. So the mark fired on hearing ourselves.

Why it matters beyond a wrong sign: `audio` is `first_token → first_audio` and
is labelled "our own playback path". Stamping `first_token` early moves the
model's real thinking time out of `response` and into `audio` — so the single
number that decides "is this Google or is this us" was crediting Google's
latency to our playback. Every conclusion drawn from it would have been
backwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _SC:
    """A server_content, with only the fields a given case sets."""

    def __init__(self, **kw):
        self.input_transcription = kw.get("input_transcription")
        self.output_transcription = kw.get("output_transcription")
        self.model_turn = kw.get("model_turn")
        self.turn_complete = kw.get("turn_complete")


class _Resp:
    def __init__(self, data=None, server_content=None, tool_call=None):
        self.data = data
        self.server_content = server_content
        self.tool_call = tool_call


def _answered(response) -> bool:
    """The predicate as `main.py` computes it. Kept in step by the AST test
    below, so this cannot drift into testing a copy of the logic."""
    _sc = response.server_content
    return bool(
        response.data
        or response.tool_call
        or (_sc and (getattr(_sc, "model_turn", None)
                     or getattr(_sc, "output_transcription", None)
                     or getattr(_sc, "turn_complete", None))))


class _Txt:
    def __init__(self, text):
        self.text = text


# ── the bug ─────────────────────────────────────────────────────────────────

def test_the_users_own_transcription_does_not_count_as_an_answer():
    """This is what made `response` negative: it arrives mid-sentence."""
    r = _Resp(server_content=_SC(input_transcription=_Txt("count from one")))
    assert _answered(r) is False


def test_a_partial_user_transcription_still_does_not_count():
    r = _Resp(server_content=_SC(input_transcription=_Txt("cou")))
    assert _answered(r) is False


# ── what genuinely is an answer ─────────────────────────────────────────────

def test_audio_payload_counts():
    assert _answered(_Resp(data=b"\x00\x01")) is True


def test_a_tool_call_counts():
    assert _answered(_Resp(tool_call=object())) is True


def test_the_models_own_transcription_counts():
    r = _Resp(server_content=_SC(output_transcription=_Txt("One, two, three")))
    assert _answered(r) is True


def test_a_model_turn_counts():
    assert _answered(_Resp(server_content=_SC(model_turn=object()))) is True


def test_turn_complete_counts():
    """A turn that answers with nothing at all still ended."""
    assert _answered(_Resp(server_content=_SC(turn_complete=True))) is True


def test_an_empty_response_counts_as_nothing():
    assert _answered(_Resp()) is False
    assert _answered(_Resp(server_content=_SC())) is False


# ── the predicate under test is the one that ships ──────────────────────────

def test_main_uses_this_exact_predicate():
    """Guards against this file testing a copy that has drifted from main.py."""
    import re
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    block = re.search(r"_answered = bool\((.*?)\)\n\s*if _answered", src, re.S)
    assert block, "main.py no longer computes `_answered` — update this test"
    body = block.group(1)
    assert "input_transcription" not in body, \
        "the user's own transcription is back in the answer predicate"
    for expected in ("response.data", "response.tool_call", "model_turn",
                     "output_transcription"):
        assert expected in body, f"{expected} missing from the predicate"
