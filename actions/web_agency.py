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

Consent, re-resolved at the last possible moment — this is the part that
matters second most:

`data-ae-ref` values are positional (`page.py`: `const ref = 'e' + n`) and
every `collect()` renumbers every one of them from scratch. A `WebNode`
resolved once, early (by `find_node`, for the up-front refusal check below),
can have its ref silently reassigned to a *different* element by the time
`act_and_verify` finishes its own polling — not merely go stale, which was
already handled, but keep working while pointing at something else. Gating
that early node and then acting on a ref resolved later is therefore not
safe: the gate's approval and the browser's actuation can end up about two
different controls. `_act_with_reresolve` is the fix — every actuation in
this file re-resolves `description` and re-runs the consent gate
(`_gate_click`/`_gate_type`, raising `_ConsentBlocked`) against exactly the
node it is about to act on, including on its own internal retry. The checks
in `_click`/`_type` above it are a fast-fail UX convenience only, never the
safety boundary; see `_act_with_reresolve`'s docstring for the full account,
including the reproduction this closes.
"""
from __future__ import annotations

import re
from typing import Any

from actions.grounding.actionability import is_editable
from actions.grounding.verify import act_and_verify
from actions.grounding.web.consent import irreversible_reason
from actions.grounding.web.grounder import WebGrounder
from actions.grounding.web.handoff import (auth_domains_for, await_human,
                                           cookie_wall_choice, login_remedy,
                                           signed_out_reason, wall_reason)
from actions.grounding.web.page import element_from, nodes_from_records, ref_of
from actions.grounding.web.sense import PageSense
from core.tool_result import ToolResult

_ACTIONS = ("open", "look", "click", "type", "sign_in",
            "import_login", "close")

_NO_BROWSER_GUIDANCE = (
    "The eagle's browser could not start. Run "
    "`.venv/bin/python -m playwright install chromium` once, then try again."
)

# The eagle's browser defaults to headless (see EagleBrowser in browser.py —
# a non-exclusive tool that could pop a visible window over the user's own
# work is the bug this default closes), and nothing today can surface that
# window for a human to use even when one is asked for: `await_human` in
# handoff.py exists but nothing calls it, and there is no UI hook that shows
# this specific, separate browser profile (never the user's own Chrome — see
# user_paths.browser_profile_dir()) to anyone. Stating that plainly here,
# every place a wall or a password field hands control back to "the user",
# is the honest alternative to a guidance string that implies a handoff path
# which does not exist yet.
_NO_HANDOFF_WINDOW = (
    "the eagle's browser runs invisibly and there is no way to hand this "
    "specific window to them yet")

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


def _import_login(browser, domains: list) -> ToolResult:
    """Bring the named sites' logins across from the user's own Chrome.

    The alternative to signing into each site inside the eagle's browser. Only
    the sites named are imported — every other cookie is deleted from the copy
    before the eagle's browser is ever pointed at it — so this is a property
    the user can verify rather than a promise to behave. Importing the lot
    would hand over their bank and their email to fetch a playlist.
    """
    from actions.grounding.web.profile_import import import_logins
    from core import user_paths

    # The browser must be down: it is about to have its profile replaced
    # underneath it, and a running Chrome holds locks on the cookie store.
    try:
        browser.close()
    except Exception:
        pass

    result = import_logins(list(domains or []),
                           into=user_paths.browser_profile_dir())
    if not result.ok:
        return ToolResult.failure(result.detail, guidance=result.guidance)
    return ToolResult.success(
        result.detail + " The eagle is now signed in to those sites and will "
        "stay signed in.",
        imported=result.imported, dropped=result.dropped,
        note=result.guidance)


#: Which site currently has a sign-in window open, if any. Module-level for
#: the same reason `_SENSE` is: the handoff spans two tool calls (two model
#: turns), and the browser is process-wide.
_HANDOFF: dict = {}

#: How long `sign_in` will wait in-call before handing the turn back. Must stay
#: comfortably inside `TOOL_SPECS["web_agency"].timeout_s` in main.py — the
#: original bug was those two numbers living in different files and never being
#: compared. `test_the_grace_wait_cannot_exceed_the_tool_budget` compares them.
_SIGN_IN_GRACE_S = 20.0


def _wall_or_signed_out(page) -> str:
    """The ONLY thing read while the user is at the keyboard: a wall check.

    Not a page read, not a screenshot. Their password is not something to
    watch, and a handoff is the one moment the eagle is pointed at a form
    somebody is typing a secret into.
    """
    nodes = _current_nodes(page)
    return wall_reason(nodes, _current_url(page)) or signed_out_reason(nodes)


def _sign_in(browser, url: str, *, grace: float = _SIGN_IN_GRACE_S,
             poll: float = 1.0) -> ToolResult:
    """Put the eagle's browser on screen so the user can sign in, once.

    The eagle keeps its own browser profile, deliberately: attaching to the
    user's Chrome would inherit every session they have open, silently, and
    fight them for their own window. The cost of that choice is one login per
    site. The session persists in the profile afterwards, so this is a
    one-time cost per site rather than a step in every task.

    **This does not block until the user is done.** The first version waited
    up to 300 seconds inside a tool whose budget is 90, so it was killed every
    time and could only ever have succeeded if the user signed in within 90
    seconds - 2FA on a phone rarely does. Worse, the kill is external, so the
    cleanup that puts the browser back to headless never ran and the window
    could be left on screen.

    Holding a tool slot for minutes is also simply the wrong shape here: it
    blocks the batch and the eagle cannot say a word while it waits. So the
    handoff is two-phase. This call shows the window and hands the turn back
    quickly; a later call - after the user says they are done - confirms it
    and puts the window away. A short grace wait covers the case where the
    user is quick, so a remembered password still finishes in one turn.

    The eagle never takes the user's word for it: phase two re-checks the wall
    rather than trusting "I signed in".
    """
    pending = _HANDOFF.get("url") == url

    if not pending:
        if not browser.surface(True):
            return ToolResult.failure(
                "Could not put the browser on screen for the user to sign in.",
                guidance=("Tell the user the sign-in window would not open. "
                          "They can retry, or sign in later."))
        try:
            browser.goto(url)
        except Exception as e:
            _HANDOFF.pop("url", None)
            browser.surface(False)
            return ToolResult.failure(
                f"Could not open {url} to sign in: {e}",
                guidance="Check the address and try again.")

    page = browser.page()
    if page is None:
        _HANDOFF.pop("url", None)
        return ToolResult.failure(
            "The browser closed during sign-in.",
            guidance="Ask the user whether they want to try again.")

    def _still_blocked() -> str:
        pg = browser.page()
        if pg is None:
            return "browser gone"
        return _wall_or_signed_out(pg)

    cleared = (not _still_blocked()) if grace <= 0 else await_human(
        _still_blocked, timeout=grace, poll=poll)

    if cleared:
        _HANDOFF.pop("url", None)
        browser.surface(False)
        return ToolResult.success(
            f"Signed in at {url}. The eagle's browser stays signed in from "
            "now on, so this is not needed again for this site.",
            signed_in=True, url=url)

    # Still blocked. Leave the window up - taking it away mid-login is the one
    # thing guaranteed to waste the user's effort - and hand the turn back so
    # the eagle can actually speak.
    _HANDOFF["url"] = url
    return ToolResult.failure(
        f"A sign-in window for {url} is open on screen and waiting.",
        guidance=("Tell the user the window is open and ask them to sign in, "
                  "then to say when they are done - and call sign_in again "
                  "for the same url to confirm it. Do not claim they are "
                  "signed in until that call succeeds."),
        awaiting_user=True, url=url)


def _clear_consent_walls(browser, grounder: WebGrounder) -> list[str]:
    """Decline cookie/consent walls automatically. Returns what was clicked.

    Not left to the model's judgement, because live testing showed why that
    fails: shown the wall and told which control clears it, the model asked
    the user for permission, then offered to open their own browser instead,
    then repeated the false claim that it could not reach their account. A
    consent banner is not a decision a person deliberates over — they dismiss
    it and carry on — and every turn spent negotiating one is a turn not spent
    on what was actually asked.

    Safe to automate precisely because of what it will click. `cookie_wall_choice`
    only ever names a decline-style control, every one of which
    `irreversible_reason` already permits; the assert below states that
    contract rather than trusting it. An "Accept all"-only wall yields no
    choice, so this does nothing and the user is asked — the eagle never
    consents to tracking on their behalf.

    Loops because Google's is two steps: the banner offers only "More options",
    which opens a second page carrying the actual "Reject all".
    """
    cleared: list[str] = []
    for _step in range(3):
        page = browser.page()
        if page is None:
            break
        choice = cookie_wall_choice(_current_nodes(page), _current_url(page))
        if not choice or choice in cleared:
            break
        # The whole safety argument in one line: only ever a control the
        # consent gate would have allowed anyway.
        if irreversible_reason(choice):
            break
        try:
            _act_with_reresolve(grounder, choice, _gate_click,
                                lambda ref: page.click(ref))
        except Exception:
            break
        cleared.append(choice)
        # The click navigates, and the destination is a single-page app that
        # mounts its content well after the navigation resolves. A fixed pause
        # is the wrong tool: too short and the next read sees a bare footer
        # (measured), too long and every consent wall costs that much. Wait
        # until the page actually has something on it, with a hard cap.
        for _ in range(6):
            try:
                browser.call(lambda pg: pg.wait_for_timeout(500), timeout=20.0)
            except Exception:
                break
            page = browser.page()
            if page is None or len(_current_nodes(page)) >= 20:
                break
    return cleared


#: A sign-in wall is resolvable by the eagle (import a session, or hand over
#: the window once). A verification code or a human check is not — only the
#: user can answer those, so they get the honest "I need you" and no remedy.
def _reassert_target(browser, url: str, cleared: list) -> None:
    """After a consent wall, go back to the page the user actually asked for.

    Clearing Google's wall does not return you to your destination — it
    bounces to `<target>&cbrd=1`, a stripped shell. Measured live on
    youtube.com/playlist?list=LL: the real page carries 58 controls including
    the Romanian sign-in prompt, the bounce page carries 7 and no prompt at
    all, and it landed there in 2 runs out of 3.

    That single fact produced the failure the user hit four times: with no
    sign-in prompt on the page there was nothing for `signed_out_reason` to
    match, so the tool reported ok=True on a page with nothing on it, and the
    model filled the silence with "YouTube keeps that private". Every earlier
    fix in this area was upstream of it and could not have helped - the wall
    detector was correct, the Romanian phrase was in the vocabulary, the
    settle logic was fine. The eagle was reading a different page than the one
    it had been asked about.

    Only after a wall was actually cleared: an unconditional second navigation
    would cost a page load on every open and re-run whatever the first load
    did. Failure here is swallowed on purpose - this is a correction, not the
    mission, and the caller still has a page to report on.
    """
    if not cleared or not url:
        return
    try:
        browser.goto(url)
    except Exception:
        pass


def _is_login_wall(reason: str) -> bool:
    lowered = (reason or "").lower()
    if any(w in lowered for w in ("verification code", "human check")):
        return False
    return any(w in lowered for w in ("sign in", "signed in", "signed-out"))



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
    needs_human = (wall_reason(sense.nodes, current_url)
                   or signed_out_reason(sense.nodes))
    # A cookie/consent wall is not a "needs a human" wall: it can be cleared
    # without consenting to anything, by declining. Surfaced explicitly
    # because the alternative is what happened live — the eagle sat on
    # consent.youtube.com unable to proceed, because the only buttons it
    # considered ("Accept all", "I agree") are refused by the consent gate,
    # and it told the user the task was impossible.
    cookie_choice = cookie_wall_choice(sense.nodes, current_url)

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

    # `sense.truncated` means COLLECT_JS had to stop before it could return
    # every named control (see `collector_truncated` in page.py). Below the
    # empty-nodes check on purpose: a truncated read still found controls to
    # report, so it belongs with the count, not the "found nothing" branch —
    # and `ToolResult.data` never reaches the model (see this module's
    # docstring), so this line in `message` is the only place this can be
    # said at all.
    truncation_note = ""
    if sense.truncated:
        truncation_note = (
            f"Stopped at {len(sense.nodes)} controls — this page has more "
            "than one read returns. Controls near the current viewport were "
            "kept; others may be missing. Scroll and look again to see more.")

    if not sense.nodes:
        lines = [f"Could not read any controls on {current_url or 'the page'}."]
        if escalation_note:
            lines.append(f"({escalation_note})")
        if needs_human:
            lines.append(f"This needs the user — {needs_human} "
                        f"({_NO_HANDOFF_WINDOW}).")
        return ToolResult.failure(
            "\n".join(lines),
            guidance=("The page may be genuinely empty, still loading, or "
                      "the read failed. Call action='look' with "
                      "want_pixels=true, or action='open' with the URL "
                      "again if the page seems gone."),
            tier=sense.tier, controls=[], needs_human=needs_human,
            has_screenshot=sense.screenshot is not None,
            truncated=sense.truncated)

    lines = [f"{len(sense.nodes)} controls on {current_url or 'the page'}:",
             _describe(sense.nodes)]
    if truncation_note:
        lines.append(f"({truncation_note})")
    if escalation_note:
        lines.append(f"({escalation_note})")
    if needs_human:
        # A sign-in wall is the one kind the eagle can actually resolve, so it
        # carries the remedy rather than just the diagnosis. Reporting "this
        # needs you" and stopping is what made the user do the eagle's job:
        # work out that a command existed, and which domains to name.
        if _is_login_wall(needs_human):
            lines.append(f"This page wants the user signed in. "
                         f"{login_remedy(current_url)}")
        else:
            lines.append(f"This needs the user — {needs_human} "
                        f"({_NO_HANDOFF_WINDOW}).")

    if cookie_choice:
        lines.append(
            f"This is a cookie/consent wall. Click '{cookie_choice}' to get "
            f"past it without agreeing to tracking, then carry on — do not "
            f"click Accept.")
    elif needs_human and "consent" in (current_url or "").lower():
        lines.append(
            "This is a consent wall with no decline option the eagle may "
            "click on its own. Tell the user what it is asking and let them "
            "decide.")

    return ToolResult.success(
        "\n".join(lines),
        tier=sense.tier,
        controls=[n.name for n in sense.nodes],
        needs_human=needs_human,
        cookie_wall=cookie_choice,
        auth_domains=auth_domains_for(current_url) if needs_human else [],
        has_screenshot=sense.screenshot is not None,
        truncated=sense.truncated,
    )


class _ConsentBlocked(Exception):
    """Raised by a `gate_check` passed to `_act_with_reresolve` when the
    freshest resolve of `description` — the exact node about to be acted on,
    not whatever was gated a few collects earlier — is something the
    consent or handoff gate refuses.

    Carries a pre-built `message`/`guidance` pair so a block that fires here
    reads exactly like one that fires at the fast, up-front check in
    `_click`/`_type`: the same refusal, whichever seam catches it. See
    `_act_with_reresolve` for why this seam has to exist at all, and
    `_safe_act`/`_actuation_result` for how a "blocked" outcome turns into a
    `ToolResult` without ever reaching the generic error path.
    """

    def __init__(self, message: str, guidance: str) -> None:
        super().__init__(message)
        self.message = message
        self.guidance = guidance


def _act_with_reresolve(grounder: WebGrounder, description: str,
                        gate_check, actuate, prefer=None) -> Any:
    """Re-resolve `description` against the page's CURRENT state, gate the
    node that resolve actually returns, and only then actuate its ref.
    Retried once, from scratch, if the actuation itself still fails.

    This is the fix for a real bug, not a defensive nicety. `data-ae-ref`
    values are positional (`page.py`: `const ref = 'e' + n`) and every
    `collect()` strips and renumbers every one of them from scratch —
    `act_and_verify` -> `wait_for` alone issues at least two before this
    function is ever called (it needs a `previous` read for the `stable`
    check), and each one silently reassigns "e0", "e1", ... to whatever the
    walk finds *now*. A ref captured once, early, and reused later — the
    previous shape of this function — does not merely risk going stale (that
    case was already handled: a stale ref fails fast via `_REF_TIMEOUT_MS`
    and used to be the only case tested). It risks silently pointing at a
    *different* element than the one the caller thinks it does: if the
    page's control list changes between collects (a cookie banner
    auto-dismissing, a row's position shifting), a live, currently-existing
    element can inherit the exact ref string a completely different, no
    longer accurate `WebNode` was holding. The consent gate — checked once,
    up front, against the *first* resolve — would then have approved a
    control that is not the one the browser goes on to click. Reproduced
    end to end through `web_agency()`: a benign "Continue" got gated, an
    irreversible "Complete purchase" got clicked, and the tool reported
    "Clicked 'Continue'" — truthfully describing the STALE node's name, not
    what actually happened.

    The fix is to never trust a `WebNode` resolved anywhere but here.
    Every actuation — click, fill, and the retry after either one fails —
    goes through this one seam, which re-resolves `description` fresh, runs
    `gate_check` against exactly that fresh node (raising `_ConsentBlocked`
    to refuse), and only then reads *that* node's ref and acts. Whatever
    gets clicked is, by construction, whatever was just gated — there is no
    window where an old node's approval is spent on a new node's ref. The
    retry (for the narrower, ordinary case of a ref going stale in the
    instant between this resolve and the actual browser call) re-resolves
    and re-gates from scratch too, rather than reusing anything from the
    failed attempt — a retry can no longer skip the gate the way it used to.

    `gate_check(node)` must raise `_ConsentBlocked` to refuse and return
    normally to proceed. Lives here rather than in `WebGrounder` because it
    is specifically about retrying an *actuation*, not about finding a node
    — `WebGrounder` has no notion of "try to act, and retry if that failed,"
    and no notion of a consent gate either.
    """
    last_exc: Exception | None = None
    for _attempt in range(2):
        # ONE structural read feeds both the match and the gate. Collecting
        # again inside `gate_check` would re-stamp every ref (see `page.py`)
        # and leave `fresh_ref` below pointing at the previous snapshot —
        # which is precisely how the type gate's own `wall_reason` check used
        # to send a fill to a different field than the one it had approved.
        fresh, nodes = grounder.resolve(description, prefer=prefer)
        if fresh is None:
            raise LookupError(f"'{description}' is no longer on the page.")
        gate_check(fresh, nodes)   # raises _ConsentBlocked to refuse
        fresh_ref = ref_of(fresh)
        if not fresh_ref:
            raise LookupError(f"'{description}' has no actionable reference.")
        try:
            actuate(fresh_ref)
            # `fresh` — not the possibly-stale `node` a caller resolved
            # before this function ran — is what actually got acted on.
            # Returned alongside the raw result so `_actuation_result` can
            # report success against the real thing, not an earlier guess.
            return fresh
        except Exception as e:
            last_exc = e
            continue
    assert last_exc is not None
    raise last_exc


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
      - "blocked" `_act_with_reresolve`'s `gate_check` raised
                  `_ConsentBlocked` against the node it actually resolved to
                  act on — nothing was sent to the browser. Checked first,
                  ahead of the generic exception cases below, because
                  `_ConsentBlocked` is deliberately raised as a plain
                  `Exception`, not a `RuntimeError`, so a refusal can never
                  be misread as "the browser thread died."
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
        except _ConsentBlocked as e:
            return ("blocked", (e.message, e.guidance))
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

    if kind == "blocked":
        # `_act_with_reresolve`'s `gate_check` refused the node it actually
        # resolved to act on — the node the up-front, fast pre-check gated
        # (if any) is no longer relevant, because this is the one that would
        # have been clicked or typed into. Not counted as a failure the way
        # the other branches below are — refusing correctly is not the eagle
        # doing something wrong, the same reasoning that keeps the fast
        # up-front refusal in `_click`/`_type` from calling note_failure().
        message, guidance = detail
        return ToolResult.failure(message, guidance=guidance,
                                  control=node.name)

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
    #
    # `detail` here is the `WebNode` `_act_with_reresolve` actually gated and
    # actuated — not `node`, the (possibly stale, possibly a DIFFERENT
    # element by now) node this function was called with. Naming the
    # ACTUALLY-clicked control, not an earlier guess at it, is the other
    # half of the blocker-2 fix: the bug this closes reported "Clicked
    # 'Continue'" while the browser had actually clicked something else.
    acted_node = detail if detail is not None else node
    _SENSE.note_success()
    if outcome["changed"]:
        message = f"{verb_past} '{acted_node.name}' — the page changed."
    else:
        message = (f"{verb_past} '{acted_node.name}'. Nothing on the page "
                  "changed — call action='look' if you expected it to.")
    return ToolResult.success(message, changed=outcome["changed"],
                              control=acted_node.name)


def _gate_click(node, nodes=()) -> None:
    """The consent check `_act_with_reresolve` re-runs against whatever node
    it actually resolved, immediately before clicking it. Mirrors the
    up-front check in `_click` exactly — same wording, same guidance — so a
    refusal reads identically regardless of which of the two catches it.

    Takes the node list for signature parity with `_gate_type`'s gate, so
    `_act_with_reresolve` can hand every gate the single collect it made
    (see there). Clicking's own check needs only the node itself.
    """
    reason = irreversible_reason(node.name, node.role)
    if reason:
        raise _ConsentBlocked(
            f"Refused to click '{node.name}' because {reason}.",
            "Tell the user exactly what this would do and ask them "
            "to confirm it themselves. The eagle does not take "
            "irreversible actions on their behalf.")


def _gate_type(page):
    """Builds the `gate_check` `_act_with_reresolve` re-runs before typing.

    A closure rather than a bare function because `wall_reason` needs the
    *whole page's* current controls, not just the one node being typed
    into — `page` is what lets it re-collect that at gate time, mirroring
    the up-front check in `_type` exactly.
    """
    def gate(node, nodes=()) -> None:
        if node.role.lower() == "password":
            raise _ConsentBlocked(
                f"Refused to type into '{node.name}' because it is a "
                "password field.",
                f"This needs the user to sign in themselves — {_NO_HANDOFF_WINDOW}. "
                "Tell them what the page is asking for; do not type a "
                "password on their behalf.")
        # `nodes` comes from the same collect that produced `node` — see
        # `_act_with_reresolve`. Re-collecting here is what used to
        # invalidate the ref about to be filled.
        reason = wall_reason(nodes, _current_url(page))
        if reason:
            raise _ConsentBlocked(
                f"Refused to type into '{node.name}' — {reason}.",
                f"This needs the user — {_NO_HANDOFF_WINDOW}. Tell them "
                "what the page is asking for and let them handle it "
                "themselves.")
    return gate


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

    # Checked BEFORE anything is sent to the browser — fast-fail UX only.
    # This is NOT the safety boundary: `node` here can be stale by the time
    # `act_and_verify` finishes its own polling (see `_act_with_reresolve`'s
    # docstring for the bug this used to cause), so `_gate_click` re-runs
    # the identical check against whatever node actually gets clicked,
    # immediately before it is clicked. This early check only saves the
    # ~5s `act_and_verify` would otherwise spend polling for actionability
    # on a control that was always going to be refused.
    try:
        _gate_click(node)
    except _ConsentBlocked as e:
        return ToolResult.failure(e.message, guidance=e.guidance)

    page = browser.page()
    outcome = act_and_verify(
        description,
        _safe_act(lambda _el: _act_with_reresolve(
            grounder, description, _gate_click, lambda ref: page.click(ref))),
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

    # Same editable preference the actuation uses, or this fast-fail check
    # rejects on a control the actuation would never have chosen: DuckDuckGo's
    # search input ties at 0.80 with sixteen buttons and links, so without the
    # preference this reported "'Search Duck.ai' is not editable" and never
    # reached the field the user meant.
    node, _nodes = grounder.resolve(
        description, prefer=lambda n: "EDITABLE" in n.states)
    if node is None:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"No field on this page matches '{description}'.",
            guidance=("Call web_agency action='look' to see what is actually "
                      "on the page, then use one of those names."))

    page = browser.page()
    # Checked BEFORE anything is sent to the browser — fast-fail UX only,
    # same caveat as `_click`'s up-front check: `_gate_type(page)` is what
    # actually re-runs immediately before typing, against whatever node the
    # actuation resolves to at that moment, not this one.
    try:
        _gate_type(page)(node)
    except _ConsentBlocked as e:
        return ToolResult.failure(e.message, guidance=e.guidance)

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
            grounder, description, _gate_type(page),
            lambda ref: page.fill(ref, text),
            # Typing into something uneditable is never what was meant, and
            # on a real page the text score alone cannot tell the field from
            # the sixteen buttons and links that share its wording. See
            # `WebGrounder.resolve`.
            prefer=lambda node: "EDITABLE" in node.states)),
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
        cleared = _clear_consent_walls(browser, WebGrounder(browser.page))
        _reassert_target(browser, url, cleared)
        result = _look(browser, want_pixels=False)
        if cleared:
            note = ("Declined tracking on the consent wall ("
                    + ", ".join(repr(c) for c in cleared) + ") and carried on.")
            result = ToolResult.success(note + "\n" + result.message,
                                        **{**result.data,
                                           "consent_cleared": cleared})
        return result

    if action == "sign_in":
        url = str(params.get("url") or "").strip()
        if not url:
            return ToolResult.failure(
                "No URL to sign in at.",
                guidance="Pass url='https://…' with action='sign_in'.")
        if "://" not in url:
            url = "https://" + url
        return _sign_in(browser, url)

    if action == "import_login":
        raw = params.get("domains") or params.get("url") or ""
        domains = raw if isinstance(raw, list) else [
            d for d in str(raw).replace(",", " ").split() if d]
        return _import_login(browser, domains)

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
