"""Logs must survive a crash, not sit in a buffer.

Python block-buffers stdout when it is not a terminal. Running the eagle
normally is fine — a terminal is line-buffered — but the moment output is
redirected to a file, which is exactly what someone does when they want to
send the log to somebody, the buffer holds ~8KB and a hard exit discards it.

Measured: `aethelark_web.py > boot.log` captured ONE line over 40 seconds.
The same run with PYTHONUNBUFFERED captured ten, including the whole startup
sequence. The lines were not missing; they were in a buffer that never
flushed.

The logs are the tool that has found nearly every real bug in this project.
Losing them precisely when something crashes is the worst possible failure
mode for them.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_stdout_is_line_buffered_even_when_redirected():
    """A child process whose stdout is a PIPE (not a tty) must still emit each
    line as it is produced."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import core.logsetup  # noqa: F401\n"
        "print('first'); print('second')\n"
        "import os; os._exit(0)          # hard exit: no interpreter flush\n"
    ) % str(REPO)
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=30).stdout
    assert "first" in out and "second" in out, (
        f"a hard exit lost buffered output: {out!r}")


def test_the_app_entrypoints_install_it():
    """Importing the module is not enough — the entrypoints have to call it,
    or the fix is present and inert."""
    for entry in ("aethelark_web.py", "main.py"):
        src = (REPO / entry).read_text(encoding="utf-8")
        assert "logsetup" in src, f"{entry} does not set up logging"


def test_it_is_safe_to_call_twice():
    import importlib
    sys.path.insert(0, str(REPO))
    import core.logsetup as ls
    ls.install()
    ls.install()          # must not raise
