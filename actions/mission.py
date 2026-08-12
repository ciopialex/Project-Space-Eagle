"""Run a goal as a sequence of small, verified steps.

`next` is the whole design in one call: load the mission, walk ONE step
through the ladder, and advance only if that step actually worked. The model
calls it until the mission reports done or blocked. It cannot thrash — the
ladder refuses a rung that already failed, and the store remembers that across
a reconnect, which is the difference between surviving the network and merely
surviving a turn.

There are no approval prompts between steps. That is deliberate: a leader
delegates and trusts. What earns the trust is that `advance()` is only ever
called on a confirmed step — never because a tool returned.

Every seam that touches the world is a module attribute (`_store_path`,
`_report_path`, `_plan_locally`, `_runners`) so the rules can be tested
without a browser, a model, or the real config directory.
"""
from __future__ import annotations

from pathlib import Path

from core import mission_store as store
from core.mission import BLOCKED, DONE, Mission, Step
from core.mission_ladder import attempt
from core.tool_result import ToolResult

_ACTIONS = ("start", "next", "status", "abandon")

#: Names the model reaches for that mean one of the above. Live, it called
#: `action='plan'` before `action='start'` — refused correctly, then
#: self-corrected, at the cost of a full round trip. Same lesson as
#: `type_text`: cheaper to accept the name than to be right about it.
_ALIASES = {"plan": "start", "begin": "start", "go": "next", "continue": "next",
            "step": "next", "resume": "next", "progress": "status",
            "stop": "abandon", "cancel": "abandon"}


# ── seams ───────────────────────────────────────────────────────────────────

def _store_path() -> Path:
    return store.default_path()


def _report_path() -> Path:
    return store.default_report_path()


def _plan_locally(goal: str):
    """Break the goal into steps.

    The other two sources the user described plug in HERE and nowhere else: a
    researched blueprint (look up the documented path before guessing at it),
    and a delegated plan from an outside agent, which
    `core/mission_handoff.context_pack` already knows how to ask for.
    """
    from core.mission_planner import plan
    return plan(goal)


def _release_browsers() -> None:
    """Leave nothing running that this mission started.

    Best-effort and never raises: a mission that SUCCEEDED must not be
    reported as failed because a browser would not close. But it must be
    attempted every single time a mission ends, however it ends — a leaked
    Chrome also holds the profile lock, and that is what previously made the
    eagle's own browser refuse to start and blame Playwright for it.
    """
    try:
        from actions.browser_control import _registry
        _registry.close_all()
    except Exception as e:
        print(f"[Mission] could not release browsers: {e}")

    # Belt and braces: the eagle's own headless browser is a separate process
    # from the user-facing one and closes through its own door.
    try:
        from actions.web_agency import web_agency
        web_agency(parameters={"action": "close"})
    except Exception:
        pass
    try:
        from core.session_port import reset_launch_budget
        reset_launch_budget()
    except Exception:
        pass


def _observe():
    """Fingerprint whatever the user's window is showing, or None.

    None when there is no window to look at — which is a real answer and NOT
    "nothing changed". The ladder treats it as unknown for exactly that
    reason.
    """
    try:
        from core.session_port import peek_window
        from core.world_state import signature_of
        port, _ = peek_window()          # peek, never user_window: see its docstring
        return None if port is None else signature_of(port)
    except Exception:
        return None


def _runners():
    """strategy name -> callable(step) -> (ok, detail).

    Bound lazily so importing this module never starts a browser.
    """
    from core.mission_runners import build_runners
    return build_runners()


# ── helpers ─────────────────────────────────────────────────────────────────

def _load() -> Mission | None:
    return store.load(_store_path())


def _describe(m: Mission) -> str:
    done, total = m.progress()
    if m.status == DONE:
        return f"Mission done — all {total} steps of “{m.goal}”."
    step = m.current()
    where = f" Now: {step.intent}" if step else ""
    return f"“{m.goal}” — {done} of {total} steps done.{where}"


# ── the tool ────────────────────────────────────────────────────────────────

