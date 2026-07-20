"""Swarm orchestrator: git worktree isolation + real-time blackboard sync.

Partitions concurrent coding agents into isolated git worktrees
(<project>/.space_eagle/worktrees/<agent>, branch swarm/<agent>) so they
share history without file collisions, and keeps a shared blackboard at
<project>/.space_eagle/swarm_state.json for status, thoughts, decisions
and file claims. Decisions are also injected live into every teammate's
PTY session, so the swarm adapts in real time.
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from actions.pty_session import POOL

STATE_DIR = ".space_eagle"
SPACE_EAGLE_HOME = Path(__file__).resolve().parent.parent


class Blackboard:
    """Shared swarm ledger with atomic, lock-guarded JSON updates."""

    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir) / STATE_DIR
        self.path = self.dir / "swarm_state.json"
        self._lock_path = self.dir / "swarm_state.lock"

    def _acquire(self, timeout=2.0, stale=5.0):
        deadline = time.time() + timeout
        self.dir.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                try:
                    if time.time() - self._lock_path.stat().st_mtime > stale:
                        self._lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError("blackboard lock timeout")
                time.sleep(0.02)

    def _release(self):
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"agents": {}, "decisions": [], "file_claims": {},
                    "updated_at": 0.0}

    def update(self, mutate) -> dict:
        """Atomically read-mutate-write the blackboard. Returns new state."""
        self._acquire()
        try:
            state = self.read()
            mutate(state)
            state["updated_at"] = time.time()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return state
        finally:
            self._release()

    # ------------------------------------------------------------- helpers

    def register_agent(self, agent: str, task: str, worktree: str, branch: str):
        def m(s):
            s["agents"][agent] = {
                "task": task, "worktree": worktree, "branch": branch,
                "status": "working", "last_thought": "", "updated_at": time.time(),
            }
        self.update(m)

    def set_agent(self, agent: str, **fields):
        def m(s):
            entry = s["agents"].setdefault(agent, {})
            entry.update(fields, updated_at=time.time())
        self.update(m)

    def add_decision(self, agent: str, text: str):
        def m(s):
            s["decisions"].append(
                {"ts": time.time(), "agent": agent, "text": text[:1000]})
            del s["decisions"][:-100]
        self.update(m)

    def claim_file(self, agent: str, rel_path: str) -> bool:
        result = {}
        def m(s):
            owner = s["file_claims"].get(rel_path)
            if owner and owner["agent"] != agent:
                result["ok"] = False
            else:
                s["file_claims"][rel_path] = {"agent": agent, "ts": time.time()}
                result["ok"] = True
        self.update(m)
        return result["ok"]

    def release_file(self, agent: str, rel_path: str):
        def m(s):
            owner = s["file_claims"].get(rel_path)
            if owner and owner["agent"] == agent:
                del s["file_claims"][rel_path]
        self.update(m)

    def summary(self) -> str:
        s = self.read()
        lines = []
        for name, a in s["agents"].items():
            thought = a.get("last_thought", "")[:80]
            lines.append(f"{name} [{a.get('status')}] on {a.get('branch')}: "
                         f"{a.get('task', '')[:60]}"
                         + (f" | 🧠 {thought}" if thought else ""))
        for d in s["decisions"][-5:]:
            lines.append(f"decision({d['agent']}): {d['text'][:100]}")
        return "\n".join(lines) or "No swarm activity recorded."


def _swarm_preamble(agent: str, task: str, branch: str, project_dir: Path) -> str:
    board = Path(project_dir) / STATE_DIR / "swarm_state.json"
    return (
        f"You are '{agent}', one member of a coordinated multi-agent engineering "
        f"swarm working on this project. You work in an isolated git worktree on "
        f"branch '{branch}' — commit your work to this branch as you reach "
        f"working milestones; never switch branches. The shared team blackboard "
        f"is at {board}; consult it before structural decisions and align with "
        f"decisions recorded there. Messages prefixed [SWARM UPDATE] are live "
        f"context from teammates — adapt to them. YOUR TASK: {task}"
    )


class SwarmOrchestrator:
    """Manages one project's agent swarm: worktrees, sessions, blackboard."""

    def __init__(self, project_dir: Path, player=None):
        self.project_dir = Path(project_dir).resolve()
        self.player = player
        self.board = Blackboard(self.project_dir)

    def _log(self, msg: str):
        if self.player:
            self.player.write_log(msg)
        print(msg)

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or self.project_dir),
            capture_output=True, text=True, timeout=60)

    # ---------------------------------------------------------------- git

    def ensure_repo(self):
        if not (self.project_dir / ".git").exists():
            self._git("init", "-b", "main")
            self._git("add", "-A")
        # Make sure swarm state never pollutes the repo.
        gi = self.project_dir / ".gitignore"
        marker = f"{STATE_DIR}/"
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if marker not in text:
            gi.write_text(text.rstrip("\n") + f"\n{marker}\n", encoding="utf-8")
        if self._git("rev-parse", "HEAD").returncode != 0:
            self._git("add", "-A")
            self._git("commit", "-m", "swarm: initial baseline",
                      "--allow-empty")

    def ensure_worktree(self, agent: str) -> Path:
        wt = self.project_dir / STATE_DIR / "worktrees" / agent
        branch = f"swarm/{agent}"
        if wt.exists() and (wt / ".git").exists():
            return wt
        wt.parent.mkdir(parents=True, exist_ok=True)
        r = self._git("worktree", "add", str(wt), "-b", branch)
        if r.returncode != 0:
            # Branch may exist from a previous run — attach without -b.
            r = self._git("worktree", "add", str(wt), branch)
        if r.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {r.stderr.strip()}")
        return wt

    # ------------------------------------------------------------- swarm

    async def launch(self, assignments: dict) -> str:
        """assignments: {registry_agent_key: task_description}"""
        from actions.agent_delegation import AGENT_REGISTRY

        self.ensure_repo()
        started, errors = [], []
        for agent_key, task in assignments.items():
            adapter = AGENT_REGISTRY.get(agent_key)
            if not adapter:
                errors.append(f"unknown agent '{agent_key}'")
                continue
            try:
                wt = await asyncio.to_thread(self.ensure_worktree, agent_key)
            except Exception as e:
                errors.append(f"{agent_key}: {e}")
                continue
            branch = f"swarm/{agent_key}"
            prompt = _swarm_preamble(agent_key, task, branch, self.project_dir)
            result = await adapter.run(prompt, wt, self.project_dir.name,
                                       player=self.player)
            if "session" not in result.lower():
                errors.append(f"{agent_key}: {result}")
                continue
            self.board.register_agent(agent_key, task, str(wt), branch)
            self._wire_thoughts(adapter, agent_key, wt)
            started.append(f"{agent_key} on {branch}")
            self._log(f"SYS: Swarm member '{agent_key}' live on {branch}.")

        report = f"Swarm launched: {', '.join(started) or 'none'}."
        if errors:
            report += f" Issues: {'; '.join(errors)}."
        return report

    def _wire_thoughts(self, adapter, agent_key: str, worktree: Path):
        sess = POOL.get_alive(adapter.name.lower(), worktree)
        watcher = getattr(sess, "watcher", None) if sess else None
        if watcher:
            watcher.on_thought = (
                lambda _n, text, status, a=agent_key:
                    self.board.set_agent(a, last_thought=text))

    def _live_sessions(self) -> dict:
        """agent_key -> live PtySession for this project's swarm."""
        state = self.board.read()
        out = {}
        for agent_key, info in state["agents"].items():
            for (_, sdir), sess in POOL.all_sessions().items():
                if sdir == info.get("worktree") and sess.is_alive():
                    out[agent_key] = sess
        return out

    def broadcast(self, from_agent: str, message: str) -> str:
        self.board.add_decision(from_agent, message)
        delivered = []
        for agent_key, sess in self._live_sessions().items():
            if agent_key == from_agent:
                continue
            try:
                sess.send_line(f"[SWARM UPDATE from {from_agent}] {message}")
                delivered.append(agent_key)
            except OSError:
                pass
        return (f"Decision recorded on blackboard and delivered live to: "
                f"{', '.join(delivered) or 'no other live agents'}.")

    def status(self) -> str:
        live = set(self._live_sessions())
        base = self.board.summary()
        return f"{base}\nLive sessions: {', '.join(sorted(live)) or 'none'}"

    def stop_all(self) -> str:
        stopped = []
        for agent_key, sess in self._live_sessions().items():
            sess.close()
            self.board.set_agent(agent_key, status="stopped")
            stopped.append(agent_key)
        return f"Stopped: {', '.join(stopped) or 'nothing running'}."


