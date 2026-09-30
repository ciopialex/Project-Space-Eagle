"""Printing takes a yes, and nothing else.

It used to take two calls with a JSON array copied between them: island_picks
handed back a jobs list and the model passed it verbatim to a3d_print_batch.
Nothing about that needed a model — the page already knows what is picked — and
a small model transcribing a JSON array is a defect waiting to happen.

Building the list here removes the copy and the mismatch it made possible: a
batch built from the screen cannot disagree with the screen. What the model
still supplies is the one thing only a human can authorise — the token proving
they said yes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
from core.module_bus.manifest import CONFIRM_PARAM

pytestmark = pytest.mark.usefixtures("user_answers_every_question")

PICKED = json.dumps([
    {"model_id": "124686", "printer": "CC1", "title": "Watch Stand/Display"},
    {"model_id": "935496", "printer": "CC2", "title": "Apple Watch Stand"},
])


class UI:
    def __init__(self, picks=PICKED, deck=True):
        self._picks, self._deck = picks, deck
    def deck_is_open(self):
        return self._deck
    def island_selection(self):
        return self._picks


class Bus:
    """Stands in for the module bus, recording what it was asked to run."""
    def __init__(self, gate=True):
        self.calls = []
        self.gate = gate
    def invoke(self, name, args, timeout_s=None):
        from core.tool_result import ToolResult
        self.calls.append((name, args))
        if self.gate and not args.get(CONFIRM_PARAM):
            return ToolResult.failure(
                "Shall I go ahead?", needs_confirmation=True,
                confirm_token="tok-123")
        return ToolResult.success("started", started=json.loads(args["jobs"]))


def test_the_model_supplies_no_job_list_at_all():
    bus = Bus()
    main.print_picked(UI(), bus)
    name, args = bus.calls[0]
    assert name == "a3d_print_batch"
    assert [(j["model_id"], j["printer"]) for j in json.loads(args["jobs"])] == [
        ("124686", "CC1"), ("935496", "CC2")]


def test_the_first_call_runs_nothing_and_asks_by_name():
    """A confirmation the human cannot check is not a confirmation."""
    out = main.print_picked(UI(), Bus())
    assert out.ok is False
    assert "Watch Stand/Display on CC1" in out.message
    assert "Apple Watch Stand on CC2" in out.message
    assert out.data["confirm_token"] == "tok-123"


def test_a_yes_carries_the_token_through_to_the_bus():
    bus = Bus()
    out = main.print_picked(UI(), bus, confirm_token="tok-123")
    assert out.ok
    assert bus.calls[-1][1][CONFIRM_PARAM] == "tok-123"


def test_the_jobs_it_prints_are_the_ones_on_screen_now():
    """Built from the live selection, so it cannot disagree with the screen."""
    bus = Bus(gate=False)
    changed = json.dumps([{"model_id": "999", "printer": "CC2", "title": "Other"}])
    main.print_picked(UI(picks=changed), bus, confirm_token="tok-123")
    assert json.loads(bus.calls[-1][1]["jobs"])[0]["model_id"] == "999"


def test_nothing_on_screen_is_a_refusal_and_runs_nothing():
    bus = Bus()
    out = main.print_picked(UI(deck=False), bus)
    assert not out.ok and bus.calls == []


def test_nothing_picked_is_a_refusal_and_runs_nothing():
    bus = Bus()
    out = main.print_picked(UI(picks="[]"), bus)
    assert not out.ok and bus.calls == []
    assert "picked" in out.message.lower()


def test_a_pick_with_no_printer_is_dropped_never_defaulted():
    bus = Bus(gate=False)
    picks = json.dumps([{"model_id": "1", "printer": "CC1", "title": "A"},
                        {"model_id": "2", "printer": None, "title": "B"}])
    main.print_picked(UI(picks=picks), bus, confirm_token="t")
    jobs = json.loads(bus.calls[-1][1]["jobs"])
    assert [j["model_id"] for j in jobs] == ["1"]


def test_picks_with_no_printer_at_all_run_nothing():
    bus = Bus()
    picks = json.dumps([{"model_id": "1", "title": "A"}])
    out = main.print_picked(UI(picks=picks), bus)
    assert not out.ok and bus.calls == []
    assert "printer" in out.message.lower()


@pytest.mark.parametrize("junk", ["", None, "not json", "{}"])
def test_an_unreadable_selection_runs_nothing(junk):
    bus = Bus()
    assert not main.print_picked(UI(picks=junk), bus).ok
    assert bus.calls == []


def test_the_tool_asks_the_model_for_only_a_token():
    """Every other parameter is a place a weak model can be wrong."""
    decl = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "print_picked")
    props = decl["parameters"]["properties"]
    assert list(props) == ["confirm_token"]
    assert not decl["parameters"].get("required")


def test_it_is_exclusive_because_it_drives_hardware():
    spec = main.TOOL_SPECS["print_picked"]
    assert spec.exclusive is True and "printer" in spec.writes


# ---- the raw batch tool is reachable, but not offered ----

def test_the_raw_batch_tool_is_not_offered_to_the_model():
    """Three ways to print is two ways to get it wrong.

    a3d_print_batch takes a job list, which is exactly the thing a small model
    should never be composing. It stays invokable because print_picked calls
    it; it stops being a choice.
    """
    from core.module_bus import ModuleBus
    bus = ModuleBus().discover()
    offered = [d["name"] for d in bus.tool_declarations()]
    assert "a3d_print_batch" not in offered
    assert bus.owns("a3d_print_batch"), "the harness can no longer call it either"


def test_the_tool_that_is_offered_is_the_safe_one():
    offered = [d["name"] for d in main.TOOL_DECLARATIONS]
    assert "print_picked" in offered


def test_hiding_it_did_not_disarm_its_gate():
    """Internal and ungated would be the worst of both."""
    import tomllib
    from pathlib import Path as _P
    manifest = tomllib.loads(
        (_P(__file__).resolve().parent.parent /
         "tests/fixtures/module_bus/manifests/a3d.toml").read_text())
    batch = next(t for t in manifest["tools"] if t["name"] == "print_batch")
    assert batch.get("internal") is True
    assert batch.get("confirm") is True
    assert batch.get("confirm_prompt")


def test_an_ordinary_tool_is_still_offered():
    """The flag must be opt-in, not a default that silently hides things."""
    from core.module_bus import ModuleBus
    bus = ModuleBus().discover()
    offered = [d["name"] for d in bus.tool_declarations()]
    for name in ("a3d_browse", "a3d_local", "a3d_discover", "a3d_print"):
        assert name in offered, f"{name} vanished from the model's view"
