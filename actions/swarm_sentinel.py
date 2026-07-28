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

# A workstream in one of these states is FINISHED. It must never be nudged or
# re-delegated, however quiet its terminal goes.
#
# This is the bug that burned an hour of quota: _scan() judged liveness purely
# by seconds_since_activity, so an agent that had finished — written its files,
# committed, recorded status "completed" on the blackboard, and gone quiet —
# was indistinguishable from one that had hung. The sentinel re-delegated it,
# the replacement found the work already done, reported "no new commit needed",
# went quiet itself, and was re-delegated in turn. Forever, spawning a window
# each time. Done and stuck produce exactly the same amount of output: none.
TERMINAL_STATUSES = {"completed", "merged", "stopped", "review_blocked"}

# How many times one workstream may be re-delegated before the sentinel gives
# up and asks a human. Without a ceiling, a genuinely un-completable task
# recruits every installed CLI in an endless relay.
MAX_FAILOVERS_PER_WORKSTREAM = 2


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
        self._nudge_ts = {}   # (agent_key, dir) -> when we nudged
        self._failovers = {}  # workstream id -> hand-offs so far (see ceiling)

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
            except Exception as _e:
                print(f"[swarm_sentinel.py] Non-fatal error at line 63: {_e}")

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
            # Ask the blackboard whether this workstream is actually finished
            # BEFORE reading anything into its silence. Quiet is ambiguous;
            # the board is not.
            if self._is_finished(root, sdir, agent_key):
                self._nudge_ts.pop((agent_key, sdir), None)
                continue

            idle = watcher.seconds_since_activity()
            tag = (agent_key, sdir)
            nudged_at = self._nudge_ts.get(tag)
            if idle >= NUDGE_AFTER_S:
                if nudged_at is None:
                    self._nudge_ts[tag] = now
                    self._nudge(agent_key, sess, root, sdir)
                elif now - nudged_at >= grace:
                    self._nudge_ts.pop(tag, None)
                    self._fail_over(agent_key, sess, root, sdir)
            elif nudged_at is not None and now - nudged_at >= grace:
                self._nudge_ts.pop(tag, None)  # recovered — stayed active

    # ------------------------------------------------------------- actions

    def _hud(self, sess, msg):
        player = getattr(sess, "player", None)
        if player:
            player.write_log(msg)
        print(msg)

    def _is_finished(self, root, sdir, agent_key) -> bool:
        """Has this workstream already reported itself done?

        Cheap board read on a 10s poll — far cheaper than the spawn it prevents.
        Fails OPEN (returns False) if the board can't be read, so a corrupt
        state file degrades to the old behaviour rather than disabling healing.
        """
        try:
            from actions.swarm_orchestrator import Blackboard
            _key, info = self._board_entry(Blackboard(root), sdir, agent_key)
            return (info.get("status") or "") in TERMINAL_STATUSES
        except Exception:
            return False

    @staticmethod
    def _board_entry(board, sdir, fallback_key):
        """Map a live session's worktree dir to its blackboard entry.

        POOL keys sessions by pool_key (the agent registry key), but the board
        keys by WORKSTREAM ID after execute_plan — so the worktree directory is
        the only reliable join. Returns (board_key, info). Falls back to a
        direct key lookup for the older launch() path where they coincide.
        """
        try:
            sdir_r = str(Path(sdir).resolve())
        except OSError:
            sdir_r = str(sdir)
        agents = board.read().get("agents", {})
        for key, info in agents.items():
            wt = info.get("worktree")
            if not wt:
                continue
            try:
                wt_r = str(Path(wt).resolve())
            except OSError:
                wt_r = wt
            if wt_r == sdir_r or wt == str(sdir):
                return key, info
        return fallback_key, agents.get(fallback_key, {})

    def _nudge(self, agent_key, sess, root, sdir):
        from actions.swarm_orchestrator import Blackboard
        board_key, info = self._board_entry(Blackboard(root), sdir, agent_key)
        task = info.get("task", "")
        self._hud(sess, f"SYS: ⚠️ Swarm agent '{board_key}' looks idle — nudging.")
        try:
            sess.send_line(
                f"[SWARM UPDATE from eagle] You appear idle. If you are "
                f"blocked, state the blocker clearly; otherwise continue "
                f"your task: {task or 'your assigned task'}")
        except OSError:
            pass

    def _fail_over(self, agent_key, sess, root, sdir):
        from actions.agent_delegation import AGENT_REGISTRY
        from actions.swarm_orchestrator import Blackboard, _swarm_preamble

        board = Blackboard(root)
        board_key, info = self._board_entry(board, sdir, agent_key)
        task = info.get("task", "")
        worktree = Path(info.get("worktree") or sess.project_dir)
        player = getattr(sess, "player", None)

        # Last check before the expensive, irreversible part. The agent may
        # have finished during the grace window — between the nudge and now —
        # in which case killing it would destroy a completed workstream.
        if (info.get("status") or "") in TERMINAL_STATUSES:
            return

        # Give up rather than relay the task around every installed CLI. An
        # un-completable workstream would otherwise recruit them one by one,
        # each spawning a session and a window, indefinitely.
        seen = self._failovers.get(board_key, 0)
        if seen >= MAX_FAILOVERS_PER_WORKSTREAM:
            self._hud(sess, f"SYS: ⛔ '{board_key}' has failed over {seen}x — "
                            f"stopping and escalating to you instead of retrying.")
            sess.close()
            board.set_agent(board_key, status="review_blocked")
            board.add_decision(
                "eagle", f"{board_key}: {seen} failed hand-offs, giving up. "
                         f"Needs a human decision.")
            try:
                from core.escalations import raise_escalation
                raise_escalation(
                    board_key,
                    type("D", (), {"rule_id": "SENTINEL_EXHAUSTED",
                                   "reason": f"{seen} agents in a row could not "
                                             f"finish this workstream"})(),
                    task[:200], player=player)
            except Exception:
                pass
            return
        self._failovers[board_key] = seen + 1

        self._hud(sess, f"SYS: 🔴 Swarm agent '{board_key}' is stuck — "
                        f"terminating and re-delegating its task "
                        f"(attempt {seen + 1}/{MAX_FAILOVERS_PER_WORKSTREAM}).")
        context_tail = sess.snapshot_tail(2000).decode("utf-8", "replace")
        sess.close()
        board.set_agent(board_key, status="failed")

        # Turn the stall into a shared lesson so teammates surface blockers early
        # instead of silently freezing.
        try:
            from actions.swarm_orchestrator import SwarmOrchestrator
            SwarmOrchestrator(root, player=player).record_lesson(
                agent_key,
                f"agent stalled with no progress on: {task[:120]}",
                fix="if blocked, state the blocker on the blackboard immediately "
                    "instead of going idle",
                tag="stall")
        except Exception as _e:
            print(f"[swarm_sentinel.py] lesson capture skipped: {_e}")

        fallback = next(
            (k for k in FALLBACK_ORDER
             if k != agent_key and k in AGENT_REGISTRY
             and _agent_available(AGENT_REGISTRY[k])), None)
        if not fallback:
            board.add_decision("eagle", f"{board_key} failed; no fallback "
                                        f"agent available for its task.")
            self._hud(sess, "SYS: No fallback agent available — task parked "
                            "on the blackboard.")
            return

        adapter = AGENT_REGISTRY[fallback]
        branch = info.get("branch", f"swarm/{board_key}")
        prompt = _swarm_preamble(fallback, task, branch, root) + (
            f"\nYou are taking over workstream '{board_key}' from '{agent_key}', "
            f"which stalled. Its final console output was:\n{context_tail[-1200:]}\n"
            f"Assess the worktree state and continue the task from there.")
        try:
            result = asyncio.run(
                adapter.run(prompt, worktree, root.name, player=player))
        except Exception as e:
            result = f"fail-over error: {e}"
        from actions.agent_delegation import spawn_succeeded
        if spawn_succeeded(result):
            # Revive the SAME workstream entry under the new agent — never orphan
            # it as a new key, or the worktree would map to two board entries.
            board.register_agent(board_key, task, str(worktree), branch)
            board.set_agent(board_key, assignee=fallback)
            board.add_decision(
                "eagle", f"Re-delegated '{board_key}' from '{agent_key}' to '{fallback}'.")
            self._hud(sess, f"SYS: Task handed to '{fallback}': {result[:120]}")
        else:
            board.add_decision(
                "eagle", f"Re-delegation of '{board_key}' to '{fallback}' FAILED: {result[:120]}")
            self._hud(sess, f"SYS: Re-delegation failed: {result[:120]}")


SENTINEL = SwarmSentinel()
