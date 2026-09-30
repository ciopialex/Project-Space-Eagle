import time

from actions.swarm_orchestrator import STATUS_FILE, Blackboard
from actions.swarm_sentinel import NUDGE_AFTER_S, SwarmSentinel, idle_seconds


def _board(tmp_path, name="web"):
    root = tmp_path / "proj"
    wt = root / ".space_eagle" / "worktrees" / "m1" / name
    wt.mkdir(parents=True)
    board = Blackboard(root)
    board.register_agent(name, "build it", str(wt), f"swarm/m1/{name}")
    return root, wt, board


def test_done_signal_completes_the_workstream(tmp_path):
    root, wt, board = _board(tmp_path)
    sentinel = SwarmSentinel()
    assert sentinel._absorb_signal(root, str(wt), "claude_code") is False
    (wt / STATUS_FILE).write_text("done\n")
    assert sentinel._absorb_signal(root, str(wt), "claude_code") is True
    assert board.read()["agents"]["web"]["status"] == "completed"


def test_blocked_signal_carries_the_reason(tmp_path):
    root, wt, board = _board(tmp_path)
    (wt / STATUS_FILE).write_text("blocked: needs a Stripe key")
    assert SwarmSentinel()._absorb_signal(root, str(wt), "claude_code") is True
    info = board.read()["agents"]["web"]
    assert info["status"] == "review_blocked"
    assert info["last_thought"] == "needs a Stripe key"


class _Headless:
    watcher = None

    def __init__(self, quiet_for, turn_open):
        self._t = time.time() - quiet_for
        self.turn_open = turn_open

    def seconds_since_activity(self):
        return time.time() - self._t


def test_headless_turn_that_ended_without_a_signal_is_idle():
    assert idle_seconds(_Headless(5, turn_open=True)) < 10
    assert idle_seconds(_Headless(40, turn_open=False)) >= NUDGE_AFTER_S
    assert idle_seconds(_Headless(40, turn_open=True)) < NUDGE_AFTER_S
