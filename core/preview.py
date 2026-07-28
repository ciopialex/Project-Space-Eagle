"""Show the user the thing they asked for.

A mission built a complete site and a working API, merged
nothing, and handed back a folder path. The user had to be told a localhost URL
by someone else before they could see their own website. That is the whole
product falling at the last inch: they asked for a page, and got a directory.

So the last step of a mission is not "merged" — it is "here it is, on screen".

    detect how to run it  ->  start it on a free port  ->  open it in the browser

Deliberately narrow. It recognises the two shapes that cover almost everything a
swarm builds — an npm start script, or a folder with an index.html — and does
nothing at all for anything else rather than guessing and launching something
unexpected. A wrong guess here opens a browser at a broken page, which is worse
than staying quiet.
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

# Where a static site usually lives, most specific first.
_STATIC_DIRS = ("public", "dist", "build", "site", "www", ".")

_servers: dict[str, "Preview"] = {}
_lock = threading.Lock()


@dataclass
class Preview:
    url: str
    kind: str                    # "node" | "static"
    root: Path
    proc: subprocess.Popen | None = None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


def _free_port() -> int:
    """Let the OS pick, so a preview never collides with the dashboard (8000),
    a dev server the user already has running, or a previous mission."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_serving(port: int, timeout_s: float = 12.0) -> bool:
    """Only claim it is ready once something actually answers on the port.

    Opening the browser optimistically shows a connection-refused page, which
    reads as "it failed" even when the server is merely two seconds slow.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def detect(project: Path) -> tuple[str, Path] | None:
    """How should this project be run? Returns (kind, dir) or None."""
    project = Path(project)
    pkg = project / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            if "start" in scripts:
                return "node", project
        except (OSError, ValueError):
            pass
    for d in _STATIC_DIRS:
        cand = project / d
        if (cand / "index.html").exists():
            return "static", cand
    return None


def start(project: Path, open_browser: bool = True) -> Preview | None:
    """Serve the project and (optionally) open it. None if nothing runnable.

    One preview per project: a second mission on the same folder replaces the
    first rather than leaving an orphan server holding a port.
    """
    project = Path(project).resolve()
    found = detect(project)
    if not found:
        return None
    kind, root = found
    port = _free_port()

    with _lock:
        old = _servers.pop(str(project), None)
    if old:
        old.stop()

    if kind == "node":
        cmd = ["npm", "start"]
        env_port = {"PORT": str(port)}
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
            env={**_env(), **env_port})
    else:
        proc = subprocess.Popen(
            ["python3", "-m", "http.server", str(port)], cwd=str(root),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    url = f"http://localhost:{port}"
    pv = Preview(url=url, kind=kind, root=root, proc=proc)
    with _lock:
        _servers[str(project)] = pv

    if not _wait_until_serving(port):
        pv.stop()
        return None
    if open_browser:
        open_in_browser(url)
    return pv


def open_in_browser(url: str) -> bool:
    """Open the finished thing where the user can actually see it.

    Prefers Chrome by name — the eagle's other browser tooling drives Chrome,
    so keeping previews in the same browser means one window, one session,
    rather than a second default browser appearing from nowhere.
    """
    for launcher in ("google-chrome", "google-chrome-stable", "chromium"):
        try:
            subprocess.Popen([launcher, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except (OSError, FileNotFoundError):
            continue
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def current(project: Path) -> Preview | None:
    with _lock:
        return _servers.get(str(Path(project).resolve()))


def stop_all() -> int:
    with _lock:
        items = list(_servers.values())
        _servers.clear()
    for p in items:
        p.stop()
    return len(items)


def _env() -> dict:
    import os
    return dict(os.environ)
