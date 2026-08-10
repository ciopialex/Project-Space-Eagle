"""The API already knows which video it means. Stop throwing that away.

`_titles()` read each item's `snippet` and returned `"title — channel"`,
discarding `snippet.resourceId.videoId` from the very same payload. So
"summarise my latest liked video" had to hand a TITLE to the next tool, which
then searched YouTube to re-find a video the API had already identified
exactly — a network round-trip that can also land on the wrong thing, because
titles collide: covers, re-uploads, lyric videos, "(Official Video)".

Carrying the id makes the chain exact. The scraper stays as the fallback for
titles that came from somewhere else (the user's own voice, a web page).

The url goes in the message rather than `ToolResult.data`, because `data` is
not sent to the model — `to_response()` emits only result/ok/guidance. It is
labelled so it is not spoken; `core/prompt.txt` already forbids reading URLs
aloud.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.youtube_api as A  # noqa: E402

PAYLOAD = {
    "items": [
        {"snippet": {"title": "what beat is this",
                     "videoOwnerChannelTitle": "maglvxx",
                     "resourceId": {"videoId": "abc12345678"}}},
        {"snippet": {"title": "Put Yourself First",
                     "channelTitle": "Napoleon Hill",
                     "resourceId": {"videoId": "def87654321"}}},
    ]
}


def test_the_video_id_survives_into_the_result():
    out = "\n".join(A._titles(PAYLOAD))
    assert "abc12345678" in out, "the API told us the id and we dropped it"


def test_the_title_is_still_first_and_readable():
    """It is spoken. The id must not get in the way of the sentence."""
    first = A._titles(PAYLOAD)[0]
    assert first.startswith("what beat is this — maglvxx")


def test_an_item_with_no_id_still_works():
    payload = {"items": [{"snippet": {"title": "Something",
                                      "channelTitle": "Someone"}}]}
    out = A._titles(payload)
    assert out and "Something — Someone" in out[0]


def test_an_item_with_no_title_is_still_skipped():
    payload = {"items": [{"snippet": {"resourceId": {"videoId": "x"}}}]}
    assert A._titles(payload) == []


def test_the_url_is_a_real_watch_url_the_next_tool_accepts():
    from actions.youtube_video import _is_valid_youtube_url
    line = A._titles(PAYLOAD)[0]
    url = [w.strip("[]() ") for w in line.split() if "youtube.com" in w or "youtu.be" in w]
    assert url, f"no url in {line!r}"
    assert _is_valid_youtube_url(url[0]), f"{url[0]!r} is not accepted downstream"


def test_the_url_is_marked_so_it_is_not_read_aloud():
    line = A._titles(PAYLOAD)[0]
    assert "youtube.com" in line
    # Whatever the marker is, the spoken title must not begin with it.
    assert not line.lstrip().startswith("http")
