"""Virtual VT100 screen watcher for agent PTY sessions.

Feeds raw PTY bytes into an in-memory pyte terminal, giving an exact,
ANSI-free snapshot of what a human would see. From stable snapshots it:

  1. Detects interactive approval prompts (numbered menus, [y/N], press-
     Enter) and injects a response ONLY when core.prompt_reflex classifies
     the screen as a recognised-safe confirmation — deterministically,
     because it only fires when the screen has stopped redrawing (the CLI
     is truly blocked on input) and never twice on the same screen state.
  2. Extracts <thinking> blocks and spinner status lines from the raw
     stream for HUD telemetry.

AUTHORITY: this class is a SENSOR AND ACTUATOR, not a decision maker. It
reconstructs the terminal and injects keystrokes, but what counts as safe to
answer lives entirely in `core.prompt_reflex`. Anything dangerous or
unrecognised is escalated via `on_escalation` and left unanswered — the agent
blocks, visibly, until something with authority decides. That is the intended
failure mode: a stalled agent is recoverable, an auto-approved `rm -rf` is not.

`on_escalation(agent_name, decision, region)` is the seam the Mission
Controller / DecisionGate hooks into. Until one exists, escalations are logged
and the prompt is simply left alone.

Attach one AgentScreenWatcher per PtySession via session.add_feed_hook.
"""

import hashlib
import re
import threading
import time

import pyte

from actions.pty_session import PTY_COLS, PTY_ROWS
from core.prompt_reflex import Verdict, classify

POLL_HZ = 5
APPROVE_COOLDOWN_S = 2.0

_THINKING_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
_ANSI_RE = re.compile(r"(?:\x1b[@-_][0-?]*[ -/]*[@-~])")
# Spinner/status lines emitted by modern agent CLIs ("✻ Pondering…")
_STATUS_RE = re.compile(r"^[✻✽✶✳·∗*]\s+([A-Z][\w'’ .-]{2,60}…?)\s*$", re.MULTILINE)


