"""A display of our own, so a browser can be headed without being seen.

The user reported browsers flickering onto his screen — popping up, doing
nothing, closing. They were the eagle's, and they were there because of a real
trade-off:

    headless  ->  invisible, and Cloudflare refuses it. Measured on
                  makerworld.com: an interstitial every single time.
    headed    ->  the page loads (259 controls), and it is on your desktop.

The first attempt at having both was `--window-position=-32000,-32000`.
Measured: 25 sightings of a Chrome window at (50,22) over 2.5 seconds — GNOME
ignores the hint. Moving it after it mapped, with xdotool, did not work
either: 44 sightings across 2583ms, because the compositor put it back.

Xvfb solves it properly rather than fighting the window manager. The browser
is genuinely headed — it renders, it has a window, it passes the checks a real
browser passes — the window is simply on a display nobody is looking at.
Verified: makerworld loaded with 160 controls and TWO inputs, and zero Chrome
windows appeared on the user's display for the whole run.

Degrades honestly. No Xvfb means headless, which works everywhere except the
sites that fingerprint it — the doctor says so rather than leaving it to be
discovered through a page that will not load.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import threading
import time

#: High enough not to collide with a real session (:0, :1) or a debug one.
_DISPLAY = ":77"
_SIZE = "1440x900x24"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None


def available() -> bool:
    """Never raises. A probe that can explode is worse than one that says no.

    `shutil.which` fails outright when `sys.platform` is faked to "win32" on a
    Linux box, which a cross-platform test legitimately does — and a browser
    must not fail to launch because looking for Xvfb went wrong.
    """
    try:
        if not sys.platform.startswith("linux"):
            return False          # X11 only; macOS and Windows have no Xvfb
        return bool(shutil.which("Xvfb"))
    except Exception:
        return False


def display() -> str | None:
    """The private display, starting it if needed. None when unavailable.

    None is a real answer: the caller falls back to headless and the doctor
    reports the consequence, rather than the user discovering it through a
    site that mysteriously will not load.
    """
    global _proc
    if not available():
        return None
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return _DISPLAY
        try:
            _proc = subprocess.Popen(
                ["Xvfb", _DISPLAY, "-screen", "0", _SIZE, "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[VirtualDisplay] could not start Xvfb: {e}")
            _proc = None
            return None
        # It needs a moment before a client can connect; a browser that
        # launches into a display that is not up yet fails in a way that reads
        # like a browser problem.
        for _ in range(30):
            if _proc.poll() is not None:
                _proc = None
                return None
            if os.path.exists(f"/tmp/.X11-unix/X{_DISPLAY.lstrip(':')}"):
                break
            time.sleep(0.1)
        return _DISPLAY


def env_for(base: dict | None = None) -> dict:
    """`base` (default the current environment) pointed at the private display.

    Returns it unchanged when there is no private display, so callers do not
    have to branch.
    """
    env = dict(base if base is not None else os.environ)
    d = display()
    if d:
        env["DISPLAY"] = d
    return env


def stop() -> None:
    """Shut the display down. Registered at exit, and safe to call twice.

    An Xvfb left running is exactly the ghost process the user has been
    chasing all week — invisible, harmless-looking, and still there tomorrow.
    """
    global _proc
    with _lock:
        p, _proc = _proc, None
    if p is None:
        return
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


atexit.register(stop)
