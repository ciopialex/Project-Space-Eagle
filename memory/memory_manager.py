"""Long-term memory: the small store of what the eagle knows about its user.

This file holds names, relationships, plans - the most personal data the
application touches. Two properties follow from that and drive every decision
below.

FIRST, IT DOES NOT LIVE IN THE SOURCE TREE.
The store used to be written to <repo>/memory/long_term.json. install.sh
git-clones into ~/.aethelark, so that put the user's private life inside a git
checkout, and on 2026-07-30 exactly that happened: a long_term.bak was
committed and published with real names in it. A data file inside a code
directory is one `git add -A` away from being public, and no .gitignore rule
survives contact with a user who does not know it is load-bearing. So the
store now lives in the platform's user-data directory, where it cannot be
swept into a commit by accident. Existing installs are migrated once, on
first use, and the old copy is removed.

SECOND, IT IS WRITTEN 0600.
Nothing else on the machine has any business reading it. The file is created
with owner-only permissions from the first byte rather than chmod'ed
afterwards, because "create then chmod" leaves a window in which it is
world-readable.

DURABILITY
Every save is: write a temp file -> fsync it -> preserve the current copy as a
backup -> atomically rename the temp over the target -> fsync the directory.
The last step is the one usually missed: on POSIX, fsync of a file does not
guarantee the *rename* survives a power cut, because the rename is a directory
operation. Without it, a save can be acknowledged and then vanish.

CONCURRENCY
The RLock below makes this safe across threads in one process, which is what
the app needs - the tool layer and the proactive engine both write. It is
deliberately NOT a cross-process lock: two Aethelark instances writing the same
store would still race, and the honest fix for that is not to run two. The
atomic rename means the worst case is a lost update, never a corrupt file.
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

# ── where the store lives ───────────────────────────────────────────────────

# One definition of where user data lives, shared with the journal. Two
# answers to that question is how a data path drifts back into the source
# tree, which is the whole subject of Amendment I.
from core.user_paths import ensure_private_dir, user_data_dir  # noqa: E402

DATA_DIR = user_data_dir()
MEMORY_PATH = DATA_DIR / "long_term.json"
BACKUP_PATH = MEMORY_PATH.with_suffix(".bak")

# Captured so migration can tell "the real store" from a test pointing the
# module somewhere else. Without this, patching MEMORY_PATH would drag the
# user's actual memory into a temp directory.
_DEFAULT_MEMORY_PATH = MEMORY_PATH

# The pre-2026-07-31 location: inside the repository itself.
_LEGACY_PATH = Path(__file__).resolve().parent / "long_term.json"

_lock = RLock()
_migrated = False

MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 2200

CATEGORIES = ("identity", "preferences", "projects", "relationships",
              "wishes", "notes")

# Owner-only. This file names the user's family.
_FILE_MODE = 0o600


def _empty_memory() -> dict:
    return {name: {} for name in CATEGORIES}


# ── migration off the old in-repo location ──────────────────────────────────

def _migrate_legacy_store() -> None:
    """Move a pre-existing in-repo store to the user-data directory, once.

    Best-effort by design: a failure here must never stop the app from
    starting. The worst case is that the user's memory looks empty and the old
    file is still on disk, which is recoverable; raising would not be.
    """
    global _migrated
    if _migrated:
        return

    # Only ever touch the real store. A patched MEMORY_PATH means a test, and
    # this must not consume the once-only flag on its way past - otherwise a
    # test that runs first would disable migration for the whole process.
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
        # Remove the originals: leaving them is what made the leak possible.
        for stale in (_LEGACY_PATH, _LEGACY_PATH.with_suffix(".bak")):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError as e:
        print(f"[Memory] Could not migrate the old store ({e}); leaving it alone.")


def _ensure_dir() -> None:
    ensure_private_dir(MEMORY_PATH.parent)


# ── reading ─────────────────────────────────────────────────────────────────

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

    # Tolerate a store written by an older version that lacked a category.
    for name in CATEGORIES:
        if not isinstance(data.get(name), dict):
            data[name] = {}
    return data


def load_memory() -> dict:
    """Read the store, falling back to the backup before giving up.

    A torn file used to mean a clean slate: the decode error was swallowed and
    every fact the eagle knew was gone with no way back. The last known-good
    copy removes that failure mode for the price of one extra file.
    """
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


# ── trimming ────────────────────────────────────────────────────────────────

def _encoded_size(memory: dict) -> int:
    return len(json.dumps(memory, ensure_ascii=False))


def _trim_to_limit(memory: dict) -> dict:
    """Evict the oldest facts until the store fits the prompt budget.

    Oldest-first, because a fact restated recently is one the user still lives
    with. The previous implementation re-serialised the entire store on every
    iteration, making a trim quadratic in the number of entries; here the cost
    of each candidate is measured once and subtracted as it goes, so a trim is
    one pass plus one confirming encode.
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

    # Oldest first; ties broken by name so a trim is deterministic and two
    # machines with the same store converge on the same result.
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    for _, category, key, cost in candidates:
        if size <= MEMORY_MAX_CHARS:
            break
        if memory.get(category, {}).pop(key, None) is not None:
            size -= cost
            print(f"[Memory] Trimmed {category}/{key}")

    # The running total is an estimate (separators, enclosing braces), so
    # confirm once and fall back to exact accounting if it was optimistic.
    while _encoded_size(memory) > MEMORY_MAX_CHARS and candidates:
        _, category, key, _ = candidates.pop(0)
        memory.get(category, {}).pop(key, None)

    return memory


