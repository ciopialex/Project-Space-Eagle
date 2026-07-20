"""Self-healing sentinel: stall detection, nudges, and fallback re-delegation.

Watches every live swarm session's virtual screen activity (Phase 2's
seconds_since_activity signal). A swarm agent whose screen freezes is
first nudged to continue; if it stays frozen it is treated as stuck —
the session is terminated, the blackboard records the failure, and its
task is re-delegated to the next available agent CLI in the SAME
worktree so no work context is lost.

Only swarm sessions (running under <project>/.space_eagle/worktrees/)
are ever auto-healed: an idle single-agent developer_mode session is
normal (it waits for the user), an idle swarm agent is not.
"""

import asyncio
import shutil
import threading
import time
from pathlib import Path

from actions.pty_session import POOL

POLL_S = 10.0
NUDGE_AFTER_S = 150.0
FAIL_AFTER_S = 420.0
FALLBACK_ORDER = ["claude_code", "antigravity_cli", "opencode", "kimi"]


def swarm_root_of(path) -> Path | None:
    """If path is a swarm worktree (…/<root>/.space_eagle/worktrees/<agent>),
    return <root>, else None."""
    parts = Path(path).resolve().parts
    for i in range(len(parts) - 2):
        if parts[i] == ".space_eagle" and parts[i + 1] == "worktrees":
            return Path(*parts[:i])
    return None


def _agent_available(adapter) -> bool:
    binary = adapter.command_template.split()[0]
    return shutil.which(binary) is not None


class SwarmSentinel:
    def __init__(self):
        self._thread = None
        self._lock = threading.Lock()
        self._nudge_ts = {}  # (agent_key, dir) -> when we nudged

    def ensure_running(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name="swarm-sentinel", daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            time.sleep(POLL_S)
            try:
                self._scan()
            except Exception:
                pass

    def _scan(self):
        # The nudge's own echo resets screen activity, so escalation is a
        # two-stage state machine: nudge once, then fail if the agent goes
        # idle AGAIN within the grace window; clear only if it stays busy.
        grace = max(FAIL_AFTER_S - NUDGE_AFTER_S, POLL_S)
        now = time.time()
        for (agent_key, sdir), sess in POOL.all_sessions().items():
            if not sess.is_alive():
                self._nudge_ts.pop((agent_key, sdir), None)
                continue
            watcher = getattr(sess, "watcher", None)
            root = swarm_root_of(sdir)
            if watcher is None or root is None:
                continue
            idle = watcher.seconds_since_activity()
            tag = (agent_key, sdir)
            nudged_at = self._nudge_ts.get(tag)
            if idle >= NUDGE_AFTER_S:
                if nudged_at is None:
                    self._nudge_ts[tag] = now
                    self._nudge(agent_key, sess, root)
                elif now - nudged_at >= grace:
                    self._nudge_ts.pop(tag, None)
                    self._fail_over(agent_key, sess, root)
            elif nudged_at is not None and now - nudged_at >= grace:
                self._nudge_ts.pop(tag, None)  # recovered — stayed active

    # ------------------------------------------------------------- actions

    def _hud(self, sess, msg):
        player = getattr(sess, "player", None)
        if player:
            player.write_log(msg)
        print(msg)

    def _task_of(self, root: Path, agent_key: str) -> str:
        from actions.swarm_orchestrator import Blackboard
        return Blackboard(root).read()["agents"].get(agent_key, {}).get("task", "")

    def _nudge(self, agent_key, sess, root):
        task = self._task_of(root, agent_key)
        self._hud(sess, f"SYS: ⚠️ Swarm agent '{agent_key}' looks idle — nudging.")
        try:
            sess.send_line(
                f"[SWARM UPDATE from eagle] You appear idle. If you are "
                f"blocked, state the blocker clearly; otherwise continue "
                f"your task: {task or 'your assigned task'}")
        except OSError:
            pass

    def _fail_over(self, agent_key, sess, root):
        from actions.agent_delegation import AGENT_REGISTRY
        from actions.swarm_orchestrator import Blackboard, _swarm_preamble

        board = Blackboard(root)
        info = board.read()["agents"].get(agent_key, {})
        task = info.get("task", "")
        worktree = Path(info.get("worktree") or sess.project_dir)
        player = getattr(sess, "player", None)

        self._hud(sess, f"SYS: 🔴 Swarm agent '{agent_key}' is stuck — "
                        f"terminating and re-delegating its task.")
        context_tail = sess.snapshot_tail(2000).decode("utf-8", "replace")
        sess.close()
        board.set_agent(agent_key, status="failed")

        fallback = next(
            (k for k in FALLBACK_ORDER
             if k != agent_key and k in AGENT_REGISTRY
             and _agent_available(AGENT_REGISTRY[k])), None)
        if not fallback:
            board.add_decision("eagle", f"{agent_key} failed; no fallback "
                                        f"agent available for its task.")
            self._hud(sess, "SYS: No fallback agent available — task parked "
                            "on the blackboard.")
            return

        adapter = AGENT_REGISTRY[fallback]
        branch = info.get("branch", f"swarm/{agent_key}")
        prompt = _swarm_preamble(fallback, task, branch, root) + (
            f"\nYou are taking over from '{agent_key}', which stalled. "
            f"Its final console output was:\n{context_tail[-1200:]}\n"
            f"Assess the worktree state and continue the task from there.")
        try:
            result = asyncio.run(
                adapter.run(prompt, worktree, root.name, player=player))
        except Exception as e:
            result = f"fail-over error: {e}"
        board.register_agent(fallback, task, str(worktree), branch)
        board.add_decision("eagle",
                           f"Re-delegated '{agent_key}' task to '{fallback}'.")
        self._hud(sess, f"SYS: Task handed over to '{fallback}': {result[:120]}")


SENTINEL = SwarmSentinel()
