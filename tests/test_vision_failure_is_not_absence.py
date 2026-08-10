"""A vision call that FAILED is not a screen that lacks the thing.

`VisionGrounder.find` ended in a bare `except Exception: return None`, and
`None` is also how it says "not on screen". So a quota error, a dropped
connection or a malformed reply all arrived at the caller as a confident
"that control does not exist."

Measured live, three vision lookups in a row against a real MakerWorld page
that plainly showed a search bar:

    vision.find('the search bar')                    -> (1477, 66)  [5808ms]
    vision.find('search box at the top of the page') -> None        [671ms]
    vision.find('Upload button')                     -> None        [719ms]

671ms is not a vision call. A real one took 5808ms. Those two raised — almost
certainly a rate limit, since they came seconds apart — and the exception was
discarded. The eagle was then told the search bar was not there, and stopped
trying something that would have worked a minute later.

This is the same defect as `bot_wall`, `youtube_video` and `code_helper`: a
failure reported as a determinate negative. `found=False` and `could_not_look`
must not be the same value.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.vision import VisionGrounder  # noqa: E402


class _Img:
    size = (1280, 720)
    def copy(self): return self
    def convert(self, _m): return self
    def thumbnail(self, _s): pass
    def save(self, buf, format=None, quality=None): buf.write(b"jpegbytes")


def _grounder(client_fn):
    return VisionGrounder(grab_fn=lambda: _Img(), client_fn=client_fn)


class _Resp:
    def __init__(self, text): self.text = text


def _client(text=None, raises=None):
    class _Models:
        def generate_content(self, model=None, contents=None):
            if raises:
                raise raises
            return _Resp(text)
    return lambda: type("C", (), {"models": _Models()})()


# ── the bug ─────────────────────────────────────────────────────────────────

def test_a_quota_error_is_not_reported_as_absence():
    g = _grounder(_client(raises=RuntimeError("429 RESOURCE_EXHAUSTED")))
    assert g.find("the search bar") is None      # still None to old callers
    assert g.last_error, "the failure left no trace at all"
    assert "429" in g.last_error or "RESOURCE_EXHAUSTED" in g.last_error


def test_a_genuine_not_found_records_no_error():
    g = _grounder(_client(text="NOT_FOUND"))
    assert g.find("a parachute") is None
    assert not g.last_error, f"a clean 'not there' looked like a failure: {g.last_error}"


def test_an_unparseable_reply_is_an_error_not_an_absence():
    g = _grounder(_client(text="I'm not sure what you mean"))
    assert g.find("the search bar") is None
    assert g.last_error, "a reply with no coordinates is a failed look"


def test_last_error_is_cleared_by_the_next_good_look():
    g = _grounder(_client(raises=RuntimeError("boom")))
    g.find("x")
    assert g.last_error
    g._client_fn = _client(text="640,360")
    el = g.find("the search bar")
    assert el is not None
    assert not g.last_error, "a stale error survived a successful look"


# ── the happy path still works, and still scales ────────────────────────────

def test_a_coordinate_is_scaled_back_to_screen_space():
    g = VisionGrounder(grab_fn=lambda: _Img(), client_fn=_client(text="640,360"))
    g._max_edge = 640            # force a 2x downscale of the 1280-wide grab
    el = g.find("the search bar")
    assert el is not None
    assert el.center == (1280, 720), el.center
