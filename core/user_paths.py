"""Where the user's data lives - defined once, for everything.

Amendment I exists because a data path was computed relative to the source
tree. The defence against that recurring is not vigilance, it is having
exactly one function that answers the question, so a second answer cannot
quietly drift into existence somewhere else.

Nothing here ever returns a path inside the repository.
"""
from __future__ import annotations

import os
import shutil
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


# ── credentials ─────────────────────────────────────────────────────────────
#
# Amendment I again: api_keys.json was still being resolved relative to the
# source tree by 22 modules, each computing it slightly differently. The memory
# store was moved out for the same reason on 2026-07-30; secrets had not been.

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LEGACY_CREDENTIALS = ("api_keys.json", "google_token.json")
_credentials_migrated = False


def config_dir() -> Path:
    """Owner-only directory holding this user's credentials."""
    return ensure_private_dir(user_data_dir() / "config")


def api_keys_path() -> Path:
    """The one answer to "where are the API keys?"."""
    _migrate_legacy_credentials()
    return config_dir() / "api_keys.json"


def google_token_path() -> Path:
    _migrate_legacy_credentials()
    return config_dir() / "google_token.json"


def browser_profile_dir() -> Path:
    """The eagle's own browser profile.

    Deliberately not the user's Chrome profile. Attaching to that would give
    the eagle every session the user has open — silently, without a moment
    where they chose to grant it — and would tie it to a window it has to
    fight them for. One login per site, granted on purpose, is the trade.
    """
    return ensure_private_dir(user_data_dir() / "browser")


def _migrate_legacy_credentials() -> None:
    """Move credentials out of the source tree, once.

    Mirrors the memory store's migration: copy, tighten the mode, verify the
    copy is readable, then remove the original - leaving it behind is what let
    private data reach a commit before. Best-effort throughout; a failure here
    must never stop the app from starting.
    """
    global _credentials_migrated
    if _credentials_migrated:
        return
    _credentials_migrated = True

    for name in _LEGACY_CREDENTIALS:
        try:
            legacy = _REPO_ROOT / "config" / name
            target = config_dir() / name
            if not legacy.is_file():
                continue
            if target.exists():
                # An earlier migration copied but did not clean up. Leaving the
                # original behind is the condition that let private data reach a
                # commit before, so finish the job when the copies agree.
                if target.read_bytes() == legacy.read_bytes():
                    legacy.unlink(missing_ok=True)
                    print(f"[Paths] Removed stale in-repo {name}")
                continue
            shutil.copy2(legacy, target)
            try:
                os.chmod(target, FILE_MODE)
            except OSError:
                pass
            # Only drop the original once the copy is provably readable.
            if target.read_bytes() == legacy.read_bytes():
                legacy.unlink(missing_ok=True)
                print(f"[Paths] Moved {name} out of the source tree -> {target}")
        except Exception:
            continue
