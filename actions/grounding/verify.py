"""Look, act, look again.

Reporting "clicked" without checking whether anything happened is the most
common way an agent lies to its operator. A person glances at the screen after
they act; so does the eagle.
"""
from __future__ import annotations

import time
from typing import Callable

from actions.grounding.base import Element
from actions.grounding.waiting import wait_for


def observe(description: str, resolver) -> dict | None:
    """Everything the eagle can currently perceive about an element."""
    try:
        el = resolver.find(description)
    except Exception:
        return None
    if el is None:
        return None
    return {"bounds": el.bounds, "states": set(el.states), "value": el.value}


def act_and_verify(description: str,
                   act: Callable[[Element], object],
                   *,
                   resolver,
                   action: str = "click",
                   settle: float = 0.15,
                   sleep: Callable[[float], None] = time.sleep,
                   **wait_kwargs) -> dict:
    """Wait until actionable, act, then re-observe and report the truth."""
    waited = wait_for(description, action, resolver=resolver,
                      sleep=sleep, **wait_kwargs)
    if not waited.ok or waited.element is None:
        return {
            "acted": False, "changed": False,
            "before": None, "after": None, "result": None,
            "detail": (f"never became actionable for {action}: "
                       f"{waited.failed_check} (after {waited.elapsed_ms:.0f}ms, "
                       f"{waited.attempts} attempts)"),
        }

    element = waited.element
    before = {"bounds": element.bounds,
              "states": set(element.states),
              "value": element.value}

    result = act(element)
    sleep(settle)
    after = observe(description, resolver)

    changed = (after is None) or (after != before)
    detail = ("observed a change after acting" if changed
              else "acted, but no observable change — it may not have worked")

    return {"acted": True, "changed": changed, "before": before,
            "after": after, "result": result, "detail": detail}
