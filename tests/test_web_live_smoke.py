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
from actions.grounding.web.page import (MAX_NODES, collector_truncated,  # noqa: E402
                                        nodes_from_records, ref_of)
from actions.web_agency import _act_with_reresolve, web_agency  # noqa: E402

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


# ── Blocker 1: HIT_TEST_JS and COLLECT_JS must never disagree ──────────────
#
# Before the fix, COLLECT_JS derived name/role via the full accName()/
# implicitRole() chain and HIT_TEST_JS used a cut-down `aria-label ||
# innerText` / `getAttribute('role') || tagName` reading of its own. The two
# only agreed by coincidence (a bare `<button>text</button>`) — every
# `<a href>`, every `<input>` named by `label[for]`, every checkbox named by
# a wrapping label, every `<img alt>`, and every `role=`+`aria-label` div
# came back from HIT_TEST_JS with a DIFFERENT (name, role) than COLLECT_JS
# reported for the exact same element. `actionability._identity` compares
# (name, role, bounds), so `receives_events` — and therefore every click —
# could never pass for any of those; the model was told "it was covered by
# something else on the page," which was false.
#
# This loops every node COLLECT_JS finds through HIT_TEST_JS at its own
# centre and asserts the two agree, which is exactly the check
# `receives_events` performs in production.
def test_hit_test_agrees_with_collect_for_every_collected_node(browser):
    nodes = nodes_from_records(browser.page().collect())
    checked_roles = set()
    for node in nodes:
        if "SHOWING" not in node.states:
            continue  # a hidden node has no rendered point to hit-test
        cx = node.left + node.width / 2
        cy = node.top + node.height / 2
        hit = browser.page().hit_test(cx, cy)
        assert hit is not None, (
            f"hit_test at {node.name!r}'s own centre found nothing")
        assert hit["name"] == node.name and hit["role"] == node.role, (
            f"collect()=({node.name!r}, {node.role!r}) but "
            f"hit_test()=({hit['name']!r}, {hit['role']!r}) — "
            "receives_events can never pass for this node")
        checked_roles.add(node.role)

    # Confirm the sample page actually exercises the named failure classes,
    # not just the bare-button case that happened to survive the old bug by
    # coincidence.
    assert "link" in checked_roles, "no <a href> in the fixture"
    assert "button" in checked_roles, "no <button> in the fixture"
    assert "textbox" in checked_roles, "no label[for]-named <input>"
    assert "checkbox" in checked_roles, "no wrapping-label checkbox"
    assert "img" in checked_roles, "no <img alt> in the fixture"
    names = {n.name for n in nodes}
    assert "Custom widget" in names, "no role=+aria-label div in the fixture"


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
    # `_act_with_reresolve` always re-resolves `description` fresh before
    # acting now (blocker 2's fix — see its docstring in web_agency.py), so
    # the stale `node` captured above no longer needs to be passed in at
    # all; a no-op `gate_check` stands in for the real consent gate, since
    # this test is about stale-ref recovery, not consent.
    _act_with_reresolve(g, "the Sign in button", lambda n, nodes=(): None,
                        lambda ref: page.click(ref))
    elapsed = time.monotonic() - start

    assert elapsed < _STALE_REF_BUDGET_S, (
        f"stale-ref recovery took {elapsed:.1f}s — should fail fast on the "
        "stale ref and recover on the retry, not wait out the old timeout")
    # No exception above is the point: `_act_with_reresolve` raises only if
    # both the original ref AND the re-resolved retry fail.


# ── Gap 1: a closed <details> must not make its contents invisible ─────────
#
# Chromium reports an empty `innerText` for content inside a closed
# <details>, even though `textContent` still holds it — this is what made
# 1,150 of 1,151 misses on developer.mozilla.org's coverage run one single
# cause (task-11-report.md). `accName()` now falls back to `textContent`,
# but the state flags must still tell the truth: a control that is present
# but not currently visible must come back without SHOWING/VISIBLE, exactly
# like the pre-existing display:none case — a collected control that
# falsely claims to be showing is a regression, because `wait_for` would
# then try to click something that is not there.
#
# `browser.call(lambda p: p.set_content(...))` swaps the document in place
# rather than navigating to a new fixture file, so these tests do not need
# their own `file://` fixture; each restores `PAGE_URL` afterward so later
# tests in this module see the fixture they expect.

_DETAILS_HTML = """<!doctype html><html><body>
<details><summary>Menu</summary>
  <a href="/a">Hidden link A</a><button>Hidden button B</button>
</details>
<details open><summary>Open menu</summary><a href="/c">Visible link C</a></details>
</body></html>"""


def test_a_closed_details_content_is_collected_but_not_showing(browser):
    browser.call(lambda p: p.set_content(_DETAILS_HTML))
    try:
        nodes = nodes_from_records(browser.page().collect())
        by_name = {n.name: n for n in nodes}
        assert "Hidden link A" in by_name, (
            "a control inside a closed <details> was dropped entirely — "
            "the textContent fallback did not fire")
        assert "Hidden button B" in by_name

        for name in ("Hidden link A", "Hidden button B"):
            node = by_name[name]
            assert "SHOWING" not in node.states, (
                f"{name!r} claims SHOWING while inside a closed <details> — "
                "wait_for would try to click something that is not there")
            assert "VISIBLE" not in node.states
    finally:
        browser.goto(PAGE_URL)


