"""file_controller on the tool contract.

Eighteen of its returns describe a failure and every one of them reached the
model with no status: a delete that hit a protected directory, a move whose
destination already existed, a read of a file that is not there. The model was
free to report any of them as done, and the prose was the only clue.

The failures here are decided deep — `_guard` eleven frames down, `move_file`
six — so this is not the `file_processor` boundary migration. The deciding
code marks its own prose with `Failed`; the entrypoint converts. That keeps
every containment test in `test_file_controller_containment.py` passing
unchanged, which is the point: those assert on the strings.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import file_controller as fc  # noqa: E402
from core.tool_result import Failed, ToolResult  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(fc, "_SAFE_ROOTS", (home,))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


# ── the entrypoint now states a verdict ─────────────────────────────────────

def test_a_successful_listing_is_an_explicit_success(sandbox):
    r = fc.file_controller({"action": "list", "path": "desktop"})
    assert isinstance(r, ToolResult)
    assert r.ok is True


def test_an_unknown_action_is_a_failure_not_a_description_of_one(sandbox):
    r = fc.file_controller({"action": "teleport"})
    assert isinstance(r, ToolResult) and r.ok is False
    assert "teleport" in r.message
    assert r.guidance, "a dead end with no next step"


def test_reading_a_missing_file_fails(sandbox):
    r = fc.file_controller({"action": "read", "path": "desktop",
                            "name": "nope.txt"})
    assert r.ok is False, "a file that is not there was reported as read"
    assert "nope.txt" in r.message


def test_a_containment_breach_fails_rather_than_reporting_done(sandbox):
    """The security case. `_guard` returns "Access denied: …" — which used to
    arrive as a result the model could summarise as success."""
    r = fc.file_controller({"action": "read", "path": "/etc", "name": "passwd"})
    assert r.ok is False
    assert "denied" in r.message.lower()
    assert r.guidance


def test_a_move_onto_an_existing_name_fails(sandbox):
    (sandbox / "Desktop" / "a.txt").write_text("a")
    (sandbox / "Desktop" / "b.txt").write_text("b")
    r = fc.file_controller({"action": "move", "path": "desktop", "name": "a.txt",
                            "destination": "b.txt"})
    assert r.ok is False, "refusing to overwrite was reported as a move"
    assert (sandbox / "Desktop" / "a.txt").exists()


def test_a_move_with_no_destination_fails(sandbox):
    (sandbox / "Desktop" / "a.txt").write_text("a")
    r = fc.file_controller({"action": "move", "path": "desktop", "name": "a.txt"})
    assert r.ok is False
    assert r.guidance, "the model needs to know to ask where"


def test_deleting_a_protected_directory_fails(sandbox):
    r = fc.file_controller({"action": "delete", "path": "home", "name": "Desktop"})
    assert r.ok is False
    assert (sandbox / "Desktop").exists(), "a protected directory was deleted"


def test_bad_parameters_are_a_failure(sandbox):
    r = fc.file_controller({"action": "read", "path": "desktop", "name": "x",
                            "max_chars": "not-a-number"})
    assert r.ok is False
    assert r.guidance


# ── the helpers keep their prose, so containment tests keep working ─────────

def test_helpers_still_return_strings(sandbox):
    """`test_file_controller_containment.py` asserts `"Access denied" in result`
    against these functions directly. Migration must not break that."""
    (sandbox / "Desktop" / "notes.txt").write_text("mine")
    out = fc.rename_file("desktop", "notes.txt", "../../../outside/stolen.txt")
    assert isinstance(out, str)
    assert "Access denied" in out


def test_a_failed_helper_string_is_marked_as_failed(sandbox):
    """...and the marker is what the entrypoint reads instead of the prose."""
    (sandbox / "Desktop" / "notes.txt").write_text("mine")
    out = fc.rename_file("desktop", "notes.txt", "../../../outside/stolen.txt")
    assert "Access denied" in out
    assert isinstance(out, Failed)


def test_a_successful_helper_string_is_not_marked(sandbox):
    out = fc.create_folder("desktop", "Reports")
    assert isinstance(out, str) and not isinstance(out, Failed)


# ── the failure this prevents ───────────────────────────────────────────────

def test_no_failure_shaped_result_reaches_the_model_as_ok(sandbox):
    """The regression net. Every one of these is a refusal; not one of them may
    come back with ok=True, whatever the wording ends up being."""
    refusals = [
        {"action": "read", "path": "desktop", "name": "missing.txt"},
        {"action": "list", "path": "desktop", "name": "", "show_hidden": False}
        | {"path": "/etc"},
        {"action": "delete", "path": "desktop", "name": "missing.txt"},
        {"action": "copy", "path": "desktop", "name": "missing.txt",
         "destination": "x.txt"},
        {"action": "rename", "path": "desktop", "name": "missing.txt",
         "new_name": "y.txt"},
        {"action": "info", "path": "desktop", "name": "missing.txt"},
        {"action": "nonsense"},
    ]
    for params in refusals:
        r = fc.file_controller(params)
        assert r.ok is False, f"{params} came back ok=True: {r.message!r}"
