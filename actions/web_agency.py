"""Use a website the way a person would.

Not a function per site. One way of perceiving any page and acting inside it,
so the answer to "can you do X on this site" stops depending on whether someone
wrote X.

Everything irreversible is refused here rather than attempted and apologised
for, and everything a site wants a human for is handed back to the human.

Exception safety — this is the part that matters most in this file:

`EagleBrowser._submit` raises `TimeoutError` whenever a browser call outlives
its deadline (exactly what a slow click does) and `RuntimeError` when the
browser thread has died. `act_and_verify` calls the `act` callable it is
given *unwrapped* — anything that callable raises propagates straight through
`act_and_verify`, straight through this module, and into whatever dispatches
tool calls, as an unhandled exception. `core/tool_result.py` exists precisely
so a tool never does that: it returns `ok=False` with actionable `guidance`
instead. `_safe_act` (below) is the wrapper that makes that true for every
actuation this module performs, and `web_agency()` itself is wrapped a second
time, at the boundary, so that a bug anywhere in this module — not just in
the actuation path — still comes back as a `ToolResult`, never a raise.

A `TimeoutError` is deliberately reported as neither success nor failure: the
browser call was abandoned by the *caller* waiting for it, not cancelled at
the browser (see `EagleBrowser._submit`'s `cancelled` flag — it only stops a
job that has not started yet). A click already in flight when the timeout
fires still completes. Telling the model "that failed" would be exactly the
kind of lie `ToolResult` exists to prevent; the honest statement is "the
outcome is unknown, go look."

`ToolResult.to_response()` (`core/tool_result.py`) only ever emits `result`
(the `message`), `ok`, and — on failure only — `guidance`. Everything in
`.data` (`changed`, `control`, `tier`, ...) is for this codebase's own
callers, never for the model. That is why every `ok=True` path below must
carry its whole truth in `message` alone: a caveat that lives only in
`.data` does not exist as far as the model is concerned.
"""
from __future__ import annotations

import re
from typing import Any

from actions.grounding.actionability import is_editable
from actions.grounding.verify import act_and_verify
from actions.grounding.web.consent import irreversible_reason
from actions.grounding.web.grounder import WebGrounder
from actions.grounding.web.handoff import wall_reason
from actions.grounding.web.page import element_from, nodes_from_records, ref_of
from actions.grounding.web.sense import PageSense
from core.tool_result import ToolResult

_ACTIONS = ("open", "look", "click", "type", "close")

_NO_BROWSER_GUIDANCE = (
    "The eagle's browser could not start. Run "
    "`.venv/bin/python -m playwright install chromium` once, then try again."
)

# One sense per process, so the failure count that drives escalation survives
# across tool calls the way a person's growing suspicion does.
_SENSE = PageSense()


def _browser(explicit):
    if explicit is not None:
        return explicit
    from actions.grounding.web.browser import default_browser
    return default_browser()


def _ready(browser) -> ToolResult | None:
    """None when the browser is usable, a failure ToolResult otherwise."""
    if not browser.running:
        try:
            browser.start()
        except Exception as e:
            return ToolResult.failure(f"The browser did not start: {e}",
                                      guidance=_NO_BROWSER_GUIDANCE)
    if not browser.running:
        detail = getattr(browser, "last_error", "") or "no further detail"
        return ToolResult.failure(f"The browser did not start: {detail}",
                                  guidance=_NO_BROWSER_GUIDANCE)
    return None


def _describe(nodes) -> str:
    return "\n".join(f"- {n.name} ({n.role})" for n in nodes[:60])


def _current_nodes(page) -> tuple:
    """A fresh structural read of `page`, or `()` if the read itself fails.

    Used where a caller needs the whole node list rather than one match —
    `wall_reason` needs to see every control on the page, not just the one
    being acted on.
    """
    try:
        return nodes_from_records(page.collect())
    except Exception:
        return ()


def _current_url(page) -> str:
    try:
        return page.url()
    except Exception:
        return ""


