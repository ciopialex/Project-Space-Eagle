"""Every write to a page happens on the GUI thread, whoever asked for it.

`_launch_main_app` puts the asyncio loop — and therefore the tool dispatcher —
on a daemon thread, while Qt keeps the main one. Most of the pill's writes
travel on pyqtSignals and Qt marshals those for free, so this was true by
accident for a long time.

Three tools broke it. `island_deck_move`, `island_deck_show` and
`island_set_printer` are called straight out of `_execute_tool` with no signal
in between, so they reached QWebEnginePage.runJavaScript from the dispatcher
thread. QtWebEngine is GUI-thread-only and does not raise about it.

Measured 2026-09-08 from a real session log. The last line ever written was

    [Tool] ▶ island_deck_move (epoch=7) {direction=next}

with no matching ✓ or ✗ and nothing after it. Saying "next" over a deck of
five models took the whole eagle down inside the call, and because the process
died there, the log could not say so either.

This is a seam test in the sense CLAUDE.md means: two components meet (the
dispatcher thread and the GUI thread), the suite is the only thing that can
watch both at once, and the failure costs the entire session.

No WebEngine here on purpose. The property under test is which thread runs the
write, not what the page does with it — and conftest warns that a second
QWebEngineView built after another has loaded segfaults under pytest.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _RecordingPage:
    """Stands in for QWebEnginePage, and remembers who called it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, threading.Thread]] = []

    def runJavaScript(self, js, *_a, **_k):        # noqa: N802 - Qt's name
        self.calls.append((js, threading.current_thread()))


class _RecordingView:
    def __init__(self) -> None:
        self._page = _RecordingPage()

    def page(self) -> _RecordingPage:
        return self._page


def _bare_window(cls):
    """A real instance of `cls` with its page swapped out.

    `__init__` builds a QWebEngineView and loads a document; neither is what is
    being measured, and both are what makes a second one unsafe. QMainWindow's
    own __init__ is what puts the object on the GUI thread, which IS what is
    being measured, so that part is kept.
    """
    from PyQt6.QtWidgets import QMainWindow

    win = cls.__new__(cls)
    QMainWindow.__init__(win)
    win.view = _RecordingView()
    return win


def _pump(seconds: float = 1.0) -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)


def _call_off_thread(fn, *args) -> None:
    done = threading.Event()

    def worker():
        try:
            fn(*args)
        finally:
            done.set()

    t = threading.Thread(target=worker, name="pretend-dispatcher", daemon=True)
    t.start()
    assert done.wait(5.0), "the off-thread call never returned"
    t.join(5.0)


@pytest.fixture()
def pill(qapp):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    import aethelark_web

    win = _bare_window(aethelark_web.PillWebWindow)
    win._run_js_sig.connect(win._run_js_on_gui_thread)
    return win


def test_a_write_from_the_dispatcher_thread_runs_on_the_gui_thread(pill):
    """The regression itself: `next` over a deck came in on the wrong thread."""
    page = pill.view.page()
    _call_off_thread(pill.run_js, "window.island && window.island.send('deck_move')")
    _pump()

    assert len(page.calls) == 1, (
        f"expected exactly one page write, saw {len(page.calls)}")
    js, ran_on = page.calls[0]
    assert "deck_move" in js
    assert ran_on is threading.main_thread(), (
        f"the page was written from {ran_on.name!r}. QWebEnginePage is "
        f"GUI-thread-only and dies silently when it is not.")


def test_the_write_is_queued_not_executed_by_the_caller(pill):
    """Queued, not merely 'ends up correct'.

    A direct call from a worker thread would also leave the right JS in the
    list. What separates the two is WHEN: a queued connection cannot have run
    before the GUI thread next processes events, so an empty list immediately
    after the worker returns is the evidence that nothing ran on it.
    """
    page = pill.view.page()
    _call_off_thread(pill.run_js, "noop()")

    assert page.calls == [], (
        "the page was written before the GUI thread ran an event loop, which "
        "means the worker thread called into it directly")

    _pump()
    assert len(page.calls) == 1
    assert page.calls[0][1] is threading.main_thread()


def test_a_write_from_the_gui_thread_is_still_synchronous(pill):
    """The marshal must not turn same-thread callers into deferred ones.

    `_apply` runs on the GUI thread and relies on the write having happened by
    the time it returns; Qt gives a same-thread signal a direct connection, and
    this is the assertion that keeps that true if the connection type is ever
    made explicit.
    """
    page = pill.view.page()
    pill.run_js("immediate()")

    assert len(page.calls) == 1, "a same-thread write should not be deferred"
    assert page.calls[0][1] is threading.main_thread()


def test_the_dashboard_has_the_same_property(qapp):
    """`_push` is reachable from the dispatcher the same way and was the same
    doorway onto a different window."""
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    import aethelark_web

    win = _bare_window(aethelark_web.DashWindow)
    win._push_sig.connect(win._push_on_gui_thread)
    page = win.view.page()

    _call_off_thread(win.push, "setState", "LISTENING")
    assert page.calls == [], "the dashboard page was written by the caller"
    _pump()

    assert len(page.calls) == 1
    js, ran_on = page.calls[0]
    assert "setState" in js
    assert ran_on is threading.main_thread()


def test_nothing_reaches_a_page_except_through_the_marshal():
    """A property over the module, not a mirror of it.

    The bug was not that one call was wrong; it was that there were five
    doorways onto a page and only some were guarded. This counts the doorways.
    Both survivors must be the marshalled slots themselves — any third means a
    new bypass was added, which is exactly how this shipped the first time.
    """
    src = (Path(__file__).resolve().parent.parent / "aethelark_web.py").read_text(
        encoding="utf-8")
    lines = [i + 1 for i, ln in enumerate(src.splitlines())
             if "page().runJavaScript" in ln]

    def enclosing_def(lineno: int) -> str:
        for ln in reversed(src.splitlines()[:lineno]):
            stripped = ln.strip()
            if stripped.startswith("def "):
                return stripped.split("(")[0][4:]
        return "?"

    owners = sorted({enclosing_def(n) for n in lines})
    assert owners == ["_do_push", "_push_on_gui_thread", "_run_js_on_gui_thread"], (
        f"a page is written outside the marshalled slots, by: {owners}. "
        f"Route it through run_js (pill) or push (dashboard) instead.")
