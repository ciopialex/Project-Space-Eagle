"""One button, and it has to tell the truth about which wall you are at.

Connecting YouTube took four separate discoveries: sign in to Google, notice
the token predates the YouTube scope, reconnect, then find that the Data API
is switched off on the Cloud project — and the eagle blamed the user's account
for that last one for two rounds. Nobody should have to learn that sequence.

The states are genuinely different and have different fixes, so the one thing
this must never do is collapse them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import youtube_setup as S  # noqa: E402


def test_no_google_account_at_all(monkeypatch):
    monkeypatch.setattr(S, "_scopes", lambda: [])
    assert S.youtube_status()["state"] == "needs_google"


def test_connected_but_the_token_predates_youtube(monkeypatch):
    monkeypatch.setattr(S, "_scopes", lambda: ["openid", "…/gmail.readonly"])
    st = S.youtube_status()
    assert st["state"] == "needs_scope"
    assert "reconnect" in st["action"].lower()


def test_scope_granted_but_the_api_is_switched_off(monkeypatch):
    monkeypatch.setattr(S, "_scopes", lambda: ["…/youtube.readonly"])
    monkeypatch.setattr(S, "_probe", lambda: ("api_off", "project 12345"))
    st = S.youtube_status()
    assert st["state"] == "needs_api"
    assert "12345" in st["fix_url"], "the console link must name the project"


def test_everything_working(monkeypatch):
    monkeypatch.setattr(S, "_scopes", lambda: ["…/youtube.readonly"])
    monkeypatch.setattr(S, "_probe", lambda: ("ok", ""))
    assert S.youtube_status()["state"] == "ready"


def test_a_network_failure_is_not_reported_as_a_missing_permission(monkeypatch):
    """The wifi being down is not the user's account being wrong. Telling them
    to reconnect their Google account over a dropped request is how they end
    up redoing work for nothing."""
    monkeypatch.setattr(S, "_scopes", lambda: ["…/youtube.readonly"])
    monkeypatch.setattr(S, "_probe", lambda: ("error", "connection refused"))
    st = S.youtube_status()
    assert st["state"] == "error"
    assert "sign in" not in st["action"].lower()


def test_every_state_offers_something_to_do():
    """A status with no next step is a dead end wearing a badge."""
    for state in S.STATES:
        assert S.STATES[state]["action"], state
        assert S.STATES[state]["label"], state


def test_enabling_reports_honestly_when_it_cannot(monkeypatch):
    """gcloud is not installed for most people. Saying "enabled!" when nothing
    happened is the failure this whole codebase keeps fighting."""
    monkeypatch.setattr(S, "_gcloud_enable", lambda project: (False, "gcloud not installed"))
    ok, detail = S.enable_api("12345")
    assert ok is False
    assert "console" in detail.lower(), "no manual route offered"
