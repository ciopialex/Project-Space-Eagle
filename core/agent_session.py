"""A live coding-agent session that is a protocol, not a terminal.

`actions/pty_session.py` runs the agent's interactive TUI behind a pseudo-
terminal. Everything downstream then exists to undo that: `AgentScreenWatcher`
rebuilds the screen with `pyte` to work out what the agent is doing, and
`_hud_pump` strips the spinner repaints back out with three stacked regexes.
Both are reading a picture of a program instead of talking to it, and both
break silently the day the vendor restyles something.

None of it is necessary. Measured against `claude` 2.1.235 on 2026-08-19:

  * `--input-format stream-json` keeps ONE process alive across many turns.
    Two messages, two results, one session id, process still running — which
    is the whole reason `PtySession` existed.
  * An interrupt is a message, not a signal:

        -> {"type":"control_request","request_id":"req_1",
            "request":{"subtype":"interrupt"}}
        <- {"type":"control_response",
            "response":{"subtype":"success","request_id":"req_1",
                        "response":{"still_queued":[]}}}

    The process stays alive and the next user message is accepted and
    answered. That is `interject_agent`'s Ctrl-C-then-redirect flow, with an
    acknowledgement it never had — `session.interrupt()` on a PTY writes 0x03
    and hopes.

What this changes about permissions, deliberately stated
-------------------------------------------------------
The PTY path let `core/prompt_reflex.py` adjudicate each approval prompt as it
appeared on screen: auto-answer the recognised-safe ones, escalate the rest to
a human and let the agent block. That model depended on the prompt being
*rendered*, and it cannot be reproduced here — this CLI has no
`--permission-prompt-tool`, so it never asks over the stream. It decides from
`--permission-mode`, `--allowed-tools` and its settings.

So the headless path is coarser by construction, and the response is to make it
coarser in the SAFE direction:

  * the default mode is `acceptEdits`, never `bypassPermissions`. A swarm agent
    exists to edit files in its own worktree; it does not need blanket
    approval, and `--dangerously-skip-permissions` is refused outright rather
    than left as an option someone reaches for at 2am.
  * whatever is refused comes back as structured `permission_denials` on the
    result, which the caller routes to `core/escalations.py`.

The behavioural difference worth knowing: an agent no longer BLOCKS waiting for
a human to approve something. It proceeds without that tool and the denial is
reported afterwards. That is a real change of shape — visible and recoverable
rather than silent, but not the same as stopping.
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
from typing import Callable, Sequence

from core.agent_stream import AgentEvent, RESULT, STREAM_PARSERS, THINKING

#: Permission modes this module will hand to an agent. `bypassPermissions` and
#: the `--dangerously-skip-permissions` flags are deliberately absent: the
#: headless path must not become more permissive than the screen-watched path
#: it replaces merely because nobody is looking at a screen any more.
ALLOWED_PERMISSION_MODES = ("acceptEdits", "manual", "plan", "default")

#: Agents with a verified streaming-session protocol. Same rule as
#: `agent_stream.HEADLESS_AGENTS`: membership requires a real capture, not a
#: promising line in `--help`.
BUILD_TOOLS = (
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)", "Bash(git diff:*)",
    "Bash(git log:*)", "Bash(git mv:*)", "Bash(git rm:*)", "Bash(git restore:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(mkdir:*)", "Bash(touch:*)", "Bash(cp:*)",
    "Bash(mv:*)", "Bash(pwd)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)",
    "Bash(npm install:*)", "Bash(npm ci:*)", "Bash(npm run:*)", "Bash(npm test:*)",
    "Bash(pnpm install:*)", "Bash(pnpm run:*)", "Bash(pnpm test:*)",
    "Bash(yarn install:*)", "Bash(yarn run:*)", "Bash(yarn test:*)",
    "Bash(pip install:*)", "Bash(python3 -m venv:*)", "Bash(python3 -m pip install:*)",
    "Bash(python3 -m pytest:*)", "Bash(pytest:*)", "Bash(python3 -m py_compile:*)",
    "Bash(cargo build:*)", "Bash(cargo test:*)", "Bash(go build:*)", "Bash(go test:*)",
    "Bash(make:*)",
)

SESSION_AGENTS = {"claude_code": "claude", "antigravity_cli": "agy"}
PROTOCOLS = {"claude_code": "claude", "antigravity_cli": "agy"}


def session_argv(agent_key: str, *, cwd: Path | str,
                 permission_mode: str = "acceptEdits",
                 model: str = "",
                 allowed_tools: Sequence[str] = (),
                 disallowed_tools: Sequence[str] = ()) -> list[str]:
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
            "--permission-mode", permission_mode]
    if not allowed_tools and permission_mode == "acceptEdits":
        allowed_tools = BUILD_TOOLS
    if model:
        argv += ["--model", model]
    if allowed_tools:
        argv += ["--allowed-tools", ",".join(allowed_tools)]
    if disallowed_tools:
        argv += ["--disallowed-tools", ",".join(disallowed_tools)]
    return argv


class HeadlessSession:
    """One long-lived agent process, spoken to in JSON.

    Deliberately API-compatible with `PtySession` where `agent_delegation`
    touches it — `send_line`, `interrupt`, `is_alive`, `close`, `log_path`,
    `snapshot_tail`, `created_at`, `player`, `watcher`, `add_feed_hook` — so
    the two can live behind one pool and one call site while agents that have
    no headless protocol keep the terminal.
    """

    def __init__(self, agent_key: str, agent_name: str,
                 project_dir: Path | str, *,
                 argv: Sequence[str] | None = None,
                 on_event: Callable[[AgentEvent], None] | None = None,
                 on_line: Callable[[str], None] | None = None,
                 permission_mode: str = "acceptEdits",
                 model: str = "") -> None:
        self.agent_key = agent_key
        self.agent_name = agent_name
        self.project_dir = Path(project_dir)
        self.created_at = time.time()
        self.player = None
        self.watcher = None                 # no screen to watch. Kept for shape.
        #: Set by `swarm_orchestrator._wire_thoughts` to feed the blackboard.
        #: The PTY path sourced this from `AgentScreenWatcher.on_thought`,
        #: which read <thinking> blocks and status lines off a reconstructed
        #: screen. Here the same signal is already a typed event, so without
        #: this hook a headless agent's row on the board would simply go blank.
        self.on_thought = None

        self._on_event = on_event
        self.on_result: Callable[[AgentEvent], None] | None = None
        self.protocol = PROTOCOLS.get(agent_key, "claude")
        self._parser = STREAM_PARSERS[self.protocol]()
        self.last_activity = time.time()
        self.turn_open = False
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
            permission_mode=permission_mode, model=model)

        # Raises OSError when the binary is missing. Callers must fail
        # honestly rather than report a session that was never started —
        # `agent_delegation` already carries that scar ("exited immediately
        # after launch").
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

    # ------------------------------------------------------------- plumbing

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

        # control_response is protocol, not conversation: it acknowledges an
        # interrupt and is not something the operator needs narrated.
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get("type") == "control_response":
            return

        for event in self._parser.feed(raw):
            # Reasoning is private and voluminous. It was the single biggest
            # source of HUD noise on the PTY path; here it simply never leaves.
            if event.kind == THINKING:
                continue
            self._emit(event)

    def _emit(self, event: AgentEvent) -> None:
        text = self._render(event)

        # The terminal `result` repeats the final assistant message verbatim,
        # so a plain reply was written to the transcript twice — measured
        # against the real binary as "alpha\nalpha\nbeta\nbeta". Dropping an
        # exact repeat of the line just emitted is enough. Note what this is
        # NOT: `_hud_pump` deduped by normalising spinner glyphs and guessing
        # from line length, because it could not tell a repaint from new work.
        # Here the events are already distinct and the comparison is equality.
        if text and text == self._last_line:
            text = ""
        elif text:
            self._last_line = text

        if text:
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
        if self.on_thought is not None and event.kind in ("text", "tool_use"):
            try:
                self.on_thought(self.agent_name,
                                event.text or f"running {event.name}",
                                event.kind)
            except Exception:
                pass

        if event.kind == RESULT:
            self.turn_open = False
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
        """One line a human would want to read.

        This is the replacement for `_hud_pump`'s three heuristics, and it is
        four cases instead of sixty lines, because the stream already says
        which is which.
        """
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

    # -------------------------------------------------------------- speaking

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
        """One user message.

        JSON-encoded, so the content is data. The PTY path interpolated the
        prompt into `claude '{prompt}'` and hand-escaped quotes; an apostrophe
        made it a shell syntax error and a semicolon made it a second command.
        A newline here stays inside one message rather than becoming two turns.
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
        """Stop what the agent is doing, and know that it heard.

        `PtySession.interrupt` writes 0x03 into a terminal and returns True if
        the write succeeded — which says the pipe is open, not that anything
        stopped. This asks, with an id, and the agent answers.
        """
        if self.protocol != "claude":
            return False
        request_id = f"aethelark_{next(self._ids)}"
        self.sent_request_ids.append(request_id)
        return self._write({"type": "control_request",
                            "request_id": request_id,
                            "request": {"subtype": "interrupt"}})

    def add_feed_hook(self, hook: Callable[[str], None]) -> None:
        self._hooks.append(hook)

    # ------------------------------------------------------------- lifecycle

    def is_alive(self) -> bool:
        return not self._closed and self._proc.poll() is None

    def snapshot_tail(self, max_bytes: int = 8192) -> bytes:
        """Bytes, matching `PtySession`. `agent_delegation` calls `.decode()`
        on this, on the failure path, where a TypeError is least welcome."""
        try:
            data = self.log_path.read_bytes()
        except OSError:
            return b""
        return data[-max_bytes:]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
