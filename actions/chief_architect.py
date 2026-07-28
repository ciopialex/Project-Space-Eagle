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

import asyncio
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
    "exceed MAX_AGENTS. "
    "EVERY project ships a README.md at its root — what it is, how to install, how "
    "to run, and the URL to open — as an explicit acceptance criterion on whichever "
    "workstream owns the root. A project a human cannot start is not finished."
)

# Design direction, injected only when the mission has a visible surface.
#
# Learned the hard way: a landing-page mission passed all EIGHT of its
# acceptance criteria and still looked mediocre, because not one criterion
# mentioned appearance — they covered endpoints, responsiveness, aria-live and
# page weight. Agents deliver exactly what is measured, so taste has to be
# measurable or it does not survive the hand-off.
#
# The old spec also banned every external asset "for reliability", which forced
# system fonts and hand-drawn SVG placeholders onto a product whose entire job
# is to look good. Self-hosted fonts and images are just as offline as no
# fonts at all; the ban conflated reliable with plain.
_DESIGN_PRINCIPLES = """
DESIGN (this mission has a user-visible surface — treat it as a product, not a page):
- Turn the design brief below into CONCRETE, CHECKABLE acceptance criteria on the
  UI workstream: name the exact palette hex values, the typeface roles (display vs
  body) and the spacing scale. "Looks good" is not checkable; "ground #F5EFE7,
  accent #8B5A3C, display serif, body sans, 8px scale" is.
- Assets are SELF-HOSTED, never CDN-linked — but self-hosting real webfonts and
  real imagery IS allowed and expected. Do not accept system-font-only output or
  hand-drawn SVG stand-ins where real photography belongs.
- Require a considered layout: deliberate hierarchy, generous whitespace, and a
  hero that states what the business is. No centred-everything default.
- Require both light and dark to be legible if the design implies a theme.
- Accessibility is a floor, not the design goal: 4.5:1 contrast and labelled
  inputs are necessary, not sufficient.
"""

# Words that mean the mission has something a human will look at.
_VISUAL_HINTS = (
    "landing", "page", "website", "site", "web app", "webapp", "dashboard",
    "portfolio", "storefront", "shop", "blog", "ui", "frontend", "front-end",
    "app", "interface", "menu", "brochure",
)


def mission_is_visual(goal: str) -> bool:
    g = (goal or "").lower()
    return any(h in g for h in _VISUAL_HINTS)


