"""Long-term memory: what the eagle knows about its user.

It lives in the platform's user-data directory, never in the source tree: a data
file in a checkout is one `git add -A` from public. Older in-repo stores are
migrated once and removed.

Written 0600 from the first byte.

Every save: write a temp file, fsync it, keep the current copy as a backup, rename
over the target, fsync the directory (on POSIX the rename is a directory
operation).

The RLock covers threads in one process, not two eagle processes; the atomic
rename makes the worst case a lost update, never a corrupt file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from threading import RLock


# One definition of where user data lives, shared with the journal.
from core.user_paths import ensure_private_dir, user_data_dir  # noqa: E402

DATA_DIR = user_data_dir()
MEMORY_PATH = DATA_DIR / "long_term.json"
BACKUP_PATH = MEMORY_PATH.with_suffix(".bak")

# Captured so migration can tell the real store from a test's patched path.
_DEFAULT_MEMORY_PATH = MEMORY_PATH

# The old location, inside the repository.
_LEGACY_PATH = Path(__file__).resolve().parent / "long_term.json"

_lock = RLock()
_migrated = False

MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 2200

CATEGORIES = ("identity", "preferences", "projects", "relationships",
              "wishes", "notes")

# Owner-only.
_FILE_MODE = 0o600


def _empty_memory() -> dict:
    return {name: {} for name in CATEGORIES}


def _migrate_legacy_store() -> None:
    """Move an old in-repo store to the user-data directory, once. Best-effort: never
    stops the app starting.
    """
    global _migrated
    if _migrated:
        return

    # Only the real store: a test must not consume the once-only flag.
    if MEMORY_PATH != _DEFAULT_MEMORY_PATH:
        return

    _migrated = True
    if MEMORY_PATH.exists() or not _LEGACY_PATH.exists():
        return

    try:
        _ensure_dir()
        shutil.copy2(_LEGACY_PATH, MEMORY_PATH)
        os.chmod(MEMORY_PATH, _FILE_MODE)
        print(f"[Memory] Moved store out of the source tree -> {MEMORY_PATH}")
        # Remove the originals.
        for stale in (_LEGACY_PATH, _LEGACY_PATH.with_suffix(".bak")):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError as e:
        print(f"[Memory] Could not migrate the old store ({e}); leaving it alone.")


def _ensure_dir() -> None:
    ensure_private_dir(MEMORY_PATH.parent)


def _read_store(path: Path) -> dict | None:
    """Parse one store file, or None if it is missing, torn, or not a dict."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None

    try:
        data = json.loads(raw)
    except ValueError as e:
        print(f"[Memory] Unreadable store {path.name}: {e}")
        return None

    if not isinstance(data, dict):
        return None

    # Tolerate older stores missing a category.
    for name in CATEGORIES:
        if not isinstance(data.get(name), dict):
            data[name] = {}
    return data


def load_memory() -> dict:
    """Read the store, falling back to the backup before giving up."""
    with _lock:
        _migrate_legacy_store()

        data = _read_store(MEMORY_PATH)
        if data is not None:
            return data

        recovered = _read_store(BACKUP_PATH)
        if recovered is not None:
            print("[Memory] Primary store unreadable - recovered from backup.")
            return recovered

        return _empty_memory()


def _encoded_size(memory: dict) -> int:
    return len(json.dumps(memory, ensure_ascii=False))


def _trim_to_limit(memory: dict) -> dict:
    """Evict the oldest facts until the store fits the prompt budget. Each entry's
    size is measured once, so a trim is one pass.
    """
    size = _encoded_size(memory)
    if size <= MEMORY_MAX_CHARS:
        return memory

    candidates = []
    for category in CATEGORIES:
        for key, entry in memory.get(category, {}).items():
            if not isinstance(entry, dict):
                continue
            cost = len(json.dumps({key: entry}, ensure_ascii=False))
            candidates.append((entry.get("updated", ""), category, key, cost))

    # Oldest first, ties by name, so a trim is deterministic.
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    for _, category, key, cost in candidates:
        if size <= MEMORY_MAX_CHARS:
            break
        if memory.get(category, {}).pop(key, None) is not None:
            size -= cost
            print(f"[Memory] Trimmed {category}/{key}")

    # The running total is an estimate: confirm once, fall back to exact accounting.
    while _encoded_size(memory) > MEMORY_MAX_CHARS and candidates:
        _, category, key, _ = candidates.pop(0)
        memory.get(category, {}).pop(key, None)

    return memory


def _preserve_current_as_backup() -> None:
    """Keep the outgoing version atomically: a hard link first, else a copy renamed
    into place.
    """
    if not MEMORY_PATH.exists():
        return

    staged = MEMORY_PATH.with_name(f".{MEMORY_PATH.name}.bak.{os.getpid()}")
    try:
        try:
            os.link(MEMORY_PATH, staged)
        except (OSError, NotImplementedError, AttributeError):
            shutil.copy2(MEMORY_PATH, staged)
        os.replace(staged, BACKUP_PATH)
    except OSError as e:
        # A missing backup is degraded, not a failed save.
        print(f"[Memory] Backup failed (continuing): {e}")
        try:
            os.unlink(staged)
        except OSError:
            pass


