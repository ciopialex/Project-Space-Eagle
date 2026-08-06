"""Declared, dispatched, and scheduled as concurrent.

The last line is the one worth a test. Every other browser tool in this file
declares writes=["desktop"], because it drives the user's own browser and
therefore their screen. This one does not, and that is the difference between
an eagle that takes your machine over and one that works alongside you.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def _declaration(name):
    for tool in main.TOOL_DECLARATIONS:
        if tool.get("name") == name:
            return tool
    return None


def test_the_tool_is_declared_to_the_model():
    decl = _declaration("web_agency")
    assert decl is not None, "web_agency is not in the tool declarations"
    assert "action" in decl["parameters"]["properties"]
    assert decl["parameters"]["required"] == ["action"]


def test_the_declaration_lists_every_action_the_tool_implements():
    from actions.web_agency import _ACTIONS
    described = _declaration("web_agency")["parameters"]["properties"]["action"]
    for verb in _ACTIONS:
        assert verb in described["description"], f"'{verb}' is undocumented"


def test_the_description_mentions_refusal_of_irreversible_actions():
    """The model needs to know from the declaration alone that this tool
    refuses irreversible actions, not by discovering it at runtime."""
    decl = _declaration("web_agency")
    description = decl["description"].lower()
    # Check for keywords indicating refusal behavior
    assert any(word in description for word in ["refus", "stop", "ask", "irrevers"]), (
        f"Description should mention refusal or irreversible actions:\n{decl['description']}"
    )


def test_it_is_not_exclusive_so_it_can_run_while_the_user_works():
    """Reads the web in its own browser; touches neither the user's screen nor
    their browser. Non-exclusive on purpose — this is the tool that can run
    while the user is doing something else.
    """
    spec = main.TOOL_SPECS["web_agency"]
    assert spec.exclusive is False, "web_agency must be non-exclusive to run alongside user"
    assert "desktop" not in spec.writes, "web_agency must not write to desktop"


def test_it_is_dispatched():
    import inspect
    source = inspect.getsource(main)
    assert 'elif name == "web_agency"' in source


# ── Blocker 4: the declaration and the behaviour must agree ────────────────
#
# `web_agency` is non-exclusive specifically because it "touches neither the
# user's screen nor their browser" (see the comment on its ToolSpec) — that
# claim is only true if the eagle's browser is actually headless. Pinned
# here rather than left to be re-broken by a future default flip nobody
# thinks to check against the declaration it justifies.

def test_the_non_exclusive_declaration_is_backed_by_a_headless_default(
        monkeypatch):
    from actions.grounding.web.browser import EagleBrowser
    monkeypatch.delenv("AETHELARK_BROWSER_HEADLESS", raising=False)
    spec = main.TOOL_SPECS["web_agency"]
    assert spec.exclusive is False
    assert EagleBrowser().headless is True, (
        "web_agency is declared non-exclusive on the strength of never "
        "showing the user a window, but the browser it drives defaults to "
        "visible — the declaration and the behaviour disagree")


# ── the "also fix": nothing closed the browser on shutdown ─────────────────
#
# `EagleBrowser` runs its own daemon thread and holds an un-`stop()`ped
# Playwright driver process for as long as the process lives; a daemon
# thread gets no chance to clean up on interpreter exit. `main()`'s graceful
# shutdown used to end without ever calling `default_browser().close()`.

def test_shutdown_closes_the_eagles_browser():
    calls = []

    class FakeDefault:
        def close(self):
            calls.append("closed")

    fake = FakeDefault()
    import actions.grounding.web.browser as browser_module
    original = browser_module.default_browser
    browser_module.default_browser = lambda: fake
    try:
        main._shutdown_web_browser()
    finally:
        browser_module.default_browser = original

    assert calls == ["closed"]


def test_shutdown_never_raises_even_if_closing_the_browser_fails():
    import actions.grounding.web.browser as browser_module

    def exploding_default_browser():
        raise RuntimeError("browser thread already dead")

    original = browser_module.default_browser
    browser_module.default_browser = exploding_default_browser
    try:
        main._shutdown_web_browser()   # must not raise
    finally:
        browser_module.default_browser = original