class AgentScreenWatcher:
    """Watches one PtySession through a virtual terminal."""

    def __init__(self, session, agent_name: str, player=None,
                 auto_approve: bool = True, on_thought=None,
                 on_escalation=None):
        self.session = session
        self.agent_name = agent_name
        self.player = player
        self.auto_approve = auto_approve
        self.on_thought = on_thought
        self.on_escalation = on_escalation

        self._screen = pyte.Screen(PTY_COLS, PTY_ROWS)
        self._stream = pyte.ByteStream(self._screen)
        self._lock = threading.Lock()

        self._text_buf = ""            # rolling ANSI-stripped raw text
        self._emitted_thoughts = set()
        self._last_status = ""

        self._prev_hash = ""
        self._approved_hashes = set()
        self._last_approve_ts = 0.0
        self.last_change_ts = time.time()   # circuit-breaker signal (Phase 4)

        self._stop = threading.Event()
        session.add_feed_hook(self._feed)
        self._poller = threading.Thread(
            target=self._poll_loop, name=f"screen-{agent_name}", daemon=True)
        self._poller.start()

    # ---------------------------------------------------------------- feed

    def _feed(self, data: bytes):
        with self._lock:
            try:
                self._stream.feed(data)
            except Exception:
                pass  # per-chunk VT decode hiccup — silent (runs on every output byte)
        self._scan_raw(data)

    def _scan_raw(self, data: bytes):
        """Thought/status extraction from the raw byte stream."""
        text = _ANSI_RE.sub("", data.decode("utf-8", "replace"))
        self._text_buf = (self._text_buf + text)[-32768:]

        for match in _THINKING_RE.finditer(self._text_buf):
            thought = " ".join(match.group(1).split())[:400]
            key = hashlib.md5(thought.encode()).hexdigest()
            if thought and key not in self._emitted_thoughts:
                self._emitted_thoughts.add(key)
                if len(self._emitted_thoughts) > 200:
                    self._emitted_thoughts.clear()
                self._emit_thought(thought)
        # Drop consumed closed blocks; keep an open tail for split tags.
        self._text_buf = _THINKING_RE.sub("", self._text_buf)

        status = _STATUS_RE.findall(text)
        if status and status[-1] != self._last_status:
            self._last_status = status[-1]
            self._emit_thought(status[-1], status=True)

    def _emit_thought(self, text: str, status: bool = False):
        tag = "…" if status else "🧠"
        if self.player:
            self.player.write_log(f"[{self.agent_name} {tag}] {text}")
        if self.on_thought:
            try:
                self.on_thought(self.agent_name, text, status)
            except Exception:
                pass  # UI callback hiccup — silent (fires per emitted thought)

    # ---------------------------------------------------------------- poll

    def snapshot(self) -> str:
        """Clean 2D text of what a human sees in the terminal right now."""
        with self._lock:
            return "\n".join(self._screen.display)

    def _active_region(self) -> str:
        """Lines around the cursor — the prompt currently awaiting input.

        Matching the whole screen would re-trigger on stale prompts still
        visible above (e.g. an already-answered menu), so approval rules
        only ever see the bottom-most live region.
        """
        with self._lock:
            rows = self._screen.display
            cur = min(self._screen.cursor.y, len(rows) - 1)
        return "\n".join(rows[max(0, cur - 11):cur + 1])

    def _poll_loop(self):
        interval = 1.0 / POLL_HZ
        while not self._stop.is_set() and self.session.is_alive():
            time.sleep(interval)
            try:
                self._poll_once()
            except Exception:
                pass  # transient poll error — silent (runs POLL_HZ times/sec)
        self._stop.set()

    def _poll_once(self):
        snap = self.snapshot()
        digest = hashlib.md5(snap.encode("utf-8", "replace")).hexdigest()
        stable = digest == self._prev_hash
        if not stable:
            self.last_change_ts = time.time()
        self._prev_hash = digest

        if not (self.auto_approve and stable):
            return
        if digest in self._approved_hashes:
            return
        if time.time() - self._last_approve_ts < APPROVE_COOLDOWN_S:
            return

        region = self._active_region().strip()
        if not region:
            return

        decision = classify(region)
        if decision.verdict is Verdict.ALLOW:
            self._respond(decision.reply, digest)
        elif decision.verdict is Verdict.ESCALATE:
            self._escalate(decision, digest, region)

    def _respond(self, reply: bytes, digest: str):
        try:
            self.session.send_raw(reply)
        except OSError:
            return
        self._mark_handled(digest)
        if self.player:
            self.player.write_log(
                f"SYS: Auto-approved {self.agent_name} prompt "
                f"({reply.decode(errors='replace').strip() or 'Enter'}).")

    def _escalate(self, decision, digest: str, region: str):
        """Refuse to answer, and surface it exactly once per screen state.

        Deduping matters here: the poll loop runs POLL_HZ times a second on a
        blocked agent, so an unguarded escalation would flood the log and any
        downstream controller with the same event several times per second.
        """
        self._mark_handled(digest)
        if self.player:
            self.player.write_log(
                f"SYS: HELD {self.agent_name} prompt — {decision.reason} "
                f"[{decision.rule_id}]. Needs authorization.")
        if self.on_escalation:
            try:
                self.on_escalation(self.agent_name, decision, region)
            except Exception:
                pass  # a broken listener must never kill the watcher thread

    def authorize_pending(self, reply: bytes = b"y\r") -> bool:
        """Type the answer a human explicitly authorized.

        The watcher owns the PTY and is the only thing that writes to it, so a
        resolved escalation comes back HERE to be injected. The decision and
        the keystroke stay in different places on purpose — same separation the
        reflex tier is built on. Returns False if the agent is no longer alive.
        """
        try:
            if not self.session.is_alive():
                return False
            self.session.send_raw(reply)
        except OSError:
            return False
        self._last_approve_ts = time.time()
        if self.player:
            self.player.write_log(
                f"SYS: {self.agent_name} — authorized by human, answer injected.")
        return True

    def _mark_handled(self, digest: str):
        self._approved_hashes.add(digest)
        if len(self._approved_hashes) > 200:
            self._approved_hashes.clear()
        self._last_approve_ts = time.time()

    # ---------------------------------------------------------------- api

    def seconds_since_activity(self) -> float:
        return time.time() - self.last_change_ts

    def stop(self):
        self._stop.set()
