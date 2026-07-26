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


# Role vocabulary so the eagle can target "the frontend one" / "backend" / "the
# UI agent" and have it resolve to the right workstream, however it was named.
_ROLE_SYNONYMS = {
    "frontend": {"frontend", "front-end", "front", "ui", "web", "client",
                 "interface", "view", "design", "css", "styling"},
    "ui":       {"ui", "frontend", "front-end", "web", "client", "interface",
                 "view", "design"},
    "backend":  {"backend", "back-end", "back", "api", "server", "service",
                 "endpoint", "db", "database", "data"},
    "api":      {"api", "backend", "back-end", "server", "service", "endpoint"},
    "database": {"database", "db", "data", "backend", "storage", "persistence"},
}
# Every word that carries role meaning — used to keep short but meaningful tokens
# ('ui', 'db', 'api') while filler words ('the', 'one', 'agent') are dropped.
_ROLE_VOCAB = set(_ROLE_SYNONYMS).union(*_ROLE_SYNONYMS.values())


def _matches_workstream(target: str, key: str, assignee: str = "",
                        task: str = "") -> bool:
    """Does a fuzzy target ('frontend', 'the ui agent', 'claude', 'api') point at
    this workstream? Matches on id, assignee, or role words in the task text.

    Only role-vocabulary words or substantive (>=4 char) words become match
    tokens, so filler like 'the'/'one'/'agent' can't accidentally match another
    workstream's task text.
    """
    t = (target or "").strip().lower()
    if not t or t in ("all", "*", "everyone", "both", "team"):
        return True
    if t == key.lower() or t == assignee.lower():
        return True
    tokens = {w for w in t.split() if w in _ROLE_VOCAB or len(w) >= 4}
    for tok in list(tokens):
        tokens |= _ROLE_SYNONYMS.get(tok, set())
    if not tokens:
        return False
    hay = f" {key} {assignee} {task} ".lower()
    return any(f" {tok} " in hay or tok == key.lower() for tok in tokens)


