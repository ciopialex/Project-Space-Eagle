"""Terminal logs that say what failed and why.

The logs are what found nearly every real bug this week — seven on one
sentence. What they could not show was the reason: the result was truncated at
80 characters, `guidance` (the field that carries the next step) was never
printed at all, and a legacy tool with no status was rendered with the same
green tick as a verified success.

So a failure looked like this:

    [Aethelark] 📤 youtube_api ✗ → Aethelark needs the user to sign in to Goog…

and the actual remedy — a Cloud console link, with the project id — was on the
next field, never shown.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.diag import tool_call, tool_result  # noqa: E402
from core.tool_result import ToolResult, normalize  # noqa: E402


def test_a_failure_shows_the_reason_and_the_next_step():
    out = tool_result("youtube_api", ToolResult.failure(
        "The API is switched off on this project.",
        guidance="Enable it at https://console.example/x?project=123 and retry."), 402)
    assert "switched off" in out
    assert "console.example" in out, "the remedy was not printed"
    assert "✗" in out


def test_a_failure_is_not_truncated():
    """Reasons are long. Cutting them at 80 characters removes the half that
    identifies the cause."""
    long_reason = "because " + ("x" * 300)
    out = tool_result("web_agency", ToolResult.failure(long_reason), 10)
    assert long_reason in out


def test_a_legacy_tool_is_not_shown_as_a_verified_success():
    """17 of 20 tools report no status. Printing a tick for them is the same
    lie the contract fix removed from the model's side — and it is worse in
    the log, because the log is what a human reads while debugging."""
    out = tool_result("browser_control",
                      normalize("focus_window requires wmctrl or xdotool"), 5)
    assert "✓" not in out, "an unverified result was shown as success"
    assert "?" in out or "no status" in out.lower()


def test_a_verified_success_still_reads_as_one():
    out = tool_result("youtube_api", ToolResult.success("Playing."), 200)
    assert "✓" in out and "Playing." in out


def test_the_call_line_shows_what_was_actually_passed():
    """When a tool misbehaves the first question is always "with what
    arguments"."""
    out = tool_call("web_agency", {"url": "https://x.test", "action": "open"}, 3)
    assert "web_agency" in out and "https://x.test" in out and "action" in out


def test_secrets_are_never_printed():
    """Arguments are logged verbatim, and some tools take credentials. A debug
    log that leaks a token into a terminal the user pastes into a chat is a
    worse bug than the one being debugged."""
    out = tool_call("some_tool", {"api_key": "AIzaSyREALKEYMATERIAL",
                                  "password": "hunter2",
                                  "token": "ya29.secret"}, 1)
    for secret in ("AIzaSyREALKEYMATERIAL", "hunter2", "ya29.secret"):
        assert secret not in out, f"leaked {secret}"
    assert "api_key" in out, "should still show that the field was present"


def test_long_arguments_are_shortened_but_not_hidden():
    out = tool_call("file_controller", {"text": "y" * 5000}, 1)
    assert len(out) < 1200
    assert "5000" in out or "…" in out


def test_it_never_raises_on_anything():
    for junk in (None, {}, {"a": object()}, {"b": b"\xff\xfe"}):
        assert tool_call("t", junk, 0) is not None
    assert tool_result("t", None, 0) is not None
