"""A live coding-agent session spoken to in JSON instead of a terminal.

`--input-format stream-json` keeps one process alive across many turns, and an
interrupt is a message with an acknowledgement:

    ->
{"type":"control_request","request_id":"req_1","request":{"subtype":"interrupt"}}
    <- {"type":"control_response","response":{"subtype":"success","request_id":"re
q_1",…}}

Permissions
-----------
`--permission-prompt-tool stdio` makes Claude ask over the stream before anything
its rules do not already allow, and wait:

    <- {"type":"control_request","request_id":…,
        "request":{"subtype":"can_use_tool","tool_name":"Bash",
                   "input":{"command":…},"description":…}}
    -> {"type":"control_response","response":{"subtype":"success",
        "request_id":…,"response":{"behavior":"allow","updatedInput":…}}}

Measured on claude 2.1.316. A danger pattern (core/prompt_reflex.py) or a file
outside the project is held for the user (core/escalations.py) while the agent
waits; anything else is allowed and logged. The mode is `default` so every change
comes here: acceptEdits lets Claude delete inside the project on its own.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Sequence

from core.agent_stream import AgentEvent, RESULT, STREAM_PARSERS, THINKING

# Permission modes offered. bypassPermissions and --dangerously-skip-permissions are
# absent on purpose.
ALLOWED_PERMISSION_MODES = ("acceptEdits", "manual", "plan", "default")

#: How long a held request waits for the user before it is refused.
HOLD_S = 600.0
# A finished session quiet this long is closed; the next request in that project resumes
# the same conversation. Idle, one holds about 230 MB.
IDLE_CLOSE_S = 1800.0
_UUID = __import__("re").compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_saved_lock = threading.Lock()


def _saved_path() -> Path:
    from core import user_paths
    return user_paths.config_dir() / "agent_sessions.json"


def _saved() -> dict:
    try:
        return json.loads(_saved_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _transcript_exists(session_id: str) -> bool:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    return any(base.glob(f"*/{session_id}.jsonl"))


def saved_conversation(agent_key: str, project_dir: Path | str) -> str:
    """The conversation to resume in this project, if Claude still has it."""
    if PROTOCOLS.get(agent_key) != "claude":
        return ""
    sid = str(_saved().get(f"{agent_key}@{Path(project_dir).resolve()}") or "")
    return sid if _UUID.fullmatch(sid) and _transcript_exists(sid) else ""


def remember_conversation(agent_key: str, project_dir: Path | str, session_id: str) -> None:
    if not _UUID.fullmatch(session_id or ""):
        return
    with _saved_lock:
        d = _saved()
        d.pop(key := f"{agent_key}@{Path(project_dir).resolve()}", None)
        d[key] = session_id
        d = dict(list(d.items())[-50:])
        try:
            _saved_path().parent.mkdir(parents=True, exist_ok=True)
            _saved_path().write_text(json.dumps(d, indent=1), encoding="utf-8")
        except OSError:
            pass


def close_idle(sessions, idle_s: float = IDLE_CLOSE_S) -> list:
    """Close finished headless sessions quiet for `idle_s`. Returns the closed."""
    closed = []
    for sess in list(sessions):
        if (isinstance(sess, HeadlessSession) and sess.is_alive() and not sess.turn_open
                and not sess.waiting_for_user()
                and sess.seconds_since_activity() >= idle_s):
            sess.close()
            closed.append(sess)
    return closed

SESSION_AGENTS = {"claude_code": "claude", "antigravity_cli": "agy"}
PROTOCOLS = {"claude_code": "claude", "antigravity_cli": "agy"}


def session_argv(agent_key: str, *, cwd: Path | str,
                 permission_mode: str = "default",
                 model: str = "",
                 allowed_tools: Sequence[str] = (),
                 disallowed_tools: Sequence[str] = (),
                 resume: str = "") -> list[str]:
    """argv for a persistent streaming session."""
    binary = SESSION_AGENTS.get(agent_key)
    if binary is None:
        raise ValueError(
            f"{agent_key!r} has no verified streaming session protocol; "
            f"use the PTY path. Known: {', '.join(sorted(SESSION_AGENTS))}.")
    if permission_mode not in ALLOWED_PERMISSION_MODES:
        raise ValueError(
            f"permission mode {permission_mode!r} is not offered here. "
            f"Allowed: {', '.join(ALLOWED_PERMISSION_MODES)}. "
            f"Bypassing permissions is not available through this path.")

    if PROTOCOLS[agent_key] == "agy":
        mode = "plan" if permission_mode == "plan" else "accept-edits"
        argv = [binary, "--input-format", "stream-json",
                "--output-format", "stream-json", "--mode", mode, "--sandbox"]
        if model:
            argv += ["--model", model]
        return argv + ["--print="]

    argv = [binary, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", permission_mode,
            "--permission-prompt-tool", "stdio"]
    if model:
        argv += ["--model", model]
    if resume:
        argv += ["--resume", resume]
    if allowed_tools:
        argv += ["--allowed-tools", ",".join(allowed_tools)]
    if disallowed_tools:
        argv += ["--disallowed-tools", ",".join(disallowed_tools)]
    return argv


class HeadlessSession:
    """One long-lived agent process, spoken to in JSON. API-compatible with
    PtySession where agent_delegation touches it, so both live behind one pool.
    """

    def __init__(self, agent_key: str, agent_name: str,
                 project_dir: Path | str, *,
                 argv: Sequence[str] | None = None,
                 on_event: Callable[[AgentEvent], None] | None = None,
                 on_line: Callable[[str], None] | None = None,
                 permission_mode: str = "default",
                 model: str = "",
                 resume: str = "") -> None:
        self.agent_key = agent_key
        self.agent_name = agent_name
        self.project_dir = Path(project_dir)
        self.created_at = time.time()
        self.player = None
        self.watcher = None                 # no screen to watch. Kept for shape.
        # Set by swarm_orchestrator._wire_thoughts to feed the blackboard.
        self.on_thought = None

        self._on_event = on_event
        self.on_result: Callable[[AgentEvent], None] | None = None
        self.protocol = PROTOCOLS.get(agent_key, "claude")
        self._parser = STREAM_PARSERS[self.protocol]()
        self.last_activity = time.time()
        self.turn_open = False
        self.last_text = ""
        self.last_result: AgentEvent | None = None
        self.last_result_at = 0.0
        self._held: list[str] = []
        self._denied: set[str] = set()
        self._hooks: list[Callable[[str], None]] = []
        if on_line is not None:
            self._hooks.append(on_line)

        self._ids = count(1)
        self._last_line = ""
        self.sent_request_ids: list[str] = []
        self._closed = False
        self._write_lock = threading.Lock()

        self.log_path = self._make_log_path()
        try:
            self.log_path.unlink()
        except OSError:
            pass
        self._log = open(self.log_path, "a", encoding="utf-8")

        self._argv = list(argv) if argv is not None else session_argv(
            agent_key, cwd=self.project_dir,
            permission_mode=permission_mode, model=model, resume=resume)
        self.session_id = resume

        # Raises OSError when the binary is missing; callers report it.
        self._err = open(self.log_path.with_suffix(".err"), "a", encoding="utf-8")
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._err,
            cwd=str(self.project_dir), text=True, bufsize=1,
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "aethelark-swarm"},
            start_new_session=True,
        )

        self._reader = threading.Thread(
            target=self._read_loop, name=f"agent-{agent_key}", daemon=True)
        self._reader.start()


    def _make_log_path(self) -> Path:
        safe = self.project_dir.name.replace(os.sep, "_") or "root"
        return Path(tempfile.gettempdir()) / (
            f"aethelark_session_{self.agent_key}_{safe}.log")

    def _read_loop(self) -> None:
        stdout = self._proc.stdout
        if stdout is None:
            return
        try:
            for line in stdout:
                self._ingest(line)
        except (OSError, ValueError):
            pass                            # pipe closed under us; close() knows

    def seconds_since_activity(self) -> float:
        return time.time() - self.last_activity

    def _ingest(self, line: str) -> None:
        raw = (line or "").strip()
        if not raw:
            return
        self.last_activity = time.time()

        # control_response acknowledges an interrupt; not narrated.
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get("type") == "control_response":
            return
        if (isinstance(obj, dict) and obj.get("type") == "control_request"
                and (obj.get("request") or {}).get("subtype") == "can_use_tool"):
            self._ask(obj)
            return

        for event in self._parser.feed(raw):
            # Reasoning is private and never leaves.
            if event.kind == THINKING:
                continue
            self._emit(event)


    def _judge(self, tool: str, inp: dict) -> tuple[str, tuple[str, str] | None]:
        """(what it wants to do, why it must be held or None)."""
        from core.prompt_reflex import find_danger
        path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
        if tool == "Bash":
            what = str(inp.get("command") or "")
        elif path:
            what = f"{tool} {path}"
        elif inp.get("url"):
            what = f"{tool} {inp.get('url')}"
        else:
            what = f"{tool} {json.dumps(inp, default=str)[:200]}"
        what = " ".join(what.split())[:300]
        danger = find_danger(what)
        if danger:
            return what, danger
        if path:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.project_dir / target
            root = self.project_dir.resolve()
            target = target.resolve()
            if target != root and root not in target.parents:
                return what, ("OUTSIDE_PROJECT", "it touches a file outside the project folder")
        return what, None

    def _ask(self, obj: dict) -> None:
        req = obj.get("request") or {}
        rid = str(obj.get("request_id") or "")
        tool = str(req.get("tool_name") or "")
        inp = req.get("input") if isinstance(req.get("input"), dict) else {}
        what, held = self._judge(tool, inp)
        if held is None:
            self._answer(rid, "allow", inp)
            self._say(f"· allowed: {what}")
            return

        from core import escalations
        said = " ".join(str(req.get("description") or inp.get("description") or "").split())
        answered = threading.Event()

        def on_resolve(verdict: str) -> None:
            answered.set()
            if verdict != "allow":
                self._denied.add(what)
            self._answer(rid, verdict, inp)
            self._say(f"· {'allowed' if verdict == 'allow' else 'refused'}: {what}")

        esc = escalations.raise_escalation(
            self.agent_name, SimpleNamespace(rule_id=held[0], reason=held[1]),
            f"{said}\n{what}" if said else what, player=self.player,
            on_resolve=on_resolve)
        self._held.append(esc.id)

        def expire() -> None:
            if not answered.wait(HOLD_S):
                escalations.resolve(esc.id, "deny", by="timeout")
        threading.Thread(target=expire, daemon=True, name="agent-hold").start()

    def _answer(self, request_id: str, verdict: str, inp: dict) -> bool:
        if verdict == "allow":
            response = {"behavior": "allow", "updatedInput": inp}
        else:
            response = {"behavior": "deny", "message": (
                "The user did not allow this. Do not try it again. Carry on "
                "without it, or stop and say what you need.")}
        return self._write({"type": "control_response",
                            "response": {"subtype": "success",
                                         "request_id": request_id,
                                         "response": response}})

    def _say(self, text: str) -> None:
        try:
            self._log.write(text + "\n")
            self._log.flush()
        except (OSError, ValueError):
            pass
        for hook in list(self._hooks):
            try:
                hook(text)
            except Exception:
                pass

    def waiting_for_user(self) -> bool:
        from core import escalations
        return any(e.id in self._held for e in escalations.pending())

    def _emit(self, event: AgentEvent) -> None:
        sid = str((event.data or {}).get("session_id") or "")
        if event.kind in ("init", RESULT) and sid and sid != self.session_id:
            self.session_id = sid
            remember_conversation(self.agent_key, self.project_dir, sid)
        text = self._render(event)
        if event.kind == "text" and event.text.strip():
            self.last_text = event.text.strip()

        # The terminal `result` repeats the final assistant message: drop an exact
        # repeat of the last line.
        if text and text == self._last_line:
            text = ""
        elif text:
            self._last_line = text

        if text:
            self._say(text)
        if self.on_thought is not None and event.kind in ("text", "tool_use"):
            try:
                self.on_thought(self.agent_name,
                                event.text or f"running {event.name}",
                                event.kind)
            except Exception:
                pass

        if event.kind == RESULT:
            self.turn_open = False
            self.last_result = event
            self.last_result_at = time.time()
            self._report_denials(event.data.get("permission_denials") or [])
            if self.on_result is not None:
                try:
                    self.on_result(event)
                except Exception:
                    pass

        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                pass

    def _report_denials(self, denials) -> None:
        fresh = []
        for d in denials:
            inp = d.get("tool_input") if isinstance(d, dict) else None
            what = (inp or {}).get("command") or (d.get("tool_name") if isinstance(d, dict) else "")
            what = " ".join(str(what or "").split())[:120]
            if what and what not in self._denied:
                self._denied.add(what)
                fresh.append(what)
        if not fresh:
            return
        try:
            from core.escalations import notify
            notify(self.agent_name, "was not allowed to run " + "; ".join(fresh[:3]))
        except Exception:
            pass

    @staticmethod
    def _render(event: AgentEvent) -> str:
        """One line a human would want to read."""
        if event.kind == "text":
            return event.text
        if event.kind == "tool_use":
            return f"· {event.name}"
        if event.kind == "progress":
            return ""                       # a counter, not a line
        if event.kind == RESULT:
            if event.ok:
                return event.text or "(done)"
            return f"FAILED: {event.text}"
        return ""


    def _write(self, payload: dict) -> bool:
        if self._closed or not self.is_alive():
            return False
        stdin = self._proc.stdin
        if stdin is None:
            return False
        try:
            with self._write_lock:
                stdin.write(json.dumps(payload) + "\n")
                stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def send_line(self, text: str) -> bool:
        """One user message, JSON-encoded: the content is data, and a newline stays
        inside one message.
        """
        if self.protocol == "agy":
            payload = {"event": "user",
                       "message": {"role": "user", "content": text}}
        else:
            payload = {"type": "user",
                       "message": {"role": "user",
                                   "content": [{"type": "text", "text": text}]}}
        sent = self._write(payload)
        if sent:
            self.turn_open = True
            self.last_activity = time.time()
        return sent

    def interrupt(self) -> bool:
        """Stop what the agent is doing, and get an acknowledgement."""
        if self.protocol != "claude":
            return False
        request_id = f"aethelark_{next(self._ids)}"
        self.sent_request_ids.append(request_id)
        return self._write({"type": "control_request",
                            "request_id": request_id,
                            "request": {"subtype": "interrupt"}})

    def add_feed_hook(self, hook: Callable[[str], None]) -> None:
        self._hooks.append(hook)


    def is_alive(self) -> bool:
        return not self._closed and self._proc.poll() is None

    def snapshot_tail(self, max_bytes: int = 8192) -> bytes:
        """Bytes, matching PtySession: agent_delegation decodes it on the failure
        path.
        """
        try:
            data = self.log_path.read_bytes()
        except OSError:
            return b""
        return data[-max_bytes:]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._held:
            from core import escalations
            for esc_id in self._held:
                escalations.resolve(esc_id, "deny", by="session closed")
        for pipe in (self._proc.stdin,):
            try:
                if pipe is not None:
                    pipe.close()            # EOF asks the agent to finish
            except Exception:
                pass
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        for pipe in (self._proc.stdout, self._proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass
        for f in (self._log, self._err):
            try:
                f.close()
            except Exception:
                pass
