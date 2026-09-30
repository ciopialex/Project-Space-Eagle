"""`window.pill` is a <div> before it is the pill, and the guard did not know.

Every launch printed this, before the session even connected:

    js: Uncaught TypeError: window.pill.set is not a function

`web/pill.html` line 248 is `<div class="pill" id="pill">`. The HTML spec gives
every element with an `id` a matching property on `window`, so from the moment
the parser reaches that line, `window.pill` IS the div — and stays the div until
the page's own script reaches line 1013 and reassigns it.

The host guarded like this:

    window.pill && window.pill.set(state, data)

A div is truthy. The guard passes, `.set` is undefined on a div, and the call
throws. Measured against a page holding only that markup:

    window.pill === document.getElementById('pill')   ->  true
    !!(window.pill)                                   ->  true
    typeof window.pill.set                            ->  "undefined"

Both host->page calls had it: `set` from `_apply`, and `registerModule`.

Skipping the call is the right answer rather than waiting, because
`loadFinished` re-applies `self._pending` once the script has run
(aethelark_web.py:598). The state was never lost, only announced as a crash.

This is a seam — the host process and the page meet here, and the page can be
in a state the host does not model. The JS is not retyped below; it is captured
from the real methods by handing them a stub `self`, so a change to either call
site is what gets tested.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import evaluate  # noqa: E402

#: The page as it exists between the parser reaching the div and the script
#: running. Nothing else about pill.html matters to this test.
MARKUP_ONLY = '<!doctype html><html><body><div class="pill" id="pill"></div></body></html>'


class _StubWindow:
    """Enough of PillWebWindow for the real methods to run and be captured."""

    def __init__(self):
        self.sent: list[str] = []
        self._pending = ("idle", {})

    def run_js(self, js: str) -> None:
        self.sent.append(js)


def _emitted() -> dict[str, str]:
    """What the real host actually sends into the page."""
    import aethelark_web

    out = {}
    stub = _StubWindow()
    aethelark_web.PillWebWindow._apply(stub)
    out["set"] = stub.sent[-1]

    stub2 = _StubWindow()
    aethelark_web.PillWebWindow.register_module(stub2, "atrade", "<b>{ticker}</b>", "b{color:red}")
    out["registerModule"] = stub2.sent[-1]
    return out


@pytest.fixture(scope="module")
def markup_page(web_view):
    from PyQt6.QtWidgets import QApplication
    import time as _t

    done = {}
    web_view.loadFinished.connect(lambda ok: done.setdefault("ok", ok))
    web_view.setHtml(MARKUP_ONLY)
    app = QApplication.instance()
    end = _t.monotonic() + 20
    while "ok" not in done and _t.monotonic() < end:
        app.processEvents()
        _t.sleep(0.004)
    assert done.get("ok"), "the markup-only page did not load"
    return web_view


def test_the_premise_window_pill_really_is_the_div(markup_page):
    """If this ever stops being true, the bug below cannot happen and this
    whole file is measuring nothing."""
    assert evaluate(markup_page, "window.pill === document.getElementById('pill')") is True
    assert evaluate(markup_page, "!!window.pill") is True
    assert evaluate(markup_page, "typeof window.pill.set") == "undefined"


@pytest.mark.parametrize("which", ["set", "registerModule"])
def test_the_host_call_does_not_throw_against_the_bare_div(markup_page, which):
    js = _emitted()[which]
    probe = "(function(){ try { %s; return 'ok'; } catch (e) { return 'THREW ' + e.message; } })()" % js
    assert evaluate(markup_page, probe) == "ok", (
        f"the host's {which} call throws when window.pill is still the div — "
        f"this is the 'window.pill.set is not a function' on every launch")


@pytest.mark.parametrize("which", ["set", "registerModule"])
def test_the_guard_tests_the_function_not_the_object(markup_page, which):
    """A truthiness test on `window.pill` cannot tell a div from the pill.

    Asserted by behaviour rather than by reading the string: with the div in
    place the call must be skipped, so nothing is invoked and nothing throws.
    """
    js = _emitted()[which]
    assert "typeof" in js, (
        f"{which} no longer type-checks the method it is about to call; a div "
        f"named `pill` will satisfy any object-level guard")


def test_a_real_pill_object_is_still_called(markup_page, which="set"):
    """The guard must not be so cautious that it never calls anything."""
    evaluate(markup_page, "window.__hits = []; "
                          "window.pill = {set:function(s,d){window.__hits.push(['set',s]);},"
                          "registerModule:function(k){window.__hits.push(['reg',k]);}}; 1")
    for js in _emitted().values():
        evaluate(markup_page, "(function(){ %s; return 1; })()" % js)
    hits = evaluate(markup_page, "JSON.stringify(window.__hits)")
    assert '"set"' in hits and '"reg"' in hits, (
        f"the guard skipped a real pill object: {hits}")
