"""Run a goal as a sequence of small, verified steps. `next` walks one step through
the ladder and advances only if it actually worked; the model calls it until done
or blocked. No approval prompts between steps: advance() runs only on a confirmed
step. The store remembers failed rungs across a reconnect.

World-touching seams (_store_path, _report_path, _plan_locally, _runners) are
module attributes so tests need no browser, model or config.
"""
from __future__ import annotations

from pathlib import Path

from core import mission_store as store
from core.mission import BLOCKED, DONE, Mission, Step
from core.mission_ladder import attempt
from core.tool_result import ToolResult

_ACTIONS = ("start", "next", "status", "abandon")

# Names the model reaches for that mean one of the above.
_ALIASES = {"plan": "start", "begin": "start", "go": "next", "continue": "next",
            "step": "next", "resume": "next", "progress": "status",
            "stop": "abandon", "cancel": "abandon"}


def _store_path() -> Path:
    return store.default_path()


def _report_path() -> Path:
    return store.default_report_path()


def _plan_locally(goal: str):
    """Break the goal into steps. Where a researched blueprint or a delegated plan
    (core/mission_handoff.context_pack) would plug in.
    """
    from core.mission_planner import plan
    return plan(goal)


def _release_browsers() -> None:
    """Leave nothing running that this mission started, however it ended. Best-
    effort, never raises: a leaked Chrome holds the profile lock.
    """
    try:
        from actions.browser_control import _registry
        _registry.close_all()
    except Exception as e:
        print(f"[Mission] could not release browsers: {e}")

    # The eagle's own browser closes separately.
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
    """Fingerprint the user's window, or None when there is none (unknown, not
    unchanged).
    """
    try:
        from core.session_port import peek_window
        from core.world_state import signature_of
        port, _ = peek_window()  # Peek, never user_window.
        return None if port is None else signature_of(port)
    except Exception:
        return None


def _runners():
    """strategy name -> callable(step) -> (ok, detail). Bound lazily so importing
    starts no browser.
    """
    from core.mission_runners import build_runners
    return build_runners()


def _load() -> Mission | None:
    return store.load(_store_path())


def _describe(m: Mission) -> str:
    done, total = m.progress()
    if m.status == DONE:
        return f"Mission done — all {total} steps of “{m.goal}”."
    step = m.current()
    where = f" Now: {step.intent}" if step else ""
    return f"“{m.goal}” — {done} of {total} steps done.{where}"


def _steps_from(raw) -> list[Step]:
    """Steps the caller supplied: a list, or one string separated by newlines or
    pipes.
    """
    from core.mission_handoff import parse_plan
    if not raw:
        return []
    if isinstance(raw, str):
        parsed = parse_plan(raw)
        if parsed:
            return parsed
        # Models also separate steps with pipes.
        import re as _re
        raw = [ln for ln in _re.split(r"[\n|;]+|(?<!:)//(?!/)", raw)
               if ln and ln.strip()]
    out = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        # Same parser as delegated steps, so urls and quoted text are extracted the same
        # way.
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

    # A running mission for the same goal is resumed, never restarted. Blocked and done
    # missions restart.
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

    # Steps the model hands over cost nothing; planning here costs a generate_content
    # call from a small daily budget. It is the fallback.
    raw = _steps_from(params.get("steps"))
    if not raw:
        raw = _plan_locally(goal)
    steps = [s if isinstance(s, Step) else Step(intent=str(s).strip())
             for s in (raw or []) if s]
    steps = [s for s in steps if s.intent.strip()]

    # An unexpected separator degrades to "cannot plan this", never to one step.
    from core.mission_ladder import is_compound
    blobs = [s.intent for s in steps if is_compound(s.intent)]
    if blobs:
        return ToolResult.failure(
            f"That is not a step, it is several: {blobs[0][:110]!r}",
            guidance=("Send the steps again, ONE action per line, each "
                      "naming one thing you can see and do. 'Open the page', "
                      "then 'Click the search box', then 'Type watch stand' — "
                      "not all of them in one line."))

    if not steps:
        # A planner that could not reach the model has not decided the goal is
        # impossible.
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

    # One yes, up front, for the whole mission: an irreversible step ("Submit", "Buy",
    # "Pay") is found on the plan before anything runs.
    confirmed = bool(params.get("confirm"))
    if not confirmed:
        risky = _first_irreversible_step(steps)
        if risky is not None:
            from actions.grounding.web.consent import irreversible_reason
            why = irreversible_reason(risky.intent) or "it commits something"
            # ok=False: the mission did not start, and ok=True would read as "call
            # next".
            return ToolResult.failure(
                f"“{goal}” includes a step that cannot be undone once it "
                f"runs: “{risky.intent}” — {why}. Nothing has started.",
                guidance=(
                    "Ask the user, in these words or close to them: "
                    "\"Are you sure this is a safe site to do this on?\" "
                    "Do NOT start the mission until they answer. If they say "
                    "yes, call start again with the SAME goal and steps and "
                    "confirm=true — every step in THIS mission will then run "
                    "without asking again, including the one named above. If "
                    "they say no, do not call start again; tell them the "
                    "mission was not started."),
                needs_confirmation=True, risky_step=risky.intent)

    m = Mission(goal=goal, steps=steps, authorized=confirmed)
    store.save(m, _store_path())
    listed = "; ".join(s.intent for s in steps[:6])
    return ToolResult.success(
        f"Planned “{goal}” as {len(steps)} steps: {listed}"
        + (" …" if len(steps) > 6 else "")
        + " Call next to begin.",
        steps=len(steps))


def _first_irreversible_step(steps: list[Step]) -> Step | None:
    """The first click step that would refuse at the consent gate, or None. Uses
    web_agency's own function. Only click steps: a disk step's wording would trip
    the web vocabulary.
    """
    from actions.grounding.web.consent import irreversible_reason
    from core.mission_ladder import kind_of
    for s in steps:
        if kind_of(s) == "click" and irreversible_reason(s.intent or s.target or ""):
            return s
    return None


def _next(params: dict) -> ToolResult:
    m = _load()
    if m is None:
        return ToolResult.failure(
            "There is no mission running.",
            guidance="Call start with a goal first.")
    if m.status == DONE or m.current() is None:
        return ToolResult.success(f"Mission done — “{m.goal}”.")

    # A blocked mission stays blocked: every rung was already tried.
    if m.status == BLOCKED:
        done, total = m.progress()
        return ToolResult.failure(
            f"“{m.goal}” is blocked at step {done + 1} of {total}: "
            f"{m.blocked_reason}",
            guidance=("Do NOT call next again — every way of doing that step "
                      "has been tried. Either call start with a DIFFERENT "
                      "plan, call abandon to stop cleanly, or tell the user "
                      "which step is stuck and ask them to do that one "
                      "thing."))

    step = m.current()
    outcome = attempt(step, m, _runners(), observe=_observe)

    # Record where the step left us before the verdict, so a loop is caught.
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
        # moved is False: advance, but carry the doubt with the result.
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

    # Out of ways: needs a better plan or a human. Everything tried is on the step.
    m.block(outcome.detail or "every approach failed")
    store.save(m, _store_path())
    # Blocked is terminal too: release the browsers.
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
