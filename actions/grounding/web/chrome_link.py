"""Work in the user's own Chrome, where they are already signed in.

Copying a Chrome session into another profile does not survive: Google binds a
session to the profile that made it. Acting inside that Chrome does. Since
Chrome 144 a user can allow it once at chrome://inspect/#remote-debugging;
after that Chrome asks "Allow remote debugging?" on every new connection, so
one connection is kept open while there is work and dropped when idle.

Measured on Chrome 154 (Linux):
* the switch persists in `Local State` as devtools.remote_debugging.user-enabled;
* the endpoint is in `DevToolsActivePort`; `/json/version` is a 404, so the
  websocket path from that file is the only way in;
* a plain TCP connect to the port raises no dialog, so `ready()` is silent;
* Chrome will not open chrome:// pages from the command line.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from actions.grounding.web.browser import (_CALL_TIMEOUT, _TEARDOWN_TIMEOUT,
                                           EagleBrowser, PagePort)

ALLOW_WAIT_MS = 45_000
IDLE_S = 600.0


@dataclass(frozen=True)
class Installed:
    name: str
    user_data: Path
    setup_url: str


def _homes() -> list[tuple[str, Path, str]]:
    chrome = "chrome://inspect/#remote-debugging"
    edge = "edge://inspect/#remote-debugging"
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        return [("Chrome", base / "Google" / "Chrome", chrome),
                ("Chrome Beta", base / "Google" / "Chrome Beta", chrome),
                ("Chrome Canary", base / "Google" / "Chrome Canary", chrome),
                ("Brave", base / "BraveSoftware" / "Brave-Browser", chrome),
                ("Edge", base / "Microsoft Edge", edge),
                ("Chromium", base / "Chromium", chrome)]
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return [("Chrome", base / "Google" / "Chrome" / "User Data", chrome),
                ("Chrome Beta", base / "Google" / "Chrome Beta" / "User Data", chrome),
                ("Chrome Canary", base / "Google" / "Chrome SxS" / "User Data", chrome),
                ("Brave", base / "BraveSoftware" / "Brave-Browser" / "User Data", chrome),
                ("Edge", base / "Microsoft" / "Edge" / "User Data", edge),
                ("Chromium", base / "Chromium" / "User Data", chrome)]
    base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [("Chrome", base / "google-chrome", chrome),
            ("Chrome Beta", base / "google-chrome-beta", chrome),
            ("Chrome Dev", base / "google-chrome-unstable", chrome),
            ("Brave", base / "BraveSoftware" / "Brave-Browser", chrome),
            ("Edge", base / "microsoft-edge", edge),
            ("Chromium", base / "chromium", chrome),
            ("Chromium", home / "snap" / "chromium" / "common" / "chromium", chrome)]


def installed() -> list[Installed]:
    return [Installed(n, p, u) for n, p, u in _homes() if (p / "Local State").is_file()]


def _local_state(b: Installed) -> dict:
    try:
        return json.loads((b.user_data / "Local State").read_text(encoding="utf-8"))
    except Exception:
        return {}


def switched_on(b: Installed) -> bool:
    rd = ((_local_state(b).get("devtools") or {}).get("remote_debugging") or {})
    return bool(rd.get("user-enabled"))


def endpoint(b: Installed) -> str | None:
    """The websocket to connect to, if that browser is running and listening."""
    try:
        port, path = (b.user_data / "DevToolsActivePort").read_text().split()[:2]
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            pass
    except Exception:
        return None
    return f"ws://127.0.0.1:{int(port)}{path}"


def find() -> tuple[Installed, str] | None:
    for b in installed():
        ws = endpoint(b)
        if ws:
            return b, ws
    return None


_LINUX_BIN = {"Chrome": ("google-chrome", "google-chrome-stable"),
              "Chrome Beta": ("google-chrome-beta",),
              "Chrome Dev": ("google-chrome-unstable",),
              "Brave": ("brave-browser", "brave"),
              "Edge": ("microsoft-edge", "microsoft-edge-stable"),
              "Chromium": ("chromium", "chromium-browser")}
_MAC_APP = {"Chrome": "Google Chrome", "Chrome Beta": "Google Chrome Beta",
            "Chrome Canary": "Google Chrome Canary", "Brave": "Brave Browser",
            "Edge": "Microsoft Edge", "Chromium": "Chromium"}
_WIN_EXE = {"Chrome": "chrome", "Chrome Beta": "chrome", "Chrome Canary": "chrome",
            "Brave": "brave", "Edge": "msedge", "Chromium": "chromium"}


def bring_forward(b: Installed) -> bool:
    """Open a new window of that browser, focused on its address bar."""
    import shutil
    import subprocess
    if sys.platform == "darwin":
        cmd = ["open", "-a", _MAC_APP.get(b.name, "Google Chrome")]
    elif sys.platform == "win32":
        cmd = ["cmd", "/c", "start", "", _WIN_EXE.get(b.name, "chrome"), "--new-window"]
    else:
        exe = next((shutil.which(x) for x in _LINUX_BIN.get(b.name, ()) if shutil.which(x)), None)
        if not exe:
            return False
        cmd = [exe, "--new-window"]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


def account_name() -> str:
    """The first name on the signed-in Chrome profile, to greet the user by."""
    for b in installed():
        cache = ((_local_state(b).get("profile") or {}).get("info_cache") or {})
        for prof in cache.values():
            name = str(prof.get("gaia_given_name") or prof.get("gaia_name") or "").strip()
            if name:
                return name.split()[0]
    return ""


def wanted() -> bool:
    from core import prefs
    return prefs.enabled("chrome_signins")


def usable() -> bool:
    return wanted() and find() is not None


def status() -> dict:
    """What Settings shows. `state`: none, off, closed, ready or connected."""
    have = installed()
    if not have:
        return {"state": "none", "browser": "", "setup_url": ""}
    live = find()
    b = live[0] if live else next((x for x in have if switched_on(x)), have[0])
    if live and wanted() and _DEFAULT is not None and _DEFAULT.running:
        state = "connected"
    elif live:
        state = "ready" if wanted() else "off"
    elif switched_on(b):
        state = "closed" if wanted() else "off"
    else:
        state = "off"
    return {"state": state, "browser": b.name, "setup_url": b.setup_url}


def begin_setup() -> dict:
    """The user said yes: remember it, and if Chrome still needs its switch
    ticked, bring Chrome forward. Returns `status()`."""
    from core import prefs
    prefs.save({"chrome_signins": True})
    st = status()
    if st["state"] in ("off", "closed"):
        b = next((x for x in installed() if x.name == st["browser"]), None)
        if b is not None:
            bring_forward(b)
    return st


def wait_until_on(timeout: float = 300.0, poll: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if find() or any(switched_on(b) for b in installed()):
            return True
        time.sleep(poll)
    return False


#: Set by the host to put one line on the island, e.g. ui.show_notice.
notify: Callable[[str], None] | None = None
#: Set by the host to run the setup that needs the clipboard, from any thread.
request_setup: Callable[[], None] | None = None


def _say(text: str) -> None:
    try:
        if notify:
            notify(text)
    except Exception:
        pass


class ChromeBrowser(EagleBrowser):
    """The eagle's own window inside the user's Chrome.

    It never closes the user's tabs or context: letting go closes the one
    window it opened and disconnects.
    """

    def __init__(self, find_fn: Callable[[], tuple[Installed, str] | None] | None = None,
                 playwright_fn: Callable[[], Any] | None = None,
                 idle_s: float = IDLE_S) -> None:
        super().__init__(headless=False, launcher=self._attach,
                         playwright_fn=playwright_fn)
        self._find = find_fn or (lambda: find())
        self._remote: Any = None
        self._idle_s = idle_s
        self._used = time.monotonic()
        self.browser_name = ""

    def _attach(self, playwright, _profile: Path, _headless: bool):
        found = self._find()
        if not found:
            raise RuntimeError("Chrome is not open with remote debugging allowed")
        b, ws = found
        self.browser_name = b.name
        _say(f"Click Allow in {b.name} so the eagle can use your sign-ins")
        try:
            remote = playwright.chromium.connect_over_cdp(ws, timeout=ALLOW_WAIT_MS)
        except Exception:
            _say(f"{b.name} did not allow it, so the eagle used its own browser")
            raise
        self._remote = remote
        page = self._open_window(remote)
        _say(f"Working in your {b.name}")
        threading.Thread(target=self._idle_watch, daemon=True,
                         name="ChromeIdle").start()
        return page

    @staticmethod
    def _open_window(remote):
        ctx = remote.contexts[0]
        cdp = remote.new_browser_cdp_session()
        with ctx.expect_page() as ev:
            cdp.send("Target.createTarget", {"url": "about:blank",
                                             "newWindow": True,
                                             "background": True})
        return ev.value

    def _page_closed(self) -> bool:
        try:
            return self._page is None or bool(self._page.is_closed())
        except Exception:
            return True

    def start(self) -> None:
        with self._lifecycle_lock:
            if (self._thread is not None and self._thread.is_alive()
                    and self._page is not None and self._page_closed()):
                remote = self._remote
                try:
                    if remote is not None and remote.is_connected():
                        self._page = self._submit(lambda: self._open_window(remote), 20.0)
                        return
                except Exception:
                    pass
                self.close()
            super().start()

    def call(self, fn, timeout: float = _CALL_TIMEOUT):
        self._used = time.monotonic()
        if self.running and self._page_closed():
            self.start()
        return super().call(fn, timeout)

    def page(self):
        if self.running and self._page_closed():
            self.start()
        if not self.running:
            return None
        return PagePort(self._page, call=self._touched)

    def _touched(self, fn):
        self._used = time.monotonic()
        return self._submit(fn, _CALL_TIMEOUT)

    def surface(self, visible: bool) -> bool:
        if not self.running:
            return False
        if visible:
            try:
                self.call(lambda p: p.bring_to_front(), timeout=10.0)
            except Exception:
                return False
        return True

    def _release(self, page: Any) -> None:
        remote, self._remote = self._remote, None
        for step in (lambda: page.close() if page is not None and not page.is_closed() else None,
                     lambda: remote.close() if remote is not None else None):
            try:
                self._submit(step, _TEARDOWN_TIMEOUT, cancellable=False)
            except Exception:
                pass

    def _idle_watch(self) -> None:
        while self._remote is not None:
            time.sleep(min(30.0, self._idle_s / 4))
            if self._remote is not None and time.monotonic() - self._used > self._idle_s:
                self.close()


_DEFAULT: ChromeBrowser | None = None
_LOCK = threading.Lock()


def chrome_browser() -> ChromeBrowser:
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is None:
            _DEFAULT = ChromeBrowser()
        return _DEFAULT
