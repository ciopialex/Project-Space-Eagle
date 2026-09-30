"""A tool that returns prose cannot tell anyone whether it worked.

`normalize()` wraps a bare string as ok=True and marks it `_legacy_string`,
and `diag.tool_result` prints those as `?` — deliberately, because "the tool
said nothing either way" is genuinely different from "it worked".

Measured 2026-09-03, from a live session log:

    [Tool] ? web_search no status reported (6399ms)
    [Tool] ? screen_process no status reported (778ms)

Two of the eagle's own actions, on the two paths a person exercises most —
looking something up, and looking at the screen.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_result import ToolResult, normalize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_a_search_with_no_query_is_a_failure_not_prose():
    from actions.web_search import web_search
    out = web_search(parameters={"query": "", "mode": "search"})
    assert isinstance(out, ToolResult)
    assert out.ok is False
    assert out.guidance, "a refusal must say what to do next"
    assert not normalize(out).data.get("_legacy_string")


def test_finding_nothing_is_a_success_that_says_so(monkeypatch):
    """An empty result is a real answer. It must not read as a failure, and it
    must not read as prose the log cannot classify either."""
    import actions.web_search as ws
    monkeypatch.setattr(ws, "_race", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(ws, "_gemini", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(ws, "_ddg", lambda *a, **k: [], raising=False)
    out = ws.web_search(parameters={"query": "zzzqqxx no such thing", "mode": "search"})
    assert isinstance(out, ToolResult)
    assert out.ok is True
    assert "No results" in out.message
    assert out.data.get("results") == 0


def test_the_vision_tool_answers_for_itself():
    """The refusal branch already returned a ToolResult, so an *accepted*
    capture logging `?` made success the only outcome the log could not
    speak about. Asserted against the source, because reaching this branch
    needs a live session."""
    src = (ROOT / "main.py").read_text()
    i = src.index("[VISION_ACTIVE]")
    window = src[max(0, i - 700):i]
    assert "ToolResult.success(" in window, (
        "the accepted-capture branch still returns a bare string")
    assert "captured_bytes=len(img_b)" in src, (
        "the capture size is the one number that tells a black frame from a "
        "real one; it belongs in the result")


def test_a_bare_string_is_still_marked_legacy():
    """The mechanism these two were failing. Kept so the `?` in the log keeps
    meaning what it means."""
    assert normalize("some prose").data.get("_legacy_string") is True
    assert normalize(ToolResult.success("done")).data.get("_legacy_string") is None
