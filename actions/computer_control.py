#computer_control.py
import io
import json
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import time
import random
from pathlib import Path

from actions.grounding import find_element
from core import user_paths
from core.tool_result import ToolResult, normalize

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

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = user_paths.api_keys_path()
from memory.memory_manager import MEMORY_PATH as _MEMORY_PATH

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "aethelark_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception as _e:
        print(f"[computer_control.py] Non-fatal error at line 74: {_e}")
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _grab_screen():
    """Cross-platform full-screen capture → PIL.Image.

    Prefers mss — it grabs directly (X11 / Windows / macOS) with NO external
    helper, unlike pyautogui.screenshot() which needs scrot or gnome-screenshot
    on Linux (often missing, which silently broke screenshot/screen_find here).
    Falls back to pyautogui only if mss is unavailable."""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            mons   = sct.monitors
            target = mons[1] if len(mons) > 1 else mons[0]
            shot   = sct.grab(target)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as e:
        print(f"[ComputerControl] mss capture failed ({e}); falling back to pyautogui")
        _require_pyautogui()
        return pyautogui.screenshot()

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception as _e:
        print(f"[computer_control.py] Non-fatal error at line 174: {_e}")
    return {}


# ── Waiting for an effect, not for a duration ──────────────────────────────
# These replace `time.sleep(0.3)` after an action that either worked or did
# not. A fixed pause is wrong in both directions: too short on a loaded
# machine and the next keystroke lands in the wrong window, too long on an
# idle one and it is pure delay. Worse, a sleep proves nothing - the code
# below used to report "Focused window: X" whether or not focus ever moved.

#: How long to keep checking before giving up on a window manager.
_FOCUS_TIMEOUT_S = 1.5
#: Grace period when the desktop cannot be read at all (no xdotool, Wayland).
#: Short, because it is a blind wait - the thing this module is moving away
#: from - but not zero, because typing instantly into an unfocused window is
#: the failure it is standing in for.
_BLIND_GRACE_S = 0.3


def _active_window_title() -> str | None:
    """The focused window's title, or None if this desktop will not say.

    Wayland deliberately refuses; X11 answers via xdotool. None is a real
    answer here and must not be confused with "no window focused" - one means
    we cannot verify, the other means we failed.
    """
    try:
        out = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            return out.stdout.strip() or ""
    except Exception:
        pass
    return None


def _await_focus(title: str, timeout: float = _FOCUS_TIMEOUT_S,
                 poll: float = 0.05):
    """True once `title` holds focus, False on timeout, None if unverifiable.

    Matched as a substring, case-insensitively: real titles carry suffixes
    nobody asked for ("report.txt — gedit"), and an exact match would fail on
    every window a person actually has open.
    """
    wanted = (title or "").strip().lower()
    if not wanted:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _active_window_title()
        if current is None:
            time.sleep(_BLIND_GRACE_S)      # cannot check; do not type blind
            return None
        if wanted in current.lower():
            return True
        time.sleep(poll)
    return False


def _clipboard_text() -> str | None:
    try:
        return pyperclip.paste()
    except Exception:
        return None


