"""Against a real browser, on a real page.

Everything else in this plan is tested against a fake page, which proves the
Python is right and proves nothing at all about the JavaScript. This file is
the only thing that can catch a collector that does not actually collect.

Skipped when Playwright's browsers are not installed, so CI and a fresh
checkout stay green:
    .venv/bin/python -m playwright install chromium

Every test here runs headless against a temporary, throwaway profile
(`tmp_path_factory`) and a local `file://` fixture — never the user's real
browser profile, never the public internet. See the `browser` fixture below.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="playwright not installed")

from actions.grounding.web.browser import EagleBrowser        # noqa: E402
from actions.grounding.web.grounder import WebGrounder        # noqa: E402
from actions.grounding.web.handoff import wall_reason          # noqa: E402
from actions.grounding.web.page import nodes_from_records, ref_of  # noqa: E402
from actions.web_agency import _act_with_reresolve             # noqa: E402

PAGE_URL = (Path(__file__).parent / "fixtures" / "web"
            / "sample_page.html").resolve().as_uri()


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    b = EagleBrowser(headless=True,
                     profile_dir=tmp_path_factory.mktemp("eagle-profile"))
    b.start()
    if not b.running:
        pytest.skip(f"could not launch chromium: {b.last_error}")
    try:
        b.goto(PAGE_URL)
        yield b
    finally:
        b.close()


def test_the_collector_reads_the_real_dom(browser):
    nodes = nodes_from_records(browser.page().collect())
    names = {n.name for n in nodes}
    assert "Sign in" in names
    assert "Home" in names
    assert "Custom widget" in names, "explicit role= was ignored"


def test_labels_become_accessible_names(browser):
    nodes = nodes_from_records(browser.page().collect())
    assert any(n.name == "Email address" and n.role == "textbox"
               for n in nodes), "a <label for=…> did not become the name"


def test_presentational_and_unnamed_elements_are_left_out(browser):
    nodes = nodes_from_records(browser.page().collect())
    names = {n.name for n in nodes}
    assert "should never be collected" not in names
    assert "bare text, no role" not in names


def test_a_disabled_button_reports_as_disabled(browser):
    nodes = nodes_from_records(browser.page().collect())
    disabled = next(n for n in nodes if n.name == "Disabled button")
    assert "ENABLED" not in disabled.states


def test_a_hidden_button_reports_as_not_showing(browser):
    nodes = nodes_from_records(browser.page().collect())
    hidden = next(n for n in nodes if n.name == "Hidden button")
    assert "SHOWING" not in hidden.states


def test_a_password_field_is_recognised_as_an_auth_wall(browser):
    nodes = nodes_from_records(browser.page().collect())
    assert wall_reason(nodes) != ""


def test_the_grounder_finds_a_control_by_plain_words(browser):
    g = WebGrounder(browser.page)
    el = g.find("the Sign in button")
    assert el is not None and el.name == "Sign in"
    assert el.width > 0 and el.height > 0, "no real geometry came back"


def test_hit_testing_returns_the_control_at_its_own_centre(browser):
    g = WebGrounder(browser.page)
    el = g.find("the Sign in button")
    hit = g.hit_test(el.x, el.y)
    assert hit is not None and hit.name == "Sign in"


def test_typing_into_a_real_field_changes_its_value(browser):
    g = WebGrounder(browser.page)
    node = g.find_node("the Email address field")
    assert node is not None
    browser.page().fill(ref_of(node), "eagle@example.test")

    after = g.find("the Email address field")
    assert after is not None and after.value == "eagle@example.test"


def test_clicking_a_real_checkbox_checks_it(browser):
    g = WebGrounder(browser.page)
    node = g.find_node("the Remember me checkbox")
    assert node is not None
    browser.page().click(ref_of(node))

    after = g.find("the Remember me checkbox")
    assert after is not None and after.has("CHECKED")


def test_a_screenshot_comes_back_from_a_headless_browser(browser):
    shot = browser.page().screenshot()
    assert shot[:4] == b"\x89PNG", "not a PNG — the compositor read failed"


# ── the stale-ref bug: navigation invalidates every ref ────────────────────
#
# `COLLECT_JS` strips every `data-ae-ref` attribute at the start of each
# fresh snapshot (see page.py), and a full navigation reloads the DOM from
# scratch regardless — either way, a ref resolved before a navigation does
# not exist after it. Before the fix, `PagePort.click`/`fill` inherited
# Playwright's 30s default timeout, so using a now-stale ref cost 30 seconds
# of dead time before failing. These two tests pin the fix: a stale ref must
# fail in single-digit seconds, and the higher-level retry seam
# (`_act_with_reresolve` in web_agency.py) must recover from that failure by
# re-resolving the description, rather than requiring the caller to retry
# by hand.
#
# Both navigate with `browser.goto(PAGE_URL)` back to the same offline
# fixture — a full navigation, so the DOM (and every ref stamped on it) is
# genuinely replaced, without ever touching the network.

_STALE_REF_BUDGET_S = 15.0  # generous margin over _REF_TIMEOUT_MS (4s);
                            # nowhere near the 30s bug this replaces.


def test_a_stale_ref_fails_in_seconds_not_in_the_old_thirty(browser):
    g = WebGrounder(browser.page)
    node = g.find_node("the Sign in button")
    assert node is not None
    stale_ref = ref_of(node)

    browser.goto(PAGE_URL)  # fresh document: `stale_ref` no longer exists

    page = browser.page()
    start = time.monotonic()
    with pytest.raises(Exception):
        page.click(stale_ref)
    elapsed = time.monotonic() - start

    assert elapsed < _STALE_REF_BUDGET_S, (
        f"a stale ref took {elapsed:.1f}s to fail — the old 30s default "
        "timeout leaked through PagePort.click again")


def test_a_stale_ref_recovers_by_reresolving_the_description(browser):
    g = WebGrounder(browser.page)
    node = g.find_node("the Sign in button")
    assert node is not None

    browser.goto(PAGE_URL)  # fresh document: `node.ref` no longer exists,
                            # but "the Sign in button" still matches
                            # something on the (identical) reloaded page.

    page = browser.page()
    start = time.monotonic()
    _act_with_reresolve(g, "the Sign in button", node,
                        lambda ref: page.click(ref))
    elapsed = time.monotonic() - start

    assert elapsed < _STALE_REF_BUDGET_S, (
        f"stale-ref recovery took {elapsed:.1f}s — should fail fast on the "
        "stale ref and recover on the retry, not wait out the old timeout")
    # No exception above is the point: `_act_with_reresolve` raises only if
    # both the original ref AND the re-resolved retry fail.
