import json
import subprocess
import sys
import time
from pathlib import Path

from core.tool_result import ToolResult

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
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

def _platform_os() -> str:
    import platform
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )


def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", _platform_os()).lower()
    except Exception:
        return _platform_os()


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    _require_pyautogui()
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, timeout=10,
                )
            time.sleep(2.5)
            return result.returncode == 0

        else: 
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via {app_name}."

def _send_whatsapp(receiver: str, message: str) -> str:
    # Primary path: WhatsApp Web driven inside the real/automation browser
    # profile — it targets the actual chat instead of blind-typing into whatever
    # window has focus (the old bug). We do NOT fall back to blind desktop typing
    # on failure; we surface the reason so the model can tell the user.
    try:
        from actions.whatsapp_web import send_whatsapp_web
        return send_whatsapp_web(receiver, message)
    except Exception as e:
        print(f"[SendMessage] WhatsApp Web path unavailable: {e}")
        return (f"Couldn't reach WhatsApp Web ({e}). "
                f"Make sure the browser automation (Playwright) is installed.")

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")   
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    """Pick a handler for a platform name.

    Short aliases have to match the whole name: "ig" is a substring of "signal",
    so a loose match sent every Signal message through the Instagram browser
    flow -- typing the user's text into the wrong app entirely. Only aliases long
    enough to be unambiguous match as substrings, which is what lets
    "whatsapp desktop" or "send on telegram" still resolve.
    """
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if key in keywords:
            return handler
    for keywords, handler in _PLATFORM_MAP:
        if any(len(k) > 3 and k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


# Idempotency guard: the live voice model sometimes re-issues the SAME
# send_message call several times in a row (slow desktop automation makes the
# tool result look "stale", so it retries). Without a guard that blind-typed the
# same text into the focused window over and over. We remember the last identical
# (platform, receiver, message) send and refuse to repeat it within this window.
# Wide enough to outlast a slow send (WhatsApp Web can take ~30s) plus the model
# talking between turns, so an identical auto-retry can't double-text the contact.
# Only *verified* sends record here (see below), so genuine failures still retry.
_DEDUPE_WINDOW_S = 120.0
_last_send: dict = {"key": None, "at": 0.0}


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    # Reject an identical re-send fired within the dedupe window.
    _key = (platform.lower(), receiver.lower(), message_text)
    _now = time.monotonic()
    if _last_send["key"] == _key and (_now - _last_send["at"]) < _DEDUPE_WINDOW_S:
        print(f"[SendMessage] ⏭️  Duplicate send suppressed: {platform} → {receiver}")
        return ToolResult.success(
            f"Already sent '{message_text}' to {receiver} on {platform} moments ago — "
            f"do NOT send it again.", deduped=True)

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        raw = handler(receiver, message_text)
    except Exception as e:
        raw = ToolResult.failure(f"Could not send message: {e}",
                                 guidance="Nothing was sent — report the failure honestly.")

    # WhatsApp now returns a structured ToolResult (explicit ok). Legacy desktop
    # handlers still return a string; detect their success by the "Message sent"
    # prefix (they all start with it). No more sniffing "sent" out of failures.
    if isinstance(raw, ToolResult):
        tr = raw
    elif raw.strip().lower().startswith("message sent"):
        tr = ToolResult.success(raw)
    else:
        tr = ToolResult.failure(raw, guidance="The message was NOT sent — tell the user.")

    # Record the dedupe key ONLY on a verified success, so a FAILED send can be
    # retried immediately instead of being falsely reported as "already sent".
    if tr.ok:
        _last_send["key"] = _key
        _last_send["at"]  = time.monotonic()

    print(f"[SendMessage] {'✅' if tr.ok else '❌'} {tr.message}")
    if player:
        player.write_log(f"[msg] {tr.message}")

    return tr