# ── writing ─────────────────────────────────────────────────────────────────

def _preserve_current_as_backup() -> None:
    """Keep the outgoing version, atomically, so a torn read has a fallback.

    A hard link is tried first: it publishes the backup in one rename with no
    data copied, so the backup can never be observed half-written. Filesystems
    that refuse links fall back to a copy staged under a temp name and renamed
    into place, which costs an extra write but keeps the same guarantee.
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
        # A missing backup is a degraded state, not a failed save.
        print(f"[Memory] Backup failed (continuing): {e}")
        try:
            os.unlink(staged)
        except OSError:
            pass


def _fsync_dir(path: Path) -> None:
    """Make the rename itself durable.

    fsync on the file only promises its *contents*. The directory entry
    created by the rename lives in the parent, so without this a save can be
    acknowledged and then disappear across a power cut. Not available on
    Windows, where the rename is already committed by the time it returns.
    """
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
    """Persist the store so that no crash can leave it empty or half-written.

    `write_text` truncates the target before the new bytes land, so a crash in
    that window destroyed the file outright. Here the new content is written
    and fsynced to a temp file first, the current copy is preserved, and only
    then does an atomic rename swap it in - a reader sees the whole old file or
    the whole new one, never a torn one.
    """
    if not isinstance(memory, dict):
        return

    memory = _trim_to_limit(memory)
    payload = json.dumps(memory, indent=2, ensure_ascii=False)

    with _lock:
        _ensure_dir()

        # mkstemp creates 0600 and O_EXCL in one syscall: the file is never
        # briefly world-readable, and two processes cannot collide on the name.
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
            # Never leave litter for the next reader to trip over.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


# ── updating ────────────────────────────────────────────────────────────────

def _truncate_value(value: str) -> str:
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH].rstrip() + "…"
    return value


def _recursive_update(target: dict, updates: dict) -> bool:
    """Merge `updates` into `target`; return whether anything actually changed.

    Reporting "changed" honestly is what lets update_memory skip a disk write
    when a model re-asserts a fact it already told us, which it does constantly.
    """
    changed = False

    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue

        # A dict without "value" is a nested category, not a leaf entry.
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
    """Read-modify-write as ONE atomic unit.

    The lock is held across load and save together, not inside each. Held only
    inside, a tool and the proactive engine saving at the same moment both read
    the same base and the second write silently discarded the first.
    """
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    with _lock:
        memory = load_memory()
        if _recursive_update(memory, memory_update):
            save_memory(memory)
            print(f"[Memory] Saved: {list(memory_update.keys())}")
        return memory


# ── rendering for the prompt ────────────────────────────────────────────────

# (category, heading, how many entries are worth the prompt budget).
# Identity is handled separately: it is rendered bare, with the fields people
# actually get asked about first.
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


def format_memory_for_prompt(memory: dict | None) -> str:
    """Render the store as the block the model reads before answering.

    The five non-identity categories differ only by heading and cap, so they
    are driven from a table rather than written out one at a time - the old
    copy-paste made it easy to fix a bug in four places and miss the fifth.
    """
    if not memory:
        return ""

    lines: list[str] = []

    identity = memory.get("identity", {}) or {}
    # Known fields first, in a stable order, then anything else learned later.
    ordered = [k for k in _IDENTITY_ORDER if k in identity]
    ordered += [k for k in identity if k not in _IDENTITY_ORDER]
    for key in ordered:
        value = _value_of(identity.get(key))
        if value:
            lines.append(f"{_titled(key)}: {value}")

    for category, heading, limit in _SECTIONS:
        items = memory.get(category, {}) or {}
        if not items:
            continue

        rendered = []
        for key, entry in list(items.items())[:limit]:
            value = _value_of(entry)
            if value:
                # Notes are free-form; a title-cased key would mangle them.
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


# ── the tool-facing surface ─────────────────────────────────────────────────

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


# Kept: callers and tools import this name.
forget_memory = forget
