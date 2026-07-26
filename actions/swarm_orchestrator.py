"""Swarm orchestrator: git worktree isolation + real-time blackboard sync.

Partitions concurrent coding agents into isolated git worktrees
(<project>/.space_eagle/worktrees/<agent>, branch swarm/<agent>) so they
share history without file collisions, and keeps a shared blackboard at
<project>/.space_eagle/swarm_state.json for status, thoughts, decisions
and file claims. Decisions are also injected live into every teammate's
PTY session, so the swarm adapts in real time.
"""

import asyncio
import hashlib
import json
import os
import re
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

    # -------------------------------------------------------- lessons (wisdom)
    # A caught error becomes a SHARED, DURABLE lesson so no agent repeats it —
    # "one agent's scar vaccinates the swarm." Kept in a SEPARATE lessons.json so
    # it survives even when swarm_state.json is reset between missions; deduped by
    # a fingerprint of the error so the same mistake is recorded once.
    @property
    def _lessons_path(self) -> Path:
        return self.dir / "lessons.json"

    @staticmethod
    def _fingerprint(error: str) -> str:
        norm = re.sub(r"\s+", " ", (error or "").lower()).strip()
        # Drop line numbers / hex / paths so "same class of error" collapses to one.
        norm = re.sub(r"(line \d+|0x[0-9a-f]+|/[\w./-]+)", "", norm)
        return hashlib.sha1(norm.encode()).hexdigest()[:12]

    def read_lessons(self) -> list[dict]:
        try:
            return json.loads(self._lessons_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def add_lesson(self, agent: str, error: str, fix: str = "", tag: str = "") -> bool:
        """Record a caught error as a lesson. Returns True if NEW (worth
        broadcasting), False if we'd already learned it."""
        error = (error or "").strip()
        if not error:
            return False
        fp = self._fingerprint(error)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._acquire()
        try:
            lessons = self.read_lessons()
            for L in lessons:
                if L.get("fp") == fp:
                    L["seen"] = L.get("seen", 1) + 1
                    L["last_ts"] = time.time()
                    self._write_lessons(lessons)
                    return False
            lessons.append({
                "fp": fp, "agent": agent, "tag": tag or "error",
                "error": error[:400], "fix": (fix or "")[:400],
                "ts": time.time(), "last_ts": time.time(), "seen": 1,
            })
            del lessons[:-80]  # cap; keep the most recent 80
            self._write_lessons(lessons)
            return True
        finally:
            self._release()

    def _write_lessons(self, lessons: list[dict]):
        tmp = self._lessons_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(lessons, indent=2), encoding="utf-8")
        os.replace(tmp, self._lessons_path)

    def lessons_text(self, limit: int = 25) -> str:
        lessons = self.read_lessons()
        if not lessons:
            return ""
        lines = []
        for L in lessons[-limit:]:
            line = f"• [{L.get('tag', 'error')}] {L['error']}"
            if L.get("fix"):
                line += f" → FIX: {L['fix']}"
            lines.append(line)
        return "\n".join(lines)

    def summary(self) -> str:
        s = self.read()
        lines = []
        nles = len(self.read_lessons())
        if nles:
            lines.append(f"lessons learned (shared, do-not-repeat): {nles}")
        for name, a in s["agents"].items():
            thought = a.get("last_thought", "")[:80]
            lines.append(f"{name} [{a.get('status')}] on {a.get('branch')}: "
                         f"{a.get('task', '')[:60]}"
                         + (f" | 🧠 {thought}" if thought else ""))
        for d in s["decisions"][-5:]:
            lines.append(f"decision({d['agent']}): {d['text'][:100]}")
        return "\n".join(lines) or "No swarm activity recorded."


def _swarm_preamble(agent: str, task: str, branch: str, project_dir: Path,
                    repo_map: str = "") -> str:
    board = Path(project_dir) / STATE_DIR / "swarm_state.json"
    text = (
        f"You are '{agent}', one member of a coordinated multi-agent engineering "
        f"swarm working on this project. You work in an isolated git worktree on "
        f"branch '{branch}' — commit your work to this branch as you reach "
        f"working milestones; never switch branches. The shared team blackboard "
        f"is at {board}; consult it before structural decisions and align with "
        f"decisions recorded there. Messages prefixed [SWARM UPDATE] are live "
        f"context from teammates — adapt to them. YOUR TASK: {task}"
    )
    # Inject accumulated lessons up front so a fresh agent starts already knowing
    # the mistakes the swarm has hit — it never has to re-learn them the hard way.
    try:
        lessons = Blackboard(project_dir).lessons_text()
        if lessons:
            text += ("\n\nLESSONS LEARNED — mistakes the swarm already made; DO NOT "
                     "REPEAT these:\n" + lessons)
    except Exception:
        pass
    if repo_map:
        text += f"\nCONDENSED CODEBASE MAP:\n{repo_map}"
    return text


