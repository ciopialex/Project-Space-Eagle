#desktop.py
import os
import sys
import json
import shutil
import subprocess
from core.run_cmd import run_cmd
import tempfile
import platform
from pathlib import Path
from datetime import datetime
from core import user_paths
from core.tool_result import Failed, ToolResult, normalize


class GenerationFailed(Failed):
    """The brain did not return code — it returned why it could not.

    A distinct type because the hazard is specific: this value must never
    reach `compile()`. It used to be `f"ERROR: {e}"`, an ordinary string, and
    `desktop_control` handed the generator's output straight to the executor.
    A rate limit — routine on a free tier — was therefore compiled as Python,
    and the user asking to tidy their desktop was told "Execution error:
    invalid syntax (<aethelark_desktop>, line 1)".
    """

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    path = user_paths.api_keys_path()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]
    
def _get_desktop() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def _build_sandbox() -> dict:
    import time

    safe_builtins = {
        "print": print,
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "tuple": tuple,
        "range": range, "enumerate": enumerate, "sorted": sorted,
        "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
        "max": max, "min": min, "sum": sum, "abs": abs,
        "zip": zip, "map": map, "filter": filter,
    }

    sandbox = {
        "__builtins__": safe_builtins,
        "Path": Path,
        "time": time,
        "shutil": type("shutil", (), {
            "copy2":      shutil.copy2,
            "copytree":   shutil.copytree,
            "disk_usage": shutil.disk_usage,
        })(),
        "os_path": os.path,  
    }

    if _PYAUTOGUI:
        sandbox["pyautogui"] = pyautogui

    if _OS == "Windows":
        try:
            import ctypes
            import winreg
            sandbox["ctypes"] = ctypes
            sandbox["winreg"] = type("winreg", (), {
                # Sadece okuma
                "OpenKey":      winreg.OpenKey,
                "QueryValueEx": winreg.QueryValueEx,
                "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
            })()
        except ImportError:
            pass

    return sandbox


def _execute_generated_code(code: str, player=None) -> str:
    if not code or code.strip() == "UNSAFE":
        return Failed(
            "This action cannot be performed safely.",
            guidance="The model declined this as unsafe. Tell the user it was not done and why; do not rephrase and retry.")

    # Kod temizleme
    if code.startswith("```"):
        lines = code.split("\n")
        code  = "\n".join(lines[1:-1]).strip()

    sandbox      = _build_sandbox()
    output_lines = []
    sandbox["__builtins__"]["print"] = lambda *a: output_lines.append(" ".join(str(x) for x in a))

    try:
        exec(compile(code, "<aethelark_desktop>", "exec"), sandbox)
        return "\n".join(output_lines) if output_lines else "Done."
    except Exception as e:
        print(f"[Desktop] Exec error: {e}\nCode:\n{code[:300]}")
        return Failed(
            f"Execution error: {e}",
            guidance="The generated step failed. Tell the user it did not work; do not claim the desktop changed.")


