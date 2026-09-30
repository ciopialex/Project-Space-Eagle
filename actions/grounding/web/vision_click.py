"""VisionGrounder, pointed at a page instead of the desktop.

The Xvfb-hidden browser's viewport is a fixed 1440x900 with no
device_scale_factor override (actions/grounding/web/browser.py:175), so
page.screenshot() is pixel-identical to page coordinates - simpler than
the desktop case (screen_click), which has to account for real
multi-monitor/HiDPI scaling. VisionGrounder's own grab_fn was already
built injectable; this just supplies a different one.

`VisionGrounder.find()` (`actions/grounding/vision.py`) calls `downscale()`
on whatever `grab_fn()` returns, and `downscale()` calls `.size`, `.copy()`
and `.thumbnail()` on it - PIL `Image` methods. The desktop's own default
`grab_fn` (`_grab_screen` in `actions/computer_control.py`) returns a PIL
`Image`, confirming that is the contract `grab_fn` implementations must
honour. `page.screenshot()` (`PagePort.screenshot`,
`actions/grounding/web/browser.py`) returns raw PNG bytes from Playwright,
not a PIL Image - so `grab_fn` here must decode them first, or `find()`
crashes on the very first real call with
`AttributeError: 'bytes' object has no attribute 'size'`.
"""
from __future__ import annotations

import io

from PIL import Image

from actions.grounding.vision import VisionGrounder


def page_vision_grounder(page) -> VisionGrounder:
    """A VisionGrounder that screenshots `page` (the browser's own
    compositor output — works even hidden/off-screen), not the desktop.

    `page.screenshot()` returns raw PNG bytes (Playwright's own return
    type); decoded into a PIL Image here so it matches what
    `VisionGrounder.find()`'s `downscale()` step requires.
    """
    def grab() -> Image.Image:
        return Image.open(io.BytesIO(page.screenshot()))

    return VisionGrounder(grab_fn=grab)