def _look(browser, want_pixels: bool) -> ToolResult:
    page = browser.page()
    if page is None:
        # Distinct from "read the page and found nothing": there is no page
        # to read at all. Reporting "0 controls" here would tell the model a
        # thing that isn't true — that it looked and the page was empty.
        return ToolResult.failure(
            "The browser has no page open right now.",
            guidance="Call action='open' with a URL first.")

    sense = _SENSE.look(page, want_pixels=want_pixels)
    current_url = _current_url(page)
    needs_human = wall_reason(sense.nodes, current_url)

    # `PageSense.look` swallows a `page.collect()` exception into an empty
    # node tuple — indistinguishable, from here, from a page that is
    # genuinely blank. Both cases below share the same escalation note, but
    # the zero-node case gets an honest "could not read" framing rather than
    # a confident "0 controls", and reports ok=False: this tool cannot tell
    # you the page is empty, only that it did not see any controls.
    escalation_note = ""
    if sense.escalated:
        if sense.screenshot is not None:
            escalation_note = (
                f"Looked closer because {sense.reason} — took a screenshot, "
                "but this tool has no way to show it to you yet; treat the "
                "structural list above as everything currently knowable.")
        else:
            escalation_note = (
                f"Looked closer because {sense.reason} — the screenshot "
                "also failed, so this is everything that could be read.")

    if not sense.nodes:
        lines = [f"Could not read any controls on {current_url or 'the page'}."]
        if escalation_note:
            lines.append(f"({escalation_note})")
        if needs_human:
            lines.append(f"This needs the user — {needs_human}.")
        return ToolResult.failure(
            "\n".join(lines),
            guidance=("The page may be genuinely empty, still loading, or "
                      "the read failed. Call action='look' with "
                      "want_pixels=true, or action='open' with the URL "
                      "again if the page seems gone."),
            tier=sense.tier, controls=[], needs_human=needs_human,
            has_screenshot=sense.screenshot is not None)

    lines = [f"{len(sense.nodes)} controls on {current_url or 'the page'}:",
             _describe(sense.nodes)]
    if escalation_note:
        lines.append(f"({escalation_note})")
    if needs_human:
        lines.append(f"This needs the user — {needs_human}.")

    return ToolResult.success(
        "\n".join(lines),
        tier=sense.tier,
        controls=[n.name for n in sense.nodes],
        needs_human=needs_human,
        has_screenshot=sense.screenshot is not None,
    )


def _act_with_reresolve(grounder: WebGrounder, description: str, node,
                        actuate) -> Any:
    """Actuate on `node`'s ref; if that fails, re-resolve `description` once
    against the page's current state and retry with whatever ref it reports
    now.

    A ref is only good until the next `collect()` — COLLECT_JS strips every
    `data-ae-ref` at the start of each fresh snapshot (see page.py) — so a
    ref captured before an async redirect or SPA route change can be stale
    by the time this runs, even though `node` itself was resolved correctly.
    `PagePort.click`/`fill` fail fast on a stale ref (`_REF_TIMEOUT_MS` in
    browser.py, a few seconds rather than Playwright's 30s navigation
    default) specifically so this can recover within the same tool call:
    re-perceive the page once, the way a person would when it moves under
    them, rather than either hanging or giving up on the first miss.

    Lives here rather than in `PagePort` because the retry needs
    `description` to re-resolve — `PagePort` only ever sees a ref, by design
    (see its docstring), so it has nothing to re-resolve *with*. Lives here
    rather than in `WebGrounder` because it is specifically about retrying an
    *actuation*, not about finding a node — `WebGrounder` has no notion of
    "try to act, and retry if that failed."
    """
    try:
        return actuate(ref_of(node))
    except Exception:
        fresh = grounder.find_node(description)
        fresh_ref = ref_of(fresh) if fresh is not None else ""
        if not fresh_ref:
            raise
        return actuate(fresh_ref)


