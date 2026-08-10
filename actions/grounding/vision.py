"""Vision grounding — the fallback for surfaces that publish no structure.

Kept for canvases, games, remote desktops, and anything else with no
accessibility tree. Optimised against the measured baseline: a full-screen PNG
cost 93ms of local encode and shipped 188KB. Downscaling to 1280px and encoding
JPEG costs 46ms and ships 107KB for the same answer.

Vision-sourced elements carry no states, because a picture cannot tell you
whether a button is disabled. That is exactly why structure is tried first.
"""
from __future__ import annotations

import io
import re
from typing import Callable

from actions.grounding.base import Element

_PROMPT = (
    "This is a screenshot of a {w}x{h} pixel screen. "
    "Locate the UI element described as: '{desc}'. "
    "Reply with ONLY the center coordinates as: x,y "
    "If the element is not visible, reply: NOT_FOUND"
)


def downscale(img, max_edge: int):
    """Shrink so the longest edge is `max_edge`. Returns (image, scale_factor).

    Multiply model-reported coordinates by scale_factor to get screen space.
    """
    longest = max(img.size)
    if longest <= max_edge:
        return img, 1.0
    scale = longest / max_edge
    resized = img.copy()
    resized.thumbnail((max_edge, max_edge))
    return resized, scale


def _default_client():
    from google import genai
    from actions.computer_control import _get_api_key
    key = _get_api_key()
    if not key:
        raise RuntimeError("no api key for vision grounding")
    return genai.Client(api_key=key)


def _default_grab():
    from actions.computer_control import _grab_screen
    return _grab_screen()


class VisionGrounder:
    """Ask a vision model where something is. Slow, remote, last resort."""

    name = "vision"
    cost = "slow"      # network round-trip, seconds

    def __init__(self,
                 client_fn: Callable[[], object] | None = None,
                 grab_fn: Callable[[], object] | None = None,
                 max_edge: int = 1280,
                 model: str = "gemini-2.5-flash") -> None:
        self._client_fn = client_fn or _default_client
        self._grab_fn = grab_fn or _default_grab
        self._max_edge = max_edge
        self._model = model
        #: Why the last look produced nothing, or "" if it produced nothing
        #: because the thing genuinely was not there. `find` returns None for
        #: both, and callers reported that as "no such control" — so a quota
        #: error read to the model as a determinate absence and the eagle gave
        #: up on a task that would have worked a minute later.
        self.last_error: str = ""

    def available(self) -> bool:
        try:
            self._client_fn()
            return True
        except Exception:
            return False

    def find(self, description: str) -> Element | None:
        self.last_error = ""
        try:
            from google.genai import types as gtypes

            full = self._grab_fn()
            small, scale = downscale(full, self._max_edge)
            buf = io.BytesIO()
            small.convert("RGB").save(buf, format="JPEG", quality=80)

            client = self._client_fn()
            response = client.models.generate_content(
                model=self._model,
                contents=[
                    gtypes.Part.from_bytes(data=buf.getvalue(),
                                           mime_type="image/jpeg"),
                    _PROMPT.format(w=small.size[0], h=small.size[1],
                                   desc=description),
                ],
            )
            text = (getattr(response, "text", "") or "").strip()
            if "NOT_FOUND" in text.upper():
                return None                      # a real, clean "not there"
            match = re.search(r"(\d+)\s*,\s*(\d+)", text)
            if not match:
                # A reply with no coordinates is a look that did not work, not
                # a screen without the control on it.
                self.last_error = f"vision returned no coordinates: {text[:120]!r}"
                return None

            x = int(int(match.group(1)) * scale)
            y = int(int(match.group(2)) * scale)
            # A point, not a region — a 1px box centres on exactly that point.
            return Element.from_bounds(description, "unknown", x, y, 1, 1,
                                       "vision")
        except Exception as e:
            # NOT the same as "not on screen". Measured live: two of three
            # lookups came back in ~700ms — far too fast for a vision call,
            # which took 5808ms when it worked — because they raised on a rate
            # limit and the exception was discarded here.
            self.last_error = f"{type(e).__name__}: {e}"
            print(f"[Vision] ⚠ look failed ({self.last_error[:110]}) — "
                  f"this is NOT evidence the control is absent")
            return None