def _ask_gemini_for_desktop_action(task: str) -> str:
    """The generated code, or a `GenerationFailed` saying why there is none.

    Never raises. Client construction is inside the try for that reason: it
    reads the API key off disk, so a missing or malformed `api_keys.json` —
    the state every new install starts in — used to escape this function
    entirely, past its own error handling.
    """
    from google import genai as _genai

    desktop = str(_get_desktop())

    os_specific = ""
    if _OS == "Windows":
        os_specific = "- ctypes (Windows API calls, read-only)\n- winreg (registry READ only)"
    elif _OS == "Darwin":
        os_specific = "- subprocess is NOT available; use pyautogui or Path only"
    else:
        os_specific = "- subprocess is NOT available; use pyautogui or Path only"

    prompt = f"""You are a desktop automation assistant.
Current OS: {_OS}
Desktop path: {desktop}

Generate safe Python code to accomplish the task below.
Allowed modules ONLY:
- pyautogui (mouse, keyboard — if needed)
- pathlib.Path (file/folder inspection only, no deletion)
- shutil.copy2, shutil.copytree, shutil.disk_usage (NO move, NO rmtree)
- os_path (os.path equivalent, read-only)
- time.sleep
{os_specific}

Hard rules:
- NO file deletion (no unlink, no rmtree, no remove)
- NO subprocess calls
- NO exec() or eval() inside the code
- NO import statements (modules are pre-injected)
- NO file write operations except explicitly requested
- If task cannot be done safely with these tools, output exactly: UNSAFE

Output ONLY the Python code. No explanation, no markdown, no backticks.

Task: {task}"""

    try:
        _client = _genai.Client(api_key=_get_api_key())
        response = _client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        code = response.text.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code  = "\n".join(lines[1:-1]).strip()
        return code
    except Exception as e:
        # NOT a bare string: the caller executes whatever this returns.
        return GenerationFailed(
            f"Could not work out how to do that: {e}",
            guidance="Tell the user this did not run. If it mentions a rate "
                     "limit or quota, say it will work again shortly — do not "
                     "describe it as a bug or retry immediately.")

def set_wallpaper(image_path: str) -> str:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        return Failed(
            f"Image not found: {image_path}",
            guidance="Ask the user to confirm the image path.")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        return Failed(
            f"Unsupported format: {path.suffix}. Use jpg, png, bmp or webp.",
            guidance="Ask the user for a jpg or png instead.")

    try:
        if _OS == "Windows":
            import ctypes
            if path.suffix.lower() in {".webp", ".png"}:
                try:
                    from PIL import Image
                    bmp_path = Path(tempfile.mktemp(suffix=".bmp"))
                    Image.open(path).convert("RGB").save(bmp_path, "BMP")
                    path = bmp_path
                except ImportError:
                    pass 
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
            return f"Wallpaper set: {path.name}"

        elif _OS == "Darwin":
            script = (
                f'tell application "System Events" to tell every desktop to '
                f'set picture to POSIX file "{path}"'
            )
            run_cmd(["osascript", "-e", script], capture_output=True)
            return f"Wallpaper set: {path.name}"

        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            uri = f"file://{path}"

            if "gnome" in desktop_env or "unity" in desktop_env:
                run_cmd([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri", uri
                ], capture_output=True)
                run_cmd([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri-dark", uri
                ], capture_output=True)

            elif "kde" in desktop_env:
                # KDE Plasma
                script = f"""
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {{
    d = allDesktops[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", "file://{path}");
}}
"""
                run_cmd(
                    ["qdbus", "org.kde.plasmashell", "/PlasmaShell",
                     "org.kde.PlasmaShell.evaluateScript", script],
                    capture_output=True
                )

            elif "xfce" in desktop_env:
                run_cmd([
                    "xfconf-query", "-c", "xfce4-desktop",
                    "-p", "/backdrop/screen0/monitor0/workspace0/last-image",
                    "-s", str(path)
                ], capture_output=True)

            else:
                result = run_cmd(
                    ["feh", "--bg-scale", str(path)],
                    capture_output=True
                )
                if result.returncode != 0:
                    return (
                        f"Could not set wallpaper automatically on {desktop_env}. "
                        f"Try manually or install 'feh'."
                    )

            return f"Wallpaper set: {path.name}"

    except Exception as e:
        return Failed(
            f"Could not set wallpaper: {e}",
            guidance="Tell the user the wallpaper was not changed.")


def set_wallpaper_from_url(url: str) -> str:
    try:
        import urllib.request
        suffix = Path(url.split("?")[0]).suffix or ".jpg"
        tmp    = Path(tempfile.mktemp(suffix=suffix))
        urllib.request.urlretrieve(url, str(tmp))
        result = set_wallpaper(str(tmp))
        try:
            tmp.unlink()
        except Exception as _e:
            print(f"[desktop.py] Non-fatal error at line 247: {_e}")
        return result
    except Exception as e:
        return Failed(
            f"Could not download wallpaper: {e}",
            guidance="Tell the user the image could not be fetched; ask for another link.")


