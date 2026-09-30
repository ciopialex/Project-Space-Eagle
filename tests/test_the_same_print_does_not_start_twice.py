"""One request that arrives twice starts one print, not two.

`test_starting_a_real_print.py` covers the confirmation gate thoroughly: an
unconfirmed print starts nothing, one agreement authorises exactly one start,
an agreement for one printer does not authorise another. All of that is about
whether the user said yes.

This is the other way a second machine starts: the user said yes once, and the
request reached the dispatcher twice. A model emitting the same function call
twice in one batch is ordinary — a retry, a duplicated tool block, a reconnect
that replays — and the confirmation gate cannot see it, because both copies
carry the same valid token for the same arguments.

`ToolSpec.one_at_a_time` is the mechanism, enforced in `_dispatch_one_tool`,
and it is keyed on the ARGUMENTS rather than the tool. Two prints on two
printers are the point of owning a fleet; the same print twice is a machine
started twice. Nothing tested it: `one_at_a_time` appeared in main.py, in
manifest.py and in a3d's manifest, and in no test at all.

CLAUDE.md names this class directly — "a print starting on a real machine burns
filament and hours" — and records that `a3d_print` could once start two at
once. The scheduler is stubbed at `_execute_tool` here, one level BELOW the
deduplication, so the test measures the guard rather than replacing it. Stubbing
`_dispatch_one_tool` instead makes every one of these pass against a broken
implementation, which is the mistake that first made this file look unnecessary.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def _live(tracker: dict):
    """An AethelarkLive with only the scheduler and the dedup guard real."""
    live = main.AethelarkLive.__new__(main.AethelarkLive)
    live._turn_epoch = 0
    live._inflight_by_args = {}

    async def fake_execute(fc, call_epoch=-1):
        tracker["active"] += 1
        tracker["runs"] += 1
        tracker["max"] = max(tracker["max"], tracker["active"])
        tracker["seen"].append((fc.name, tuple(sorted((fc.args or {}).items()))))
        await asyncio.sleep(0.12)
        tracker["active"] -= 1
        return SimpleNamespace(id=fc.id, name=fc.name, response={"ok": True})

    live._execute_tool = fake_execute
    return live


def _call(name: str, i: int, **args):
    return SimpleNamespace(id=f"{name}-{i}", name=name, args=args)


def _run(calls) -> dict:
    tracker = {"active": 0, "max": 0, "runs": 0, "seen": []}
    live = _live(tracker)
    started = time.time()
    responses = asyncio.run(live._schedule_tool_calls(calls, 0))
    tracker["responses"] = responses
    tracker["seconds"] = time.time() - started
    return tracker


PRINT = "a3d_print"


def test_the_tool_declares_that_it_must_not_race_itself():
    """The guard is opt-in per tool, so its absence is silent.

    If a3d ever stops declaring `one_at_a_time`, every assertion below keeps
    passing while two prints start — the guard simply never engages.
    """
    spec = main.TOOL_SPECS.get(PRINT)
    assert spec is not None, f"{PRINT} has no ToolSpec at all"
    assert spec.one_at_a_time, (
        f"{PRINT} no longer declares one_at_a_time, so the same print twice "
        f"starts two machines and nothing here would notice")


def test_the_same_print_twice_starts_one_machine():
    """The defect. Identical arguments, one batch, two calls."""
    out = _run([_call(PRINT, 0, query_or_id="benchy", printer="CC1"),
                _call(PRINT, 1, query_or_id="benchy", printer="CC1")])

    assert out["runs"] == 1, (
        f"the print ran {out['runs']} times. Identical arguments mean the same "
        f"physical job: filament and hours, twice.")
    assert out["max"] == 1


def test_both_callers_still_get_an_answer():
    """The protocol needs one response per call.

    The waiting call is handed the first one's result rather than a refusal,
    because from the model's side its request did succeed — dropping it would
    leave a function call unanswered and wedge the turn.
    """
    calls = [_call(PRINT, 0, query_or_id="benchy", printer="CC1"),
             _call(PRINT, 1, query_or_id="benchy", printer="CC1")]
    out = _run(calls)

    assert len(out["responses"]) == 2, "a call went unanswered"
    ids = {r.id for r in out["responses"]}
    assert ids == {"a3d_print-0", "a3d_print-1"}, (
        f"responses came back under the wrong ids: {ids}")


def test_two_printers_are_the_point_of_owning_a_fleet():
    """The guard must not become a global print lock.

    Same model, different machines, is exactly what a fleet is for. A guard
    keyed on the tool rather than the arguments would serialise these, and the
    feature would be removed within a week.
    """
    out = _run([_call(PRINT, 0, query_or_id="benchy", printer="CC1"),
                _call(PRINT, 1, query_or_id="benchy", printer="CC2")])

    assert out["runs"] == 2, (
        "a print on CC1 blocked a print on CC2 — the guard is keyed on the "
        "tool instead of on the arguments")


def test_a_different_model_on_the_same_printer_is_a_different_job():
    out = _run([_call(PRINT, 0, query_or_id="benchy", printer="CC1"),
                _call(PRINT, 1, query_or_id="keychain", printer="CC1")])
    assert out["runs"] == 2


def test_argument_order_does_not_make_it_a_different_request():
    """The key is built from sorted arguments, so the same request written two
    ways is one request. If it were not, a model that emitted the keys in a
    different order would defeat the guard completely."""
    out = _run([_call(PRINT, 0, query_or_id="benchy", printer="CC1"),
                _call(PRINT, 1, printer="CC1", query_or_id="benchy")])
    assert out["runs"] == 1, (
        "the same print written with its arguments in a different order "
        "started twice")


def test_three_copies_still_start_one():
    """A retry storm is the realistic shape of this, not a neat pair."""
    out = _run([_call(PRINT, i, query_or_id="benchy", printer="CC1")
                for i in range(3)])
    assert out["runs"] == 1
    assert len(out["responses"]) == 3
