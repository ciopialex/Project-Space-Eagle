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

def _start(params: dict) -> ToolResult:
    goal = str(params.get("goal") or "").strip()
    if not goal:
        return ToolResult.failure(
            "A mission needs a goal.",
            guidance="Ask the user what they want done, in their own words, "
                     "then call start again with it as 'goal'.")

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
                guidance="This is not a limit of the task. Tell the user it "
                         "will work again shortly and offer to retry; do not "
                         "say the goal is impossible.")
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
    outcome = attempt(step, m, _runners())

    if outcome.ok:
        m.advance()
        store.save(m, _store_path())
        done, total = m.progress()
        if m.status == DONE:
            return ToolResult.success(
                f"{step.intent} — done. Mission done: all {total} steps of "
                f"“{m.goal}”.")
        return ToolResult.success(
            f"{step.intent} — done ({done} of {total}). "
            f"Next: {m.current().intent}")

    # Out of ways. Not a failed mission — a mission that needs a better plan
    # or a human. Everything tried is on the step, ready for the handoff.
    m.block(outcome.detail or "every approach failed")
    store.save(m, _store_path())
    tried = ", ".join(f"{a.strategy} ({a.detail[:40]})" for a in step.attempts)
    return ToolResult.failure(
        f"Stuck on: {step.intent}. Tried {tried or 'nothing available'}.",
        guidance=("Every way of doing this step has been tried and none "
                  "worked. Do NOT retry it. Either ask an outside agent for a "
                  "new plan using the mission handoff, or tell the user "
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
