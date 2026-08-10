"""Summarising by title, because that is what the previous tool hands you.

Straight from a real session log:

    [Tool] ✓ youtube_api (464ms)
       result: Put Yourself First & Success Will Follow Relentlessly | Napoleon Hill
    [Tool] ▶ youtube_video {action=summarize, query=Put Yourself First & ...}
    [Tool] ? youtube_video no status reported (0ms)
       said: That does not look like a YouTube link: Put Yourself First ...

`youtube_api` returns TITLES. `summarize` demanded a URL. So the one chain a
user actually asks for — "summarise my latest liked video" — could not
complete, and the refusal came back with no status at all, so the model was
free to read it as anything.

`_scrape_first_video_url` was already in the same file doing exactly this job
for `play`. Two actions of one tool disagreeing about what an argument means.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.youtube_video as Y  # noqa: E402


def test_a_title_is_resolved_to_a_video_instead_of_refused(monkeypatch):
    seen = {}

    def fake_scrape(q):
        seen["query"] = q
        return "https://www.youtube.com/watch?v=abc12345678"

    monkeypatch.setattr(Y, "_scrape_first_video_url", fake_scrape)
    monkeypatch.setattr(Y, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(Y, "_extract_video_id", lambda u: "abc12345678")
    # `_get_transcript`, not `_fetch_transcript` — the first draft named the
    # wrong function with raising=False, so monkeypatch silently did nothing
    # and these tests hit YouTube and Gemini for real (14s). A stub that does
    # not stub is a test that passes without testing.
    monkeypatch.setattr(Y, "_get_transcript",
                        lambda vid: "a transcript about putting yourself first")
    monkeypatch.setattr(Y, "_summarize_with_gemini", lambda t, u: "a summary")

    out = Y._handle_summarize(
        {"query": "Put Yourself First & Success Will Follow | Napoleon Hill"},
        player=None, speak=None)

    assert "does not look like a YouTube link" not in str(out), \
        "still refusing the title that youtube_api hands it"
    assert seen.get("query", "").startswith("Put Yourself First")


def test_a_title_that_matches_nothing_fails_with_a_next_step(monkeypatch):
    monkeypatch.setattr(Y, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(Y, "_scrape_first_video_url", lambda q: None)
    out = Y._handle_summarize({"query": "asdkjhasdkjh not a real video"},
                              player=None, speak=None)
    assert "Could not find" in str(out)


def test_a_real_url_still_goes_straight_through_without_a_search(monkeypatch):
    called = []
    monkeypatch.setattr(Y, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(Y, "_scrape_first_video_url",
                        lambda q: called.append(q) or None)
    monkeypatch.setattr(Y, "_extract_video_id", lambda u: "dQw4w9WgXcQ")
    monkeypatch.setattr(Y, "_get_transcript", lambda vid: "words")
    monkeypatch.setattr(Y, "_summarize_with_gemini", lambda t, u: "a summary")

    Y._handle_summarize({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                        player=None, speak=None)
    assert called == [], "searched YouTube for a URL it was already given"


def test_no_input_at_all_still_asks_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(Y, "_TRANSCRIPT_OK", True)
    out = Y._handle_summarize({}, player=None, speak=None)
    assert "which video" in str(out).lower()