def get_current_wallpaper() -> str:
    try:
        if _OS == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop"
            )
            val, _ = winreg.QueryValueEx(key, "Wallpaper")
            winreg.CloseKey(key)
            return f"Current wallpaper: {val}"

        elif _OS == "Darwin":
            script = (
                'tell application "System Events" to get picture of desktop 1'
            )
            result = run_cmd(
                ["osascript", "-e", script],
                capture_output=True, text=True
            )
            return f"Current wallpaper: {result.stdout.strip()}"

        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            if "gnome" in desktop_env or "unity" in desktop_env:
                result = run_cmd(
                    ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                    capture_output=True, text=True
                )
                return f"Current wallpaper: {result.stdout.strip()}"
            return Failed(
                "Wallpaper path retrieval not supported for this desktop environment.",
                guidance="Tell the user this desktop does not report it.")

    except Exception as e:
        return Failed(
            f"Could not get wallpaper: {e}",
            guidance="Tell the user the current wallpaper could not be read.")

FILE_TYPE_MAP = {
    "Images":      {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
    "Documents":   {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                    ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
    "Videos":      {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Music":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Archives":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code":        {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                    ".cpp", ".java", ".cs", ".go", ".rs", ".sh", ".php"},
    "Executables": {".exe", ".msi", ".bat", ".cmd", ".sh", ".appimage", ".deb", ".rpm"},
}

_SKIP_EXTENSIONS = {
    "Windows": {".lnk", ".url"},
    "Darwin":  {".webloc"},
    "Linux":   {".desktop"},
}


def organize_desktop(mode: str = "by_type") -> str:
    desktop       = _get_desktop()
    skip_exts     = _SKIP_EXTENSIONS.get(_OS, set())
    moved, skipped = [], []

    for item in desktop.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        if item.suffix.lower() in skip_exts:
            continue

        if mode == "by_date":
            mtime       = datetime.fromtimestamp(item.stat().st_mtime)
            folder_name = mtime.strftime("%Y-%m")
        else:
            ext         = item.suffix.lower()
            folder_name = "Others"
            for folder, exts in FILE_TYPE_MAP.items():
                if ext in exts:
                    folder_name = folder
                    break

        target_dir = desktop / folder_name
        target_dir.mkdir(exist_ok=True)
        new_path = target_dir / item.name

        if new_path.exists():
            skipped.append(item.name)
            continue

        shutil.move(str(item), str(new_path))
        moved.append(f"{item.name} → {folder_name}/")

    result = f"Desktop organized ({mode}): {len(moved)} files moved."
    if moved:
        result += "\n" + "\n".join(moved[:8])
        if len(moved) > 8:
            result += f"\n... and {len(moved) - 8} more."
    if skipped:
        result += f"\n{len(skipped)} file(s) skipped (name conflict)."
    return result


def list_desktop() -> str:
    desktop = _get_desktop()
    items   = []
    for item in sorted(desktop.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            try:
                count = len(list(item.iterdir()))
            except PermissionError:
                count = "?"
            items.append(f"📁 {item.name}/ ({count} items)")
        else:
            size     = item.stat().st_size
            size_str = (
                f"{size / 1024:.1f} KB" if size < 1024 * 1024
                else f"{size / 1024 / 1024:.1f} MB"
            )
            items.append(f"📄 {item.name} ({size_str})")

    if not items:
        return "Desktop is empty."
    return f"Desktop ({len(items)} items):\n" + "\n".join(items)


def clean_desktop() -> str:
    desktop     = _get_desktop()
    skip_exts   = _SKIP_EXTENSIONS.get(_OS, set())
    today       = datetime.now().strftime("%Y-%m-%d")
    archive_dir = desktop / f"Desktop Archive {today}"
    archive_dir.mkdir(exist_ok=True)

    moved = 0
    for item in desktop.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        if item.suffix.lower() in skip_exts:
            continue
        new_path = archive_dir / item.name
        if not new_path.exists():
            shutil.move(str(item), str(new_path))
            moved += 1

    return f"Desktop cleaned: {moved} files archived to '{archive_dir.name}'."


def get_desktop_stats() -> str:
    desktop    = _get_desktop()
    files      = [i for i in desktop.iterdir() if i.is_file()]
    folders    = [i for i in desktop.iterdir() if i.is_dir()]
    total_size = sum(f.stat().st_size for f in files if f.exists())
    size_str   = (
        f"{total_size / 1024:.1f} KB" if total_size < 1024 * 1024
        else f"{total_size / 1024 / 1024:.1f} MB"
    )
    return (
        f"Desktop stats ({_OS}):\n"
        f"  Files   : {len(files)}\n"
        f"  Folders : {len(folders)}\n"
        f"  Size    : {size_str}\n"
        f"  Path    : {desktop}"
    )

#: The actions this tool implements. Named in one place so the "unknown
#: action" message cannot drift away from what actually exists.
_ACTIONS = ("wallpaper", "wallpaper_url", "current_wallpaper", "organize",
            "clean", "list", "stats", "task")


def desktop_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    parameters:
        action : wallpaper | wallpaper_url | current_wallpaper |
                 organize  | clean | list | stats |
                 task (AI-powered)
        path   : image path for 'wallpaper'
        url    : image URL for 'wallpaper_url'
        mode   : 'by_type' or 'by_date' for 'organize'
        task   : natural language description for AI-powered actions
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    task   = params.get("task", "").strip()

    if player:
        player.write_log(f"[desktop] {action or task[:40]}")

    try:
        if action == "wallpaper":
            path = params.get("path", "")
            return normalize(set_wallpaper(path) if path else Failed(
                "No image path provided.",
                guidance="Ask the user which image they want as the wallpaper."))

        elif action == "wallpaper_url":
            url = params.get("url", "")
            return normalize(set_wallpaper_from_url(url) if url else Failed(
                "No URL provided.",
                guidance="Ask the user for the image address."))

        elif action == "current_wallpaper":
            return normalize(get_current_wallpaper())

        elif action == "organize":
            return normalize(organize_desktop(params.get("mode", "by_type")))

        elif action == "clean":
            return normalize(clean_desktop())

        elif action == "list":
            return normalize(list_desktop())

        elif action == "stats":
            return normalize(get_desktop_stats())

        elif action == "task" or task:
            actual_task = task or params.get("description", "")
            if not actual_task:
                return ToolResult.failure(
                    "Please describe what you want to do on the desktop.",
                    guidance="Ask the user what they want done.")

            print(f"[Desktop] Asking Gemini: {actual_task}")
            if player:
                player.write_log("[Desktop] Generating action...")

            code = _ask_gemini_for_desktop_action(actual_task)
            # The gate. Generation failing is not code to run — it used to be
            # compiled anyway, turning every rate limit into a syntax error.
            if isinstance(code, GenerationFailed):
                return normalize(code)
            return normalize(_execute_generated_code(code, player=player))

        else:
            if action:
                # This used to hand the unrecognised string to Gemini and exec
                # the result. A typo - or an action name the model invented -
                # silently became code execution, and the caller could not tell
                # "no such action" from "it ran something". Found with
                # action='list_windows', which generated a Windows-only
                # pyautogui call and reported its AttributeError as the answer.
                #
                # Code generation is still here; it just has to be asked for.
                return ToolResult.failure(
                    f"'{action}' is not a desktop action. Use one of: "
                    f"{', '.join(_ACTIONS)}. For anything else, pass "
                    "action='task' with a plain-English description and it "
                    "will be worked out from there.",
                    guidance="Nothing ran. Call this again with a listed "
                             "action, or with action='task'.")
            return ToolResult.failure(
                "No action or task specified.",
                guidance="Ask the user what they want done on the desktop.")

    except Exception as e:
        print(f"[Desktop] Error: {e}")
        return ToolResult.failure(
            f"Desktop control error: {e}",
            guidance="Tell the user this action failed; do not claim it worked.")