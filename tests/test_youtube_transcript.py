"""Transcript fetching, which had been silently broken.

`youtube_video` advertises "summarizes a video's content" and has a full
handler for it. It never worked: the code calls
`YouTubeTranscriptApi.list_transcripts`, which the library removed. Every
fetch raised AttributeError, was caught, logged as a non-fatal error, and
returned None — so the eagle reported "no transcript available" for every
video on YouTube, including ones with perfectly good captions.

That is a capability declared to the model and dead in the implementation:
the mirror of reporting a limit you have not hit, and just as damaging,
because the user asks for something the tool says it can do.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.youtube_video as Y  # noqa: E402


def test_it_uses_an_api_the_library_actually_has():
    """Pinned by name. The break was a silent rename, and the failure was
    swallowed into "no transcript" — indistinguishable from a video that
    genuinely has none."""
    from youtube_transcript_api import YouTubeTranscriptApi
    import inspect
    src = inspect.getsource(Y._get_transcript)
    # The CALL, not the mention — the docstring names the removed method
    # deliberately, to explain what broke.
    assert ".list_transcripts(" not in src, "calls a method the library removed"
    assert hasattr(YouTubeTranscriptApi, "fetch")


def test_a_captioned_video_returns_its_text(monkeypatch):
    class Snip:
        def __init__(self, t): self.text = t

    class Api:
        def fetch(self, video_id, languages=None):
            assert video_id == "abc123"
            return [Snip("hello there"), Snip("second line")]

    monkeypatch.setattr(Y, "YouTubeTranscriptApi", lambda: Api())
    got = Y._get_transcript("abc123")
    assert got and "hello there" in got and "second line" in got


def test_a_video_without_captions_returns_none(monkeypatch):
    class Api:
        def fetch(self, video_id, languages=None):
            raise RuntimeError("No transcripts were found")

    monkeypatch.setattr(Y, "YouTubeTranscriptApi", lambda: Api())
    assert Y._get_transcript("abc123") is None


def test_english_is_preferred_but_other_languages_are_accepted(monkeypatch):
    """A Romanian video with Romanian captions must still be summarisable."""
    tried = []

    class Api:
        def fetch(self, video_id, languages=None):
            tried.append(tuple(languages or ()))
            if languages and languages[0] == "en":
                raise RuntimeError("no english")
            class S:
                text = "salut lume"
            return [S()]

    monkeypatch.setattr(Y, "YouTubeTranscriptApi", lambda: Api())
    got = Y._get_transcript("abc123")
    assert got == "salut lume", f"gave up instead of trying other languages: {tried}"


def test_it_never_raises(monkeypatch):
    class Api:
        def fetch(self, *a, **k):
            raise Exception("anything at all")
    monkeypatch.setattr(Y, "YouTubeTranscriptApi", lambda: Api())
    assert Y._get_transcript("x") is None


# ── The URL the model passes must be the URL that is used ──────────────────
# `_handle_summarize` called `_ask_for_url(...)`, which opens a GUI box and
# waits for a paste — ignoring the `url` parameter completely. So "summarise
# this video" by voice was impossible: it silently demanded a manual paste,
# and whatever the user pasted is what got summarised. Live, that produced a
# confident summary of a completely different video.
#
# A voice assistant that opens a dialog and waits is not a voice assistant.

def test_the_url_parameter_is_used_and_no_box_appears(monkeypatch):
    asked = []
    monkeypatch.setattr(Y, "_ask_for_url", lambda *a, **k: asked.append(1) or "")
    monkeypatch.setattr(Y, "_get_transcript", lambda vid: "the real transcript")
    monkeypatch.setattr(Y, "_summarize_with_gemini",
                        lambda t, u: f"summary of {u}")
    monkeypatch.setattr(Y, "_save_summary", lambda c, u: "/tmp/x.txt")

    out = str(Y._handle_summarize(
        {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}, None, None))
    assert not asked, "opened a dialog instead of using the url it was given"
    assert "jNQXAC9IVRw" in out


def test_a_bare_video_id_or_short_link_works(monkeypatch):
    monkeypatch.setattr(Y, "_ask_for_url", lambda *a, **k: "")
    monkeypatch.setattr(Y, "_get_transcript", lambda vid: "text")
    monkeypatch.setattr(Y, "_summarize_with_gemini", lambda t, u: "ok")
    monkeypatch.setattr(Y, "_save_summary", lambda c, u: "/tmp/x.txt")
    for given in ("https://youtu.be/jNQXAC9IVRw",
                  "youtube.com/watch?v=jNQXAC9IVRw"):
        assert "couldn't" not in str(Y._handle_summarize({"url": given}, None, None)).lower()


def test_no_url_asks_the_user_in_WORDS_not_in_a_dialog(monkeypatch):
    """With nothing to work from it must hand the question back to the
    conversation, where the user actually is."""
    asked = []
    monkeypatch.setattr(Y, "_ask_for_url", lambda *a, **k: asked.append(1) or "")
    out = str(Y._handle_summarize({}, None, None))
    assert not asked, "popped a box at a user who is talking, not typing"
    assert "which video" in out.lower() or "url" in out.lower()
