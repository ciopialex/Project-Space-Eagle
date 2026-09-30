"""Window operations, aimed at a resolved window id.

Everything here exists because a keystroke has no target. `alt+f4`, `ctrl+w`,
`super+left` all go to whatever holds focus at the moment the key lands, which
is not necessarily what the caller looked at a moment earlier. `close_window`
already carried the scar — it resolves the focused window's name and refuses to
close a terminal, because the eagle once closed the session running it — and
then fired a keystroke anyway, leaving the guard and the action pointed at
different things.

Resolve the id once; act on that id; read it back.
"""
from __future__ import annotations

from actions.cli import registry
from actions.cli.base import Backend
from core.tool_result import ToolResult


def backends_for(platform: str) -> list:
    """Only Linux has a native backend today.

    Windows and macOS keep their existing keystroke paths in
    `computer_settings.py`. Writing untested backends for them from a Linux
    machine would add two more code paths nobody has ever run — the audio and
    media layers get away with cross-platform backends because their commands
    were verified against real captured output, and no such capture exists for
    a Windows window manager here.
    """
    if platform == "linux":
        from actions.cli.window.linux import XdotoolBackend
        return [XdotoolBackend()]
    return []


def resolve(backend=None):
    if backend is not None:
        return backend
    return registry.pick(backends_for(registry.current_platform()))


def close_active(backend=None, protected=(), _sleep=None) -> ToolResult:
    """Close the focused window, having checked WHICH window it is.

    The name check and the close now address the same window id, so nothing
    that steals focus in between can route around the terminal guard.
    """
    chosen = resolve(backend)
    if chosen is None:
        return ToolResult.failure("No native window backend on this machine.",
                                  guidance="Install xdotool.")

    window = chosen.active()
    if window is None:
        return ToolResult.failure(
            "Refusing to close: nothing identifiable has focus, so it could "
            "be any window.",
            guidance="Ask the user which window to close, or focus it first.",
            backend=chosen.name)

    low = (window.name or "").lower()
    if any(bad in low for bad in protected):
        return ToolResult.failure(
            f"Refusing to close {window.name!r} — that looks like a terminal, "
            f"and closing it would kill the session running the eagle.",
            guidance="Tell the user which window it is and let them close it.",
            backend=chosen.name)

    if not chosen.close(window.id):
        return ToolResult.failure(
            f"Could not close {window.name or window.id}.",
            guidance="The window manager refused the request.",
            backend=chosen.name)

    # A close is a request, not a command: an editor with unsaved work is
    # entitled to put up a dialog and stay open. Reporting "closed" for a
    # window still on screen is the lie ToolResult exists to prevent, so say
    # what is actually true.
    if _sleep is not None:
        _sleep(0.25)
    if chosen.exists(window.id):
        return ToolResult.success(
            f"Asked {window.name or 'the window'} to close; it is still open "
            f"— it may be asking about unsaved work.",
            backend=chosen.name, window=window.name, verified=False)

    return ToolResult.success(f"Closed: {window.name or window.id}",
                              backend=chosen.name, window=window.name,
                              verified=True)


def minimize_active(backend=None) -> ToolResult:
    chosen = resolve(backend)
    if chosen is None:
        return ToolResult.failure("No native window backend on this machine.",
                                  guidance="Install xdotool.")
    window = chosen.active()
    if window is None:
        return ToolResult.failure(
            "Nothing identifiable has focus, so there is no window to minimise.",
            guidance="Focus the window first.", backend=chosen.name)
    if not chosen.minimize(window.id):
        return ToolResult.failure(f"Could not minimise {window.name or window.id}.",
                                  backend=chosen.name)
    return ToolResult.success(f"Minimised: {window.name or window.id}",
                              backend=chosen.name, window=window.name)


def snap(side: str, backend=None) -> ToolResult:
    """Move the focused window to the left or right half of the screen.

    The half is computed from the real display geometry. The previous Linux
    path passed a hardcoded `0,0,0,960,1080` to `wmctrl` — a binary that is not
    installed here, so it raised, was swallowed by a bare except, and did
    nothing at all while reporting nothing wrong.
    """
    side = (side or "").strip().lower()
    if side not in ("left", "right"):
        return ToolResult.failure(f"Unknown side {side!r}.",
                                  guidance="Use 'left' or 'right'.")

    chosen = resolve(backend)
    if chosen is None:
        return ToolResult.failure("No native window backend on this machine.",
                                  guidance="Install xdotool.")

    size = chosen.screen_size()
    if size is None:
        return ToolResult.failure(
            "Could not read the screen size, so there is no half to snap to.",
            guidance="Check that xdotool can talk to the display.",
            backend=chosen.name)

    window = chosen.active()
    if window is None:
        return ToolResult.failure(
            "Nothing identifiable has focus, so there is no window to snap.",
            guidance="Focus the window first.", backend=chosen.name)

    width, height = size
    half = width // 2
    x = 0 if side == "left" else half

    if not chosen.move_resize(window.id, x, 0, half, height):
        return ToolResult.failure(f"Could not snap {window.name or window.id}.",
                                  backend=chosen.name)
    return ToolResult.success(
        f"Snapped {window.name or 'the window'} to the {side} half "
        f"({half}x{height}).",
        backend=chosen.name, window=window.name, side=side)


def maximize_active(backend=None) -> ToolResult:
    """Fill the screen with the focused window.

    Not the same thing as the window manager's maximised STATE — that needs an
    EWMH `_NET_WM_STATE` client message, and neither `wmctrl` nor a new enough
    `xdotool` (this one is the 2016 build, with no `windowstate`) is here to
    send one. Sizing the window to the display is what can actually be done,
    and the message says so rather than claiming the state was set.
    """
    chosen = resolve(backend)
    if chosen is None:
        return ToolResult.failure("No native window backend on this machine.",
                                  guidance="Install xdotool.")
    size = chosen.screen_size()
    window = chosen.active()
    if size is None or window is None:
        return ToolResult.failure(
            "Could not identify the window or the screen to fill.",
            guidance="Focus the window first.", backend=chosen.name)
    width, height = size
    if not chosen.move_resize(window.id, 0, 0, width, height):
        return ToolResult.failure(f"Could not resize {window.name or window.id}.",
                                  backend=chosen.name)
    return ToolResult.success(
        f"Filled the screen with {window.name or 'the window'}.",
        backend=chosen.name, window=window.name)
