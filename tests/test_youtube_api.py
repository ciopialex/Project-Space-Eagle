"""The user's own account, read through the API he already authorised.

This exists because the browser route cannot work for Google and never will.
Chrome binds a Google session to the profile that created it, so a session
lifted into the eagle's browser is detected and revoked — measured: every
cookie transferred and decrypted, the browser loaded all 61 including SID and
SAPISID, and YouTube reported signed out anyway and deleted LOGIN_INFO.

That is Google's session-theft protection working correctly, and the answer is
not to defeat it. Aethelark is already an authorised OAuth client for this
user's account. "My latest liked video" is one API call against a token that
already exists — no browser, no window, nothing to revoke.

The user's own rule for this trade: use an API over emulation when it does the
same job at least 30-40% faster. This is roughly 20x faster and it is also the
difference between working and not working.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from actions import youtube_api as Y  # noqa: E402


def liked_payload(*titles, next_page=None):
    body = {"items": [
        {"snippet": {"title": t,
                     "videoOwnerChannelTitle": f"{t} Channel",
                     "resourceId": {"videoId": f"id-{i}"}}}
        for i, t in enumerate(titles)]}
    if next_page:
        body["nextPageToken"] = next_page
    return body


@pytest.fixture
def api(monkeypatch):
    """Drive the module without a network or a real token."""
    calls = []

    def fake_get(url, params=None, token=None):
        calls.append({"url": url, "params": dict(params or {})})
        return fake_get.responses.pop(0)

    fake_get.responses = []
    monkeypatch.setattr(Y, "_get", fake_get)
    monkeypatch.setattr(Y, "_token", lambda: "tok")
    fake_get.calls = calls
    return fake_get


def test_the_latest_liked_video_is_named(api):
    api.responses = [liked_payload("Sky Whisper", "Older One")]
    r = Y.youtube_api({"action": "liked", "limit": 1})
    assert r.ok
    assert "Sky Whisper" in r.message
    assert "Older One" not in r.message, "asked for one, got the whole list"


def test_it_reads_the_liked_playlist_newest_first(api):
    """`LL` is the liked-videos playlist and YouTube returns it most-recent
    first. Asking for `mine` ordering instead would quietly return them by
    upload date, which is a different question."""
    api.responses = [liked_payload("A")]
    Y.youtube_api({"action": "liked", "limit": 1})
    assert api.calls[0]["params"]["playlistId"] == "LL"


def test_history_says_plainly_that_google_does_not_expose_it(api):
    """Watch history has had no API since 2016. Saying "I can't" here is
    correct — the failure this codebase guards against is claiming a limit
    that does NOT exist, not admitting one that does."""
    r = Y.youtube_api({"action": "history"})
    assert r.ok is False
    assert "history" in r.message.lower()
    assert "web_agency" in (r.guidance or ""), (
        "a real limit still deserves the route that might work")


def test_a_missing_youtube_scope_asks_for_one_reconnect(monkeypatch):
    """The token predates the YouTube scope, so the first run after this ships
    will hit exactly this path. It has to be actionable, not a shrug."""
    monkeypatch.setattr(Y, "_token", lambda: None)
    r = Y.youtube_api({"action": "liked"})
    assert r.ok is False
    assert "sign in" in (r.message + r.guidance).lower()


def test_an_empty_playlist_is_not_an_error(api):
    api.responses = [liked_payload()]
    r = Y.youtube_api({"action": "liked"})
    assert r.ok
    assert "no liked videos" in r.message.lower()


def test_subscriptions_and_playlists_are_reachable(api):
    api.responses = [{"items": [{"snippet": {"title": "Some Channel"}}]}]
    r = Y.youtube_api({"action": "subscriptions", "limit": 5})
    assert r.ok and "Some Channel" in r.message


def test_the_limit_is_capped_so_a_reply_stays_speakable(api):
    """This is a voice assistant: length is latency. Reading 50 titles aloud
    is a minute of someone's life."""
    api.responses = [liked_payload(*[f"V{i}" for i in range(50)])]
    r = Y.youtube_api({"action": "liked", "limit": 999})
    assert r.message.count("\n") <= Y.MAX_ITEMS


def test_an_api_error_does_not_pretend_to_have_an_answer(monkeypatch):
    monkeypatch.setattr(Y, "_token", lambda: "tok")
    def boom(url, params=None, token=None):
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(Y, "_get", boom)
    r = Y.youtube_api({"action": "liked"})
    assert r.ok is False
    assert "quota" in r.message.lower()


def test_an_unknown_action_lists_what_it_can_do(api):
    r = Y.youtube_api({"action": "chandelier"})
    assert r.ok is False
    assert "liked" in (r.guidance or r.message)


def test_never_raises_whatever_it_is_handed():
    for junk in (None, {}, {"action": None}, {"action": 5}, {"limit": "x"}):
        assert Y.youtube_api(junk) is not None


def test_the_youtube_scope_is_actually_requested():
    """The whole feature depends on one line in google_auth.SCOPES. Without it
    the token comes back without YouTube and every call 403s."""
    from actions.google_auth import SCOPES
    assert any("youtube" in s for s in SCOPES), (
        "youtube.readonly is not in the OAuth scopes")


def test_an_expired_or_unscoped_token_asks_for_a_reconnect(monkeypatch):
    """The first run after this ships hits exactly this: a token issued before
    the YouTube scope existed. "YouTube did not answer: 401 Unauthorized" is
    true and useless — it reads as a broken service rather than one click of
    the user's."""
    monkeypatch.setattr(Y, "_token", lambda: "stale")

    class Resp:
        status_code = 401
        def raise_for_status(self): raise RuntimeError("401 Client Error")

    def unauthorised(url, params=None, token=None):
        raise Y.NeedsReconnect("401")
    monkeypatch.setattr(Y, "_get", unauthorised)

    r = Y.youtube_api({"action": "liked"})
    assert r.ok is False
    assert "sign in to google again" in (r.message + " " + r.guidance).lower()
    assert "401" not in r.message, "leaked the HTTP status into speech"
