"""Drive a coding-agent CLI as a typed event stream instead of a repainting TUI.

`agent_delegation.py` runs `claude '{prompt}'` — the *interactive* application —
through a pseudo-terminal, and then spends sixty lines undoing the consequences.
A full-screen TUI repaints its status line continuously, the PTY reports every
repaint as another line, and `_hud_pump` filters them back out with three
heuristics stacked on each other:

    Gen / Gene / Gener / Generating…          -> "progressive redraw"
    1thinking / ✱still thinking / ✻thinking   -> a regex over nine keywords
    Deliberating… (42s · 880 tokens)          -> a length cap and a "/" check

That is screen-scraping, and it fails the way screen-scraping always fails: the
vendor restyles a spinner, the parser silently starts lying, and nothing in the
system knows. The comment "real work lines name a file, status lines never do"
is a heuristic about someone else's UI, held only by hope.

There is no need for any of it. `claude -p --output-format stream-json` emits
one JSON object per line, and the objects say what the heuristics were guessing:

    {"type":"system","subtype":"init","model":…,"session_id":…,"cwd":…}
    {"type":"system","subtype":"thinking_tokens","estimated_tokens":12,…}
    {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read",…}]}}
    {"type":"user","message":{"content":[{"type":"tool_result","content":…}]}}
    {"type":"result","subtype":"success","is_error":false,"result":"pong",…}

Progress stops being a string to pattern-match and becomes a monotonically
rising token count. "Which tool is it running" stops being inferred from
whether a line contains a slash and becomes `.name`. Success stops being the
absence of scary words and becomes `is_error`.

Scope, deliberately narrow: this parses and drives. It does not decide which
agent to use (`core/capability/agents.py`) or where work happens
(`swarm_orchestrator.py`). The PTY path stays exactly as it is for agents with
no headless JSON mode, and for the interject/Ctrl-C flow, which needs a live
terminal by nature.

Verified against `claude` 2.1.235 on 2026-08-19; the captures that verified it
are checked in under `tests/fixtures/agent_stream/`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

#: Event kinds. Deliberately fewer than the wire has: the caller cares about
#: what happened, not which of three `system` subtypes carried it.
INIT = "init"                 # session opened; model, cwd and session id known
THINKING = "thinking"         # private reasoning. Never show this to a user.
TEXT = "text"                 # prose the agent addressed to whoever asked
TOOL_USE = "tool_use"         # the agent decided to run something
TOOL_RESULT = "tool_result"   # what running it produced
PROGRESS = "progress"         # still alive, with a number attached
RATE_LIMIT = "rate_limit"     # the provider pushed back
RESULT = "result"             # terminal. Exactly one per run, always emitted.


@dataclass(frozen=True)
class AgentEvent:
    """One thing that happened, already decided.

    `ok` is tri-state on purpose. `None` means "this kind of event carries no
    verdict" — the same reason `ToolResult.to_response()` omits `ok` for tools
    that never migrated. A progress tick that claimed success would be a lie
    with teeth, and this codebase has already paid for one of those.
    """
    kind: str
    text: str = ""
    name: str = ""
    ok: bool | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.kind == RESULT


# --------------------------------------------------------------------- parsing

def _blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The content blocks of an assistant/user envelope, defensively.

    The envelope has been `{"message": {"content": [...]}}` since the format
    shipped, but a `content` that arrives as a bare string (some providers do
    this) must degrade to one text block rather than raise mid-mission.
    """
    content = (payload.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _result_event(obj: dict[str, Any]) -> AgentEvent:
    """The terminal verdict.

    `is_error` is the authority, and `subtype` is a decoy. Measured against the
    real binary on 2026-08-19: a request for a model that does not exist comes
    back as

        {"is_error": true, "subtype": "success", "api_error_status": 404,
         "terminal_reason": "api_error", "result": "There's an issue with …"}

    — `subtype: "success"` on a hard 404. Anything that decided success by
    reading `subtype`, or by looking for scary words in `result`, would call
    that a passing run. This is the same shape as the failure `ToolResult` was
    built for: prose that reads like success over a state that is not.
    """
    usage = obj.get("usage") or {}
    return AgentEvent(
        kind=RESULT,
        text=str(obj.get("result") or ""),
        ok=not bool(obj.get("is_error")),
        data={
            "subtype": obj.get("subtype") or "",
            "session_id": obj.get("session_id") or "",
            "duration_ms": obj.get("duration_ms") or 0,
            "num_turns": obj.get("num_turns") or 0,
            "cost_usd": obj.get("total_cost_usd") or 0.0,
            "input_tokens": usage.get("input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
            # An empty list is meaningfully different from a missing key: the
            # headless path has no way to show an approval prompt, so a run that
            # did nothing because it was denied must be distinguishable from a
            # run that did nothing because there was nothing to do.
            "permission_denials": obj.get("permission_denials") or [],
            # Why it ended, in the CLI's own words. `api_error_status` carries
            # the HTTP code when the provider refused, which is the difference
            # between "retry this" and "your config is wrong".
            "terminal_reason": obj.get("terminal_reason") or "",
            "api_error_status": obj.get("api_error_status"),
        },
    )


def parse_line(line: str) -> AgentEvent | None:
    """One line of the stream, or None if it carries nothing we model.

    None for four separate reasons, all of them normal: the line was blank, it
    was not JSON at all (agent CLIs print npm warnings to stdout), it was an
    event type this version does not know, or it was an envelope whose blocks
    all resolved to nothing. Returning None rather than raising is the whole
    robustness story — a stray warning on stdout must not end a mission.

    Multi-block assistant envelopes yield only their first interesting block;
    use `parse_lines` to see all of them.
    """
    events = list(parse_lines([line]))
    return events[0] if events else None


def parse_lines(lines: Iterable[str]) -> Iterator[AgentEvent]:
    """Every event in a stream, in order. One line may yield several events —
    an assistant turn can carry thinking and a tool call in one envelope."""
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue                    # not JSON: someone else's warning
        if not isinstance(obj, dict):
            continue

        kind = obj.get("type")

        if kind == "result":
            yield _result_event(obj)

        elif kind == "rate_limit_event":
            yield AgentEvent(kind=RATE_LIMIT, data=dict(obj))

        elif kind == "system":
            subtype = obj.get("subtype")
            if subtype == "init":
                tools = obj.get("tools")
                yield AgentEvent(kind=INIT, data={
                    "session_id": obj.get("session_id") or "",
                    "model": obj.get("model") or "",
                    "cwd": obj.get("cwd") or "",
                    "tools": len(tools) if isinstance(tools, (list, tuple)) else (tools or 0),
                })
            elif subtype == "thinking_tokens":
                yield AgentEvent(kind=PROGRESS, data={
                    "estimated_tokens": int(obj.get("estimated_tokens") or 0),
                    "delta": int(obj.get("estimated_tokens_delta") or 0),
                })
            # hook_started / hook_response and any future subtype: not modelled.

        elif kind == "assistant":
            for block in _blocks(obj):
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text") or "").strip()
                    if text:
                        yield AgentEvent(kind=TEXT, text=text)
                elif btype == "thinking":
                    yield AgentEvent(kind=THINKING,
                                     text=str(block.get("thinking") or ""))
                elif btype == "tool_use":
                    yield AgentEvent(
                        kind=TOOL_USE,
                        name=str(block.get("name") or ""),
                        data={"id": block.get("id") or "",
                              "input": block.get("input") or {}},
                    )

        elif kind == "user":
            for block in _blocks(obj):
                if block.get("type") != "tool_result":
                    continue
                body = block.get("content")
                if not isinstance(body, str):
                    body = json.dumps(body, ensure_ascii=False) if body else ""
                yield AgentEvent(
                    kind=TOOL_RESULT,
                    text=body,
                    ok=not bool(block.get("is_error")),
                    data={"tool_use_id": block.get("tool_use_id") or ""},
                )


# ------------------------------------------------------------------ which argv

@dataclass(frozen=True)
class HeadlessSpec:
    """How one agent is asked for a machine-readable stream.

    Membership of `HEADLESS_AGENTS` is a factual claim — that this binary emits
    newline-delimited JSON this module can parse — and the evidence for it is a
    checked-in capture. An agent is added here after a capture exists, never
    because its `--help` mentions JSON.
    """
    binary: str
    #: argv around the prompt. `None` marks where the prompt itself goes, so it
    #: stays a separate element and never passes through a shell.
    template: tuple[str | None, ...]

    def argv(self, prompt: str) -> list[str]:
        return [prompt if p is None else p for p in self.template]


#: Verified against `claude` 2.1.235, 2026-08-19. `--verbose` is not optional:
#: the CLI refuses `--output-format stream-json` in print mode without it.
HEADLESS_AGENTS: dict[str, HeadlessSpec] = {
    "claude_code": HeadlessSpec(
        binary="claude",
        template=("claude", "-p", None, "--output-format", "stream-json",
                  "--verbose"),
    ),
}


def supports_headless(agent_key: str) -> bool:
    """True only for agents with a captured, parsed stream format."""
    return agent_key in HEADLESS_AGENTS


def headless_argv(agent_key: str, prompt: str, *,
                  model: str = "", allowed_tools: Sequence[str] = ()) -> list[str]:
    """argv for a headless run. The prompt is one element, never interpolated.

    The PTY path built `claude '{prompt}'` as a string. A prompt containing an
    apostrophe — "don't touch the parser" — became a shell syntax error, and a
    prompt containing a semicolon became a second command.
    """
    spec = HEADLESS_AGENTS.get(agent_key)
    if spec is None:
        raise ValueError(
            f"{agent_key!r} has no verified headless JSON mode; "
            f"use the PTY path. Known: {', '.join(sorted(HEADLESS_AGENTS))}.")
    argv = spec.argv(prompt)
    if model:
        argv += ["--model", model]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    return argv


def headless_available(agent_key: str) -> bool:
    """Headless mode AND a binary on PATH. Presence is not availability —
    the same distinction `capability/agents.py` draws, for the same reason."""
    spec = HEADLESS_AGENTS.get(agent_key)
    return bool(spec and shutil.which(spec.binary))


# ------------------------------------------------------------------- the driver

def stream_events(argv: Sequence[str], *, cwd: Path | str | None = None,
                  timeout_s: float = 1800.0,
                  env: dict[str, str] | None = None,
                  _spawned: Callable[[subprocess.Popen], None] | None = None,
                  ) -> Iterator[AgentEvent]:
    """Run an agent and yield its events as they arrive.

    Guarantees the callers rely on, in order of how much they cost to learn:

    1. **It always terminates.** A wedged agent is killed at `timeout_s`. The
       swarm holds a git worktree per agent; a process that never exits holds
       that worktree forever.
    2. **A terminal RESULT is always yielded** — including when the binary is
       missing, the process is killed, or it exits non-zero having said
       nothing. Silence plus a bad exit code previously read as "finished".
    3. **The agent's own verdict outranks the shell's.** If a `result` line was
       emitted, that is the answer even if the exit code disagrees; the CLI
       knows why it stopped and `$?` does not.
    4. **It streams.** Events are yielded as lines arrive, not collected first,
       so a HUD has something to show for the length of a mission.

    stderr is captured separately and only used to explain a failure — mixing
    it into stdout would corrupt the JSON stream with the exact kind of noise
    this module exists to stop parsing.
    """
    try:
        proc = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None, env=env,
            text=True, bufsize=1,     # line buffered; run_cmd's scar was bytes
        )
    except (OSError, ValueError) as e:
        yield AgentEvent(kind=RESULT, ok=False, text=f"could not start {argv[0]!r}: {e}",
                         data={"subtype": "spawn_failed"})
        return

    if _spawned is not None:
        _spawned(proc)

    # stderr is drained on a thread. A CLI that writes more than a pipe buffer
    # of warnings would otherwise block forever on a write nobody is reading,
    # and present as a hang rather than as the chatty binary it is.
    stderr_chunks: list[str] = []

    def _drain() -> None:
        try:
            if proc.stderr is not None:
                stderr_chunks.append(proc.stderr.read() or "")
        except Exception:
            pass

    drainer = threading.Thread(target=_drain, name="agent-stderr", daemon=True)
    drainer.start()

    # The timeout has to be enforced by a watchdog, not by checking the clock
    # inside the read loop. `for line in proc.stdout` blocks in readline until a
    # line arrives, so an agent that wedges *without printing* — the exact
    # failure the timeout exists for — never reaches a check placed after the
    # read. Measured: a 30s sleeper against a 1s timeout ran the full 30s and
    # yielded no events at all. Killing the process from a timer instead makes
    # the blocked read return EOF, which unblocks the loop from outside.
    timed_out = threading.Event()

    def _watchdog() -> None:
        try:
            proc.wait(timeout=timeout_s)
            return                      # exited on its own; nothing to do
        except subprocess.TimeoutExpired:
            pass
        timed_out.set()
        try:
            proc.kill()
        except Exception:
            pass

    watchdog = threading.Thread(target=_watchdog, name="agent-watchdog", daemon=True)
    watchdog.start()

    saw_result = False

    try:
        for line in proc.stdout or ():
            for event in parse_lines([line]):
                saw_result = saw_result or event.is_terminal
                yield event
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass

    watchdog.join(timeout=2)
    drainer.join(timeout=2)
    stderr_text = "".join(stderr_chunks).strip()

    if timed_out.is_set():
        yield AgentEvent(
            kind=RESULT, ok=False,
            text=f"agent timed out after {timeout_s:.0f}s and was killed",
            data={"subtype": "timeout", "stderr": stderr_text[-2000:]})
        return

    if saw_result:
        return                          # the agent already gave its verdict

    code = proc.returncode or 0
    if code != 0:
        detail = stderr_text[-2000:] or f"exit code {code}"
        yield AgentEvent(kind=RESULT, ok=False,
                         text=f"agent exited with code {code}: {detail}",
                         data={"subtype": "nonzero_exit", "exit_code": code,
                               "stderr": stderr_text[-2000:]})


class AgyStream:
    """Antigravity CLI (`agy --output-format stream-json`), verified against
    agy 1.2.11. Text arrives as deltas per step, so it is assembled here."""

    def __init__(self) -> None:
        self._text: dict[int, str] = {}

    def feed(self, line: str) -> list[AgentEvent]:
        try:
            obj = json.loads((line or "").strip())
        except (ValueError, TypeError):
            return []
        if not isinstance(obj, dict):
            return []
        kind = obj.get("event")
        if kind == "init":
            init = obj.get("init") or {}
            return [AgentEvent(kind=INIT, data={
                "session_id": obj.get("conversation_id") or "",
                "cwd": init.get("cwd") or "",
                "tools": len(init.get("tools") or [])})]
        if kind == "result":
            r = obj.get("result") or {}
            ok = str(r.get("status") or "").upper() == "SUCCESS"
            usage = r.get("usage") or {}
            return [AgentEvent(kind=RESULT, ok=ok,
                               text=str(r.get("response") or r.get("error") or "").strip(),
                               data={"session_id": r.get("conversation_id") or "",
                                     "num_turns": r.get("num_turns") or 0,
                                     "input_tokens": usage.get("input_tokens") or 0,
                                     "output_tokens": usage.get("output_tokens") or 0,
                                     "permission_denials": []})]
        if kind != "step_update":
            return []
        step = obj.get("step_update") or {}
        stype, state = step.get("step_type"), step.get("state")
        index = int(step.get("step_index") or 0)
        if stype == "agent_response":
            self._text[index] = self._text.get(index, "") + str(step.get("text_delta") or "")
            if state == "DONE":
                text = self._text.pop(index, "").strip()
                return [AgentEvent(kind=TEXT, text=text)] if text else []
            return []
        if stype == "tool" and state == "ACTIVE":
            info = step.get("tool_info") or {}
            return [AgentEvent(kind=TOOL_USE, name=str(step.get("tool_name") or info.get("name") or ""),
                               data={"input": info.get("parameters") or {}})]
        return []


class ClaudeStream:
    def feed(self, line: str) -> list[AgentEvent]:
        return list(parse_lines([line]))


STREAM_PARSERS = {"claude": ClaudeStream, "agy": AgyStream}
