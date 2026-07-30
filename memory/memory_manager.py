import json
import os
import shutil
from datetime import datetime
from threading import RLock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
BACKUP_PATH      = MEMORY_PATH.with_suffix(".bak")

# Reentrant on purpose: update_memory must hold the lock across load → mutate →
# save, and both of those take the lock themselves. With a plain Lock that is a
# deadlock; without holding it across the pair, two concurrent updates read the
# same base and the second write silently discards the first.
_lock            = RLock()
MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 2200

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def _read_store(path: Path) -> dict | None:
    """Parse one store file, or None if it is missing/unreadable/not a dict."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Memory] ⚠️ Unreadable store {path.name}: {e}")
        return None
    if not isinstance(data, dict):
        return None
    base = _empty_memory()
    for key in base:
        if key not in data:
            data[key] = {}
    return data


def load_memory() -> dict:
    """Read the store, falling back to the backup before giving up.

    A torn file used to mean a clean slate: the JSONDecodeError was caught, one
    warning was printed, and every fact the eagle knew about the user was gone
    with no way back. The last known-good copy costs one file and removes that
    entire failure mode.
    """
    with _lock:
        data = _read_store(MEMORY_PATH)
        if data is not None:
            return data

        recovered = _read_store(BACKUP_PATH)
        if recovered is not None:
            print("[Memory] ♻️  Primary store unreadable — recovered from backup.")
            return recovered

        return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        print(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    return memory

def save_memory(memory: dict) -> None:
    """Persist the store so that no crash can leave it empty or half-written.

    `write_text` truncates the target before the new bytes land, so a crash in
    that window destroyed the file. Here the new content is fully written and
    fsynced to a temp file FIRST, the current good copy is kept as a backup,
    and only then does an atomic rename swap it in. A reader either sees the
    whole old file or the whole new one — never a torn one.
    """
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    payload = json.dumps(memory, indent=2, ensure_ascii=False)

    with _lock:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MEMORY_PATH.with_name(MEMORY_PATH.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())   # bytes on disk, not just in the page cache

            # Keep the last good copy before replacing it, so even a corrupt
            # read later has somewhere to fall back to.
            if MEMORY_PATH.exists():
                try:
                    shutil.copy2(MEMORY_PATH, BACKUP_PATH)
                except Exception as e:
                    print(f"[Memory] ⚠️ Backup failed (continuing): {e}")

            os.replace(tmp, MEMORY_PATH)   # atomic on POSIX and Windows
        except BaseException:
            # Never leave litter for the next reader to trip over.
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    """Read-modify-write as ONE atomic unit.

    The lock used to be held inside load and inside save, but never across the
    pair — so a tool and the proactive engine updating at the same moment both
    read the same base and the second save discarded the first fact silently.
    """
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    with _lock:
        memory = load_memory()
        if _recursive_update(memory, memory_update):
            save_memory(memory)
            print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
        return memory

def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    if not lines:
        return ""

    header = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "…"

    return result + "\n"

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget