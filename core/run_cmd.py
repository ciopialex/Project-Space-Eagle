"""External commands with a deadline that actually reclaims what it kills.

WHY THIS EXISTS
---------------
Tools run through `loop.run_in_executor(None, ...)`. `asyncio.wait_for` cancels
the await; it cannot cancel the OS thread underneath. So a tool blocked on a
subprocess holds its executor worker forever. The default executor has
min(32, cpu+4) workers shared by every tool in the app — enough of those and
tool dispatch silently stops working with no error raised anywhere, and
`asyncio.run()` then blocks on the same threads at shutdown.

`subprocess.run(timeout=...)` is necessary but not sufficient. On timeout it
kills only the direct child. A launcher that backgrounds a browser, a shell
pipeline, a git command that forked a helper — those grandchildren survive,
still holding the CPU, files and ports the timeout was meant to reclaim.

So: every command gets a default deadline, and every command runs in its own
process group so the deadline can take the whole tree with it.
"""
from __future__ import annotations

import os
import signal
import subprocess

# Long enough for a slow package manager or a cold git fetch, short enough that
# a wedged command surfaces as an error inside one conversation rather than
# silently consuming a worker for the rest of the session.
DEFAULT_TIMEOUT_S = 60.0

_POSIX = os.name == "posix"


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it started.

    Sends to the process GROUP, which is why the command was launched into one.
    SIGTERM first so a well-behaved child can flush and exit; SIGKILL after, for
    the ones that ignore it.
    """
    try:
        if _POSIX:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
        else:
            # Windows: the group was created with CREATE_NEW_PROCESS_GROUP, and
            # taskkill /T is the only reliable way to take the tree with it.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
    except (ProcessLookupError, PermissionError, OSError):
        pass  # already gone, or not ours to kill — nothing left to reclaim
    finally:
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


def run_cmd(args, *, timeout: float | None = None, check: bool = False,
            **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` with a mandatory deadline and process-group cleanup.

    Drop-in compatible: returns a CompletedProcess, honours `check`, and raises
    `subprocess.TimeoutExpired` on the deadline exactly as `subprocess.run`
    does — so existing `try/except` and `returncode` branches keep working.
    The difference is what happens on timeout: the whole process tree dies,
    not just the process we can see.
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_S

    # `capture_output` and `input` are subprocess.run() conveniences, not Popen
    # kwargs. Translate them so call sites can be swapped over verbatim.
    if kwargs.pop("capture_output", False):
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("capture_output cannot be combined with stdout/stderr")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    stdin_data = kwargs.pop("input", None)
    if stdin_data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("input cannot be combined with stdin")
        kwargs["stdin"] = subprocess.PIPE

    # Put the child in its own group so the timeout path can reach its children.
    # Callers that manage their own session/group win — we never override.
    if _POSIX:
        kwargs.setdefault("start_new_session", True)
    else:
        flags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = flags | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )

    with subprocess.Popen(args, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            # Drain whatever was produced before the deadline; the pipes are
            # closed now, so this returns immediately rather than blocking.
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stdout, stderr = None, None
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
        except BaseException:
            _kill_tree(proc)
            raise

        retcode = proc.poll() or 0

    if check and retcode:
        raise subprocess.CalledProcessError(retcode, args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(args, retcode, stdout, stderr)
