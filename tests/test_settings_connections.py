"""What the settings panel needs to know about sign-ins.

The user's question — "how do I log in?" — had no answer in the interface. It
was a voice command you had to know to say, or a script you had to run in a
terminal. Two states were invisible:

1. The Google connection predates the YouTube scope, so every YouTube request
   fails with a message the user only sees if they happen to ask. The settings
   panel showed a green LINKED badge the whole time, which was true and
   misleading.
2. The eagle's browser has its own sign-ins, entirely separate from Chrome.
   Nothing anywhere showed which sites it could actually use.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import app_settings as S  # noqa: E402


def test_a_google_link_without_youtube_is_reported_as_partial(monkeypatch):
    """LINKED is not the whole truth when the scope the user is about to need
    is missing. Showing it as fully connected is how "just reconnect" becomes
    a thing only the eagle knows."""
    monkeypatch.setattr(S, "_granted_scopes", lambda: [
        "openid", "https://www.googleapis.com/auth/gmail.readonly"])
    state = S.google_capabilities()
    assert state["youtube"] is False
    assert state["needs_reconnect"] is True


def test_a_google_link_with_youtube_is_complete(monkeypatch):
    monkeypatch.setattr(S, "_granted_scopes", lambda: [
        "openid", "https://www.googleapis.com/auth/youtube.readonly"])
    state = S.google_capabilities()
    assert state["youtube"] is True
    assert state["needs_reconnect"] is False


def test_no_token_is_not_reported_as_needing_a_reconnect(monkeypatch):
    """Never connected and connected-but-stale are different problems with
    different buttons. Conflating them offers "Reconnect" to someone who has
    never connected."""
    monkeypatch.setattr(S, "_granted_scopes", lambda: [])
    state = S.google_capabilities()
    assert state["connected"] is False
    assert state["needs_reconnect"] is False


def test_browser_sessions_lists_what_the_eagle_can_actually_use(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_browser_profile", lambda: tmp_path)
    (tmp_path / ".aethelark-imported").write_text("youtube.com\ngoogle.com\n")
    assert S.browser_sessions() == ["google.com", "youtube.com"]


def test_no_browser_profile_is_an_empty_list_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_browser_profile", lambda: tmp_path / "nope")
    assert S.browser_sessions() == []


def test_the_settings_payload_carries_both(monkeypatch, tmp_path):
    """The panel renders from one dict. A field the builder forgets is a row
    that silently never appears."""
    monkeypatch.setattr(S, "_browser_profile", lambda: tmp_path)
    (tmp_path / ".aethelark-imported").write_text("github.com\n")
    monkeypatch.setattr(S, "_granted_scopes", lambda: ["openid"])

    payload = S.snapshot()
    google = payload["accounts"]["google"]
    assert "youtube" in google and "needs_reconnect" in google
    assert payload["accounts"]["browser"]["sites"] == ["github.com"]