def _steps_from(raw) -> list[Step]:
    """Steps the CALLER supplied — a list, or one newline-separated string."""
    from core.mission_handoff import parse_plan
    if not raw:
        return []
    if isinstance(raw, str):
        parsed = parse_plan(raw)
        if parsed:
            return parsed
        raw = [ln for ln in raw.splitlines() if ln.strip()]
    out = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        # Reuse the same parser so a supplied step gets its url and quoted
        # text extracted exactly like a delegated one.
        parsed = parse_plan(f"1. {text}")
        out.append(parsed[0] if parsed else Step(intent=text))
    return out


def _start(params: dict) -> ToolResult:
    goal = str(params.get("goal") or "").strip()
    if not goal:
        return ToolResult.failure(
            "A mission needs a goal.",
            guidance="Ask the user what they want done, in their own words, "
                     "then call start again with it as 'goal'.")

    # A mission for this same goal that is still RUNNING is resumed, never
    # restarted. Reported live as "it opens the makerworld page again and
    # again": a blocked or slow mission makes the model call start again to be
    # helpful, and every start threw the old one away, reset the cursor to
    # zero, and re-ran step one. Forever.
    #
    # Blocked and done missions DO restart — a stuck mission resumed is just
    # the same wall again, and a finished goal must stay repeatable.
    existing = _load()
    if (existing is not None
            and existing.status == "running"
            and existing.goal.strip().lower() == goal.strip().lower()
            and existing.current() is not None):
        done, total = existing.progress()
        return ToolResult.success(
            f"Already running “{goal}” — {done} of {total} steps done, "
            f"resuming at: {existing.current().intent}. Call next.",
            resumed=True)

    # The brain is ALREADY thinking about this goal. Steps it hands over cost
    # nothing; a second generate_content call costs one of a DAILY budget of
    # twenty - shared with vision, summarising and code_helper - and measured
    # 10.7 seconds before the tool even began. Planning here is the fallback,
    # not the path.
    raw = _steps_from(params.get("steps"))
    if not raw:
        raw = _plan_locally(goal)
    steps = [s if isinstance(s, Step) else Step(intent=str(s).strip())
             for s in (raw or []) if s]
    steps = [s for s in steps if s.intent.strip()]
    if not steps:
        # A planner that could not REACH the model has not decided the goal is
        # impossible. Saying so is the difference between the user waiting
        # forty seconds and the user being told it cannot be done.
        from core import mission_planner
        why = getattr(mission_planner, "last_error", "")
        if why:
            return ToolResult.failure(
                f"Could not plan “{goal}” — {why}.",
                guidance="DO NOT retry this call — it will fail the same way "
                         "and each attempt costs another request. Instead, "
                         "work out the steps YOURSELF and call start again "
                         "with them in 'steps'. That needs no extra request. "
                         "The goal is not impossible; only the extra planning "
                         "call is unavailable.")
        return ToolResult.failure(
            f"Could not break “{goal}” into steps.",
            guidance="Nothing has started. Either ask the user for more "
                     "detail about what they want, or do it with a single "
                     "tool if it really is one action.")

    m = Mission(goal=goal, steps=steps)
    store.save(m, _store_path())
    listed = "; ".join(s.intent for s in steps[:6])
    return ToolResult.success(
        f"Planned “{goal}” as {len(steps)} steps: {listed}"
        + (" …" if len(steps) > 6 else "")
        + " Call next to begin.",
        steps=len(steps))


