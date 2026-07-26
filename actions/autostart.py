"""Cross-platform auto-start on boot — parity+ with Mark-XLIX.

Registers/unregisters Aethelark to launch at login on:
  • Windows — HKCU ...\\CurrentVersion\\Run registry value (pythonw → no console)
  • macOS   — ~/Library/LaunchAgents/com.aethelark.assistant.plist (RunAtLoad)
  • Linux   — ~/.config/autostart/aethelark.desktop (XDG autostart)

Exposed as the `autostart` tool so the user can toggle it by voice
("start yourself when my computer boots", "stop auto-starting").
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

_OS     = platform.system()
APP_NAME = "Aethelark"
_BASE   = Path(__file__).resolve().parent.parent
_SCRIPT = _BASE / "aethelark_web.py"


def _python_exe() -> str:
    exe = sys.executable or ("python" if _OS == "Windows" else "python3")
    # On Windows prefer pythonw.exe so no console window flashes at login.
    if _OS == "Windows":
        pw = Path(exe).with_name("pythonw.exe")
        if pw.exists():
            return str(pw)
    return exe


def _launch_argv() -> list[str]:
    # Packaged binary → run itself. Dev → python + aethelark_web.py.
    if getattr(sys, "frozen", False):
        return [sys.executable]
    # Prefer the `eagle` wrapper on Unix if present (handles venv + cwd).
    if _OS != "Windows":
        wrapper = Path.home() / ".local" / "bin" / "eagle"
        if wrapper.exists():
            return [str(wrapper)]
    return [_python_exe(), str(_SCRIPT)]


# ── Windows ──────────────────────────────────────────────────────────────────
def _win_key():
    import winreg
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_ALL_ACCESS,
    )


def _win_enable() -> bool:
    import winreg
    cmd = " ".join(f'"{a}"' for a in _launch_argv())
    with _win_key() as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
    return True


def _win_disable() -> bool:
    import winreg
    try:
        with _win_key() as k:
            winreg.DeleteValue(k, APP_NAME)
    except FileNotFoundError:
        pass
    return True


def _win_status() -> bool:
    import winreg
    try:
        with _win_key() as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False


# ── macOS ────────────────────────────────────────────────────────────────────
_MAC_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.aethelark.assistant.plist"


def _mac_enable() -> bool:
    args_xml = "".join(f"      <string>{a}</string>\n" for a in _launch_argv())
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        '  <key>Label</key><string>com.aethelark.assistant</string>\n'
        '  <key>ProgramArguments</key><array>\n'
        f'{args_xml}'
        '  </array>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '</dict></plist>\n'
    )
    _MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
    _MAC_PLIST.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "load", str(_MAC_PLIST)], capture_output=True)
    return True


def _mac_disable() -> bool:
    if _MAC_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(_MAC_PLIST)], capture_output=True)
        _MAC_PLIST.unlink(missing_ok=True)
    return True


def _mac_status() -> bool:
    return _MAC_PLIST.exists()


# ── Linux ────────────────────────────────────────────────────────────────────
_LIN_DESKTOP = Path.home() / ".config" / "autostart" / "aethelark.desktop"


def _lin_enable() -> bool:
    exec_cmd = " ".join(_launch_argv())
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Aethelark\n"
        f"Exec={exec_cmd}\n"
        "Icon=aethelark\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    _LIN_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    _LIN_DESKTOP.write_text(content, encoding="utf-8")
    return True


def _lin_disable() -> bool:
    _LIN_DESKTOP.unlink(missing_ok=True)
    return True


def _lin_status() -> bool:
    return _LIN_DESKTOP.exists()


_TABLE = {
    "Windows": (_win_enable, _win_disable, _win_status),
    "Darwin":  (_mac_enable, _mac_disable, _mac_status),
    "Linux":   (_lin_enable, _lin_disable, _lin_status),
}


def is_enabled() -> bool:
    t = _TABLE.get(_OS)
    if not t:
        return False
    try:
        return t[2]()
    except Exception:
        return False


def set_enabled(on: bool) -> bool:
    t = _TABLE.get(_OS)
    if not t:
        return False
    return t[0]() if on else t[1]()


def autostart(parameters=None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "status").lower().strip()
    if _OS not in _TABLE:
        return f"Auto-start on boot isn't supported on {_OS}."
    try:
        if action in ("enable", "on", "true", "yes", "start"):
            set_enabled(True)
            return "Auto-start on boot is ON — I'll wake up with your computer."
        if action in ("disable", "off", "false", "no", "stop"):
            set_enabled(False)
            return "Auto-start on boot is OFF."
        if action == "toggle":
            new = not is_enabled()
            set_enabled(new)
            return f"Auto-start on boot is now {'ON' if new else 'OFF'}."
        return f"Auto-start on boot is currently {'ON' if is_enabled() else 'OFF'}."
    except Exception as e:
        return f"Auto-start change failed: {e}"


if __name__ == "__main__":
    import json
    print(json.dumps({"os": _OS, "argv": _launch_argv(), "enabled": is_enabled()}, indent=2))
