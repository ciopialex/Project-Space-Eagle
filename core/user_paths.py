"""Where the user's data lives - defined once, for everything.

Amendment I exists because a data path was computed relative to the source
tree. The defence against that recurring is not vigilance, it is having
exactly one function that answers the question, so a second answer cannot
quietly drift into existence somewhere else.

Nothing here ever returns a path inside the repository.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Aethelark"

# Owner-only: everything under here is the user's private material - what they
# told the assistant, and the previous contents of files it changed.
DIR_MODE = 0o700
FILE_MODE = 0o600


def user_data_dir() -> Path:
    """The platform's per-user data directory for this application.

    AETHELARK_DATA_DIR overrides it, which is how tests point the whole
    application at a sandbox without patching each consumer separately.
    """
    override = os.environ.get("AETHELARK_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(root) / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    root = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(root) / APP_NAME.lower()


def ensure_private_dir(path: Path) -> Path:
    """Create `path` (and parents) and make it owner-only where the OS allows."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, DIR_MODE)
    except OSError:
        pass   # Windows and some network mounts do not honour POSIX modes.
    return path
