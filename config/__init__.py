"""What the user configured, answered without going to disk every time.

Fifty-five call sites in this repository ask which operating system this is.
The answer lives in a JSON file the user edits by hand perhaps twice in the
life of an install, and it was being re-read and re-parsed on every one of
those calls. The cache below is keyed on the file's mtime and size, so a hand
edit is picked up on the very next call and a loop asking the same question
fifty times pays for one read.

The client factory's lock is a module-level object created at import. The
previous one was created inside the function it guarded, behind an unguarded
`hasattr` check, which is not a guard: two threads could each install their own
lock and both proceed into the critical section, ending with two clients and
two TLS pools where the whole point was to have one.
"""
from __future__ import annotations

import json
import os
import platform
import threading
from typing import Any

from core import user_paths

_CONFIG_PATH = user_paths.api_keys_path()

#: Guards the client cache. At module scope deliberately — see the docstring.
_CLIENT_LOCK = threading.Lock()
_CLIENTS: dict[str, Any] = {}

_CONFIG_LOCK = threading.Lock()
_CONFIG_CACHE: dict[str, Any] = {}
_CONFIG_STAMP: tuple[int, int] | None = None

_OS_NAMES = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}


def _stamp() -> tuple[int, int] | None:
    """(mtime_ns, size) — cheap, and enough to notice any real edit."""
    try:
        st = _CONFIG_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def get_config() -> dict:
    """The user's settings. `{}` when the file is absent or unreadable —
    every caller here treats a missing key as "not configured", so a corrupt
    file degrades to defaults instead of taking the application down."""
    global _CONFIG_STAMP
    stamp = _stamp()
    with _CONFIG_LOCK:
        if stamp is not None and stamp == _CONFIG_STAMP:
            return dict(_CONFIG_CACHE)
        loaded: dict = {}
        if stamp is not None:
            try:
                parsed = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    loaded = parsed
            except (OSError, ValueError):
                loaded = {}
        _CONFIG_CACHE.clear()
        _CONFIG_CACHE.update(loaded)
        _CONFIG_STAMP = stamp
        return dict(_CONFIG_CACHE)


def get_os() -> str:
    """`windows` | `mac` | `linux`. The file wins; the running platform is the
    fallback, so an install that never wrote a config still behaves."""
    configured = get_config().get("os_system")
    if configured:
        return str(configured).lower()
    return _OS_NAMES.get(platform.system(), "linux")


def is_windows() -> bool:
    return get_os() == "windows"


def is_mac() -> bool:
    return get_os() == "mac"


def is_linux() -> bool:
    return get_os() == "linux"


def reset_client_cache() -> None:
    """Drop cached clients. For tests, and for a key change at runtime."""
    with _CLIENT_LOCK:
        _CLIENTS.clear()


def get_client(api_version: str | None = None):
    """A `genai.Client`, one per API version, reused so TCP and TLS are not
    renegotiated per call. Imported lazily because `google.genai` costs real
    time to import and not every entry point needs it."""
    key = api_version or "default"
    with _CLIENT_LOCK:
        client = _CLIENTS.get(key)
        if client is not None:
            return client
        from google import genai
        cfg = get_config()
        api_key = cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        options = {"api_version": api_version} if api_version else None
        client = genai.Client(api_key=api_key, http_options=options)
        _CLIENTS[key] = client
        return client
