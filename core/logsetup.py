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


class _Stamped:
    """Every line starts with the wall-clock time, to the millisecond."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._fresh = True

    def write(self, text: str) -> int:
        if not text:
            return 0
        import datetime
        out = []
        for piece in text.splitlines(keepends=True):
            if self._fresh:
                out.append(datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3] + " ")
            out.append(piece)
            self._fresh = piece.endswith("\n")
        self._stream.write("".join(out))
        return len(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install() -> None:
    """Line-buffer stdout and stderr, and stamp each line. Idempotent, never fatal."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(line_buffering=True)
        except Exception:
            # An unusual stream (a pytest capture object, a Qt redirect) that
            # cannot be reconfigured. Losing the setting is not worth losing
            # the process over.
            pass
        if stream is not None and not isinstance(stream, _Stamped):
            setattr(sys, name, _Stamped(stream))


install()

# ─── who shut down the event loop's default executor? ────────────────────────
#
# Measured 2026-09-07: mid-session the loop's default ThreadPoolExecutor was
# shut down. That pool backs `asyncio.to_thread` AND `getaddrinfo`, so every
# DNS lookup failed and every reconnect to Gemini failed with it — the eagle
# spent the rest of its life retrying every three seconds with the window up
# and the pill animating. The supervisor now rebuilds the loop, so it is
# survivable; it is still not explained.
#
# Reading found nothing. Nothing in this repo, uvicorn, starlette, anyio,
# websockets or google-genai calls shutdown on it on any path that was taken.
# So rather than guess, leave a probe on the pin: the NEXT time it happens the
# log says who did it, with the stack.
#
# Cheap by construction — one wrapper installed once, and the body only runs
# when something is actually being shut down, which is rare and already slow.
def _watch_default_executor_shutdown() -> None:
    import asyncio
    import concurrent.futures.thread as _cft
    import traceback

    original = _cft.ThreadPoolExecutor.shutdown

    def shutdown(self, wait=True, *, cancel_futures=False):
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            is_default = getattr(loop, "_default_executor", None) is self
        except Exception:
            is_default = False
        if is_default:
            print("\n[executor] *** THE LOOP'S DEFAULT EXECUTOR IS BEING SHUT "
                  "DOWN ***\n[executor] after this, asyncio.to_thread and every "
                  "DNS lookup on this loop fail.\n[executor] called from:")
            traceback.print_stack()
            print("[executor] *** end of trace ***\n", flush=True)
        return original(self, wait=wait, cancel_futures=cancel_futures)

    _cft.ThreadPoolExecutor.shutdown = shutdown


_watch_default_executor_shutdown()
