"""A migrated tool must state success, not just failure.

Found by running the tools rather than testing them. `file_controller` and
`desktop_control` were migrated last night and their failures correctly carry
`ok=False` — but every SUCCESS arrived at the model with no `ok` key at all,
which is the signal reserved for "this tool has not migrated, read the prose."

The cause is that the entrypoints ended with `normalize(outcome)`, and
`normalize` is written for the FOURTEEN tools that have not migrated: there, a
plain string genuinely carries no verdict, so it is marked `_legacy_string`
and `to_response()` withholds `ok`. For a migrated tool the same string means
the opposite — every refusal is marked `Failed` where it is decided, so an
unmarked string is a result.

The tests that missed this asserted `r.ok is True`. That passes, because
`normalize` defaults a legacy string to `ok=True` on the object; it just never
reaches the wire. The assertion has to be on `to_response()`, which is the
only thing the model ever sees.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.code_helper import code_helper  # noqa: E402
from actions.desktop import desktop_control  # noqa: E402
from actions.file_controller import file_controller  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    import actions.file_controller as fc
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(fc, "_SAFE_ROOTS", (home,))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def test_a_successful_file_listing_says_so_on_the_wire(sandbox):
    resp = file_controller({"action": "list", "path": "desktop"}).to_response()
    assert "ok" in resp, "success reached the model with no verdict at all"
    assert resp["ok"] is True


def test_a_failed_file_read_says_so_on_the_wire(sandbox):
    resp = file_controller({"action": "read", "path": "desktop",
                            "name": "nope.txt"}).to_response()
    assert resp["ok"] is False
    assert resp["guidance"]


def test_a_successful_desktop_action_says_so_on_the_wire(monkeypatch, tmp_path):
    import actions.desktop as D
    monkeypatch.setattr(D, "_get_desktop", lambda: tmp_path)
    (tmp_path / "a.txt").write_text("x")
    resp = desktop_control({"action": "list"}).to_response()
    assert "ok" in resp, "success reached the model with no verdict at all"
    assert resp["ok"] is True


def test_a_successful_code_action_says_so_on_the_wire(monkeypatch, tmp_path):
    import actions.code_helper as C
    target = tmp_path / "f.py"
    monkeypatch.setattr(C, "_write", lambda *a, **k: ("print(1)", target))
    resp = code_helper({"action": "write", "description": "x"}).to_response()
    assert "ok" in resp
    assert resp["ok"] is True


def test_an_unmigrated_tool_still_withholds_its_verdict():
    """The other half of the contract, and the reason `normalize` cannot simply
    be changed: fourteen tools still return bare strings that describe
    failures. Claiming ok=True for those is the exact lie this was built to
    stop."""
    from core.tool_result import normalize
    resp = normalize("could not reach the server").to_response()
    assert "ok" not in resp, "invented a verdict for a tool that never gave one"


def test_the_two_paths_are_distinguishable_at_the_source():
    """`settled` is for migrated tools, `normalize` for the rest. Same input,
    deliberately different verdicts."""
    from core.tool_result import normalize, settled
    text = "Contents of Desktop/ (12 items):"
    assert settled(text).to_response()["ok"] is True
    assert "ok" not in normalize(text).to_response()


def test_settled_still_honours_a_marked_failure():
    from core.tool_result import Failed, settled
    resp = settled(Failed("Access denied: /etc", guidance="Tell the user.")).to_response()
    assert resp["ok"] is False
    assert resp["guidance"] == "Tell the user."


def test_settled_passes_a_real_toolresult_through_untouched():
    from core.tool_result import ToolResult, settled
    tr = ToolResult.failure("nope", guidance="ask")
    assert settled(tr) is tr
