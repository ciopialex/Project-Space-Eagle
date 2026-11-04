"""The whole screen as a PIL image, on every desktop.

X11, macOS and Windows: mss. On Wayland (the default session of Ubuntu 24.04 and
26.04) an app reaches the display through Xwayland, which holds only other X11
windows, so mss returned a black frame and the model was shown nothing. There the
picture comes from the desktop's screenshot portal: GNOME asks the user once
("Allow Apps to Take Screenshots?") and remembers the answer; after that a
capture takes a fraction of a second.
"""
from __future__ import annotations

import io
import os
import sys
import time
from urllib.parse import unquote, urlparse

REFUSED = ("The screen can't be seen: screenshots are switched off for apps on this "
           "computer. Tell the user they can turn them on in Settings, Privacy, "
           "Screenshots, then ask again.")
NOT_ANSWERED = ("The screen can't be seen yet: the computer asked the user whether apps may "
                "take screenshots, and it was not answered. Ask them to click Allow when "
                "it appears, then ask again.")
NO_PORTAL = ("The screen can't be seen on this desktop: it offers apps no way to take "
             "a screenshot. Tell the user, and ask them to describe what they need.")


class ScreenshotUnavailable(RuntimeError):
    """The screen cannot be captured; str(e) says what to tell the user."""


def on_wayland(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return sys.platform.startswith("linux") and (
        env.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(env.get("WAYLAND_DISPLAY")))


def grab():
    """A PIL RGB image of the main screen. Raises ScreenshotUnavailable."""
    if on_wayland():
        return _from_portal()
    return _from_mss()


def png_bytes() -> bytes:
    buf = io.BytesIO()
    grab().save(buf, format="PNG")
    return buf.getvalue()


def _from_mss():
    import mss
    from PIL import Image
    with mss.mss() as sct:
        mons = sct.monitors              # [0] is all screens together, [1..] each screen
        shot = sct.grab(mons[1] if len(mons) > 1 else mons[0])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _from_portal(timeout: float = 90.0):
    """org.freedesktop.portal.Screenshot. The first call waits while the user
    answers GNOME's question, hence the long timeout."""
    from PIL import Image
    try:
        code, results = _portal_call(timeout)
    except ScreenshotUnavailable:
        raise
    except TimeoutError as e:
        raise ScreenshotUnavailable(NOT_ANSWERED) from e
    except Exception as e:
        raise ScreenshotUnavailable(NO_PORTAL) from e
    if code != 0:                        # 1: the user said no; 2: refused or failed
        raise ScreenshotUnavailable(REFUSED)
    path = unquote(urlparse(str(results.get("uri", ("s", ""))[1])).path)
    if not path:
        raise ScreenshotUnavailable(NO_PORTAL)
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    finally:
        try:                             # the portal saves into the user's Pictures
            os.remove(path)
        except OSError:
            pass


def _portal_call(timeout: float) -> tuple[int, dict]:
    from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
    from jeepney.io.blocking import Proxy, open_dbus_connection

    with open_dbus_connection(bus="SESSION") as conn:
        token = f"aethelark{os.getpid()}_{time.monotonic_ns()}"
        sender = conn.unique_name[1:].replace(".", "_")
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request",
                         member="Response",
                         path=f"/org/freedesktop/portal/desktop/request/{sender}/{token}")
        Proxy(message_bus, conn).AddMatch(rule)
        portal = DBusAddress("/org/freedesktop/portal/desktop",
                             bus_name="org.freedesktop.portal.Desktop",
                             interface="org.freedesktop.portal.Screenshot")
        with conn.filter(rule) as responses:
            reply = conn.send_and_get_reply(new_method_call(
                portal, "Screenshot", "sa{sv}",
                ("", {"handle_token": ("s", token), "interactive": ("b", False)})))
            if reply.header.message_type.name == "error":
                raise ScreenshotUnavailable(NO_PORTAL)
            signal = conn.recv_until_filtered(responses, timeout=timeout)
        return signal.body[0], signal.body[1]
