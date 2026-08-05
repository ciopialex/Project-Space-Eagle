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
"""
from __future__ import annotations

from typing import Any

from actions.grounding.verify import act_and_verify
from actions.grounding.web.consent import irreversible_reason
from actions.grounding.web.grounder import WebGrounder
from actions.grounding.web.handoff import wall_reason
from actions.grounding.web.page import ref_of
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


def _look(browser, want_pixels: bool) -> ToolResult:
    page = browser.page()
    sense = _SENSE.look(page, want_pixels=want_pixels)
    needs_human = wall_reason(sense.nodes, page.url() if page else "")

    lines = [f"{len(sense.nodes)} controls on {page.url() if page else 'the page'}:"]
    if sense.nodes:
        lines.append(_describe(sense.nodes))
    if sense.escalated:
        lines.append(f"(Looked closer: {sense.reason}.)")
    if needs_human:
        lines.append(f"This needs the user — {needs_human}.")

    return ToolResult.success(
        "\n".join(lines),
        tier=sense.tier,
        controls=[n.name for n in sense.nodes],
        needs_human=needs_human,
        has_screenshot=sense.screenshot is not None,
    )


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
      - "dead"    the browser thread was not running (`RuntimeError` from
                  `_submit`); nothing was sent to the browser at all.
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


def _actuation_result(verb_ing: str, verb_past: str, node,
                      outcome: dict) -> ToolResult:
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
            f"Could not {verb_ing} '{node.name}': {outcome['detail']}",
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

    if kind == "dead":
        _SENSE.note_failure()
        return ToolResult.failure(
            f"Could not {verb_ing} '{node.name}': the browser stopped "
            f"responding ({detail}).",
            guidance=("The browser's thread is no longer running, so nothing "
                      "was sent to the page. Call action='open' with a URL "
                      "(the browser restarts automatically), then retry."),
            control=node.name)

    if kind == "error":
        _SENSE.note_failure()
        return ToolResult.failure(
            f"Could not {verb_ing} '{node.name}': {detail}",
            guidance=("Call action='look' to check the page's current state, "
                      "then decide whether to retry."),
            control=node.name)

    _SENSE.note_success()
    return ToolResult.success(
        f"{verb_past} '{node.name}' — {outcome['detail']}.",
        changed=outcome["changed"], control=node.name)


def _click(browser, grounder: WebGrounder, description: str) -> ToolResult:
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
    ref = ref_of(node)
    outcome = act_and_verify(
        description,
        _safe_act(lambda _el: page.click(ref)),
        resolver=grounder,
        action="click",
        hit_test=grounder.hit_test,
        timeout=5.0,
    )
    return _actuation_result("click", "Clicked", node, outcome)


def _type(browser, grounder: WebGrounder, description: str,
          text: str) -> ToolResult:
    node = grounder.find_node(description)
    if node is None:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"No field on this page matches '{description}'.",
            guidance=("Call web_agency action='look' to see what is actually "
                      "on the page, then use one of those names."))

    page = browser.page()
    ref = ref_of(node)
    outcome = act_and_verify(
        description,
        _safe_act(lambda _el: page.fill(ref, text)),
        resolver=grounder,
        action="fill",
        hit_test=grounder.hit_test,
        timeout=5.0,
    )
    result = _actuation_result("type into", "Typed into", node, outcome)
    if (not result.ok and not outcome["acted"]
            and "editable" in outcome["detail"].lower()):
        # Sharpen the not-a-field case specifically: "never became
        # actionable" is accurate but generic, and a weaker model does
        # better with the concrete instruction than with the raw
        # waiting-machinery vocabulary the detail string carries.
        result.guidance = ("If the failed check was 'editable', that control "
                           "is not a text field — call action='look' and "
                           "pick one whose role is a textbox.")
    return result


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
        return _look(browser, want_pixels=False)

    if action == "look":
        return _look(browser, want_pixels=bool(params.get("want_pixels")))

    grounder = WebGrounder(browser.page, sense=_SENSE)
    description = str(params.get("description") or "").strip()
    if not description:
        return ToolResult.failure(
            f"No control described for '{action}'.",
            guidance="Pass description='the Sign in button'.")

    if action == "click":
        return _click(browser, grounder, description)

    return _type(browser, grounder, description,
                 str(params.get("text") or ""))


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
