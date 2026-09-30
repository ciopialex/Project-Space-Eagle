"""Media transport on Windows, via SMTC.

`GlobalSystemMediaTransportControlsSessionManager` is the system-wide API macOS
lacks: any app that registers shows up here, browsers included, so this rung is
as broad as MPRIS. `session_factory` is injected because winsdk is a WinRT
binding that cannot load off Windows — hence the import inside
`_default_session`. It must stay there, or this module stops importing on every
machine we develop on and the Windows path stops being tested at all.

Why per-action gating and `status()` exist at all: `MediaBackend` in `base.py`.
"""
from __future__ import annotations

from typing import Any, Callable

from actions.cli.base import MediaBackend

#: What `target()` answers for a session that exists but will not name itself.
#: Never shown to the user — the interface only asks whether it is None.
_UNNAMED = "the current session"

#: PlaybackStatus. Closed, Opened and Changing (0-2) are deliberately absent:
#: none of them is "stopped", and unknown must not read as stopped.
_STATUS = {3: "stopped", 4: "playing", 5: "paused"}

#: action -> (the flag saying whether it would be obeyed, the method). Never a
#: general "can control" flag — see `MediaBackend`.
_ACTIONS = {
    "play": ("is_play_enabled", "try_play_async"),
    "pause": ("is_pause_enabled", "try_pause_async"),
    "next": ("is_next_enabled", "try_skip_next_async"),
    "previous": ("is_previous_enabled", "try_skip_previous_async"),
}


def _default_session() -> Any:
    """The session SMTC considers foremost, or None if nothing is playing."""
    # WinRT is per-thread, and only the thread that imported winsdk has an
    # apartment, but `computer_settings` runs tools on a multi-worker pool: the
    # turn after the importing one lands on another worker and request_async
    # raises RoInitialize-has-not-been-called, which `_session` swallows into
    # "Nothing is playing that I can control." while Spotify is audibly
    # playing. The same bug pycaw's CoInitialize fixes in the audio slice.
    # Idempotent per thread, and already-initialised is not an error, so
    # calling it on every resolve is correct and cheap.
    try:
        from winsdk.system import init_apartment
        init_apartment()
    except Exception:
        pass

    from winsdk.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as Manager

    # WinRT hands back an IAsyncOperation; .get() blocks for it.
    return Manager.request_async().get().get_current_session()


class SmtcBackend(MediaBackend):
    """Every app registered with the system transport controls, as one
    transport.

    It settles on one session for its whole life, per `MediaBackend.target`:
    the foremost session changes as the user switches apps, so re-resolving
    would verify a pause against whatever became foremost afterwards.
    """

    name = "smtc"

    def __init__(self,
                 session_factory: Callable[[], Any] | None = None) -> None:
        self._factory = session_factory or _default_session
        self._settled: Any | None = None

    def _session(self) -> Any | None:
        # No winsdk, no session and a dead RPC endpoint all arrive as
        # exceptions, and all three mean the same thing to the caller. Nothing
        # is cached while there is none: one may start between two questions.
        if self._settled is None:
            try:
                self._settled = self._factory()
            except Exception:
                return None
        return self._settled

    def available(self) -> bool:
        return self._session() is not None

    def target(self) -> str | None:
        session = self._session()
        if session is None:
            return None
        try:
            return session.source_app_user_model_id or _UNNAMED
        except Exception:
            # There is something to command; WinRT just will not say what.
            # None here would report a live session as an idle machine.
            return _UNNAMED

    def status(self) -> str | None:
        session = self._session()
        if session is None:
            return None
        try:
            code = int(session.get_playback_info().playback_status)
        except Exception:
            return None
        return _STATUS.get(code)

    def _can_on(self, session: Any, action: str) -> bool:
        entry = _ACTIONS.get(action)
        if entry is None:
            return False  # an action we cannot spell is one we cannot promise
        try:
            return bool(getattr(session.get_playback_info().controls, entry[0]))
        except Exception:
            return False  # unreadable is not permission

    def can(self, action: str) -> bool:
        session = self._session()
        return session is not None and self._can_on(session, action)

    def _do(self, action: str) -> bool:
        # Resolve once: between two lookups the foremost session can change,
        # and checking one before commanding another is not checking at all.
        session = self._session()
        if session is None or not self._can_on(session, action):
            return False
        try:
            return bool(getattr(session, _ACTIONS[action][1])().get())
        except Exception:
            return False

    def play(self) -> bool:
        return self._do("play")

    def pause(self) -> bool:
        return self._do("pause")

    def next(self) -> bool:
        return self._do("next")

    def previous(self) -> bool:
        return self._do("previous")