def test_an_open_details_content_is_collected_and_showing(browser):
    browser.call(lambda p: p.set_content(_DETAILS_HTML))
    try:
        nodes = nodes_from_records(browser.page().collect())
        by_name = {n.name: n for n in nodes}
        visible = by_name["Visible link C"]
        assert "SHOWING" in visible.states
        assert "VISIBLE" in visible.states
    finally:
        browser.goto(PAGE_URL)


# ── Gap 2: MAX_NODES used to be a silent, document-order ceiling ───────────

def test_the_truncation_flag_appears_once_the_page_exceeds_the_cap(browser):
    html = "".join(
        ["<!doctype html><html><body>"]
        + [f"<button>btn{i}</button>" for i in range(MAX_NODES + 150)]
        + ["</body></html>"])
    browser.call(lambda p: p.set_content(html))
    try:
        records = browser.page().collect()
        nodes = nodes_from_records(records)
        assert collector_truncated(records) is True, (
            "more than MAX_NODES named controls were on the page, but "
            "nothing reported the collector stopped counting")
        assert len(nodes) == MAX_NODES, (
            "the output cap must still hold at exactly MAX_NODES even "
            "though more controls existed")
    finally:
        browser.goto(PAGE_URL)


def test_the_truncation_flag_is_absent_under_the_cap(browser):
    browser.goto(PAGE_URL)  # the small sample_page.html fixture
    records = browser.page().collect()
    assert collector_truncated(records) is False


def test_truncation_prefers_controls_in_or_near_the_viewport(browser):
    """Before this fix, truncation kept document order: the first MAX_NODES
    elements found, wherever they sat on screen. This fixture puts 700
    off-screen buttons *first* in the DOM and 50 on-screen buttons *last* —
    under naive document-order truncation, document order alone fills the
    entire 600-slot budget on the earlier off-screen block, and none of the
    on-screen buttons would survive at all. If this test starts failing,
    the most likely cause is the viewport-preference cut in COLLECT_JS
    silently going back to plain document order.

    `position: absolute; top: <px>` is what decouples on-screen-ness from
    DOM order here — `getBoundingClientRect()` (what COLLECT_JS's viewport
    check uses) reports the CSS position, not the position in the markup.
    """
    parts = ["<!doctype html><html><body>"]
    for i in range(700):
        parts.append(f'<button style="position:absolute; top:{5000+i}px; '
                     f'left:10px;">off{i}</button>')
    for i in range(50):
        parts.append(f'<button style="position:absolute; top:{10+i*15}px; '
                     f'left:10px;">near{i}</button>')
    parts.append("</body></html>")
    browser.call(lambda p: p.set_content("".join(parts)))
    try:
        records = browser.page().collect()
        nodes = nodes_from_records(records)
        names = {n.name for n in nodes}

        missing_near = [f"near{i}" for i in range(50) if f"near{i}" not in names]
        assert not missing_near, (
            f"{len(missing_near)} on-screen controls were dropped in favour "
            "of off-screen controls earlier in the DOM — truncation is not "
            "preferring the viewport")
        assert collector_truncated(records) is True
        assert len(nodes) == MAX_NODES
    finally:
        browser.goto(PAGE_URL)


# ── Blocker 2: the consent gate must never be spent on the wrong control ───
#
# `data-ae-ref` values are positional (`const ref = 'e' + n` in page.py) and
# every `collect()` renumbers all of them from scratch. `act_and_verify` ->
# `wait_for` issues at least two collects before `web_agency()` ever acts
# (it needs a `previous` read for the `stable` check). Before the fix, the
# ref used to actuate was the one captured by the FIRST resolve — the same
# one the consent gate checked — so if the page's control list changed in
# between, that ref could silently be reassigned to a DIFFERENT element by
# the time the click landed. Reproduced end to end through the public
# `web_agency()` entry point (never `page.click(ref)` directly, which would
# bypass `act_and_verify` and prove nothing — see this file's own
# docstring): a benign "Continue" got gated, an irreversible "Complete
# purchase" got clicked instead, and the tool reported "Clicked 'Continue'".
#
# This fixture puts "Continue" alone in the DOM, then inserts "Complete
# purchase" — a control `irreversible_reason` refuses — as the FIRST
# element in the body right after the very first `collect()` call. That
# collect is exactly the one `_click`'s up-front resolve performs, before
# the consent gate is even consulted: from the SECOND collect onward
# (everything `act_and_verify`/`wait_for` does), "Complete purchase" holds
# whatever ref "Continue" held originally, and "Continue" holds a new one.
# The trigger is a real `collect()` call count, not a wall-clock guess, so
# this is deterministic rather than timing-dependent. Nothing about
# COLLECT_JS, act_and_verify, wait_for, the consent gate, or
# `_act_with_reresolve` is faked — only the moment the page mutates is
# driven by the test instead of by an in-page timer.