def _swarm_preamble(agent: str, task: str, branch: str, project_dir: Path,
                    repo_map: str = "", *, coupled: bool = True,
                    contract: dict | None = None, owns=None,
                    acceptance=None, extra_note: str = "") -> str:
    """Build the marching orders handed to a swarm member.

    Coupling flips how the agent is told to coordinate:
      • coupled   → build to the frozen shared contract, watch the blackboard.
      • independent → ignore other agents entirely, build straight to acceptance.
    """
    board = Path(project_dir) / STATE_DIR / "swarm_state.json"
    head = (
        f"You are '{agent}', one member of a coordinated multi-agent engineering "
        f"swarm working on this project. You work in an isolated git worktree on "
        f"branch '{branch}' — commit your work to this branch as you reach "
        f"working milestones; never switch branches."
    )
    if coupled:
        coord = (
            f" The shared team blackboard is at {board}; consult it before "
            f"structural decisions and align with decisions recorded there. "
            f"Messages prefixed [SWARM UPDATE] are live context from teammates — "
            f"adapt to them."
        )
    else:
        coord = (
            " You work INDEPENDENTLY — there is no shared blackboard and no other "
            "agent depends on you. Do not wait on, coordinate with, or block on "
            "anyone; build strictly to YOUR TASK and the acceptance criteria below."
        )
    text = f"{head}{coord} YOUR TASK: {task}"

    # The frozen interface — so coupled agents never have to negotiate live.
    if contract:
        public = {k: v for k, v in contract.items() if k != "ownership"}
        if public:
            text += ("\n\nFROZEN INTERFACE CONTRACT (build EXACTLY to this; do not "
                     "change its shape):\n" + json.dumps(public, indent=2)[:1800])
    # Ownership boundary — the files this agent may touch and no other will.
    if owns:
        text += ("\n\nYOU EXCLUSIVELY OWN these paths (edit only inside them; other "
                 "agents own the rest):\n  " + "\n  ".join(str(o) for o in owns))
    # Definition of done — what the reviewer will check the branch against.
    if acceptance:
        text += ("\n\nACCEPTANCE CRITERIA — your branch is not done until ALL pass:\n"
                 + "\n".join(f"  - {a}" for a in acceptance))
    # A late requirement the user voiced at the approval gate — folded into the
    # spawn so the agent honors it from its very first action.
    if extra_note:
        text += ("\n\nUSER-ADDED REQUIREMENT (voiced by the operator — honor this "
                 f"throughout): {extra_note}")
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
        from actions.agent_delegation import AGENT_REGISTRY, spawn_succeeded

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
            if not spawn_succeeded(result):
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

    async def execute_plan(self, plan: dict, notes=None) -> str:
        """Turn a validated Chief-Architect plan into a live swarm.

        One isolated worktree per WORKSTREAM (keyed by workstream id, not agent
        key — so two same-brand agents never share a branch), each spawned with a
        coupling-aware, contract-bound preamble. Assumes human approval already
        happened at the front door; this only executes.

        `notes` carries requirements the operator voiced AT the approval gate
        ("yes, but make the UI beautiful"): a plain string applies to every
        agent; a dict {target: text} routes each note to the matching workstream
        (target may be an id, an assignee, or a role word like 'frontend').
        """
        from actions.agent_delegation import (AGENT_REGISTRY, agent_available,
                                              spawn_succeeded)
        from actions.chief_architect import validate_plan

        # Defensive gate — never execute a malformed plan even if a caller skipped
        # validation. Size the check to the plan's own agent_count.
        available = [k for k in AGENT_REGISTRY if agent_available(k)]
        ok, why = validate_plan(plan, plan.get("agent_count", len(available) or 1),
                                available)
        if not ok:
            return f"Plan rejected: {why}."

        self.ensure_repo()
        coupled = bool(plan.get("coupled"))
        contract = plan.get("contract") or {}
        try:
            from actions.repo_map import build_repo_map
            repo_map = await asyncio.to_thread(build_repo_map, self.project_dir, 4000)
        except Exception:
            repo_map = ""

        # Persist the dependency-first merge order so the Reviewer merges branches
        # in the right sequence later, even across a restart.
        ids = [w["id"] for w in plan.get("workstreams", [])]
        merge_order = [i for i in (plan.get("merge_order") or ids) if i in ids]
        self.board.update(lambda s: s.__setitem__("merge_order", merge_order))

        # Freeze the contract onto the blackboard up front so every coupled agent
        # reads the same interface from a single source of truth.
        if coupled and contract:
            public = {k: v for k, v in contract.items() if k != "ownership"}
            self.board.add_decision(
                "chief", "FROZEN CONTRACT: " + json.dumps(public)[:1500])

        started, errors = [], []
        for w in plan.get("workstreams", []):
            ws_id = w["id"]
            agent_key = w["assignee"]
            adapter = AGENT_REGISTRY.get(agent_key)
            if not adapter:
                errors.append(f"{ws_id}: unknown agent '{agent_key}'")
                continue
            try:
                wt = await asyncio.to_thread(self.ensure_worktree, ws_id)
            except Exception as e:
                errors.append(f"{ws_id}: {e}")
                continue
            branch = f"swarm/{ws_id}"
            note = self._note_for(notes, ws_id, agent_key, w.get("task", ""))
            prompt = _swarm_preamble(
                ws_id, w["task"], branch, self.project_dir, repo_map=repo_map,
                coupled=coupled, contract=contract if coupled else None,
                owns=w.get("owns"), acceptance=w.get("acceptance"),
                extra_note=note)
            result = await adapter.run(prompt, wt, self.project_dir.name,
                                       player=self.player)
            if not spawn_succeeded(result):
                errors.append(f"{ws_id} ({agent_key}): {result}")
                continue
            self.board.register_agent(ws_id, w["task"], str(wt), branch)
            self.board.set_agent(ws_id, assignee=agent_key)
            self._wire_thoughts(adapter, ws_id, wt)
            started.append(f"{ws_id}→{agent_key} on {branch}")
            self._log(f"SYS: 🐝 Swarm member '{ws_id}' ({agent_key}) live on {branch}.")

        mode = "coupled (shared contract)" if coupled else "independent"
        report = (f"Swarm executing [{mode}]: {', '.join(started) or 'none'}. "
                  f"Merge order: {' → '.join(plan.get('merge_order') or [w['id'] for w in plan.get('workstreams', [])])}.")
        if errors:
            report += f" Issues: {'; '.join(errors)}."
        return report

    @staticmethod
    def _note_for(notes, ws_id: str, assignee: str, task: str) -> str:
        """Resolve which approval-gate note (if any) applies to this workstream."""
        if not notes:
            return ""
        if isinstance(notes, str):
            return notes.strip()          # a blanket note → every agent gets it
        if isinstance(notes, dict):
            for target, text in notes.items():
                if text and _matches_workstream(str(target), ws_id, assignee, task):
                    return str(text).strip()
        return ""

    def inject(self, message: str, target: str = "all",
               interrupt: bool = False, source: str = "user") -> str:
        """Chime in on running agents mid-development with a fresh user request.

        `target` selects who hears it — a workstream id, an assignee, a role word
        ('frontend', 'the backend one'), or 'all'. `interrupt=True` does a hard
        Ctrl+C redirect (drop what you're doing); default just types the note into
        the live session so the agent folds it in and keeps working. The request
        is also recorded on the blackboard so a later-spawned agent still sees it.
        """
        message = (message or "").strip()
        if not message:
            return "Nothing to inject — no message given."
        live = self._live_sessions()
        if not live:
            return "No live agents to update — the swarm isn't running."
        state = self.board.read()
        matched = [k for k in live
                   if _matches_workstream(
                       target, k,
                       state["agents"].get(k, {}).get("assignee", ""),
                       state["agents"].get(k, {}).get("task", ""))]
        if not matched:
            return (f"No live agent matches '{target}'. Running now: "
                    f"{', '.join(sorted(live))}.")

        line = f"[USER REQUEST — {source} added this] {message}"
        delivered = []
        for key in matched:
            sess = live[key]
            try:
                if interrupt:
                    sess.interrupt()
                    time.sleep(0.7)       # let the CLI settle back to its prompt
                sess.send_line(line)
                delivered.append(key)
            except OSError:
                pass
        # Durable trail: the request lives on the board too.
        self.board.add_decision(source, f"USER REQUEST → {', '.join(matched)}: {message}")
        verb = "redirected" if interrupt else "notified"
        self._log(f"SYS: 🗣 Operator {verb} {', '.join(delivered)}: {message[:80]}")
        return (f"{verb.capitalize()} {', '.join(delivered) or 'no one'} with your "
                f"request. It's on the blackboard too, so any later agent sees it.")

    def _wire_thoughts(self, adapter, agent_key: str, worktree: Path):
        sess = POOL.get_alive(adapter.pool_key, worktree)
        watcher = getattr(sess, "watcher", None) if sess else None
        if watcher:
            watcher.on_thought = (
                lambda _n, text, status, a=agent_key:
                    self.board.set_agent(a, last_thought=text))

    def _live_sessions(self) -> dict:
        """board key (workstream id or agent key) -> live PtySession.

        Joins on the resolved WORKTREE PATH, not the agent key — POOL keys
        sessions by (pool_key, resolved_dir) while the board keys by workstream
        id, so the directory is the only reliable join. Both sides are resolved
        so a symlinked project path can't silently break the match.
        """
        state = self.board.read()
        sessions = {}
        for (_, sdir), sess in POOL.all_sessions().items():
            if sess.is_alive():
                try:
                    sessions[str(Path(sdir).resolve())] = sess
                except OSError:
                    sessions[sdir] = sess
        out = {}
        for board_key, info in state["agents"].items():
            wt = info.get("worktree")
            if not wt:
                continue
            try:
                wt_r = str(Path(wt).resolve())
            except OSError:
                wt_r = wt
            sess = sessions.get(wt_r) or sessions.get(wt)
            if sess:
                out[board_key] = sess
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
        if agent:
            targets = [agent]
        else:
            # Merge in the plan's dependency-first order; anything not listed
            # (e.g. a sentinel re-delegation) goes last.
            keys = list(state["agents"].keys())
            order = [k for k in (state.get("merge_order") or []) if k in keys]
            targets = order + [k for k in keys if k not in order]
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


