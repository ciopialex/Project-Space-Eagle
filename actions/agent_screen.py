"""Virtual VT100 screen watcher for agent PTY sessions.

Feeds raw PTY bytes into an in-memory pyte terminal, giving an exact,
ANSI-free snapshot of what a human would see. From stable snapshots it:

  1. Detects interactive approval prompts (numbered menus, [y/N], press-
     Enter) and auto-injects the affirmative response — deterministically,
     because it only fires when the screen has stopped redrawing (the CLI
     is truly blocked on input) and never twice on the same screen state.
  2. Extracts <thinking> blocks and spinner status lines from the raw
     stream for HUD telemetry.

Attach one AgentScreenWatcher per PtySession via session.add_feed_hook.
"""

import hashlib
import re
import threading
import time

import pyte

from actions.pty_session import PTY_COLS, PTY_ROWS

POLL_HZ = 5
APPROVE_COOLDOWN_S = 2.0

# Ink/Rich-style highlighted menu marker (e.g. "❯ 1. Yes")
_SELECTOR_MARK = "❯"

# (pattern, response builder) — checked against the visible screen text.
_YES_WORDS = r"(?:yes|accept|approve|allow|proceed|continue|trust|confirm)"
_PROMPT_RULES = [
    # Numbered menu whose option 1 is affirmative: "1. Yes", "❯ 1. Accept edits"
    (re.compile(rf"^\s*(?:{re.escape(_SELECTOR_MARK)}\s*)?1[.)]\s+{_YES_WORDS}",
                re.IGNORECASE | re.MULTILINE), "menu1"),
    (re.compile(r"\[y/N\]|\[Y/n\]|\(y/n\)", re.IGNORECASE), "yes"),
    (re.compile(r"press\s+enter\s+to\s+continue", re.IGNORECASE), "enter"),
]

_THINKING_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
_ANSI_RE = re.compile(r"(?:\x1b[@-_][0-?]*[ -/]*[@-~])")
# Spinner/status lines emitted by modern agent CLIs ("✻ Pondering…")
_STATUS_RE = re.compile(r"^[✻✽✶✳·∗*]\s+([A-Z][\w'’ .-]{2,60}…?)\s*$", re.MULTILINE)


class AgentScreenWatcher:
    """Watches one PtySession through a virtual terminal."""

    def __init__(self, session, agent_name: str, player=None,
                 auto_approve: bool = True, on_thought=None):
        self.session = session
        self.agent_name = agent_name
        self.player = player
        self.auto_approve = auto_approve
        self.on_thought = on_thought

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
                pass
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
                pass

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
                pass
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
        # If several rules match, answer the bottom-most (most recent) prompt.
        best = None
        for pattern, kind in _PROMPT_RULES:
            matches = list(pattern.finditer(region))
            if matches and (best is None or matches[-1].start() > best[0]):
                best = (matches[-1].start(), kind)
        if best:
            self._respond(best[1], digest, region)

    def _respond(self, kind: str, digest: str, visible: str):
        # Ink-style menus react to the bare digit; readline needs Enter.
        if kind == "menu1":
            reply = b"1" if _SELECTOR_MARK in visible else b"1\r"
        elif kind == "yes":
            reply = b"y\r"
        else:
            reply = b"\r"
        try:
            self.session.send_raw(reply)
        except OSError:
            return
        self._approved_hashes.add(digest)
        if len(self._approved_hashes) > 200:
            self._approved_hashes.clear()
        self._last_approve_ts = time.time()
        if self.player:
            self.player.write_log(
                f"SYS: Auto-approved {self.agent_name} prompt "
                f"({reply.decode(errors='replace').strip() or 'Enter'}).")

    # ---------------------------------------------------------------- api

    def seconds_since_activity(self) -> float:
        return time.time() - self.last_change_ts

    def stop(self):
        self._stop.set()