# ------------------------------------------------------------- tool entry

_ORCHESTRATORS = {}


def get_orchestrator(project_dir, player=None) -> SwarmOrchestrator:
    key = str(Path(project_dir).resolve())
    orch = _ORCHESTRATORS.get(key)
    if orch is None:
        orch = _ORCHESTRATORS[key] = SwarmOrchestrator(project_dir, player)
    orch.player = player or orch.player
    return orch


async def swarm_orchestrate(parameters: dict, player=None) -> str:
    """Voice-tool entry point. Actions: launch | status | broadcast | stop."""
    action = (parameters.get("action") or "status").strip().lower()
    directory = (parameters.get("directory") or "").strip()
    if not directory:
        return "Ask: Which project directory should the swarm operate on?"

    project_dir = Path(directory).expanduser().resolve()
    if project_dir == SPACE_EAGLE_HOME or SPACE_EAGLE_HOME in project_dir.parents:
        return ("Blocked: Space-Eagle safeguard — the swarm cannot operate on "
                "Aethelark's own codebase.")
    project_dir.mkdir(parents=True, exist_ok=True)

    orch = get_orchestrator(project_dir, player)
    if action == "launch":
        assignments = parameters.get("assignments") or {}
        if isinstance(assignments, str):
            try:
                assignments = json.loads(assignments)
            except ValueError:
                return "Invalid assignments: expected {agent: task} mapping."
        if not assignments:
            return "Ask: Which agents should join the swarm, and on what tasks?"
        return await orch.launch(assignments)
    if action == "broadcast":
        return await asyncio.to_thread(
            orch.broadcast,
            parameters.get("agent", "eagle"),
            parameters.get("message", ""))
    if action == "stop":
        return await asyncio.to_thread(orch.stop_all)
    return await asyncio.to_thread(orch.status)