def build_architect_prompt(goal: str, project_dir: Path, max_agents: int,
                           available_agents: list[str],
                           design_brief: str = "") -> str:
    """The prompt that turns an ordinary coding tool into the chief architect.

    `design_brief` carries the user's taste — chosen from the aesthetic picker,
    typed freehand, or elicited by voice. It is threaded through to the plan so
    it lands in acceptance criteria rather than evaporating at this boundary,
    which is where the last mission lost its entire visual direction.
    """
    plan_path = Path(project_dir) / STATE_DIR / PLAN_FILE
    schema = PLAN_SCHEMA_DOC.replace("MAX_AGENTS", str(max_agents))
    design = ""
    if design_brief.strip():
        design = (f"\nDESIGN BRIEF (the user's stated taste — honour it exactly, and "
                  f"encode it as acceptance criteria):\n{design_brief.strip()}\n"
                  f"{_DESIGN_PRINCIPLES}\n")
    elif mission_is_visual(goal):
        design = (f"\nNo explicit design brief was given, so CHOOSE a coherent visual "
                  f"direction appropriate to this business and state it explicitly in "
                  f"the plan — do not leave it to the implementing agent's defaults.\n"
                  f"{_DESIGN_PRINCIPLES}\n")
    return (
        f"You are the CHIEF ARCHITECT for an autonomous engineering swarm. Do NOT "
        f"write any application code yet — your ONLY job right now is to plan.\n\n"
        f"GOAL: {goal}\n\n"
        f"MAX_AGENTS: {max_agents}\n"
        f"AVAILABLE_AGENTS (assignee must be one of these): {', '.join(available_agents)}\n\n"
        f"{_ARCHITECT_PRINCIPLES}\n{design}\n"
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


async def run_chief(goal: str, project_dir, max_agents: int = 2,
                    player=None, timeout_s: float = 180.0,
                    design_brief: str = "") -> tuple[dict | None, str]:
    """Spawn the smartest INSTALLED coding tool as chief architect, hand it the
    schema-locked prompt, and wait for its validated plan. Returns (plan, status).

    Reuses the existing agent-spawn pipeline (PTY + screen-watcher auto-approval),
    so the chief can create the plan file autonomously. The chief keeps running
    afterward — it can be re-tasked as worker #1 during execution."""
    from actions.agent_delegation import (AGENT_REGISTRY, agent_available,
                                          first_available_agent, spawn_succeeded)
    project_dir = Path(project_dir)
    (project_dir / STATE_DIR).mkdir(parents=True, exist_ok=True)

    available = [k for k in AGENT_REGISTRY if agent_available(k)]
    chief_key = first_available_agent()
    if not chief_key:
        return None, ("no coding-agent CLI installed (looked for claude, agy, "
                      "opencode, kimi) — install one to plan a build")

    # Clear any stale plan so we only ever read THIS run's output.
    try:
        _plan_path(project_dir).unlink(missing_ok=True)
    except OSError:
        pass

    prompt = build_architect_prompt(goal, project_dir, max_agents, available,
                                    design_brief=design_brief)
    if player:
        player.write_log(f"SYS: 🧭 Chief architect ({_agent_label(chief_key)}) planning: {goal[:60]}")

    chief = AGENT_REGISTRY[chief_key]
    spawn = await chief.run(prompt=prompt, project_dir=project_dir,
                            project_name=project_dir.name, player=player)
    # agent.run returns an honest error if the CLI isn't installed / died on launch.
    if not spawn_succeeded(spawn):
        return None, f"chief failed to launch: {spawn}"

    # The plan poll is a blocking time.sleep loop (up to timeout_s). Run it OFF
    # the event loop so the eagle's audio/mic stay live while the chief thinks.
    plan, status = await asyncio.to_thread(
        read_plan_when_ready, project_dir, max_agents, available, timeout_s)
    if plan is not None:
        plan["_chief"] = chief_key   # remember who planned, for chief-as-worker reuse
    return plan, status


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


def _agent_label(agent_key: str) -> str:
    try:
        from actions.agent_delegation import AGENT_REGISTRY
        a = AGENT_REGISTRY.get(agent_key)
        if a:
            return a.name
    except Exception:
        pass
    return agent_key.replace("_", " ").title()


def render_plan_summary(plan: dict) -> str:
    """The short, voice-friendly plan the eagle SPEAKS at the approval gate.
    Deterministic — no LLM — so what the user hears is exactly what will run."""
    n = plan.get("agent_count", len(plan.get("workstreams", [])))
    parts = [f"Plan: {n} agent{'s' if n != 1 else ''}."]
    for w in plan.get("workstreams", []):
        # First sentence, word-boundary capped — clean for speech, no "builds Build".
        first = (w.get("task", "") or "").split(". ")[0].strip()
        if len(first) > 110:
            first = first[:110].rsplit(" ", 1)[0] + "…"
        parts.append(f"{_agent_label(w.get('assignee', '?'))}: {first.rstrip('.')}.")
    if plan.get("coupled"):
        c = plan.get("contract", {}) or {}
        keys = [k for k in c if k != "ownership"]
        parts.append("Coupled" + (f" — shared interface: {', '.join(keys[:4])}." if keys
                                   else " — shared blackboard on."))
    else:
        parts.append("Independent — no shared blackboard needed.")
    mo = plan.get("merge_order") or []
    if len(mo) > 1:
        parts.append(f"Merge order: {' then '.join(mo)}.")
    parts.append("Approve?")
    return " ".join(parts)


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
