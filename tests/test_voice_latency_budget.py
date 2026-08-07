"""The fixed costs paid on every voice turn, pinned as numbers.

These are the only latency knobs the eagle actually controls. Everything else
in the turn — network, model, the user's own speaking — is not ours to set.
They live in main.py's session config, they had never been tuned, and nothing
stopped them drifting back up.

`silence_duration_ms` is the big one: the server waits that long in SILENCE
after the user stops before it will admit the turn is over. It is paid on
every turn, before any work begins, and it is pure latency — the model is not
thinking during it, nothing is in flight.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SOURCE = (Path(__file__).resolve().parent.parent / "main.py").read_text()


def _default(name: str) -> int:
    m = re.search(rf'_cfg\.get\("{name}"\)\s*or\s*(\d+)', SOURCE)
    assert m, f"{name} default not found — did the session config move?"
    return int(m.group(1))


def test_the_end_of_turn_wait_is_not_half_a_second():
    """550ms was the shipped default and it is most of a 1s target, spent
    doing nothing. Every turn pays it, including 'yes'."""
    assert _default("end_of_turn_silence_ms") <= 400


def test_the_end_of_turn_wait_is_not_so_short_it_interrupts(monkeypatch):
    """The other side, and the reason this is not simply zero. Below roughly
    250ms an ordinary pause mid-sentence — drawing breath, hunting for a word —
    reads as the end of the turn, and the eagle talks over the user. That is a
    far worse experience than waiting."""
    assert _default("end_of_turn_silence_ms") >= 250


def test_the_prefix_padding_stays_small():
    """Audio kept before speech onset. Useful against clipped first syllables,
    pure latency beyond that."""
    assert _default("speech_prefix_padding_ms") <= 200


def test_thinking_is_still_off_for_voice():
    """A reasoning budget is invisible latency in a spoken turn."""
    assert _default("thinking_budget") == 0


def test_the_budget_is_stated_where_someone_will_see_it():
    """A tuned constant with no explanation gets 'fixed' back to a round
    number by the next person who reads it."""
    assert "TURN LATENCY BUDGET" in SOURCE
