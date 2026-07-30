"""Where Aethelark reads code from, and where it writes your data to.

Running from a git checkout these are the same directory, which is why the app
has been able to ignore the distinction so far. In an installed build they are
not, and getting it wrong loses data:

  * A frozen bundle unpacks read-only. On Windows it lands in Program Files and
    on macOS inside Aethelark.app - both places a normal user cannot write to.
  * Anything written beside the bundled files is either refused or, on a onefile
    build, dropped into a temp directory that is deleted when the app exits.

So: BASE_DIR for things we ship, USER_DIR for things you own. API keys, memory
and logs are yours - they go in USER_DIR, they survive upgrades, and they are
never overwritten by an install.

Wired in for frozen builds; a git checkout resolves BASE_DIR == USER_DIR, so
the curl install path behaves exactly as before.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Aethelark"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle rather than a checkout."""
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """Read-only root of the shipped files (html, fonts, images, seeds)."""
    if is_frozen():
        # onedir: alongside the executable. onefile: the extraction dir.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def user_dir() -> Path:
    """Writable per-user directory. Created on first access.

    Honours AETHELARK_HOME so a portable install or a test run can redirect
    everything without touching the real profile.
    """
    override = os.environ.get("AETHELARK_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        root = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        path = Path(root) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        path = Path(root) / APP_NAME.lower()

    path.mkdir(parents=True, exist_ok=True)
    return path


def user_file(*parts: str, seed: bool = True) -> Path:
    """Resolve a writable path under user_dir().

    When `seed` and the file does not exist yet, the shipped copy at the same
    relative path is copied in. That is how a fresh install starts with the
    default memory without the running app ever writing to the bundle.
    """
    target = user_dir().joinpath(*parts)
    if seed and not target.exists():
        shipped = base_dir().joinpath(*parts)
        if shipped.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(shipped, target)
    return target


# --- the three paths the app actually cares about ------------------------

def api_keys_path() -> Path:
    """config/api_keys.json - secrets, never seeded from the bundle."""
    return user_file("config", "api_keys.json", seed=False)


def memory_path() -> Path:
    """memory/long_term.json - seeded once, then owned by the user."""
    return user_file("memory", "long_term.json")


def log_path() -> Path:
    return user_file("logs", "aethelark.log", seed=False)


def read_json(path: Path, default):
    """Read JSON, falling back to `default` on a missing or corrupt file.

    A half-written config must not stop the app from starting - the user would
    have no way back in to fix it.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload) -> None:
    """Write JSON atomically, so an interrupted save cannot corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def ensure_browsers() -> bool:
    """Install Playwright's browsers on first use.

    They are ~400MB and most sessions never touch browser automation, so they
    are fetched on demand instead of tripling the installer. Returns False if
    the install fails; the caller decides whether that is fatal.
    """
    import subprocess

    marker = user_dir() / ".browsers-installed"
    if marker.exists():
        return True

    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(user_dir() / "browsers"))
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, env=env,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

    marker.touch()
    return True
