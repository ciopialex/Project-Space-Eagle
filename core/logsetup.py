"""Make sure a log line written is a log line kept.

Python block-buffers stdout when it is not a terminal. In a terminal that is
invisible — output is line-buffered and everything appears immediately. Redirect
it to a file, which is exactly what someone does when they want to send a log to
somebody, and up to 8KB sits in a buffer that a hard exit throws away.

Measured on this app: `aethelark_web.py > boot.log` captured ONE line across 40
seconds. The same run unbuffered captured the entire startup sequence. Nothing
was missing — it was in a buffer that never flushed.

These logs are the tool that has found nearly every real bug in this project.
Losing them exactly when something crashes is the worst failure mode available
to them, so this is installed at import time by both entrypoints.
"""
from __future__ import annotations

import sys


def install() -> None:
    """Line-buffer stdout and stderr. Idempotent, and never fatal."""
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(line_buffering=True)
        except Exception:
            # An unusual stream (a pytest capture object, a Qt redirect) that
            # cannot be reconfigured. Losing the setting is not worth losing
            # the process over.
            pass


install()
