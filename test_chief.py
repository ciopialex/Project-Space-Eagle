#!/usr/bin/env python3
"""Live test of the Chief Architect PLANNING step only (no execution yet).

    .venv/bin/python test_chief.py "make a todo app with a backend" /tmp/aetest

Spawns the smartest installed coding tool as chief architect, waits for it to
write a validated plan.json, and prints the plan + the spoken approval summary.
A terminal window opens for the chief; auto-approval handles its prompts.
"""
import asyncio
import json
import sys

from actions.chief_architect import run_chief, render_plan_summary


async def main() -> int:
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Make a Flappy Bird clone with an online leaderboard"
    dirp = sys.argv[2] if len(sys.argv) > 2 else "/tmp/aethelark_chief_test"
    print(f"Goal : {goal}")
    print(f"Dir  : {dirp}")
    print("Spawning chief architect… (a console opens; it should write .space_eagle/plan.json)\n")

    plan, status = await run_chief(goal, dirp, max_agents=2, player=None, timeout_s=180)
    print(f"\nSTATUS: {status}")
    if plan:
        clean = {k: v for k, v in plan.items() if not k.startswith("_")}
        print("PLAN:\n" + json.dumps(clean, indent=2))
        print("\nSPOKEN APPROVAL:\n  " + render_plan_summary(plan))
        return 0
    print("No valid plan produced.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
