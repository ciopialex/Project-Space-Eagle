"""The human's "yes" is asked once, before the mission runs, never per click.

The alternative — asking at the moment a commit-shaped control is about to be
clicked — breaks the "no mid-mission approval prompts" design this whole tool
is built on, and by then the model has usually already told the user the
mission is under way, so the question lands as an interruption instead of an
informed choice made before anything started.

`_start` scans the PLAN for a step that would refuse at `web_agency`'s own
consent gate (`irreversible_reason`, the exact function `_gate_click` calls)
and, if one exists and `confirm` was not passed, refuses to start at all —
`ok=False`, because nothing ran, and this codebase does not report success it
never had. Passing `confirm=True` starts the mission with `authorized=True`,
which `mission_ladder.attempt` copies onto every step as it runs, and
`mission_runners._web` reads to tell `web_agency` the human already said yes
— `confirmed=True` on the wire, a parameter never declared to the model's own
tool schema, so the model itself has no way to grant this on its own.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.mission as M  # noqa: E402
from core.mission import Step  # noqa: E402


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "stuck.md")
    return tmp_path


# ── the up-front ask ─────────────────────────────────────────────────────────

def test_a_real_commit_step_blocks_the_start_and_asks():
    r = M.mission({"action": "start", "goal": "g",
                   "steps": ["Open http://x/", "Click the Submit form button"]})
    assert r.ok is False
    assert r.data.get("needs_confirmation") is True
    assert "Submit form button" in r.data.get("risky_step", "")
    # And nothing was actually started.
    assert "no mission" in M.mission({"action": "status"}).message.lower()


def test_confirm_true_starts_it_and_marks_it_authorized():
    steps = ["Open http://x/", "Click the Submit form button"]
    blocked = M.mission({"action": "start", "goal": "g", "steps": steps})
    assert blocked.ok is False

    r = M.mission({"action": "start", "goal": "g", "steps": steps,
                  "confirm": True})
    assert r.ok is True
    m = M._load()
    assert m.authorized is True


def test_a_plan_with_no_commit_step_never_asks():
    r = M.mission({"action": "start", "goal": "g",
                   "steps": ["Open http://x/", "Click the search box"]})
    assert r.ok is True
    assert r.data.get("needs_confirmation") is None


def test_only_click_steps_are_scanned_not_file_operations():
    """The regression: `_COMMITTING["file"]` means a web button like "File a
    complaint", not a filesystem read. Scanning a `file_read` step's own
    English wording against that vocabulary flagged "Read the file
    notes.txt..." as the reason to ask — the wrong step, for the wrong
    reason, while the real commit later in the same plan went unmentioned."""
    r = M.mission({"action": "start", "goal": "g", "steps": [
        "Open http://x/",
        "Read the file notes.txt on the Desktop",
        "Click the Submit form button",
    ]})
    assert r.ok is False
    assert "Submit form button" in r.data.get("risky_step", "")


def test_from_first_irreversible_step_directly():
    from actions.mission import _first_irreversible_step
    steps = [Step(intent="Read the file notes.txt on the Desktop"),
             Step(intent="Click the Submit form button")]
    risky = _first_irreversible_step(steps)
    assert risky is not None and "Submit" in risky.intent


# ── the authorization actually reaches web_agency ───────────────────────────

def test_an_authorized_step_tells_web_agency_it_was_confirmed(monkeypatch):
    from core.mission_runners import _web
    seen = {}

    def fake_web_agency(parameters=None, **kw):
        seen.update(parameters or {})
        from core.tool_result import ToolResult
        return ToolResult.success("ok")

    import actions.web_agency as WA
    monkeypatch.setattr(WA, "web_agency", fake_web_agency)

    step = Step(intent="Click the Submit form button",
               target="the Submit form button")
    step.authorized = True
    _web("click")(step)
    assert seen.get("confirmed") is True


def test_an_unauthorized_step_never_sets_confirmed(monkeypatch):
    from core.mission_runners import _web
    seen = {}

    def fake_web_agency(parameters=None, **kw):
        seen.update(parameters or {})
        from core.tool_result import ToolResult
        return ToolResult.success("ok")

    import actions.web_agency as WA
    monkeypatch.setattr(WA, "web_agency", fake_web_agency)

    step = Step(intent="Click the search box", target="the search box")
    _web("click")(step)
    assert "confirmed" not in seen


def test_confirmed_is_not_in_the_models_own_tool_declaration():
    """If the model could set this itself, the whole consent guard would be
    theatre — it must only ever be plumbed in by mission code that has
    already gotten an explicit human yes."""
    import main
    decl = next(t for t in main.TOOL_DECLARATIONS if t["name"] == "web_agency")
    assert "confirmed" not in decl["parameters"]["properties"]
