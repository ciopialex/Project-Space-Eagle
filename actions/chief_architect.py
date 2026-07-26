"""Chief Architect — the planning trace of the swarm.

The eagle-brain (free) is a CONDUCTOR, not a composer: for a non-trivial build it
spawns the smartest INSTALLED coding tool as a "chief", which decomposes the goal
into a structured plan and writes it to disk. The eagle reads that plan, shows it
to the human for approval, then drives the existing orchestrator to execute it.

This module is the DETERMINISTIC substrate — schema, prompt, validation, and the
plan-file handoff protocol. It has no LLM cognition of its own; the intelligence
is borrowed from the chief instance. Everything here is testable without a live
agent, which is the point: nail the circuit, and any brain flowing through it
produces a safe, well-formed plan.

Handoff protocol (robust, not naive polling):
  1. chief writes  <dir>/.space_eagle/plan.json.tmp  then ATOMIC-renames to plan.json
     (a rename is atomic → the eagle never reads a half-written file), and
  2. prints the token PLAN_READY to its terminal as a done-signal.
The eagle waits for the file (bounded poll as backstop) + validates it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PLAN_READY_TOKEN = "PLAN_READY"
STATE_DIR = ".space_eagle"
PLAN_FILE = "plan.json"

# The mold handed to the chief so it can't spit out the wrong shape.
PLAN_SCHEMA_DOC = """{
  "goal":        "<one-line restatement of the mission>",
  "agent_count": <int, 1..MAX_AGENTS>,
  "coupled":     <bool — do the workstreams share an interface/files?>,
  "blackboard":  <bool — MUST be true if coupled, false if truly independent>,
  "contract":    { <REQUIRED when coupled: the frozen interface both sides build to —
                    endpoints, data shapes, and "ownership": {"<id>": "<dir/glob>"} > },
  "workstreams": [
    { "id":         "<short id, e.g. 'api'>",
      "assignee":   "<one of AVAILABLE_AGENTS>",
      "task":       "<what this agent builds, in 1-3 sentences>",
      "owns":       ["<dir/glob this agent exclusively edits>"],
      "acceptance": ["<checkable done-criteria>", "..."] }
  ],
  "merge_order":  ["<workstream id>", "..."]   // dependencies first
}"""

# Defaults the architect should lean on — the "frictionless + reliable + secure"
# principle, stated once so every plan inherits it.
_ARCHITECT_PRINCIPLES = (
    "Principles: prefer the SIMPLEST reliable stack (e.g. SQLite over a DB server "
    "unless scale demands it — frictionless, serverless, ACID); keep workstreams "
    "as INDEPENDENT as possible (independent → blackboard off, less coordination); "
    "only mark coupled when they genuinely share an interface, and then define that "
    "interface fully in `contract` so the agents never have to negotiate live; never "
    "exceed MAX_AGENTS."
)


def build_architect_prompt(goal: str, project_dir: Path, max_agents: int,
                           available_agents: list[str]) -> str:
    """The prompt that turns an ordinary coding tool into the chief architect."""
    plan_path = Path(project_dir) / STATE_DIR / PLAN_FILE
    schema = PLAN_SCHEMA_DOC.replace("MAX_AGENTS", str(max_agents))
    return (
        f"You are the CHIEF ARCHITECT for an autonomous engineering swarm. Do NOT "
        f"write any application code yet — your ONLY job right now is to plan.\n\n"
        f"GOAL: {goal}\n\n"
        f"MAX_AGENTS: {max_agents}\n"
        f"AVAILABLE_AGENTS (assignee must be one of these): {', '.join(available_agents)}\n\n"
        f"{_ARCHITECT_PRINCIPLES}\n\n"
        f"Decompose the goal into at most {max_agents} workstreams and produce a plan "
        f"EXACTLY in this JSON schema:\n{schema}\n\n"
        f"Write the plan to '{plan_path}' — write to '{plan_path}.tmp' first, then "
        f"rename it to '{plan_path}' (atomic). Create the '{STATE_DIR}' folder if "
        f"needed. After the file is renamed, print this token on its own line so I "
        f"know you are done:\n{PLAN_READY_TOKEN}\n\n"
        f"Output ONLY valid JSON in the file — no markdown fences, no commentary."
    )


def _plan_path(project_dir: Path) -> Path:
    return Path(project_dir) / STATE_DIR / PLAN_FILE


def validate_plan(plan: dict, max_agents: int,
                  available_agents: list[str]) -> tuple[bool, str]:
    """Deterministic gate: a plan only executes if it's well-formed and safe.
    Returns (ok, reason)."""
    if not isinstance(plan, dict):
        return False, "plan is not a JSON object"

    n = plan.get("agent_count")
    ws = plan.get("workstreams")
    if not isinstance(n, int) or not (1 <= n <= max_agents):
        return False, f"agent_count must be an int in 1..{max_agents} (got {n!r})"
    if not isinstance(ws, list) or len(ws) != n:
        return False, f"workstreams must be a list of exactly agent_count ({n}) items"

    ids: list[str] = []
    avail = set(available_agents)
    for i, w in enumerate(ws):
        if not isinstance(w, dict):
            return False, f"workstream[{i}] is not an object"
        for key in ("id", "assignee", "task"):
            if not w.get(key) or not isinstance(w[key], str):
                return False, f"workstream[{i}] missing required string '{key}'"
        if w["assignee"] not in avail:
            return False, (f"workstream[{i}] assignee '{w['assignee']}' is not an "
                           f"available agent {sorted(avail)}")
        ids.append(w["id"])
    if len(set(ids)) != len(ids):
        return False, f"duplicate workstream ids: {ids}"

    coupled = bool(plan.get("coupled"))
    if coupled and not plan.get("contract"):
        return False, "coupled plan must include a non-empty 'contract'"
    if coupled and not plan.get("blackboard", True):
        return False, "coupled plan must have blackboard=true"

    mo = plan.get("merge_order") or ids
    if not set(mo).issubset(set(ids)):
        return False, f"merge_order {mo} references unknown workstream ids"

    return True, "ok"


def read_plan_when_ready(project_dir: Path, max_agents: int,
                         available_agents: list[str],
                         timeout_s: float = 180.0) -> tuple[dict | None, str]:
    """Backstop poll for the plan file (the chief also prints PLAN_READY, which the
    eagle's screen-watcher can catch to short-circuit this wait). Returns
    (plan, status) where status is 'ok' | 'timeout' | 'invalid: <reason>' |
    'unparseable'."""
    path = _plan_path(project_dir)
    deadline = time.monotonic() + timeout_s
    last_mtime = None
    while time.monotonic() < deadline:
        if path.exists():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = None
            # Only parse once it's stable (atomic rename means it's whole, but a
            # tool that appends could still be mid-write — require a settled mtime).
            if mtime is not None and mtime == last_mtime:
                try:
                    plan = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None, "unparseable"
                ok, reason = validate_plan(plan, max_agents, available_agents)
                return (plan, "ok") if ok else (None, f"invalid: {reason}")
            last_mtime = mtime
        time.sleep(0.5)
    return None, "timeout"