class SwarmOrchestrator:
    """Manages one project's agent swarm: worktrees, sessions, blackboard."""

    def __init__(self, project_dir: Path, player=None):
        self.project_dir = Path(project_dir).resolve()
        self.player = player
        self.board = Blackboard(self.project_dir)
        # Every orchestrator is visible to telemetry regardless of how it
        # was constructed (voice tool, sentinel, tests).
        _ORCHESTRATORS[str(self.project_dir)] = self

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
        try:
            from actions.repo_map import build_repo_map
            repo_map = await asyncio.to_thread(
                build_repo_map, self.project_dir, 4000)
        except Exception:
            repo_map = ""
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
            prompt = _swarm_preamble(agent_key, task, branch, self.project_dir,
                                     repo_map=repo_map)
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
        sess = POOL.get_alive(adapter.pool_key, worktree)
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

    def record_lesson(self, agent: str, error: str, fix: str = "", tag: str = "") -> str:
        """A caught error → durable shared lesson, streamed LIVE to every running
        agent so they stop repeating it immediately, and baked into the preamble
        so future agents never learn it the hard way."""
        is_new = self.board.add_lesson(agent, error, fix, tag)
        if not is_new:
            return "lesson already known (count incremented)"
        note = f"⚠ LESSON [{tag or 'error'}] — {error[:200]}" + (f" → FIX: {fix[:160]}" if fix else "")
        self._log(f"SYS: 📌 Swarm lesson recorded: {error[:100]}")
        delivered = []
        for agent_key, sess in self._live_sessions().items():
            try:
                sess.send_line(f"[SWARM LESSON — do not repeat] {note}")
                delivered.append(agent_key)
            except OSError:
                pass
        return (f"Lesson recorded (shared, durable) and streamed to: "
                f"{', '.join(delivered) or 'no live agents'}.")

    def status(self) -> str:
        live = set(self._live_sessions())
        base = self.board.summary()
        return f"{base}\nLive sessions: {', '.join(sorted(live)) or 'none'}"

    def review(self, agent: str = "", deep: bool = False) -> str:
        """Offload verification + merge of swarm branches to the Reviewer."""
        from actions.swarm_reviewer import ReviewerAgent
        state = self.board.read()
        targets = ([agent] if agent else list(state["agents"].keys()))
        if not targets:
            return "No swarm agents registered — nothing to review."
        reviewer = ReviewerAgent(self.project_dir, self.player)
        results = []
        for key in targets:
            info = state["agents"].get(key)
            if not info:
                results.append(f"{key}: unknown agent")
                continue
            outcome = reviewer.review_and_merge(
                key, info["branch"], info["worktree"], deep=deep)
            merged = outcome.startswith("MERGED")
            self.board.set_agent(key, status="merged" if merged else "review_blocked")
            self.board.add_decision("reviewer", outcome[:300])
            results.append(outcome)
        return " || ".join(results)

    def stop_all(self) -> str:
        stopped = []
        for agent_key, sess in self._live_sessions().items():
            sess.close()
            self.board.set_agent(agent_key, status="stopped")
            stopped.append(agent_key)
        return f"Stopped: {', '.join(stopped) or 'nothing running'}."


def swarm_snapshot() -> dict:
    """Full swarm telemetry for dashboard streaming: every tracked project's
    blackboard plus live session tails."""
    import re as _re
    ansi = _re.compile(r"(?:\x1b[@-_][0-?]*[ -/]*[@-~])")
    projects = {}
    for key, orch in list(_ORCHESTRATORS.items()):
        state = orch.board.read()
        projects[key] = {
            "agents": state.get("agents", {}),
            "decisions": state.get("decisions", [])[-10:],
            "file_claims": state.get("file_claims", {}),
        }
    sessions = {}
    for (agent_key, sdir), sess in POOL.all_sessions().items():
        tail = ansi.sub("", sess.snapshot_tail(1200).decode("utf-8", "replace"))
        sessions[f"{agent_key}@{sdir}"] = {
            "alive": sess.is_alive(),
            "age_s": round(time.time() - sess.created_at, 1),
            "tail": tail[-800:],
        }
    return {"ts": time.time(), "projects": projects, "sessions": sessions}


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
    if action == "review":
        return await asyncio.to_thread(
            orch.review,
            (parameters.get("agent") or "").strip(),
            bool(parameters.get("deep")))
    if action == "stop":
        return await asyncio.to_thread(orch.stop_all)
    return await asyncio.to_thread(orch.status)
