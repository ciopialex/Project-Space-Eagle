"""Persistent PTY session pool for delegated agent CLIs.

Replaces stateless one-shot terminal spawns with long-lived pseudo-terminal
sessions keyed by (agent_key, project_dir). Follow-up prompts are injected
into the running agent's stdin, so one project folder == one live agent
conversation, never duplicate windows.

Cross-platform: Unix (pty/termios) and Windows (pywinpty, optional).
"""

import atexit
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"

if not IS_WINDOWS:
    import fcntl
    import pty
    import struct
    import termios

# PTY dimensions presented to the agent CLI (rich TUIs render to this size).
PTY_COLS = 120
PTY_ROWS = 40

# Cap on retained raw output per session (bytes).
RAW_BUFFER_LIMIT = 1 * 1024 * 1024

CTRL_C = b"\x03"


class PtySession:
    """One live agent CLI process on a pseudo-terminal we own."""

    def __init__(self, agent_key: str, agent_name: str, command: str,
                 project_dir: Path, on_line=None):
        self.agent_key = agent_key
        self.agent_name = agent_name
        self.command = command
        self.project_dir = Path(project_dir)
        self.created_at = time.time()
        self.log_path = self._make_log_path()

        # Callbacks
        self._on_line = on_line          # cleaned text lines -> HUD
        self._feed_hooks = []            # raw bytes subscribers (pyte in Phase 2)

        self._raw = bytearray()
        self._line_buf = b""
        self._lock = threading.Lock()
        self._closed = False

        self._proc = None                # subprocess.Popen (unix)
        self._winpty = None              # winpty.PtyProcess (windows)
        self._master_fd = None

        self._spawn()
        self._reader = threading.Thread(
            target=self._read_loop, name=f"pty-{agent_key}", daemon=True)
        self._reader.start()

    # ---------------------------------------------------------------- spawn

    def _make_log_path(self) -> Path:
        safe_proj = self.project_dir.name.replace(os.sep, "_") or "root"
        return Path(tempfile.gettempdir()) / (
            f"aethelark_session_{self.agent_key}_{safe_proj}.log")

    def _spawn(self):
        try:
            self.log_path.unlink()
        except OSError:
            pass
        self._log_file = open(self.log_path, "ab", buffering=0)

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["COLUMNS"] = str(PTY_COLS)
        env["LINES"] = str(PTY_ROWS)

        if IS_WINDOWS:
            import winpty  # pywinpty
            self._winpty = winpty.PtyProcess.spawn(
                ["cmd", "/c", self.command],
                cwd=str(self.project_dir),
                dimensions=(PTY_ROWS, PTY_COLS),
                env=env,
            )
            return

        master_fd, slave_fd = pty.openpty()
        # Present a real terminal size so TUI agents render sanely.
        winsize = struct.pack("HHHH", PTY_ROWS, PTY_COLS, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        self._proc = subprocess.Popen(
            ["bash", "-lc", self.command],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=str(self.project_dir),
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        self._master_fd = master_fd

    # ---------------------------------------------------------------- io

    def _read_loop(self):
        try:
            while not self._closed:
                data = self._read_chunk()
                if data is None:
                    break
                if data:
                    self._ingest(data)
        finally:
            self._mark_dead()

    def _read_chunk(self):
        """Return bytes, empty bytes to keep polling, or None on EOF."""
        if IS_WINDOWS:
            try:
                text = self._winpty.read(4096)
                return text.encode("utf-8", "replace") if text else b""
            except EOFError:
                return None
            except Exception:
                return None if not self._winpty.isalive() else b""
        try:
            return os.read(self._master_fd, 4096) or None
        except OSError:
            return None  # EIO: child side of the PTY closed

    def _ingest(self, data: bytes):
        with self._lock:
            self._raw.extend(data)
            if len(self._raw) > RAW_BUFFER_LIMIT:
                del self._raw[:len(self._raw) - RAW_BUFFER_LIMIT]
        try:
            self._log_file.write(data)
        except OSError:
            pass
        for hook in list(self._feed_hooks):
            try:
                hook(data)
            except Exception:
                pass  # feed-hook hiccup — silent (runs on every PTY output chunk)
        self._emit_lines(data)

    def _emit_lines(self, data: bytes):
        if not self._on_line:
            return
        self._line_buf += data
        # Rich TUIs emit \r for in-place redraws; treat \r, \n, \r\n as breaks.
        parts = self._line_buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        self._line_buf = parts.pop()
        if len(self._line_buf) > 8192:  # runaway unterminated line
            parts.append(self._line_buf)
            self._line_buf = b""
        for part in parts:
            if part:
                try:
                    self._on_line(part.decode("utf-8", "replace"))
                except Exception:
                    pass  # per-line callback hiccup — silent (fires per output line)

    # ---------------------------------------------------------------- api

    def add_feed_hook(self, hook):
        """Subscribe a callable(bytes) to the raw PTY output stream."""
        self._feed_hooks.append(hook)

    def send_raw(self, data: bytes):
        if IS_WINDOWS:
            self._winpty.write(data.decode("utf-8", "replace"))
        else:
            os.write(self._master_fd, data)

    def send_line(self, text: str):
        """Type text into the agent's terminal and press Enter."""
        self.send_raw(text.encode("utf-8", "replace") + b"\r")

    def interrupt(self):
        """Graceful Ctrl+C into the agent's PTY."""
        try:
            self.send_raw(CTRL_C)
            return True
        except OSError:
            return False

    def is_alive(self) -> bool:
        if self._closed:
            return False
        if IS_WINDOWS:
            return bool(self._winpty and self._winpty.isalive())
        return bool(self._proc and self._proc.poll() is None)

    def snapshot_tail(self, max_bytes: int = 8192) -> bytes:
        with self._lock:
            return bytes(self._raw[-max_bytes:])

    def _mark_dead(self):
        if self._on_line and not self._closed:
            try:
                self._on_line(f"[session] {self.agent_name} process ended.")
            except Exception as _e:
                print(f"[pty_session.py] Non-fatal error at line 216: {_e}")

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if IS_WINDOWS:
                if self._winpty and self._winpty.isalive():
                    self._winpty.terminate(force=True)
            elif self._proc and self._proc.poll() is None:
                # Terminate the whole process group (agent + its children).
                try:
                    os.killpg(self._proc.pid, signal.SIGHUP)
                except ProcessLookupError:
                    pass
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self._proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        finally:
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
            try:
                self._log_file.close()
            except OSError:
                pass


class SessionPool:
    """Registry of live agent sessions keyed by (agent_key, project_dir)."""

    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(agent_key: str, project_dir: Path):
        return (agent_key, str(Path(project_dir).resolve()))

    def get_alive(self, agent_key: str, project_dir: Path):
        key = self._key(agent_key, project_dir)
        with self._lock:
            sess = self._sessions.get(key)
            if sess and sess.is_alive():
                return sess
            if sess:  # dead leftover — reap it
                sess.close()
                del self._sessions[key]
            return None

    def create(self, agent_key: str, agent_name: str, command: str,
               project_dir: Path, on_line=None) -> PtySession:
        key = self._key(agent_key, project_dir)
        with self._lock:
            old = self._sessions.pop(key, None)
        if old:
            old.close()
        sess = PtySession(agent_key, agent_name, command, project_dir, on_line=on_line)
        with self._lock:
            self._sessions[key] = sess
        return sess

    def all_sessions(self):
        with self._lock:
            return dict(self._sessions)

    def close_all(self):
        for sess in self.all_sessions().values():
            try:
                sess.close()
            except Exception:
                pass  # never let one bad session block shutdown of the rest
        with self._lock:
            self._sessions.clear()


POOL = SessionPool()
atexit.register(POOL.close_all)


# ------------------------------------------------------------- viewer window

def open_viewer_terminal(title: str, log_path: Path) -> bool:
    """Open ONE read-only terminal that live-tails a session log (best effort).

    The agent itself runs on our hidden PTY; this window is purely a viewer,
    so closing it never kills the session.
    """
    try:
        if IS_WINDOWS:
            cmd = (f'start "{title}" powershell -NoExit -Command '
                   f'"Get-Content -Path \'{log_path}\' -Wait -Tail 200"')
            subprocess.Popen(cmd, shell=True)
            return True
        if IS_MACOS:
            script = f'tell app "Terminal" to do script "tail -F -n +1 {log_path}"'
            subprocess.Popen(["osascript", "-e", script])
            return True
        tail_cmd = f"tail -F -n +1 {log_path}"
        for term, args in (
            ("gnome-terminal", ["--title", title, "--", "bash", "-c", tail_cmd]),
            ("konsole", ["--title", title, "-e", "bash", "-c", tail_cmd]),
            ("xterm", ["-T", title, "-e", "bash", "-c", tail_cmd]),
        ):
            if shutil.which(term):
                subprocess.Popen([term] + args)
                return True
    except Exception as _e:
        print(f"[pty_session.py] Non-fatal error at line 329: {_e}")
    return False
