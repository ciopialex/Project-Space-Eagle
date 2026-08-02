"""The boundary between the eagle and the human.

`AethelarkLive` drives a UI it never names. Today that contract is discovered
by grepping for `self.ui.` and by crashing, which is why `aethelark_web.py`
carries a `_WinShim` whose docstring reads "Stands in for ui._win — the backend
only touches ._ready". Two fake Qt objects, invented to satisfy an interface
nobody wrote down.

This module writes it down. Everything the backend translates into something a
person can see or hear passes through these sixteen members and nothing else.

Adding a UI surface — Ghost, a CLI, a headless double — means implementing this
and nothing more. Adding a call to the backend that isn't here is a type error,
not a mystery bug that shows up for one user on one code path months later.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class AethelarkUI(Protocol):
    """What the live session expects of any surface showing it to a human."""

    # ---- state the backend reads ---------------------------------------
    muted: bool
    current_file: str | None
    assistant_name: str

    # ---- callbacks the backend assigns at startup ----------------------
    # main.py:850-852 wires these; the UI invokes them when the user acts.
    on_text_command: Callable[[str], None]
    on_interrupt: Callable[[], None]
    on_remote_clicked: Callable[[], Any]

    # ---- the showcase itself -------------------------------------------
    def write_log(self, text: str) -> None:
        """Show a line of transcript or status to the user."""

    def set_state(self, state: str) -> None:
        """LISTENING | THINKING | SPEAKING | WORKING | SLEEPING | MUTED."""

    def set_audio_level(self, level: float) -> None:
        """Drive a waveform. 0.0-1.0. May be a no-op for CSS-animated UIs."""

    def show_content(self, title: str, text: str) -> None:
        """Surface a larger result the user may want to read."""

    # ---- vision ---------------------------------------------------------
    def start_camera_stream(self) -> None: ...
    def stop_camera_stream(self) -> None: ...

    # ---- lifecycle ------------------------------------------------------
    def prompt_reconfig(self) -> None:
        """Ask the user to re-enter credentials."""

    def reconfig_complete(self) -> bool:
        """Has the user finished reconfiguring?

        Replaces the backend reaching into `ui._win._ready`. The old form
        forced every UI to own a private Qt-shaped object with a `_ready`
        attribute, whether or not it had any concept of a window.
        """

    def request_shutdown(self) -> None:
        """Ask the UI to close.

        Replaces the backend calling `ui.root.quit()`, which forced every UI
        to expose a `root` with a Tk-shaped API.
        """

    def notify_phone_connected(self) -> None: ...


#: Every member the backend touches. Kept explicit rather than derived,
#: because a Protocol's attribute declarations are invisible to isinstance().
REQUIRED_MEMBERS: tuple[str, ...] = (
    "muted",
    "current_file",
    "assistant_name",
    "on_text_command",
    "on_interrupt",
    "on_remote_clicked",
    "write_log",
    "set_state",
    "set_audio_level",
    "show_content",
    "start_camera_stream",
    "stop_camera_stream",
    "prompt_reconfig",
    "reconfig_complete",
    "request_shutdown",
    "notify_phone_connected",
)


def missing_members(ui: Any) -> list[str]:
    """Which parts of the contract does `ui` not provide?

    Accepts a class or an instance. Instance attributes assigned in __init__
    are invisible on the class, so a class-level check reports only what can
    be seen statically — which is exactly what a conformance test wants.
    """
    return [name for name in REQUIRED_MEMBERS if not hasattr(ui, name)]


def conforms(ui: Any) -> bool:
    return not missing_members(ui)
