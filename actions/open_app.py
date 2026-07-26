import time
import os
import shlex
import subprocess
import platform
import shutil
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

import threading

def _launch_windows(app_name: str) -> bool:
    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            return True
        except Exception as _e:
            print(f"[open_app.py] Non-fatal error at line 102: {_e}")

    try:
        import pyautogui
        def gui_sequence():
            try:
                pyautogui.PAUSE = 0.1
                pyautogui.press("win")
                time.sleep(0.7)
                pyautogui.write(app_name, interval=0.05)
                time.sleep(0.9)
                pyautogui.press("enter")
            except Exception as e:
                print(f"[open_app] background gui sequence failed: {e}")
        
        threading.Thread(target=gui_sequence, name="WinAppLaunchGUI", daemon=True).start()
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:
    # `open -a` is the correct launcher; check its return code so we only claim
    # success when the app actually existed and launched (Popen alone always
    # "succeeds" because it just spawns `open`, which then errors invisibly).
    for target in (app_name, f"{app_name}.app"):
        try:
            r = subprocess.run(
                ["open", "-a", target],
                capture_output=True, timeout=12,
            )
            if r.returncode == 0:
                return True
        except Exception as _e:
            print(f"[open_app.py] Non-fatal error at line 138: {_e}")

    parts  = _split_cmd(app_name)
    binary = shutil.which(parts[0]) or shutil.which(parts[0].lower())
    if binary:
        try:
            subprocess.Popen([binary, *parts[1:]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as _e:
            print(f"[open_app.py] Non-fatal error at line 148: {_e}")

    # Spotlight fallback (best-effort — can't verify, so only used last).
    try:
        import pyautogui
        def gui_sequence():
            try:
                pyautogui.hotkey("command", "space")
                time.sleep(0.6)
                pyautogui.write(app_name, interval=0.05)
                time.sleep(0.8)
                pyautogui.press("enter")
            except Exception as e:
                print(f"[open_app] background gui sequence failed: {e}")
        threading.Thread(target=gui_sequence, name="MacAppLaunchGUI", daemon=True).start()
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

_LINUX_DESKTOP_DIRS = [
    Path.home() / ".local" / "share" / "applications",
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
    Path("/var/lib/snapd/desktop/applications"),
]


def _split_cmd(app_name: str) -> list[str]:
    """Split 'libreoffice --writer' into ['libreoffice','--writer'] so multi-word
    launch commands resolve their real binary instead of failing which()."""
    try:
        parts = shlex.split(app_name)
    except ValueError:
        parts = app_name.split()
    return parts or [app_name]


def _linux_desktop_id(app_name: str) -> str | None:
    """Find a .desktop id matching the app across standard + Flatpak/Snap dirs,
    so GUI apps not on $PATH (most Flatpaks) still launch.

    Matching is deliberately STRICT — a wrong match opens the wrong app, which is
    worse than an honest miss. We only accept: an exact stem match, or a
    reverse-DNS segment match (e.g. 'spotify' → com.spotify.Client,
    'calculator' → org.gnome.Calculator). We never do loose substring matching
    (which mis-matched 'code' → claude-code-url-handler)."""
    key     = app_name.lower().strip()
    keynorm = key.replace(" ", "").replace("-", "").replace("_", "")
    fallback = None
    for d in _LINUX_DESKTOP_DIRS:
        if not d.is_dir():
            continue
        try:
            for f in d.glob("*.desktop"):
                stem = f.stem
                s    = stem.lower()
                snorm = s.replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
                if s == key or snorm == keynorm:
                    return stem                      # exact — best possible
                segs = s.split(".")                  # reverse-DNS (dots only)
                if len(segs) > 1 and (key in segs or keynorm in segs):
                    fallback = fallback or stem       # e.g. com.spotify.Client
        except Exception:
            continue
    return fallback


def _launch_linux(app_name: str) -> bool:
    # URLs / file paths → xdg-open (its actual purpose).
    if app_name.startswith(("http://", "https://", "file:", "/", "~")):
        try:
            subprocess.Popen(["xdg-open", os.path.expanduser(app_name)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    # Terminal emulators: try common ones in order.
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    continue

    # Resolve a real binary on $PATH (handles multi-word commands + name variants).
    parts   = _split_cmd(app_name)
    first   = parts[0]
    binary  = (
        shutil.which(first) or
        shutil.which(first.lower()) or
        shutil.which(first.lower().replace(" ", "-")) or
        shutil.which(first.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen([binary, *parts[1:]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as _e:
            print(f"[open_app.py] Non-fatal error at line 260: {_e}")

    # Not on $PATH → resolve a .desktop entry (Flatpak/Snap/GUI apps). gtk-launch
    # returns quickly with an HONEST exit code, so we only claim success on rc==0.
    desktop_id = _linux_desktop_id(app_name)
    if desktop_id and shutil.which("gtk-launch"):
        try:
            r = subprocess.run(["gtk-launch", desktop_id],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return True
        except Exception as _e:
            print(f"[open_app.py] Non-fatal error at line 272: {_e}")
    # gio launch <desktop-file> is the modern alternative.
    if desktop_id and shutil.which("gio"):
        for d in _LINUX_DESKTOP_DIRS:
            f = d / f"{desktop_id}.desktop"
            if f.exists():
                try:
                    r = subprocess.run(["gio", "launch", str(f)],
                                       capture_output=True, timeout=10)
                    if r.returncode == 0:
                        return True
                except Exception as _e:
                    print(f"[open_app.py] Non-fatal error at line 284: {_e}")
                break

    return False   # honest failure — no false "Opened" claim


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"