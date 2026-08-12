"""Noticing you have been here before.

The MakerWorld loop was patched by matching on the goal's TEXT — a proxy for
the thing actually wanted, which is "am I somewhere I have already been with
nothing to show for it". `Signature` makes the real question answerable, so
this replaces the proxy with the measurement.

The rule has to be careful in both directions.

Legitimately returning to a page is normal work: open results, open an item,
go back, open the next item. Two visits to the same place is a person doing
their job. So a single revisit must NOT trip anything.

But arriving at the same place a third time, on the same step, having made no
progress, is not work. That is the shape the user actually watched happen:
open makerworld, fail, open makerworld, fail, open makerworld.

And a place that could not be READ is never recorded. Counting failed reads
as visits would make a flaky page look like a loop, which is the same
collapse of "could not look" into "nothing there" that this codebase keeps
producing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402
from core.world_state import Signature  # noqa: E402


def _sig(url="https://makerworld.com/", n=8, h="aaa"):
    return Signature(url=url, control_count=n, controls_hash=h)


def _m(n=3):
    return Mission(goal="g", steps=[Step(intent=f"step {i}") for i in range(n)])


# ── recording ───────────────────────────────────────────────────────────────

def test_a_place_is_remembered_once_visited():
    m = _m()
    m.note_place(_sig())
    assert m.times_at(_sig()) == 1


def test_the_same_place_twice_counts_twice():
    m = _m()
    m.note_place(_sig())
    m.note_place(_sig())
    assert m.times_at(_sig()) == 2


def test_a_different_place_is_counted_separately():
    m = _m()
    m.note_place(_sig(url="https://a.test/"))
    m.note_place(_sig(url="https://b.test/"))
    assert m.times_at(_sig(url="https://a.test/")) == 1


def test_a_page_that_could_not_be_read_is_never_recorded():
    """Otherwise a flaky page looks exactly like a loop."""
    m = _m()
    m.note_place(Signature(unknown=True, nonce=1))
    m.note_place(Signature(unknown=True, nonce=2))
    assert m.times_at(Signature(unknown=True, nonce=3)) == 0
    assert m.going_in_circles(Signature(unknown=True, nonce=4)) is False


def test_noting_nothing_is_harmless():
    m = _m()
    m.note_place(None)
    assert m.times_at(_sig()) == 0


# ── the judgement ───────────────────────────────────────────────────────────

def test_returning_to_a_page_once_is_ordinary_work():
    """Open results, open an item, come back. That is a person doing a job."""
    m = _m()
    m.note_place(_sig())
    m.note_place(_sig(url="https://item.test/"))
    m.note_place(_sig())
    assert m.going_in_circles(_sig()) is False


def test_arriving_a_third_time_is_going_in_circles():
    m = _m()
    for _ in range(3):
        m.note_place(_sig())
    assert m.going_in_circles(_sig()) is True


def test_a_place_never_visited_is_not_a_circle():
    m = _m()
    m.note_place(_sig(url="https://a.test/"))
    assert m.going_in_circles(_sig(url="https://elsewhere.test/")) is False


def test_progress_through_steps_does_not_read_as_a_circle():
    """Four different places, four steps. Nothing repeated."""
    m = _m(4)
    for i in range(4):
        m.note_place(_sig(url=f"https://p{i}.test/"))
        m.advance()
    assert all(not m.going_in_circles(_sig(url=f"https://p{i}.test/"))
               for i in range(4))


# ── it survives a reconnect, like everything else on the mission ────────────

def test_places_survive_being_saved_and_loaded(tmp_path):
    from core import mission_store as store
    m = _m()
    m.note_place(_sig())
    m.note_place(_sig())
    store.save(m, tmp_path / "m.json")
    back = store.load(tmp_path / "m.json")
    assert back.times_at(_sig()) == 2, \
        "a reconnect wiped the loop memory, so the loop resumes"
