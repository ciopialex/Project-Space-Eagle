"""What the model is told a tool is, and how the scheduler treats it, agree.

There are two registries in main.py and they were kept in step by hand:

    TOOL_DECLARATIONS   what the model is told exists
    TOOL_SPECS          timeout, priority, exclusivity — how it is scheduled

`mission` was in the first and not the second, and the failure was silent
because an undeclared tool does not get "no policy". It gets whichever default
the caller happened to write, and the scheduler asks four different ways:

    main.py  pending.sort(key=... TOOL_SPECS.get(fc.name, ToolSpec()).priority)
    main.py  spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))     x3

So a missing entry is simultaneously priority 1 and exclusive — a policy nobody
chose, nobody wrote down, and nobody could read off either registry. It cannot
crash and it cannot be noticed by using the app; the tool simply schedules
differently from how it appears to.

This is the same shape as test_declared_actions_match.py, and a property rather
than a mirror: it compares two independent data structures against each other
and fails on a real disagreement. It reads no source and asserts no code path,
so a rename cannot break it and a reordering cannot fool it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main  # noqa: E402


def _declared() -> list[str]:
    return [d["name"] for d in main.TOOL_DECLARATIONS
            if isinstance(d, dict) and d.get("name")]


def test_every_tool_the_model_is_told_about_has_a_scheduling_policy():
    """The defect itself."""
    missing = sorted(n for n in _declared() if n not in main.TOOL_SPECS)
    assert missing == [], (
        f"declared to the model with no ToolSpec: {missing}\n"
        f"These do not get 'no policy' — they get ToolSpec() when the "
        f"scheduler sorts by priority and ToolSpec(exclusive=True) when it "
        f"checks exclusion, so they run under a policy nobody wrote. Declare "
        f"one in TOOL_SPECS, even if it only records what the fallback was "
        f"already doing.")


def test_every_scheduling_policy_belongs_to_a_tool_that_exists():
    """The other direction. A spec for a tool nobody can call is dead policy,
    and dead policy is indistinguishable from policy that stopped being
    applied — which is the harder bug of the two."""
    declared = set(_declared())
    orphans = sorted(n for n in main.TOOL_SPECS
                     if n not in declared and not main.MODULE_BUS.owns(n))
    assert orphans == [], (
        f"ToolSpec entries for tools that are neither declared to the model "
        f"nor owned by the module bus: {orphans}")


def test_module_tools_are_specced_by_their_own_manifest():
    """The bus fills these in at import, from each module's manifest.

    Guards the guard above: if module tools stopped getting specs, the second
    test would still pass — they would simply vanish from TOOL_SPECS — while
    every module tool silently fell back to the unwritten default.
    """
    owned = [n for n in main.TOOL_SPECS if main.MODULE_BUS.owns(n)]
    if not owned:
        pytest.skip("no modules installed in this environment")
    assert len(owned) >= 5, (
        f"only {len(owned)} module tools carry a ToolSpec; the manifest loop "
        f"in main.py that fills them from `[[tools]]` has stopped running")


def test_the_two_defaults_a_missing_spec_falls_into_still_disagree():
    """Why the first test matters, asserted rather than argued.

    If these two ever became the same object, a missing entry would at least be
    consistent and this whole file would be worth less. They are not the same,
    and that is the point: the same absent tool is low-priority to one caller
    and exclusive to another.
    """
    sort_default = main.ToolSpec()
    exclusion_default = main.ToolSpec(exclusive=True)
    assert sort_default.exclusive != exclusion_default.exclusive, (
        "the fallbacks agree now; if that is deliberate, this file's premise "
        "is gone and the first test is merely tidy rather than load-bearing")


def test_mission_is_declared_the_way_it_was_already_behaving():
    """The specific regression, pinned.

    `mission` drives real applications one step at a time, so it holds the
    desktop while a step runs. The scheduler had been treating it as exclusive
    via the fallback; this asserts the written policy did not quietly relax
    that while making it visible.
    """
    spec = main.TOOL_SPECS.get("mission")
    assert spec is not None, "mission lost its ToolSpec again"
    assert spec.exclusive, (
        "mission is no longer exclusive. It drives the desktop a step at a "
        "time and the scheduler's own fallback treated it as exclusive; "
        "relaxing that lets a second tool touch the screen mid-step.")
    assert "desktop" in spec.writes