def _await_clipboard(text: str, timeout: float = 1.0, poll: float = 0.02) -> bool:
    """True once the clipboard actually holds `text`.

    Pasting before the copy lands puts the PREVIOUS clipboard contents into
    somebody's document - silently, and usually into something that matters.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _clipboard_text() == text:
            return True
        time.sleep(poll)
    return False


def _focus_result(title: str) -> ToolResult:
    """What to report after asking a window manager to focus something.

    Every platform branch used to `time.sleep(0.3)` and then return
    "Focused window: X" unconditionally. If focus never moved - a wrong title,
    a minimised window, a window manager that ignored the request - the eagle
    said it had worked and every keystroke after it went somewhere else, with
    nothing in the transcript to explain the damage.
    """
    focused = _await_focus(title)
    if focused is True:
        return ToolResult.success(f"Focused window: {title}", window=title)
    if focused is None:
        # A real third answer: Wayland refuses to say which window is focused.
        # Flattening it either way would be a lie, so it is a failure with the
        # uncertainty stated - the caller must not type into it blind.
        return ToolResult.failure(
            f"Asked the desktop to focus '{title}', but this system will not "
            "report which window is focused, so it is unverified.",
            guidance=("Do not type anything important yet. Ask the user to "
                      "confirm the right window is in front."))
    return ToolResult.failure(
        f"Could not focus a window matching '{title}'. Nothing was typed.",
        guidance=("It may be closed, minimised, or named differently. Ask the "
                  "user what the window is actually called - do not guess a "
                  "similar title and type into whatever comes forward."))


def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _PYPERCLIP:
        pyperclip.copy(text)
        if not _await_clipboard(text):
            # Fall back to typing rather than pasting whatever was there
            # before. A wrong paste is silent and lands in the user's document.
            pyautogui.typewrite(text, interval=0.01)
            return f"Typed (clipboard did not take): {text[:60]}{'…' if len(text) > 60 else ''}"
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        pyautogui.click(x, y, button=button, clicks=clicks)
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({x}, {y}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse → ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x1, y1, duration=0.2)
    pyautogui.dragTo(x2, y2, duration=duration, button="left")
    return f"Dragged ({x1},{y1}) → ({x2},{y2})"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    # No reader available, so the copy cannot be confirmed. The pause is a
    # blind one and labelled as such rather than dressed up as a result.
    _hotkey("ctrl", "c")
    time.sleep(_BLIND_GRACE_S)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if not _PYPERCLIP:
        return "pyperclip not available"
    pyperclip.copy(text)
    # Ctrl+V before the copy lands pastes whatever was in the clipboard
    # BEFORE - silently, into whatever the user had open. Wait for the text
    # to actually be there, and refuse rather than paste something else.
    if not _await_clipboard(text):
        return ToolResult.failure(
            "Could not put that on the clipboard, so nothing was pasted.",
            guidance=("Pasting anyway would have inserted the PREVIOUS "
                      "clipboard contents into the user's document. Try "
                      "action='type' instead."))
    _require_pyautogui()
    paste_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(paste_key, "v")
    return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"


def _screenshot(save_path: str | None = None) -> str:
    path = _safe_screenshot_path(save_path)
    img  = _grab_screen()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=5, **_WIN_HIDE,
            )
            return _focus_result(title)
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            return _focus_result(title)
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return _focus_result(title)
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            return _focus_result(title)
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"

def _screen_find(description: str) -> tuple[int, int] | None:
    """Locate a UI element on screen.

    Delegates to the tiered resolver: remembered position, then the app's own
    accessibility tree (local, exact, ~19ms), then a vision model as the last
    resort. Signature is unchanged — callers do not need to know that grounding
    got faster.
    """
    try:
        element = find_element(description)
    except Exception as e:
        print(f"[ComputerControl] ⚠️ screen_find failed: {e}")
        return None
    if element is None:
        return None
    return element.center

def _grounding_deps():
    """Imported lazily so a grounding failure can never break tool dispatch."""
    from actions.grounding import default_resolver
    from actions.grounding.resolver import platform_hit_test
    from actions.grounding.verify import act_and_verify
    from actions.grounding.waiting import wait_for
    return default_resolver(), platform_hit_test(), act_and_verify, wait_for


def _screen_click(description: str, intent: str = "click",
                  timeout: float = 5.0, force: bool = False) -> str:
    """Click something on screen — waiting for it, then checking it worked.

    Replaces a hardcoded 200ms sleep followed by an unconditional "Clicked"
    claim. A person waits for the button to settle, notices if it's greyed
    out or covered, clicks, and then glances at the screen. The result string
    says what actually happened, because reporting success without looking is
    the most common way an agent misleads its operator.
    """
    if not description:
        return "screen_click needs a 'description' of what to click."
    try:
        resolver, hit_test, act_and_verify, _ = _grounding_deps()
    except Exception as e:
        return f"Grounding unavailable: {e}"

    outcome = act_and_verify(
        description,
        lambda el: _click(x=el.x, y=el.y),
        resolver=resolver, action=intent,
        timeout=timeout, force=force, hit_test=hit_test,
    )

    if not outcome["acted"]:
        return (f"Did not click '{description}'. {outcome['detail']} "
                f"Nothing was clicked, so the screen is unchanged. Try "
                f"scroll_into_view first, or wait_for_element if it is still "
                f"loading.")

    where = outcome["before"]["bounds"] if outcome["before"] else "?"
    if outcome["changed"]:
        return f"Clicked '{description}' at {where}; the interface changed."
    return (f"Clicked '{description}' at {where}, but nothing observably "
            f"changed — the click may not have taken effect.")


def _wait_for_element(description: str, intent: str = "click",
                      timeout: float = 10.0) -> str:
    """Wait for something to appear and become usable, as a person would."""
    if not description:
        return "wait_for_element needs a 'description' of what to wait for."
    try:
        resolver, hit_test, _, wait_for = _grounding_deps()
    except Exception as e:
        return f"Grounding unavailable: {e}"

    result = wait_for(description, intent, resolver=resolver,
                      timeout=timeout, hit_test=hit_test)
    if result.ok:
        return (f"'{description}' is ready at {result.element.center} "
                f"(after {result.elapsed_ms:.0f}ms).")
    return (f"'{description}' did not become ready for {intent} within "
            f"{timeout:.0f}s — blocked on: {result.failed_check}.")


#: Every action this tool implements, named once so the error
#: messages cannot drift from what is actually dispatched below.
_ACTIONS = (
    "type", "smart_type", "click", "left_click", "double_click",
    "right_click", "move", "drag", "hotkey", "press", "scroll", "copy",
    "paste", "screenshot", "wait", "clear_field", "focus_window",
    "screen_find", "screen_click", "random_data", "user_data",
    "wait_for_element", "scroll_into_view",
)


def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      button        : 'left' | 'right' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed)
      click         — left click
      double_click  — double left click
      right_click   — right click
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      random_data   — generate fake form data
      user_data     — pull real data from memory
      wait_for_element — poll until an element appears
      scroll_into_view — scroll until an element is on screen
    """
    params = parameters or {}
    action = str(params.get("action") or "").lower().strip()

    if not action:
        return ToolResult.failure(
            "No action was given.",
            guidance=f"Pick one of: {', '.join(_ACTIONS)}.")

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {params}")

    try:
        result = _dispatch_action(action, params, player)
        return result if isinstance(result, ToolResult) else normalize(result)
    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return ToolResult.failure(
            f"'{action}' failed: {e}",
            guidance=("Tell the user it did not work. Do NOT assume the "
                      "keyboard or mouse did anything, and do not retry the "
                      "same way."))