def _safe_act(act):
    """Wrap an `act(element)` callable so nothing it raises can escape.

    `act_and_verify` invokes its `act` argument unwrapped — see this module's
    docstring. This turns whatever `act` raises into a plain result tuple
    instead: `("ok", return_value)` on success, or `(kind, str(exc))` for a
    known-shape failure. `act_and_verify` only ever sees a normal return
    value from this wrapper, so it proceeds to re-observe the page exactly as
    it would after a real success — which is correct: after a timeout in
    particular, whether anything actually happened is precisely what
    re-observing is for.

    `kind` is one of:
      - "ok"      the actuation ran to completion.
      - "timeout" `EagleBrowser._submit` gave up waiting for the browser
                  thread; the call may still be in flight or may have landed.
      - "dead"    a `RuntimeError` surfaced. This is the shape
                  `EagleBrowser._submit` raises for "the browser thread is
                  not running", but `_submit` also re-raises worker-side
                  exceptions with their original type, so a `RuntimeError`
                  raised *inside* the page call itself lands here too — the
                  caller must check whether the thread is actually dead
                  before claiming it is (see `_actuation_result`).
      - "error"   anything else — an unanticipated failure, reported honestly
                  rather than allowed to crash the caller.
    """
    def wrapped(element):
        try:
            return ("ok", act(element))
        except TimeoutError as e:
            return ("timeout", str(e))
        except RuntimeError as e:
            return ("dead", str(e))
        except Exception as e:
            return ("error", str(e))
    return wrapped


#: `waiting.py`'s internal check vocabulary, translated into plain language
#: for a message the model reads. See `_plain_english` — this is deliberately
#: matched against the *exact* string shape `act_and_verify`'s `detail`
#: builds, and falls back to the raw detail (not to nothing) if that shape
#: ever changes, so a drift in `verify.py` degrades to jargon rather than to
#: silence.
_CHECK_EXPLANATIONS = {
    "not_found": "it was not found on the page",
    "visible": "it was not visible",
    "enabled": "it was disabled",
    "editable": "it was not an editable field",
    "stable": "it kept moving or changing, never settling",
    "receives_events": "it was covered by something else on the page",
}

_DETAIL_RE = re.compile(
    r"^never became actionable for \w+: (?P<check>\w*) "
    r"\(after (?P<ms>\d+)ms, (?P<attempts>\d+) attempts\)$")


def _plain_english(detail: str) -> str:
    """Turn `act_and_verify`'s "never became actionable for click:
    receives_events (after 5001ms, 78 attempts)" into a sentence, not a log
    line. Falls back to the raw detail if the shape doesn't match or the
    check name isn't in the table — never loses information, only sometimes
    fails to translate it."""
    m = _DETAIL_RE.match(detail)
    if not m:
        return detail
    plain = _CHECK_EXPLANATIONS.get(m.group("check"))
    if not plain:
        return detail
    return (f"{plain} (waited {m.group('ms')}ms across "
            f"{m.group('attempts')} tries)")


