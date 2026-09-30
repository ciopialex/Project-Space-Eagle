"""One question, two identical answers, so the eagle says it twice.

`test_the_same_print_does_not_start_twice.py` covers the same guard for tools
that declare `one_at_a_time`. That flag is about physical danger — filament and
hours — and it was gating a check that is really about correctness, so every
tool WITHOUT the flag went on racing itself.

Measured in ~/eagle.log, 2026-09-07, on `a3d_status`, which does not declare it:

    [Tool] > a3d_status (epoch=7) {printer=CC2}
    [Tool] > a3d_status (epoch=8) {printer=CC2}
    [Tool] ! a3d_status STALE (epoch 7 -> 8) - the turn moved on
    [Tool] v a3d_status (10652ms)
    [Tool] v a3d_status (10613ms)

Two calls, 39 ms apart, ten and a half seconds each because the printer was
unreachable, both answered. The model asked one question and received two
identical tool results, and the operator heard the answer twice.

The shape matters more than the tool. It needs a call slow enough to outlive
its own turn — an unreachable host, a cold API, a large download — and then any
turn boundary while it is in flight gives the model a reason to re-issue it.
Neither copy is wrong; the pair is.

This is a seam: the scheduler, the dispatcher and the turn epoch meet here, and
no batch-level check can see across two turns because they arrive as two
separate batches. `_dispatch_one_tool` is the only place that can, because
`_inflight_by_args` outlives a batch.

The scheduler is stubbed at `_execute_tool`, one level BELOW the deduplication,
so these measure the guard rather than replacing it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

#: A tool that does NOT declare one_at_a_time. If it ever starts to, this file
#: stops testing what it was written for — see the guard test at the bottom.
SLOW = "a3d_status"


def _live(tracker: dict, delay: float = 0.15):
    live = main.AethelarkLive.__new__(main.AethelarkLive)
    live._turn_epoch = 0
    live._inflight_by_args = {}

    async def fake_execute(fc, call_epoch=-1):
        tracker["active"] += 1
        tracker["runs"] += 1
        tracker["max"] = max(tracker["max"], tracker["active"])
        await asyncio.sleep(delay)          # the unreachable printer
        tracker["active"] -= 1
        return SimpleNamespace(id=fc.id, name=fc.name,
                               response={"ok": True, "printer": fc.args.get("printer")})

    live._execute_tool = fake_execute
    return live


def _call(name: str, i: int, **args):
    return SimpleNamespace(id=f"{name}-{i}", name=name, args=args)


def _two_turns(name: str, args_a: dict, args_b: dict) -> dict:
    """Two separate turns, overlapping in time — what the log recorded."""
    tracker = {"active": 0, "max": 0, "runs": 0}
    live = _live(tracker)

    async def go():
        first = asyncio.create_task(
            live._schedule_tool_calls([_call(name, 0, **args_a)], 7))
        await asyncio.sleep(0.02)           # the 39 ms between the two log lines
        second = asyncio.create_task(
            live._schedule_tool_calls([_call(name, 1, **args_b)], 8))
        return await asyncio.gather(first, second)

    a, b = asyncio.run(go())
    tracker["responses"] = list(a) + list(b)
    return tracker


# ── the defect ──────────────────────────────────────────────────────────────

def test_the_same_call_in_a_later_turn_does_not_run_twice():
    out = _two_turns(SLOW, {"printer": "CC2"}, {"printer": "CC2"})
    assert out["runs"] == 1, (
        f"{SLOW} ran {out['runs']} times for one question. The turn moved on "
        f"while it was in flight and the model re-issued it; both answers came "
        f"back and the eagle said it twice.")
    assert out["max"] == 1


def test_both_turns_still_get_an_answer():
    """The protocol needs one response per call, whichever turn asked."""
    out = _two_turns(SLOW, {"printer": "CC2"}, {"printer": "CC2"})
    ids = {r.id for r in out["responses"]}
    assert ids == {f"{SLOW}-0", f"{SLOW}-1"}, (
        f"a call went unanswered: {ids}. An unanswered function call wedges "
        f"the turn.")


def test_the_second_caller_gets_the_real_result_not_an_error():
    out = _two_turns(SLOW, {"printer": "CC2"}, {"printer": "CC2"})
    for r in out["responses"]:
        assert r.response.get("ok") is True, (
            f"{r.id} was handed {r.response!r} instead of the answer the first "
            f"call produced")


# ── and it must not become a global lock ────────────────────────────────────

def test_a_different_printer_still_runs():
    """Asking about CC1 while CC2 is still timing out is a real question."""
    out = _two_turns(SLOW, {"printer": "CC1"}, {"printer": "CC2"})
    assert out["runs"] == 2, (
        "a call about one printer blocked a call about another — the guard is "
        "keyed on the tool instead of on the arguments")


def test_the_same_call_after_the_first_finishes_runs_again():
    """This is a de-duplicator, not a cache. State changes between questions."""
    tracker = {"active": 0, "max": 0, "runs": 0}
    live = _live(tracker, delay=0.01)

    async def go():
        await live._schedule_tool_calls([_call(SLOW, 0, printer="CC2")], 7)
        await asyncio.sleep(0.05)          # the first is long done
        await live._schedule_tool_calls([_call(SLOW, 1, printer="CC2")], 9)

    asyncio.run(go())
    assert tracker["runs"] == 2, (
        "the second question was answered from the first one's result after it "
        "had already finished — that is a cache, and the printer may have "
        "changed state")


def test_argument_order_does_not_make_it_a_different_call():
    out = _two_turns(SLOW, {"printer": "CC2", "detail": "full"},
                     {"detail": "full", "printer": "CC2"})
    assert out["runs"] == 1


# ── the premise ─────────────────────────────────────────────────────────────

def test_this_tool_does_not_declare_one_at_a_time():
    """Everything above is only meaningful while that is true.

    If a3d ever adds the flag to a3d_status, these assertions start passing via
    the old narrow path and stop testing that the guard is universal.
    """
    spec = main.TOOL_SPECS.get(SLOW)
    if spec is None:
        pytest.skip(f"{SLOW} is not installed on this machine")
    assert not spec.one_at_a_time, (
        f"{SLOW} now declares one_at_a_time, so this file no longer proves the "
        f"guard covers tools without it — point it at another slow tool")