#: Names the model reaches for that this tool does not use. `type_text` is
#: what several other computer-use APIs call `type`, so the prior is strong —
#: live, the model called it twice, was told the correct list both times, and
#: called it again. Arguing with that prior is a decision that depends on how
#: clever the model is; §0 of the roadmap says to move those into code.
_ACTION_ALIASES = {
    "type_text": "type", "write": "type", "write_text": "type",
    "enter_text": "type", "input_text": "type", "keyboard_type": "type",
    "send_keys": "type", "typetext": "type",
    "leftclick": "left_click", "rightclick": "right_click",
    "doubleclick": "double_click", "mouse_move": "move", "move_mouse": "move",
    "key_press": "press", "keypress": "press", "hot_key": "hotkey",
    "screengrab": "screenshot", "capture": "screenshot",
}

#: Keys the model puts the text under. `type` reads `text`; the live call sent
#: `{"action": "type_text", "value": "laptop stand"}` — so even with the action
#: name corrected it would have typed an EMPTY STRING and reported success.
_TEXT_KEYS = ("text", "value", "content", "string", "input")


def _text_of(params: dict) -> str:
    for key in _TEXT_KEYS:
        got = params.get(key)
        if isinstance(got, str) and got:
            return got
    return ""


def _dispatch_action(action: str, params: dict, player=None):
    try:
        action = _ACTION_ALIASES.get(action, action)

        if action in ("type", "smart_type"):
            text = _text_of(params)
            if not text:
                # Typing nothing is not a successful type. This used to call
                # _type("") and report "Typed: " as a success.
                return ToolResult.failure(
                    f"'{action}' was given no text to type.",
                    guidance="Call it again with the text under 'text'.")
            if action == "type":
                return _type(text)
            return _smart_type(text, clear_first=params.get("clear_first", True))

        if action in ("click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left", 1)

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"))

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            return _screen_click(
                params.get("description", ""),
                intent=params.get("intent", "click"),
                timeout=float(params.get("timeout", 5.0)),
                force=bool(params.get("force", False)),
            )

        if action == "wait_for_element":
            return _wait_for_element(
                params.get("description", ""),
                intent=params.get("intent", "click"),
                timeout=float(params.get("timeout", 10.0)),
            )

        if action == "scroll_into_view":
            desc = params.get("description", "")
            import sys as _sys
            if _sys.platform.startswith("win"):
                from actions.grounding.windows import scroll_to_element
            else:
                from actions.grounding.atspi import scroll_to_element
            if scroll_to_element(desc):
                return f"Scrolled '{desc}' into view."
            return (f"Could not scroll '{desc}' into view — it may not exist, "
                    f"or its application does not support scrolling requests.")

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return ToolResult.failure(
            f"'{action}' is not something computer_control does.",
            guidance=f"Use one of: {', '.join(_ACTIONS)}.")

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        # This used to return a string, which normalize turned into ok=True -
        # a crash reported as success, by the tool that drives the keyboard.
        return ToolResult.failure(
            f"'{action}' failed: {e}",
            guidance=("Tell the user it did not work. Do NOT assume the "
                      "keyboard or mouse did anything, and do not retry the "
                      "same way."))