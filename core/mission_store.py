"""Keep the mission across a reconnect, and leave a note when it gives up.

Gemini sessions end mid-task. Both of these are from one real MakerWorld
attempt, while the user was in the middle of working through it:

    🔻 GoAway received (time_left=50s) — reconnecting with resume handle
    ⏳ No server response for 26s — session wedged, reconnecting.

A mission held only in memory dies with that socket, and the user starts
again from nothing. Persisting it is what makes the loop survive the network
rather than merely survive a turn — and crucially it carries the ATTEMPTS
across, so the ladder does not restart and re-run rungs already known to fail.

Nothing here undoes anything, deliberately. Every checkpoint reached is worth
keeping: a downloaded file is still downloaded, a completed sign-in is still
completed. Abandonment writes down where it got to and stops.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from core.mission import Attempt, Mission, Step


def _base_dir() -> Path:
    from core import user_paths
    base = Path(user_paths.api_keys_path()).parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_path() -> Path:
    return _base_dir() / "mission.json"


def default_report_path() -> Path:
    return _base_dir() / "mission-stuck.md"


def save(mission: Mission, path: Path | None = None) -> Path:
    p = Path(path or default_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(mission), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(p)          # atomic: a crash mid-write leaves the old one intact
    return p


def load(path: Path | None = None) -> Mission | None:
    """The saved mission, or None. Never raises, and never half-loads.

    A partial mission is worse than none: the eagle would resume from a
    cursor into steps it does not have.
    """
    p = Path(path or default_path())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        goal = raw["goal"]                       # KeyError -> not a mission
        steps = []
        for s in raw.get("steps", []):
            attempts = [Attempt(**a) for a in s.get("attempts", [])]
            steps.append(Step(**{**s, "attempts": attempts}))
        m = Mission(goal=goal, steps=steps, cursor=int(raw.get("cursor", 0)))
        m.status = raw.get("status", m.status)
        m.blocked_reason = raw.get("blocked_reason", "")
        # Without this a reconnect wipes the loop memory and the loop simply
        # resumes — and GoAway happens mid-mission routinely.
        m.places = list(raw.get("places", []) or [])
        return m
    except Exception:
        return None


def clear(path: Path | None = None) -> None:
    try:
        Path(path or default_path()).unlink()
    except Exception:
        pass


def write_stuck_report(mission: Mission, path: Path | None = None) -> Path:
    """What was achieved, where it stopped, and why — as a file to wake up to.

    "It failed" is useless. "These four are done and still in place, it
    stopped on the fifth because the control is not in the accessibility tree
    and vision was rate-limited" is a handover.
    """
    p = Path(path or default_report_path())
    done, total = mission.progress()
    lines = [
        f"# Mission stopped — {datetime.now():%Y-%m-%d %H:%M}", "",
        f"**Goal:** {mission.goal}", "",
        f"**Progress:** {done} of {total} steps completed.",
    ]
    if mission.blocked_reason:
        lines += ["", f"**Stopped because:** {mission.blocked_reason}"]
    lines += ["", "## Steps", ""]
    for s in mission.steps:
        mark = "x" if s.done else " "
        here = "   ← stopped here" if s is mission.current() else ""
        lines.append(f"- [{mark}] {s.intent}{here}")
        for a in s.attempts:
            lines.append(f"  - `{a.strategy}`: "
                         f"{'ok' if a.ok else 'FAILED'} — {a.detail}")
    lines += ["", "---", "",
              "Nothing was undone. Every step ticked above actually happened "
              "and is still in place — a downloaded file is still downloaded, "
              "a sign-in is still signed in. Resuming means continuing from "
              "the step marked above, not starting again."]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
