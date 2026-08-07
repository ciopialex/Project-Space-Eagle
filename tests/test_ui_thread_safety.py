"""Worker threads must never touch the web view.

The user hit this as a hard crash: pressing "Sign in…" in Settings took the
whole app down. `browser_sign_in` did its work on a background thread — it has
to, because making the browser visible restarts it — and then called
`self._push(...)`, which drives the QWebEngine view directly. Qt does not
survive that.

The codebase already knew: `_push_settings_async` exists for exactly this and
carries the comment "From a worker thread we can't touch the web view
directly." The knowledge was there and nothing enforced it, so the next person
to add a threaded handler reintroduced the crash. This is the enforcement.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SOURCE = Path(__file__).resolve().parent.parent / "aethelark_web.py"

#: Marshals to the GUI thread via a Qt signal. Safe from anywhere.
SAFE = {"write_log", "_push_settings_async", "show_content", "prompt_reconfig",
        "set_audio_level", "emit"}

#: Drives the web view in-process. GUI thread only.
UNSAFE = {"_push", "_push_all", "_push_memory", "_push_metrics", "_push_swarm"}


def _threaded_functions(tree):
    """Every function that starts a thread, with its nested bodies."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        src = ast.dump(node)
        if "threading" in src and "Thread" in src:
            out.append(node)
    return out


def test_no_threaded_handler_drives_the_web_view_directly():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for fn in _threaded_functions(tree):
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            attr = getattr(call.func, "attr", None)
            if attr in UNSAFE:
                offenders.append(f"{fn.name}() calls self.{attr}()")
    assert offenders == [], (
        "these run on a worker thread and touch the web view — Qt crashes:\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\nUse _push_settings_async() or write_log(), which emit signals.")


def test_the_guard_would_notice_a_real_offender():
    """A test that scans source can quietly stop matching anything. Prove it
    still fires on the exact shape it is meant to catch."""
    bad = ast.parse(
        "import threading\n"
        "class W:\n"
        "    def handler(self):\n"
        "        def _run():\n"
        "            self._push('setSettings', {})\n"
        "        threading.Thread(target=_run).start()\n")
    found = [c for fn in _threaded_functions(bad) for c in ast.walk(fn)
             if isinstance(c, ast.Call) and getattr(c.func, "attr", None) in UNSAFE]
    assert found, "the scan no longer detects a direct _push from a thread"


def test_browser_sign_in_still_reports_back_somehow():
    """The fix must not be "delete the callback". The user still needs to be
    told the window is open."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    # There are TWO browser_sign_in methods: the QWebChannel bridge one-liner
    # that forwards to the window, and the window's own threaded handler. Only
    # the second does the work, and `next()` returns the first.
    handlers = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "browser_sign_in"]
    assert len(handlers) == 2, f"expected bridge + handler, found {len(handlers)}"
    fn = max(handlers, key=lambda n: len(ast.dump(n)))
    called = {getattr(c.func, "attr", None) for c in ast.walk(fn)
              if isinstance(c, ast.Call)}
    assert called & SAFE, "browser_sign_in no longer reports anything to the UI"