async def execute_plan(plan: dict, project_dir, player=None, notes=None) -> str:
    """Module-level entry: execute a validated Chief-Architect plan on a project.

    The front door (voice dispatch) calls this AFTER the human approves the spoken
    plan summary. `notes` carries any requirement the operator voiced with their
    approval. Enforces the Space-Eagle self-edit safeguard, then delegates to the
    project's orchestrator.
    """
    project_dir = Path(project_dir).expanduser().resolve()
    if project_dir == SPACE_EAGLE_HOME or SPACE_EAGLE_HOME in project_dir.parents:
        return ("Blocked: Space-Eagle safeguard — the swarm cannot operate on "
                "Aethelark's own codebase.")
    project_dir.mkdir(parents=True, exist_ok=True)
    orch = get_orchestrator(project_dir, player)
    return await orch.execute_plan(plan, notes=notes)


async def swarm_orchestrate(parameters: dict, player=None) -> str:
    """Voice-tool entry point. Actions: plan | execute | launch | status |
    broadcast | review | stop."""
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
    if action == "plan":
        # Spawn the chief architect; return the spoken plan summary for the
        # approval gate. The validated plan is left at .space_eagle/plan.json.
        from actions.chief_architect import run_chief, render_plan_summary
        goal = (parameters.get("goal") or parameters.get("mission") or "").strip()
        if not goal:
            return "Ask: What should the swarm build?"
        max_agents = int(parameters.get("max_agents") or 2)
        plan, status = await run_chief(goal, project_dir, max_agents=max_agents,
                                       player=player)
        if not plan:
            return f"Chief architect could not produce a plan: {status}."
        return ("PLAN READY (awaiting your approval). "
                + render_plan_summary(plan))
    if action == "execute":
        plan = parameters.get("plan")
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except ValueError:
                return "Invalid plan: expected a JSON object."
        if not plan:
            # Fall back to the last plan the chief wrote for this project.
            pf = project_dir / STATE_DIR / "plan.json"
            if pf.exists():
                try:
                    plan = json.loads(pf.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    plan = None
        if not plan:
            return ("Ask: No plan provided or found — run the chief architect "
                    "first (action 'plan').")
        notes = parameters.get("notes")
        if isinstance(notes, str) and notes.strip().startswith("{"):
            try:
                notes = json.loads(notes)
            except ValueError:
                pass  # keep it as a blanket string note
        return await orch.execute_plan(plan, notes=notes)
    if action == "inject":
        # Mid-development: relay a fresh user request to running agent(s).
        return await asyncio.to_thread(
            orch.inject,
            parameters.get("message", ""),
            (parameters.get("target") or "all").strip(),
            bool(parameters.get("interrupt")),
            parameters.get("source") or "user")
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