_RACY_CLICK_HTML = """<!doctype html><html><body>
  <button id="continue-btn">Continue</button>
  <script>
    window.__clicked = [];
    document.getElementById('continue-btn')
      .addEventListener('click', () => window.__clicked.push('continue'));
  </script>
</body></html>"""


def test_a_control_that_changes_mid_click_never_hits_the_wrong_ref(browser):
    browser.call(lambda p: p.set_content(_RACY_CLICK_HTML))
    try:
        collect_calls = {"n": 0}
        real_page = browser.page()
        real_collect = real_page.collect

        def counting_collect():
            collect_calls["n"] += 1
            result = real_collect()
            if collect_calls["n"] == 1:
                # Runs once, right after the up-front resolve inside
                # `_click` has already captured (and gated) "Continue" —
                # every collect from here on reports a DIFFERENT arrangement:
                # "Complete purchase" first (inheriting whatever ref
                # "Continue" held a moment ago), "Continue" second.
                browser.call(lambda p: p.evaluate(
                    "(() => {"
                    "  const b = document.createElement('button');"
                    "  b.id = 'purchase-btn';"
                    "  b.textContent = 'Complete purchase';"
                    "  b.addEventListener('click', "
                    "    () => window.__clicked.push('purchase'));"
                    "  document.body.insertBefore(b, document.body.firstChild);"
                    "})()"))
            return result

        original_page_method = browser.page

        def patched_page():
            p = original_page_method()
            if p is not None:
                p.collect = counting_collect
            return p

        # `browser.page` is a plain instance attribute once patched (not a
        # bound method), so every consumer of it — `_click`'s own
        # `page = browser.page()` AND `WebGrounder`'s internal
        # `self._page_fn()` calls — sees the same counting hook.
        browser.page = patched_page
        try:
            result = web_agency(
                {"action": "click", "description": "the Continue button"},
                browser=browser)
        finally:
            browser.page = original_page_method

        clicked = browser.call(lambda p: p.evaluate("window.__clicked")) or []

        assert "purchase" not in clicked, (
            f"the gate approved 'Continue' but the browser actually clicked "
            f"{clicked!r} — a reused ref silently hit the wrong control")
        if result.ok:
            assert clicked == ["continue"], (
                "reported success but did not actually click the control "
                "the gate approved")
            assert "Continue" in result.message
        else:
            assert clicked == [], (
                "reported failure but something was still clicked anyway")
    finally:
        browser.goto(PAGE_URL)


def test_refs_are_never_reused_across_collects(browser):
    """The structural close of the wrong-element bug class.

    Refs used to be positional — "e0", "e1", ... restarting at zero on every
    collect — so any collect between resolving a control and acting on it
    silently handed a live element the ref string a different, older node was
    holding. Review reproduced the consequence twice on code that was
    supposed to be fixed: a gate approving "Search" while the browser typed
    into "Message to seller", and a gate approving "Continue" while the
    browser clicked "Complete purchase".

    A monotonic counter makes it impossible rather than merely unlikely: a
    re-stamped ref matches nothing, so the actuation fails fast and retries
    against a fresh resolve instead of hitting the wrong element.
    """
    page = browser.page()
    browser.call(lambda pg: pg.set_content(
        '<div id="banner"><button>Continue</button></div>'
        '<button id="buy">Complete purchase</button>'))

    first = nodes_from_records(page.collect())
    continue_ref = next(n.ref for n in first if n.name == "Continue")

    # The banner goes away, exactly as a cookie notice does mid-interaction.
    browser.call(lambda pg: pg.evaluate(
        "() => document.getElementById('banner').remove()"))
    second = nodes_from_records(page.collect())

    assert [n.name for n in second] == ["Complete purchase"]
    # The decisive assertion: the old ref must not now name a live element.
    assert continue_ref not in {n.ref for n in second}
    matching = browser.call(lambda pg: pg.evaluate(
        "(ref) => document.querySelectorAll('[data-ae-ref=\"' + ref + '\"]').length",
        continue_ref))
    assert matching == 0, "a stale ref still matches an element — it can be actuated"


def test_a_collect_between_resolve_and_actuation_cannot_redirect_the_click(browser):
    """The end-to-end shape of the bug, driven through the real actuation path.

    Whatever else changes on the page, a ref captured before an intervening
    collect must never land on a *different* control. Failing is acceptable
    here; hitting the wrong element is not.
    """
    browser.call(lambda pg: pg.set_content(
        '<button id="a" onclick="document.title=\'A\'">Continue</button>'
        '<button id="b" onclick="document.title=\'B\'">Complete purchase</button>'))
    page = browser.page()

    nodes = nodes_from_records(page.collect())
    stale = next(n.ref for n in nodes if n.name == "Continue")

    # Something re-collects (the type gate's own wall check does exactly this).
    browser.call(lambda pg: pg.evaluate("() => document.getElementById('a').remove()"))
    page.collect()

    try:
        page.click(stale)
    except Exception:
        pass                     # failing fast is the correct outcome

    title = browser.call(lambda pg: pg.title())
    assert title != "B", "the stale ref actuated the irreversible control"
