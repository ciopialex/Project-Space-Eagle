"""What the model is told a tool can do, versus what it can do.

`computer_settings` implements 66 actions and its declaration said only "The
action to perform". The model cannot invoke what it has never heard of, so 59
working capabilities — lock screen, mute, snap window, switch tab, toggle
wifi, task manager — were unreachable by voice. The eagle was less capable
than the eagle.

That gap is invisible in every other kind of test: the code works, the tests
pass, and the feature is dead because nothing advertises it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from actions.computer_settings import ACTION_MAP  # noqa: E402


def _declaration(name: str) -> dict:
    for d in main.TOOL_DECLARATIONS:
        if isinstance(d, dict) and d.get("name") == name:
            return d
    raise AssertionError(f"{name} is not declared to the model at all")


def test_computer_settings_advertises_what_it_implements():
    decl = _declaration("computer_settings")
    described = str(decl["parameters"]["properties"]["action"])
    missing = [a for a in sorted(ACTION_MAP) if a not in described]
    assert missing == [], (
        f"{len(missing)} working actions the model is never told about: "
        f"{missing[:12]}")


def test_the_advertised_list_is_generated_not_typed():
    """A hand-typed list drifts the moment someone adds an action — which is
    exactly how 59 of them went missing. Deriving it from ACTION_MAP means the
    declaration cannot fall behind the implementation."""
    import inspect
    src = inspect.getsource(main)
    assert "ACTION_MAP" in src, (
        "the declaration should be built from the implementation's own map")


def test_every_declared_tool_names_its_actions_or_has_none():
    """A tool with an `action` parameter and no vocabulary forces the model to
    guess. Guessed action names come back as 'Unknown action', which reads to
    the user as the eagle being unable to do something it can."""
    vague = []
    for d in main.TOOL_DECLARATIONS:
        if not isinstance(d, dict):
            continue
        props = (d.get("parameters") or {}).get("properties") or {}
        act = props.get("action")
        if not act:
            continue
        text = str(act.get("enum") or "") + str(act.get("description") or "")
        # Some vocabulary must be present: an enum, or names in the prose.
        if len(text) < 40:
            vague.append(d.get("name"))
    assert vague == [], f"these give the model no action vocabulary: {vague}"


def test_no_tool_hides_an_action_it_can_perform():
    """The general form. An action that is dispatched but never declared is a
    dead feature: it works, it is tested, and nothing can reach it. Three
    tools were in this state — computer_settings hiding 59, computer_control
    hiding 3, code_helper hiding 2."""
    import pathlib
    import re

    declared = {}
    for d in main.TOOL_DECLARATIONS:
        if not isinstance(d, dict):
            continue
        props = (d.get("parameters") or {}).get("properties") or {}
        act = props.get("action") or {}
        declared[d.get("name")] = str(act.get("enum") or "") + str(act.get("description") or "")

    pattern = re.compile(r'action\s*(?:==|in)\s*[\("\']+([a-z_]{3,})')
    hidden = {}
    root = Path(__file__).resolve().parent.parent / "actions"
    for path in sorted(root.glob("*.py")):
        tool = path.stem
        if tool not in declared:
            continue
        dispatched = set(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
        missing = sorted(a for a in dispatched if a not in declared[tool])
        if missing:
            hidden[tool] = missing
    assert hidden == {}, f"dispatched but never advertised: {hidden}"
