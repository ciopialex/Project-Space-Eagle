#computer_settings.py
import json
from core.tool_result import Failed, ToolResult, settled
import re
import sys
import time
import subprocess
from core.run_cmd import run_cmd
import platform
from pathlib import Path
from core import user_paths
from core import models

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    path = user_paths.api_keys_path()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_macos_wifi_interface() -> str:
    try:
        result = run_cmd(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception as _e:
        print(f"[computer_settings.py] Non-fatal error at line 54: {_e}")
    return "en0" 

#: How far "turn it up" moves. Was five keypresses of unknown size on Windows
#: and a 10% shell argument on Linux; now one number we choose, apply from the
#: measured value, and verify.
_VOLUME_STEP = 10


def volume_up():
    """+10%, measured from where the volume actually is, read back after."""
    from actions.cli import audio
    return audio.nudge_volume(+_VOLUME_STEP)


def volume_down():
    from actions.cli import audio
    return audio.nudge_volume(-_VOLUME_STEP)


def volume_mute():
    from actions.cli import audio
    return audio.set_mute(True)


def volume_unmute():
    """Its own function, deliberately.

    `ACTION_MAP` used to alias "unmute" onto `volume_mute` because the old
    implementation was a toggle, so both words landed on the same keypress.
    Against an explicit `set_mute(True)` that alias would mute the user when
    they asked to be unmuted - the loudest possible version of the tool lying
    about what it did.
    """
    from actions.cli import audio
    return audio.set_mute(False)


def volume_toggle_mute():
    """Reads the state, then sets its opposite explicitly.

    It was a hardcoded refusal - correct while `AudioBackend` was get / set /
    set_muted with no reader, but still advertised in the action vocabulary, so
    every "toggle mute" cost the model a turn to be told no. With `get_muted`
    on the contract the toggle is measurable: read, invert, verify. It still
    refuses on a backend that cannot report the state, because a toggle that
    guesses cannot say what it changed to.
    """
    from actions.cli import audio
    return audio.toggle_mute()


def volume_get():
    from actions.cli import audio
    return audio.get_volume()


def volume_set(value: int):
    from actions.cli import audio
    return audio.set_volume(int(value))


def _linux_sysfs_brightness(delta_pct: float) -> bool:
    """Adjust brightness via /sys/class/backlight sysfs — pure Python, no subprocess.
    delta_pct: fraction to add (e.g. +0.1 or -0.1). Returns True on success."""
    backlight_dir = Path("/sys/class/backlight")
    if not backlight_dir.exists():
        return False
    devices = list(backlight_dir.iterdir())
    if not devices:
        return False
    dev = devices[0]  # first backlight device (intel_backlight, amdgpu_bl0, etc.)
    try:
        max_b = int((dev / "max_brightness").read_text().strip())
        cur_b = int((dev / "brightness").read_text().strip())
        step = max(1, int(max_b * abs(delta_pct)))
        if delta_pct > 0:
            new_b = min(max_b, cur_b + step)
        else:
            new_b = max(max(1, int(max_b * 0.01)), cur_b - step)  # floor at ~1%
        # sysfs write requires root or udev rule; try brightnessctl as writer
        # since direct write may fail on most distros without polkit
        (dev / "brightness").write_text(str(new_b))
        return True
    except PermissionError:
        # Fall back to brightnessctl with the computed absolute value
        try:
            run_cmd(
                ["brightnessctl", "set", str(new_b)],
                capture_output=True, timeout=5
            )
            return True
        except Exception:
            return False
    except Exception:
        return False


#: GNOME owns the backlight on a desktop session and will set it without root,
#: which is the whole problem xrandr was being used to work around. Measured on
#: this machine 2026-09-03: 27% -> 32% moved /sys/class/backlight from 26621 to
#: 31373. The xrandr path this replaces returned True while changing nothing —
#: it clamped `min(1.0, current + delta)` against a ceiling the display already
#: sat at, and it moved software gamma rather than the panel in any case.
_GNOME_POWER = ("--session", "--dest", "org.gnome.SettingsDaemon.Power",
                "--object-path", "/org/gnome/SettingsDaemon/Power")
_SCREEN_IFACE = "org.gnome.SettingsDaemon.Power.Screen"


def _read_brightness():
    """The panel's brightness as a percentage, or None if it cannot be read.

    Tried in the order that works without privileges. Reading matters as much
    as writing: an action that cannot check itself can only ever claim success.
    """
    try:
        done = run_cmd(["gdbus", "call", *_GNOME_POWER, "--method",
                        "org.freedesktop.DBus.Properties.Get",
                        _SCREEN_IFACE, "Brightness"],
                       capture_output=True, text=True, timeout=5)
        if done.returncode == 0:
            digits = "".join(c for c in done.stdout if c.isdigit())
            if digits:
                return int(digits)
    except Exception:
        pass
    try:
        base = Path("/sys/class/backlight")
        devices = sorted(base.iterdir()) if base.exists() else []
        if devices:
            dev = devices[0]
            now = int((dev / "brightness").read_text().strip())
            top = int((dev / "max_brightness").read_text().strip())
            if top > 0:
                return round(now / top * 100)
    except Exception:
        pass
    return None


def _write_brightness(pct):
    """Ask GNOME for a level. Whether it took is decided by reading it back."""
    try:
        run_cmd(["gdbus", "call", *_GNOME_POWER, "--method",
                 "org.freedesktop.DBus.Properties.Set",
                 _SCREEN_IFACE, "Brightness",
                 "<int32 " + str(max(0, min(100, int(pct)))) + ">"],
                capture_output=True, text=True, timeout=5)
    except Exception as e:
        print("[Settings] brightness write failed: " + str(e))


def _step_brightness(delta_pct):
    """Move the backlight and report what actually happened.

    The shape `volume_set` already uses: hand back what the interface measured,
    never what was asked for. Before this, every path reported
    "Done: brightness_up." — brightnessctl was absent, the sysfs node is
    root-owned, and xrandr clamped to a no-op. Three dead routes, three
    successes claimed, and the screen never moved.
    """
    import shutil

    before = _read_brightness()

    if shutil.which("brightnessctl"):
        arg = "+%d%%" % delta_pct if delta_pct > 0 else "%d%%-" % abs(delta_pct)
        try:
            run_cmd(["brightnessctl", "set", arg], capture_output=True, timeout=5)
        except Exception as e:
            print("[Settings] brightnessctl failed: " + str(e))
    elif before is not None:
        _write_brightness(before + delta_pct)
    else:
        _linux_sysfs_brightness(delta_pct / 100.0)

    after = _read_brightness()
    if after is None:
        return Failed(
            "Could not read the screen brightness, so I cannot tell you whether "
            "it changed.",
            guidance="Do not claim the brightness changed. On Linux this needs a "
                     "GNOME session or brightnessctl installed.")
    if before is not None and after == before:
        direction = "brighter" if delta_pct > 0 else "dimmer"
        if (delta_pct > 0 and after >= 100) or (delta_pct < 0 and after <= 0):
            return ("The screen is already at %d%%, as %s as it goes. Say so; "
                    "nothing else needs doing." % (after, "bright" if delta_pct > 0 else "dim"))
        return Failed(
            "The screen is still at %d%% — it would not go any %s." % (after, direction),
            guidance="Say the brightness did not change. It may already be at the "
                     "limit, or this display may not be controllable.")
    return "Brightness %d%%." % after


def brightness_set(pct):
    """Set the panel to `pct` (0-100) on Linux and report the measured level."""
    pct = max(0, min(100, int(pct)))
    if _OS != "Linux":
        return Failed("Setting an exact brightness is only supported on Linux.",
                      guidance="Offer to turn it up or down instead.")
    before = _read_brightness()
    if before is None:
        return Failed(
            "Could not read the screen brightness, so I cannot set it.",
            guidance="Do not claim the brightness changed.")
    if before == pct:
        return "The screen is already at %d%%. Nothing else needs doing." % pct
    _write_brightness(pct)
    after = _read_brightness()
    for _ in range(6):
        if after is not None and after != before:
            break
        time.sleep(0.1)
        after = _read_brightness()
    if after is None or after == before:
        return Failed(
            "The screen is still at %d%%; this display did not accept the change." % before,
            guidance="Say the brightness did not change.")
    return "Brightness %d%%." % after


def brightness_up():
    if _OS == "Darwin":
        run_cmd(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
        return None
    if _OS == "Linux":
        return _step_brightness(10)
    try:
        run_cmd(
            ["powershell", "-Command",
             "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
             ".WmiSetBrightness(1, [math]::Min(100, "
             "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness)"
             ".CurrentBrightness + 10))"],
            capture_output=True, timeout=5, **_WIN_HIDE
        )
    except Exception as e:
        print("[Settings] brightness_up failed on Windows: " + str(e))
    return None

def brightness_down():
    if _OS == "Darwin":
        run_cmd(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
        return None
    if _OS == "Linux":
        return _step_brightness(-10)
    try:
        run_cmd(
            ["powershell", "-Command",
             "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
             ".WmiSetBrightness(1, [math]::Max(0, "
             "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness)"
             ".CurrentBrightness - 10))"],
            capture_output=True, timeout=5, **_WIN_HIDE
        )
    except Exception as e:
        print("[Settings] brightness_down failed on Windows: " + str(e))
    return None

def _focused_window_name() -> str | None:
    """The title of the window that currently has focus, or None.

    None means "I do not know what is in front" — never permission. An action
    that destroys something must name its target and fail CLOSED when it
    cannot.
    """
    try:
        from actions.grounding.resolver import structural_grounder
        g = structural_grounder()
        if g is None:
            return None
        for node in (g.nodes() if hasattr(g, "nodes") else []):
            states = getattr(node, "states", frozenset())
            role = str(getattr(node, "role", "") or "").lower()
            if "ACTIVE" in states or (role in ("frame", "window")
                                      and "FOCUSED" in states):
                name = str(getattr(node, "name", "") or "").strip()
                if name:
                    return name
    except Exception:
        return None
    return None


#: Windows the eagle must never close, however clearly it can see them. A
#: terminal is where the operator lives — closing it kills the session driving
#: the eagle, which is exactly what happened.
_NEVER_CLOSE = ("terminal", "konsole", "xterm", "iterm", "tmux", "screen",
                "@", "bash", "zsh", "powershell", "cmd.exe", "claude")

#: Titles the assistant actually gives its own windows — "AETHELARK",
#: "AETHELARK — Ignition", "<page> — AETHELARK" (aethelark_web.py:308,
#: ui.py:2872). Deliberately NOT "eagle": nothing here is ever titled that, and
#: the substring matches a terminal sitting in ~/Projects/Space-Eagle, which is
#: how "close this window" over a shell shut the assistant down instead.
_OWN_WINDOW_MARKERS = ("aethelark", "dynamic island")


def _is_own_window(title: str) -> bool:
    return any(marker in (title or "").lower() for marker in _OWN_WINDOW_MARKERS)


def quit_eagle():
    """Shut the assistant down from whatever thread asked.

    Tools run on a shared ThreadPoolExecutor, which is not a QThread and has no
    event loop, so `QTimer.singleShot` started there never fires — Qt warns
    "Timers can only be used with threads started with QThread" and the call
    returns having scheduled nothing. `QMetaObject.invokeMethod` with a queued
    connection is the cross-thread primitive: it posts to the receiver's own
    thread, which for the application object is the GUI thread.
    """
    try:
        from PyQt6.QtCore import QMetaObject, Qt
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            QMetaObject.invokeMethod(app, "quit",
                                     Qt.ConnectionType.QueuedConnection)
            return "Shutting down."
    except Exception as e:
        print(f"[computer_settings] graceful quit unavailable: {e}")
    # No Qt application to ask politely. Signalling our own process is the
    # honest remaining option, and it is reported as what it is.
    import os
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return "Exited."


def close_app():
    """Quit the focused application — once it is known which.

    Guarded for the same reason as close_window: this chord kills whatever
    holds focus, and live it went for the user's terminal.
    """
    try:
        where = _focused_window_name()
    except Exception:
        where = None
    if not where:
        return Failed(
            "Refusing to quit: nothing identifiable has focus, so it could "
            "be any window.",
            guidance="Ask the user which window they mean, or focus it first.")
    low = where.lower()
    # The refusal runs first. A protected window whose title happens to name
    # this project — a shell in ~/Projects/Space-Eagle — must be refused, not
    # treated as the assistant's own window.
    if any(bad in low for bad in _NEVER_CLOSE):
        return Failed(
            f"Refusing to quit {where!r} — that looks like a terminal, and "
            f"closing it would kill the session running the eagle.",
            guidance="Tell the user which window it is and let them do it.")
    # Only after the refusal: this is genuinely one of our own windows, so the
    # honest action is to shut the assistant down rather than send it a
    # keystroke and leave a half-dead process behind.
    if _is_own_window(low):
        return quit_eagle()
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")
    return f"Quit: {where}"


def close_window(backend=None):
    """Close the focused window — once it is known WHICH window that is.

    Was one line: `pyautogui.hotkey("alt", "f4")`, which closes whatever holds
    focus. Live, that tried to close the user's terminal — the one running the
    session driving the eagle. Third instance of the same bug: an OS-level
    action fired without knowing its target.
    """
    try:
        where = _focused_window_name()
    except Exception:
        where = None
    if not where:
        return Failed(
            "Refusing to close: nothing identifiable has focus, so it could "
            "be any window.",
            guidance="Ask the user which window to close, or focus it first.")
    low = where.lower()
    # The refusal runs first. A protected window whose title happens to name
    # this project — a shell in ~/Projects/Space-Eagle — must be refused, not
    # treated as the assistant's own window.
    if any(bad in low for bad in _NEVER_CLOSE):
        return Failed(
            f"Refusing to close {where!r} — that looks like a terminal, and "
            f"closing it would kill the session running the eagle.",
            guidance="Tell the user which window it is and let them close it.")
    # Only after the refusal: this is genuinely one of our own windows, so the
    # honest action is to shut the assistant down rather than send it a
    # keystroke and leave a half-dead process behind.
    if _is_own_window(low):
        return quit_eagle()
    if _OS == "Linux":
        # The name check above and the close now address the same window id.
        # A keystroke lands on whatever holds focus when it arrives, which is
        # not necessarily the window that was just validated — the guard and
        # the action were pointed at different things.
        from actions.cli import window as _window
        native = _window.close_active(backend=backend, protected=_NEVER_CLOSE)
        if native.ok or native.data.get("backend"):
            return native
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")
    return f"Closed: {where}"

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window(backend=None):
    if _OS == "Linux":
        from actions.cli import window as _window
        native = _window.minimize_active(backend=backend)
        if native.ok:
            return native
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window(backend=None):
    if _OS == "Darwin":
        run_cmd(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        # wmctrl is not installed on the reference machine and this branch
        # therefore raised into the bare except every time, silently landing
        # on the keystroke. Ask the native layer first and only fall back for
        # real.
        from actions.cli import window as _window
        native = _window.maximize_active(backend=backend)
        if native.ok:
            return native
        pyautogui.hotkey("super", "up")

def snap_left(backend=None):
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Darwin":
        # macOS has no built-in snap; try Rectangle app shortcut if installed
        try:
            run_cmd(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception as _e:
            print(f"[computer_settings.py] Non-fatal error at line 246: {_e}")
        pyautogui.hotkey("ctrl", "option", "left")
    else:  # Linux
        # Was `wmctrl ... 0,0,0,960,1080`: a binary that is not installed here
        # (so it raised), swallowed by a bare except (so it reported nothing),
        # at a size hardcoded for one monitor (so it was wrong anyway).
        from actions.cli import window as _window
        return _window.snap("left", backend=backend)

def snap_right(backend=None):
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Darwin":
        try:
            run_cmd(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception as _e:
            print(f"[computer_settings.py] Non-fatal error at line 262: {_e}")
        pyautogui.hotkey("ctrl", "option", "right")
    else:  # Linux
        from actions.cli import window as _window
        return _window.snap("right", backend=backend)

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if run_cmd(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():
    """Play/pause on whatever is really playing.

    Keeps its old name on purpose: `ACTION_MAP`, `core/capabilities.py` and the
    model's learned vocabulary all reach for `pause_video`, and renaming it
    would strand every one of them. Only the body changed - it was
    `pyautogui.press("space")`, a keystroke to whichever window had focus. With
    a podcast playing while the user is in a game, that space went to the game:
    the podcast kept playing, and the tool reported success either way.

    It reads the state and sends the explicit verb, so it refuses rather than
    guess when nothing can tell it what is playing.
    """
    from actions.cli import media
    return media.play_pause()


def media_status():
    """What is playing, if anything. There was no way to ask before."""
    from actions.cli import media
    return media.get_status()


def media_play():
    from actions.cli import media
    return media.play()


def media_pause():
    """Pause, explicitly. Distinct from `pause_video` for the same reason
    `volume_unmute` is distinct from `volume_mute`: a toggle asked to pause
    will happily resume."""
    from actions.cli import media
    return media.pause()


def media_next():
    from actions.cli import media
    return media.next_track()


def media_previous():
    from actions.cli import media
    return media.previous_track()



def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if run_cmd(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        run_cmd(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if run_cmd(["which", cmd[0]], capture_output=True).returncode == 0:
                run_cmd(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if run_cmd(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if run_cmd(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        run_cmd(["pmset", "displaysleepnow"], capture_output=True)
    else:
        run_cmd(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        run_cmd(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = run_cmd(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            run_cmd(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = run_cmd(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        run_cmd(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            run_cmd(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = run_cmd(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            run_cmd(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        run_cmd(["shutdown", "/r", "/t", "10"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        run_cmd(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        run_cmd(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        run_cmd(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        run_cmd(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        run_cmd(["systemctl", "poweroff"], capture_output=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "volume_get":          volume_get,
    "mute":                volume_mute,
    "unmute":              volume_unmute,
    "toggle_mute":         volume_toggle_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "media_status":        media_status,
    # The explicit verbs, reachable on purpose: `play_pause` refuses when it
    # cannot read the state and tells the model to ask explicitly instead, so
    # the two actions that guidance names have to exist.
    "media_play":          media_play,
    "media_pause":         media_pause,
    "next_track":          media_next,
    "skip":                media_next,
    "previous_track":      media_previous,
    "close_app":           close_app,
    "close_window":        close_window,
    "quit_eagle":          quit_eagle,
    "close_eagle":         quit_eagle,
    "exit_eagle":          quit_eagle,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

_DANGEROUS_ACTIONS = {"restart", "shutdown"}

from core.confirm import PARAM as CONFIRM_PARAM, Gate  # noqa: E402

#: One gate for this tool, living as long as the process does.
_GATE = Gate()

#: Actions that reach the machine through a native interface (`actions.cli`)
#: instead of emulated input. They need no pyautogui, so the missing-pyautogui
#: guard must not refuse them: the BARE case - a machine with nothing
#: installed - still gets everything that genuinely works. Listing the clified
#: actions rather than the emulated ones keeps the guard conservative: a new
#: action is assumed to need pyautogui until someone clifies it.
_CLIFIED_ACTIONS = frozenset({
    "volume_up", "volume_down", "volume_set", "volume_get",
    "mute", "unmute", "toggle_mute",
    # Read the level back and report the measured one, so the generic
    # "Done: brightness_up." can never fire over them.
    "brightness_up", "brightness_down", "brightness_set", "brightness_max", "brightness_min",
    # Media transport speaks to the player itself - MPRIS, SMTC, AppleScript -
    # so it works on a machine with no emulation library at all. `pause_video`
    # and `play_pause` are in here because they no longer press a key.
    "pause_video", "play_pause", "media_status", "media_play", "media_pause",
    "next_track", "skip", "previous_track",
})



def _detect_action(description: str) -> dict:

    from google import genai as _genai
    _client = _genai.Client(api_key=_get_api_key())

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, brightness_set, type_text, press_key, reload_n"

    prompt = f"""You are an intent detector for a computer control assistant.

The user issued a command (possibly in any language): "{description}"

Available actions: {available}

Return ONLY a valid JSON object:
{{"action": "action_name", "value": null_or_value}}

Rules:
- Pick the single best matching action from the available list.
- For volume_set: value is an integer 0-100.
- For brightness_set: value is an integer 0-100 (minimum is 0, maximum is 100).
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer (number of times to reload).
- If no clear match, pick the closest action.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        resp = _client.models.generate_content(model=models.FAST, contents=prompt)
        text = re.sub(r"```(?:json)?", "", resp.text).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> ToolResult:
    """Returns a ToolResult. It reported `?` no-status live on a plain
    volume_up, so the model had no way to know whether the volume moved."""
    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return ToolResult.failure(
            "No action could be determined.",
            guidance="Ask the user what they want changed - volume, brightness, "
                     "a key press.")

    # Narrow, not global. This used to refuse EVERY action when pyautogui was
    # absent, including the ones that never touch it: volume now goes through
    # wpctl / pactl / pycaw / osascript, and refusing it for a missing
    # emulation library is the opposite of what clifying is for.
    if not _PYAUTOGUI and action not in _CLIFIED_ACTIONS:
        return ToolResult.failure(
            "pyautogui is not installed. Run: pip install pyautogui",
            guidance="Nothing changed. Tell the user the dependency is missing.")

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    if action in _DANGEROUS_ACTIONS:
        # The same spoken-yes gate a module's print goes through. This used to
        # wait for `confirmed=yes`, an argument the tool's schema never
        # declared: the model could pass it on the first call without asking
        # anyone -- or have no declared way to pass it at all.
        token = str(params.get(CONFIRM_PARAM) or "")
        cleared, refused, lead = _GATE.check(
            f"computer_settings.{action}", {"action": action}, token,
            had_token=CONFIRM_PARAM in params)
        if not cleared:
            fresh = _GATE.issue(f"computer_settings.{action}", {"action": action})
            return ToolResult.failure(
                (f"{refused} " if refused else "")
                + f"This will {action.replace('_', ' ')} the computer now. "
                  "Shall I go ahead?",
                guidance=((f"{lead} " if lead else "")
                          + "Nothing has happened yet. Ask the user this OUT "
                          "LOUD and wait. If they say yes, call computer_settings "
                          f"again with action={action!r} and "
                          f"confirm_token={fresh!r}. If they say no, do not "
                          "call it again."),
                needs_confirmation=True, confirm_token=fresh)

    if action == "volume_set":
        try:
            # `int(value or 50)` turned "set the volume to 0" into 50%, and the
            # return line claimed the REQUESTED value - so a device that
            # stopped at 60 still reported "Volume set to 90%". Hand back what
            # the interface measured instead.
            requested = 50 if value is None or value == "" else int(value)
            return volume_set(requested)
        except Exception as e:
            return Failed(f"Could not set volume: {e}",
                          guidance="The volume did not change.")

    if action in ("brightness_set", "brightness_max", "brightness_min"):
        if action == "brightness_max":
            value = 100
        elif action == "brightness_min":
            value = 0
        try:
            return brightness_set(int(float(str(value).strip().rstrip("%"))))
        except (TypeError, ValueError):
            return Failed("brightness_set needs a level from 0 to 100.",
                          guidance="Call it again with value set to the level, "
                                   "0 for minimum, 100 for maximum.")

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return ToolResult.failure(
            f"Unknown action: '{raw_action}'.",
            guidance=f"Nothing changed. Known actions: {', '.join(sorted(ACTION_MAP))}.")

    try:
        outcome = func()
        # A clified action already answered the question - it read the value
        # back and its ToolResult carries the real one. Overwriting that with
        # "Done: volume_up." was the same lie one layer up, and it made the
        # whole native-backend slice invisible to the model. Every other
        # action still returns None and still lands on `settled` below, so
        # nothing else changes shape.
        # `Failed` is included because it IS a `str` subclass: a ToolResult-only
        # check missed it, so `close_window`'s "refusing to close your terminal"
        # refusal reached the model as ok=True with no guidance - the model
        # believing it had closed a window it had deliberately not touched.
        # `settled` already tells the two apart, and still turns None into the
        # generic "Done: {action}." every non-migrated action relies on.
        if isinstance(outcome, (ToolResult, Failed)) or (isinstance(outcome, str) and outcome):
            return settled(outcome)
        # `settled`, not a bare string: this exact call reported `?` no-status
        # live on a plain volume_up, so the model was told nothing about
        # whether the volume had actually moved.
        return settled(f"Done: {action}.")
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return ToolResult.failure(
            f"Action failed ({action}): {e}",
            guidance="Nothing changed. Do not tell the user it worked.")