def _fsync_dir(path: Path) -> None:
    """Make the rename durable: fsync the parent directory. Not on Windows."""
    if sys.platform == "win32":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save_memory(memory: dict) -> None:
    """Persist the store so no crash can leave it empty or half-written: a reader
    sees the whole old file or the whole new one.
    """
    if not isinstance(memory, dict):
        return

    memory = _trim_to_limit(memory)
    payload = json.dumps(memory, indent=2, ensure_ascii=False)

    with _lock:
        _ensure_dir()

        # mkstemp creates 0600 and O_EXCL in one call.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(MEMORY_PATH.parent),
            prefix=f".{MEMORY_PATH.name}.",
            suffix=".tmp",
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())   # bytes on disk, not just in the page cache

            _preserve_current_as_backup()

            os.replace(tmp, MEMORY_PATH)   # atomic on POSIX and Windows
            _fsync_dir(MEMORY_PATH.parent)
        except BaseException:
            # Never leave temp files behind.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _truncate_value(value: str) -> str:
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH].rstrip() + "…"
    return value


def _recursive_update(target: dict, updates: dict) -> bool:
    """Merge `updates` into `target`; return whether anything changed, so a re-
    asserted fact skips the disk write.
    """
    changed = False

    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue

        # A dict without "value" is a nested category.
        if isinstance(value, dict) and "value" not in value:
            branch = target.get(key)
            if not isinstance(branch, dict):
                branch = {}
                target[key] = branch
                changed = True
            if _recursive_update(branch, value):
                changed = True
            continue

        raw = value["value"] if isinstance(value, dict) else value
        new_value = _truncate_value(str(raw))
        existing = target.get(key)
        if isinstance(existing, dict) and existing.get("value") == new_value:
            continue

        target[key] = {
            "value": new_value,
            "updated": datetime.now().strftime("%Y-%m-%d"),
        }
        changed = True

    return changed


def update_memory(memory_update: dict) -> dict:
    """Read-modify-write as one locked unit, so two concurrent writers cannot lose an
    update.
    """
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    with _lock:
        memory = load_memory()
        if _recursive_update(memory, memory_update):
            save_memory(memory)
            print(f"[Memory] Saved: {list(memory_update.keys())}")
        return memory


# (category, heading, entries worth the prompt budget). Identity is rendered separately.
_SECTIONS = (
    ("preferences",   "Preferences:",              15),
    ("projects",      "Active Projects / Goals:",   8),
    ("relationships", "People in their life:",     10),
    ("wishes",        "Wishes / Plans / Wants:",    8),
    ("notes",         "Other notes:",               8),
)

_IDENTITY_ORDER = ("name", "age", "birthday", "city", "job", "language",
                   "school", "nationality")

_PROMPT_MAX_CHARS = 2000


def _value_of(entry) -> str | None:
    """Entries are {"value": ..., "updated": ...}; older stores wrote bare values."""
    if isinstance(entry, dict):
        entry = entry.get("value")
    if entry in (None, ""):
        return None
    return str(entry)


def _titled(key: str) -> str:
    return key.replace("_", " ").title()


_LANGUAGE_KEYS = {"language", "languages", "reply_language", "preferred_language",
                  "spoken_language", "native_language"}


def format_memory_for_prompt(memory: dict | None) -> str:
    """Render the store as the block the model reads before answering."""
    if not memory:
        return ""

    lines: list[str] = []

    identity = memory.get("identity", {}) or {}
    # Known fields first, then anything learned later.
    ordered = [k for k in _IDENTITY_ORDER if k in identity]
    ordered += [k for k in identity if k not in _IDENTITY_ORDER]
    for key in ordered:
        if key.lower() in _LANGUAGE_KEYS:
            continue
        value = _value_of(identity.get(key))
        if value:
            lines.append(f"{_titled(key)}: {value}")

    for category, heading, limit in _SECTIONS:
        items = memory.get(category, {}) or {}
        if not items:
            continue

        rendered = []
        for key, entry in list(items.items())[:limit]:
            if key.lower() in _LANGUAGE_KEYS:
                continue
            value = _value_of(entry)
            if value:
                # Notes are free-form; do not title-case them.
                label = key if category == "notes" else _titled(key)
                rendered.append(f"  - {label}: {value}")

        if rendered:
            lines.append("")
            lines.append(heading)
            lines.extend(rendered)

    if not lines:
        return ""

    header = ("[WHAT YOU KNOW ABOUT THIS PERSON - use naturally, "
              "never recite like a list]\n")
    result = header + "\n".join(lines)
    if len(result) > _PROMPT_MAX_CHARS:
        result = result[:_PROMPT_MAX_CHARS - 3] + "…"
    return result + "\n"


def remember(key: str, value: str, category: str = "notes") -> str:
    if category not in CATEGORIES:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    with _lock:
        memory = load_memory()
        items = memory.get(category)
        if not isinstance(items, dict) or key not in items:
            return f"Not found: {category}/{key}"
        del items[key]
        save_memory(memory)
        return f"Forgotten: {category}/{key}"


# Callers and tools import this name.
forget_memory = forget
