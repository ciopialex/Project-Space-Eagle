"""Where held prompts go.

`core.prompt_reflex` decides a terminal prompt is dangerous or unrecognised and
refuses to answer it. That refusal is only half a system: the agent is now
blocked, and without somewhere for the question to GO it blocks forever, which
is a different failure from the one we set out to fix.

This is the destination — deliberately small and process-local, so it works
before the Mission Controller exists and can be lifted into it later without
changing callers.

Each escalation is one pending question:

    agent is stuck on <prompt>, because <rule>, waiting for a human yes/no

It is logged loudly to the terminal (this is the moment the operator most needs
to see), recorded so the HUD and voice layer can read it, and resolvable by
exactly one authority — a human, relayed through the eagle. Nothing here
decides anything itself; that is the entire point of the reflex refusing.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field

_counter = itertools.count(1)
_lock = threading.Lock()
_pending: dict[str, "Escalation"] = {}
_resolved: list["Escalation"] = []


@dataclass
class Escalation:
    """One blocked agent awaiting authorization."""

    id: str
    agent: str
    rule_id: str
    reason: str
    excerpt: str                      # what the agent is actually asking
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    verdict: str | None = None        # "allow" | "deny"
    resolved_by: str = ""

    @property
    def waiting_s(self) -> float:
        return time.time() - self.created_at

    def question(self) -> str:
        """Phrased for the eagle to SPEAK — plain language, no rule codes."""
        return (f"{self.agent} wants to do something I won't approve on my own: "
                f"{self.reason}. It's asking: {self.excerpt[:160]}. Allow it?")


def _excerpt(region: str) -> str:
    """The prompt line the agent is blocked on, not the whole screen."""
    lines = [ln.strip() for ln in (region or "").splitlines() if ln.strip()]
    return " / ".join(lines[-3:])[:300] if lines else "(no visible prompt)"


def raise_escalation(agent: str, decision, region: str, player=None,
                     on_new=None) -> "Escalation":
    """Record a held prompt and surface it everywhere a human might be looking."""
    esc = Escalation(
        id=f"esc{next(_counter)}",
        agent=agent,
        rule_id=getattr(decision, "rule_id", "UNKNOWN"),
        reason=getattr(decision, "reason", "unrecognised prompt"),
        excerpt=_excerpt(region),
    )
    with _lock:
        _pending[esc.id] = esc

    # The operator is watching the terminal; make this impossible to miss.
    print("\n" + "=" * 72)
    print(f"  ⛔ HELD — {agent} is blocked and needs you  [{esc.id}]")
    print(f"     why    : {esc.reason}  ({esc.rule_id})")
    print(f"     asking : {esc.excerpt}")
    print(f"     resolve: say \"allow it\" / \"deny it\", or answer in the agent's terminal")
    print("=" * 72 + "\n")

    if player:
        player.write_log(f"SYS: ⛔ {agent} HELD — {esc.reason} [{esc.id}]")
    if on_new:
        try:
            on_new(esc)
        except Exception:
            pass          # a broken listener must not break the watcher thread
    return esc


def pending() -> list["Escalation"]:
    with _lock:
        return sorted(_pending.values(), key=lambda e: e.created_at)


def oldest() -> "Escalation | None":
    p = pending()
    return p[0] if p else None


def resolve(esc_id: str, verdict: str, by: str = "human") -> "Escalation | None":
    """Mark an escalation decided. Returns it so the caller can act on it.

    Resolution does NOT inject anything itself — the watcher owns the PTY and
    is the only thing that types. Keeping the decision and the keystroke in
    different places is the same separation the reflex tier is built on.
    """
    with _lock:
        esc = _pending.pop(esc_id, None)
    if esc is None:
        return None
    esc.verdict = verdict
    esc.resolved_at = time.time()
    esc.resolved_by = by
    _resolved.append(esc)
    del _resolved[:-50]
    print(f"[Escalation] {esc.id} {verdict.upper()} by {by} "
          f"after {esc.waiting_s:.0f}s — {esc.agent}")
    return esc


def resolve_oldest(verdict: str, by: str = "human") -> "Escalation | None":
    """What "allow it" means when the user doesn't name an id."""
    esc = oldest()
    return resolve(esc.id, verdict, by) if esc else None


def snapshot() -> dict:
    """For the HUD and swarm telemetry."""
    return {
        "pending": [
            {"id": e.id, "agent": e.agent, "reason": e.reason,
             "rule_id": e.rule_id, "excerpt": e.excerpt,
             "waiting_s": round(e.waiting_s, 1)}
            for e in pending()
        ],
        "recent": [
            {"id": e.id, "agent": e.agent, "verdict": e.verdict}
            for e in _resolved[-5:]
        ],
    }


def clear() -> None:
    """Test hook — process-local state would otherwise leak between cases."""
    with _lock:
        _pending.clear()
        _resolved.clear()
