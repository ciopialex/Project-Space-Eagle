#!/usr/bin/env python3
"""MANUAL live end-to-end swarm harness: PLAN → approve → EXECUTE.

NOT a unit test, despite the old `test_swarm.py` name. It spawns REAL billable
coding agents, opens terminal windows, and blocks on `input()` at the approval
gate, so it can never run unattended or in CI. The free automated coverage of
the same logic lives in `tests/`.

    .venv/bin/python manual_swarm.py "make a todo app with a backend" /tmp/aeswarm

Spawns the chief architect, prints the plan + the spoken approval summary, asks
you to approve (the human gate), then executes the plan — one isolated git
worktree per workstream, each agent spawned with its contract-bound orders.
Terminal windows open for the workers; recognised-safe prompts are answered
automatically, dangerous ones are HELD for you (see core/prompt_reflex.py).
Afterwards it prints the live swarm status (use it to watch, review, or stop).
"""
import asyncio
import json
import sys

from actions.chief_architect import run_chief, render_plan_summary
from actions.swarm_orchestrator import execute_plan, get_orchestrator


async def main() -> int:
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Make a todo app with a REST backend and a web UI"
    dirp = sys.argv[2] if len(sys.argv) > 2 else "/tmp/aethelark_swarm_test"
    print(f"Goal : {goal}")
    print(f"Dir  : {dirp}\n")

    # 1) PLAN — chief architect writes + validates .space_eagle/plan.json
    print("① Spawning chief architect…")
    plan, status = await run_chief(goal, dirp, max_agents=2, player=None,
                                   timeout_s=180)
    if not plan:
        print(f"✗ No valid plan produced ({status}).")
        return 1
    clean = {k: v for k, v in plan.items() if not k.startswith("_")}
    print("\nPLAN:\n" + json.dumps(clean, indent=2))
    print("\n🦅 SPOKEN APPROVAL:\n  " + render_plan_summary(plan))

    # 2) HUMAN GATE — the eagle never executes without a yes.
    if "--yes" not in sys.argv:
        ans = input("\n② Approve and execute this plan? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted — plan left at .space_eagle/plan.json.")
            return 0

    # 3) EXECUTE — one worktree per workstream, contract-bound spawns.
    print("\n③ Executing plan…")
    report = await execute_plan(plan, dirp, player=None)
    print("\nEXECUTION:\n  " + report)

    # 4) STATUS — what's live now.
    orch = get_orchestrator(dirp)
    print("\nSWARM STATUS:\n" + orch.status())
    print("\n(Agents run in the background. Re-check status, or review/merge, "
          "via the swarm tool.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