def _next(params: dict) -> ToolResult:
    m = _load()
    if m is None:
        return ToolResult.failure(
            "There is no mission running.",
            guidance="Call start with a goal first.")
    if m.status == DONE or m.current() is None:
        return ToolResult.success(f"Mission done — “{m.goal}”.")

    step = m.current()
    outcome = attempt(step, m, _runners(), observe=_observe)

    # Where did that leave us? Recorded before the verdict, so a step that
    # "succeeded" into a place we have been three times is still caught.
    here = _observe()
    m.note_place(here)
    if m.going_in_circles(here):
        m.block(f"back at the same page for the {m.times_at(here)}rd time "
                f"with no progress")
        store.save(m, _store_path())
        _release_browsers()
        return ToolResult.failure(
            f"Going in circles — “{step.intent}” has returned to the same "
            f"page {m.times_at(here)} times without progress.",
            guidance=("Stop. Repeating this will not work. Either call start "
                      "with a DIFFERENT plan that reaches the goal another "
                      "way, or tell the user which step is stuck and what you "
                      "have already tried."))

    if outcome.ok:
        # `ok` means the call worked. `moved is False` means the page did not
        # react to it — the step is still advanced (the rung genuinely did its
        # job, and a step CAN legitimately change nothing), but the doubt
        # travels with the result instead of being swallowed.
        m.advance()
        store.save(m, _store_path())
        done, total = m.progress()
        if m.status == DONE:
            _release_browsers()
            return ToolResult.success(
                f"{step.intent} — done. Mission done: all {total} steps of "
                f"“{m.goal}”.")
        doubt = ("" if outcome.moved is not False
                 else "  (nothing on the page changed — if the next step "
                      "fails, this one may not have taken effect)")
        return ToolResult.success(
            f"{step.intent} — done ({done} of {total}).{doubt} "
            f"Next: {m.current().intent}")

    # Out of ways. Not a failed mission — a mission that needs a better plan
    # or a human. Everything tried is on the step, ready for the handoff.
    m.block(outcome.detail or "every approach failed")
    store.save(m, _store_path())
    # Blocked is terminal too. Measured: a mission that blocked left EIGHT
    # browsers running, because release only ran on done and abandon.
    _release_browsers()
    tried = ", ".join(f"{a.strategy} ({a.detail[:40]})" for a in step.attempts)
    return ToolResult.failure(
        f"Stuck on: {step.intent}. Tried {tried or 'nothing available'}.",
        guidance=("Every way of doing this step has been tried and none "
                  "worked. Do NOT retry it, and do NOT call start again with "
                  "the same goal and the same steps — that re-runs the steps "
                  "already done and loops. Either call start with a "
                  "DIFFERENT plan that avoids this step, or tell the user "
                  "exactly which step blocked and why, and ask them to do "
                  "that one thing."))


def _status(params: dict) -> ToolResult:
    m = _load()
    if m is None:
        return ToolResult.success("No mission is running.")
    if m.status == BLOCKED:
        return ToolResult.failure(
            f"{_describe(m)} Blocked: {m.blocked_reason}",
            guidance="Get a new plan or ask the user to clear that one step.")
    return ToolResult.success(_describe(m))


def _abandon(params: dict) -> ToolResult:
    m = _load()
    if m is None:
        return ToolResult.success("No mission was running.")
    m.abandon()
    _release_browsers()
    report = store.write_stuck_report(m, _report_path())
    store.clear(_store_path())
    done, total = m.progress()
    return ToolResult.success(
        f"Stopped “{m.goal}” after {done} of {total} steps. "
        f"What was done is still in place; written up in {report.name}.")


_HANDLERS = {"start": _start, "next": _next,
             "status": _status, "abandon": _abandon}


def mission(parameters: dict | None = None, player=None,
            session_memory=None, response=None) -> ToolResult:
    params = parameters or {}
    action = str(params.get("action") or "").lower().strip()
    action = _ALIASES.get(action, action)

    handler = _HANDLERS.get(action)
    if handler is None:
        return ToolResult.failure(
            f"'{action}' is not a mission action. Use one of: "
            f"{', '.join(_ACTIONS)}.",
            guidance="Nothing ran. Call this again with a listed action.")

    try:
        result = handler(params)
    except Exception as e:
        return ToolResult.failure(
            f"The mission tool hit an unexpected error: {e}",
            guidance="Call status to see where the mission stands before "
                     "doing anything else.")

    if player is not None:
        try:
            player.write_log(f"[Mission] {action}: {result.message[:80]}")
        except Exception:
            pass          # a UI that cannot log must not fail the mission
    return result
