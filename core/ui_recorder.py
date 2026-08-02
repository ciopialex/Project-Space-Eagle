"""A UI that shows the user nothing and remembers everything.

Conforms to `core.ui_contract.AethelarkUI`, so `AethelarkLive` can be driven in
a test with no Qt, no display, and no human — and then asked what it tried to
show. Until this existed the 1,535-line live-session class had no test double
and therefore no tests.
"""
from __future__ import annotations

from typing import Any


class RecordingUI:
    """Records every call the backend makes to the human-showcase boundary."""

    consumes_audio_level = True

    # Class-level so conformance is statically verifiable, exactly as the two
    # shipped UIs declare them. Instances override in __init__.
    muted = False
    current_file = None
    assistant_name = "Aethelark"

    on_text_command = None
    on_interrupt = None
    on_remote_clicked = None

    def __init__(self, *, assistant_name: str = "Aethelark",
                 muted: bool = False, current_file: str | None = None,
                 reconfig_done: bool = True) -> None:
        self.assistant_name = assistant_name
        self.muted = muted
        self.current_file = current_file
        self._reconfig_done = reconfig_done

        self.logs: list[str] = []
        self.states: list[str] = []
        self.audio_levels: list[float] = []
        self.content: list[tuple[str, str]] = []
        self.camera_events: list[str] = []
        self.calls: list[tuple[str, tuple, dict]] = []
        self.reconfig_prompts = 0
        self.shutdown_requested = False
        self.phone_notifications = 0

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    # ---- the showcase ---------------------------------------------------
    def write_log(self, text: str) -> None:
        self._record("write_log", text)
        self.logs.append(text)

    def set_state(self, state: str) -> None:
        self._record("set_state", state)
        self.states.append(state)

    def set_audio_level(self, level: float) -> None:
        self._record("set_audio_level", level)
        self.audio_levels.append(level)

    def show_content(self, title: str, text: str) -> None:
        self._record("show_content", title, text)
        self.content.append((title, text))

    # ---- vision ---------------------------------------------------------
    def start_camera_stream(self) -> None:
        self._record("start_camera_stream")
        self.camera_events.append("start")

    def stop_camera_stream(self) -> None:
        self._record("stop_camera_stream")
        self.camera_events.append("stop")

    # ---- lifecycle ------------------------------------------------------
    def prompt_reconfig(self) -> None:
        self._record("prompt_reconfig")
        self.reconfig_prompts += 1

    def reconfig_complete(self) -> bool:
        self._record("reconfig_complete")
        return self._reconfig_done

    def request_shutdown(self) -> None:
        self._record("request_shutdown")
        self.shutdown_requested = True

    def notify_phone_connected(self) -> None:
        self._record("notify_phone_connected")
        self.phone_notifications += 1

    # ---- assertions helpers --------------------------------------------
    @property
    def last_state(self) -> str | None:
        return self.states[-1] if self.states else None

    def logged(self, needle: str) -> bool:
        return any(needle in line for line in self.logs)

    def call_names(self) -> list[str]:
        return [name for name, _, _ in self.calls]