def _actuation_result(verb_ing: str, verb_past: str, node, outcome: dict,
                      browser) -> ToolResult:
    """Turn an `act_and_verify` outcome — whose `act` was `_safe_act`-wrapped
    — into the `ToolResult` the model sees. The only place that reads
    `outcome["result"]`'s tuple and decides what actually happened.
    """
    if not outcome["acted"]:
        # Never reached the actuation at all — the control was found but
        # never became actionable (not visible, not enabled, covered by
        # something else, ...).
        _SENSE.note_failure()
        return ToolResult.failure(
            f"Could not {verb_ing} '{node.name}': "
            f"{_plain_english(outcome['detail'])}.",
            guidance=("The control was found but never became ready. Call "
                      "action='look' with want_pixels=true to see the page as "
                      "an image, then decide."))

    kind, detail = outcome["result"]

    if kind == "timeout":
        # Not a confirmed failure — say so, and treat it with the same
        # suspicion a real failure gets, since "we don't know" is exactly
        # the moment to look harder rather than assume the best.
        _SENSE.note_failure()
        return ToolResult.failure(
            f"'{node.name}' did not confirm before the call timed out — the "
            "outcome is unknown, not a failure. It may have landed.",
            guidance=("The call was abandoned waiting for a response; it was "
                      "not cancelled at the browser, so the action may still "
                      "have taken effect. Call action='look' to see the "
                      "page's current state before deciding whether to "
                      "retry — do not assume it failed and do not assume it "
                      "succeeded."),
            control=node.name, outcome="unknown")

    if kind in ("dead", "error"):
        _SENSE.note_failure()
        if kind == "dead" and not browser.running:
            # Only make the strong claim ("nothing was sent") when the
            # browser is actually confirmed down right now. A RuntimeError
            # can also surface from inside a live page call — see
            # `_safe_act`'s docstring — and "nothing was sent" would be
            # false in that case.
            return ToolResult.failure(
                f"Could not {verb_ing} '{node.name}': the browser stopped "
                f"responding ({detail}).",
                guidance=("The browser's thread is no longer running, so "
                          "nothing was sent to the page. Call action='open' "
                          "with a URL (the browser restarts automatically), "
                          "then retry."),
                control=node.name)
        return ToolResult.failure(
            f"Could not {verb_ing} '{node.name}': {detail}",
            guidance=("Call action='look' to check the page's current state, "
                      "then decide whether to retry."),
            control=node.name)

    # kind == "ok": the actuation itself completed without raising. `ok=True`
    # below asserts exactly that — the call was genuinely delivered — never
    # that the intended effect was confirmed. `outcome["changed"]` decides
    # the wording, not the `ok` value: a delivered click with no observable
    # change is a real, common, benign outcome (toggles, no-ops, async
    # updates), not a failure. What must NOT happen is claiming success while
    # hedging in the same breath ("...it may not have worked") — that
    # sentence next to `ok: true` is the exact lie `ToolResult` exists to
    # prevent, and it shipped here once already (see the fix-round report).
    _SENSE.note_success()
    if outcome["changed"]:
        message = f"{verb_past} '{node.name}' — the page changed."
    else:
        message = (f"{verb_past} '{node.name}'. Nothing on the page changed "
                  "— call action='look' if you expected it to.")
    return ToolResult.success(message, changed=outcome["changed"],
                              control=node.name)


def _click(browser, grounder: WebGrounder, description: str) -> ToolResult:
    if not grounder.available():
        # `grounder.find_node` swallows a `browser.page()` failure into
        # `None`, which is indistinguishable from "genuinely no match" —
        # checking `available()` first keeps this tool from telling the
        # model a confident "no control matches" when the truth is "the
        # read itself failed".
        return ToolResult.failure(
            f"Could not check the page for '{description}' — the page "
            "could not be read right now.",
            guidance=("Call action='look' to see the page's current state, "
                      "or action='open' again if the page seems gone."))

    node = grounder.find_node(description)
    if node is None:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"No control on this page matches '{description}'.",
            guidance=("Call web_agency action='look' to see what is actually "
                      "on the page, then use one of those names."))

    # Checked BEFORE anything is sent to the browser — a refusal that fires
    # after the click is not a refusal.
    reason = irreversible_reason(node.name, node.role)
    if reason:
        return ToolResult.failure(
            f"Refused to click '{node.name}' because {reason}.",
            guidance=("Tell the user exactly what this would do and ask them "
                      "to confirm it themselves. The eagle does not take "
                      "irreversible actions on their behalf."))

    page = browser.page()
    outcome = act_and_verify(
        description,
        _safe_act(lambda _el: _act_with_reresolve(
            grounder, description, node, lambda ref: page.click(ref))),
        resolver=grounder,
        action="click",
        hit_test=grounder.hit_test,
        timeout=5.0,
    )
    return _actuation_result("click", "Clicked", node, outcome, browser)


