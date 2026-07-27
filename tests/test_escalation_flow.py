"""The escalation loop: held prompt → human → blocked agent unblocked.

Run:  .venv/bin/python -m pytest tests/ -q

Refusing to answer a dangerous prompt is only half a system. Without somewhere
for the question to go, the agent blocks forever — a different failure from the
one the reflex tier set out to fix, and arguably a worse one, because it looks
like a hang rather than a decision.

These cover the whole path with no live agent: the watcher raises, the registry
holds, a human resolves, and the answer is injected by the component that owns
the PTY.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.agent_screen import AgentScreenWatcher  # noqa: E402
from core import escalations  # noqa: E402
from core.prompt_reflex import classify  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    escalations.clear()
    yield
    escalations.clear()


class FakeSession:
    def __init__(self, alive=True):
        self.sent = []
        self._hooks = []
        self._alive = alive

    def add_feed_hook(self, fn):
        self._hooks.append(fn)

    def is_alive(self):
        return self._alive

    def send_raw(self, data):
        self.sent.append(data)

    def feed(self, text):
        for h in self._hooks:
            h(text.encode())


def blocked_watcher(screen="sudo rm -rf /var/data\nProceed? [y/N]"):
    """Drive a watcher to the point where it has held a dangerous prompt."""
    s = FakeSession()
    w = AgentScreenWatcher(
        s, "claude_code",
        on_escalation=lambda a, d, r: escalations.raise_escalation(a, d, r))
    w._stop.set()
    s.feed(screen + "\n")
    w._poll_once()
    w._poll_once()
    return s, w


# ------------------------------------------------------------- registration

def test_a_held_prompt_becomes_a_pending_escalation():
    s, _ = blocked_watcher()
    assert s.sent == [], "watcher answered a dangerous prompt"
    p = escalations.pending()
    assert len(p) == 1
    assert p[0].agent == "claude_code"
    assert p[0].rule_id.startswith(("PRIV", "FS")), p[0].rule_id


def test_escalation_carries_what_the_agent_is_asking():
    """A human cannot decide from a rule code alone."""
    blocked_watcher()
    e = escalations.oldest()
    assert "rm -rf" in e.excerpt or "Proceed" in e.excerpt


def test_question_is_phrased_for_speech_not_for_logs():
    blocked_watcher()
    q = escalations.oldest().question()
    assert "?" in q
    assert "FS_RM_RECURSIVE" not in q, "rule codes must not reach the user"


# --------------------------------------------------------------- resolution

def test_authorizing_injects_the_answer_into_the_blocked_agent():
    s, w = blocked_watcher()
    e = escalations.oldest()
    assert escalations.resolve(e.id, "allow") is not None
    assert w.authorize_pending() is True
    assert s.sent == [b"y\r"], "authorized answer never reached the agent"


def test_denying_leaves_the_agent_untouched():
    """Deny must not type anything — the agent stays blocked on purpose."""
    s, _ = blocked_watcher()
    escalations.resolve_oldest("deny")
    assert s.sent == []
    assert escalations.pending() == []


def test_resolving_removes_it_from_pending():
    blocked_watcher()
    escalations.resolve_oldest("allow")
    assert escalations.pending() == []


def test_resolve_oldest_is_what_allow_it_means():
    """The user says "allow it" without naming an id; the oldest wins."""
    blocked_watcher()
    blocked_watcher("git push --force origin main\nProceed? [y/N]")
    first = escalations.pending()[0].id
    assert escalations.resolve_oldest("allow").id == first


def test_resolving_an_unknown_id_is_not_an_error():
    assert escalations.resolve("esc999", "allow") is None


def test_authorizing_a_dead_agent_reports_failure_rather_than_lying():
    s = FakeSession(alive=False)
    w = AgentScreenWatcher(s, "claude_code")
    w._stop.set()
    assert w.authorize_pending() is False
    assert s.sent == []


# -------------------------------------------------------------- voice entry

def test_authorize_with_nothing_pending_says_so():
    from actions.swarm_orchestrator import swarm_orchestrate
    r = asyncio.run(swarm_orchestrate({"action": "authorize", "directory": "/tmp/x"}))
    assert "waiting" in r.lower()


def test_escalations_query_reports_what_is_blocked():
    from actions.swarm_orchestrator import swarm_orchestrate
    blocked_watcher()
    r = asyncio.run(swarm_orchestrate({"action": "escalations", "directory": "/tmp/x"}))
    assert "claude_code" in r and "waiting" in r.lower()


def test_escalations_query_is_calm_when_nothing_is_blocked():
    from actions.swarm_orchestrator import swarm_orchestrate
    r = asyncio.run(swarm_orchestrate({"action": "escalations", "directory": "/tmp/x"}))
    assert "nothing" in r.lower()


# ------------------------------------------------------------- observability

def test_snapshot_exposes_pending_for_the_hud():
    blocked_watcher()
    snap = escalations.snapshot()
    assert snap["pending"] and snap["pending"][0]["agent"] == "claude_code"
    assert "waiting_s" in snap["pending"][0]


def test_safe_prompts_never_create_an_escalation():
    """The fast path must stay silent — an escalation per file write would
    train the user to ignore them."""
    s, _ = blocked_watcher("Create file src/app.py?\n[y/N]")
    assert s.sent == [b"y\r"]
    assert escalations.pending() == []


def test_classify_and_registry_agree_on_what_is_dangerous():
    d = classify("terraform apply -auto-approve\nProceed? [y/N]")
    e = escalations.raise_escalation("agy", d, "terraform apply\nProceed? [y/N]")
    assert e.rule_id == d.rule_id