def _type(browser, grounder: WebGrounder, description: str,
          text: str) -> ToolResult:
    if not grounder.available():
        return ToolResult.failure(
            f"Could not check the page for '{description}' — the page "
            "could not be read right now.",
            guidance=("Call action='look' to see the page's current state, "
                      "or action='open' again if the page seems gone."))

    node = grounder.find_node(description)
    if node is None:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"No field on this page matches '{description}'.",
            guidance=("Call web_agency action='look' to see what is actually "
                      "on the page, then use one of those names."))

    if node.role.lower() == "password":
        # `look` already surfaces this page as needing the user; typing into
        # the password field itself would be doing the sign-in on their
        # behalf. Refused unconditionally, not merely reported.
        return ToolResult.failure(
            f"Refused to type into '{node.name}' because it is a password "
            "field.",
            guidance=("This needs the user to sign in themselves. Tell them "
                      "what the page is asking for; do not type a password "
                      "on their behalf."))

    page = browser.page()
    # Re-check the page as a whole, not just the field being targeted — a
    # page asking for a human-verification challenge or a code the eagle
    # cannot have is not safe to type into just because the specific control
    # requested isn't itself a password field.
    reason = wall_reason(_current_nodes(page), _current_url(page))
    if reason:
        return ToolResult.failure(
            f"Refused to type into '{node.name}' — {reason}.",
            guidance=("This needs the user. Tell them what the page is "
                      "asking for and let them handle it themselves."))

    if not is_editable(element_from(node)):
        # A structural check, not a string match against waiting.py's
        # vocabulary — checked up front, before ever asking the browser to
        # try. Cheaper (no five-second actionability wait for a control that
        # was never going to become editable) and doesn't depend on
        # `outcome['detail']`'s wording staying stable.
        _SENSE.note_failure()
        return ToolResult.failure(
            f"'{node.name}' is not editable — it is not a text field.",
            guidance=("Call action='look' and pick a control whose role is "
                      "a textbox, searchbox, or password field."))

    outcome = act_and_verify(
        description,
        _safe_act(lambda _el: _act_with_reresolve(
            grounder, description, node, lambda ref: page.fill(ref, text))),
        resolver=grounder,
        action="fill",
        hit_test=grounder.hit_test,
        timeout=5.0,
    )
    return _actuation_result("type into", "Typed into", node, outcome,
                             browser)


def _coerce_text(value: Any) -> str:
    """`params.get("text") or ""` turns `0` and `False` into `""` — a page
    that wants literal "0" typed into it is not a hypothetical. Only a
    missing value becomes empty text; anything present is stringified."""
    return "" if value is None else str(value)


def _web_agency(params: dict, player: Any, browser: Any) -> ToolResult:
    action = str(params.get("action") or "").strip().lower()

    if action not in _ACTIONS:
        return ToolResult.failure(
            f"'{action or '(none)'}' is not something this tool does.",
            guidance=f"Use one of: {', '.join(_ACTIONS)}.")

    browser = _browser(browser)

    if action == "close":
        try:
            browser.close()
        except Exception as e:
            return ToolResult.failure(f"Could not close the browser: {e}",
                                      guidance="It may already be closed.")
        return ToolResult.success("Closed the eagle's browser.")

    not_ready = _ready(browser)
    if not_ready is not None:
        return not_ready

    if action == "open":
        url = str(params.get("url") or "").strip()
        if not url:
            return ToolResult.failure(
                "No URL to open.",
                guidance="Pass url='https://…' with action='open'.")
        if "://" not in url:
            url = "https://" + url
        try:
            browser.goto(url)
        except Exception as e:
            return ToolResult.failure(f"Could not open {url}: {e}",
                                      guidance=("Check the address, or tell "
                                                "the user the site did not "
                                                "respond."))
        # A deliberate navigation invalidates whatever suspicion the last
        # page earned; a fresh site shouldn't inherit a stale failure count.
        _SENSE.note_success()
        return _look(browser, want_pixels=False)

    if action == "look":
        return _look(browser, want_pixels=bool(params.get("want_pixels")))

    grounder = WebGrounder(browser.page)
    description = str(params.get("description") or "").strip()
    if not description:
        return ToolResult.failure(
            f"No control described for '{action}'.",
            guidance="Pass description='the Sign in button'.")

    if action == "click":
        return _click(browser, grounder, description)

    return _type(browser, grounder, description,
                 _coerce_text(params.get("text")))


def web_agency(parameters: dict | None = None, player: Any = None,
               browser: Any = None) -> ToolResult:
    """Perceive and act inside a web page. See `_ACTIONS` for the verbs.

    Never raises. Every path — including a bug in this function itself, not
    only the actuation path — returns a `ToolResult`, so the caller's
    dispatch loop never has to catch anything from here.
    """
    try:
        return _web_agency(parameters or {}, player, browser)
    except Exception as e:
        return ToolResult.failure(
            f"The web tool hit an unexpected error: {e}",
            guidance=("Call action='look' to see the page's current state, "
                      "then decide whether to retry."))
