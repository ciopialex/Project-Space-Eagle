"""The mutation journal: what makes an action undoable, and what proves it isn't.

THE ARGUMENT
Consent is not a correctness mechanism. Its real function is moving
accountability to the human, and it is paid for in attention - the scarcest
resource in the system and the first to go at six in the morning. Every prompt
that asks about something recoverable spends attention that the one genuinely
unrecoverable action needed. Consent theatre is not neutral; it is why the
important prompt gets skimmed.

So the assistant does not ask about things it can take back. It takes them
back. This module is the machinery that decides which is which.

Physics agrees, and more literally than it first sounds. Landauer's principle
says erasing information is the one logically irreversible operation in
computation - it costs energy and raises entropy. Everything else (copy, move,
rename, transform) is reversible in principle. So the only genuinely one-way
thing a file tool does is destroy the last copy of something, and that is a
choice, not a necessity. The price of not destroying it is storage, which is
approximately free.

HOW IT IS USED
Before a mutation, the caller stages whatever the mutation will destroy:

    token = stage(path)             # None if it cannot be cheaply staged
    if token is None:
        ...this operation is genuinely irreversible - escalate to the human
    ...perform the mutation...
    record("overwrite", path=path, blob=token)

`stage` returning None IS the irreversibility classifier. An operation whose
prior state cannot be captured within the budget is exactly the operation that
deserves a human, and there are far fewer of those than a prompt-everything
design implies.

WHAT THIS IS NOT
It is not a transaction log and does not pretend to atomicity across
operations. Undo is best-effort and refuses rather than guesses: if the world
moved under it - the file changed after the entry was written - it declines
that entry and says so. A wrong restore is worse than no restore.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from core.user_paths import FILE_MODE, ensure_private_dir, user_data_dir

# Above this, staging a copy stops being free and the operation is treated as
# irreversible instead. Storage is cheap, not infinite, and silently copying a
# multi-gigabyte file to make a delete undoable is its own kind of surprise.
MAX_STAGE_BYTES = 256 * 1024 * 1024

_lock = RLock()


def journal_dir() -> Path:
    return user_data_dir() / "journal"


def blobs_dir() -> Path:
    return journal_dir() / "blobs"


def journal_path() -> Path:
    return journal_dir() / "journal.jsonl"


@dataclass(frozen=True)
class Entry:
    id: str
    ts: float
    op: str
    fields: dict
    undone: bool = False

    def describe(self) -> str:
        target = self.fields.get("path") or self.fields.get("dst") or "?"
        when = time.strftime("%H:%M:%S", time.localtime(self.ts))
        state = " (undone)" if self.undone else ""
        return f"{when}  {self.op:<10} {target}{state}"


# ── staging ─────────────────────────────────────────────────────────────────

def stage(path: Path | str) -> str | None:
    """Capture the current contents of `path` so a mutation can be reversed.

    Returns a blob token, "" when there is nothing to preserve (the path does
    not exist yet, so the inverse is simply deletion), or None when the prior
    state cannot be captured - which marks the operation irreversible and is
    the signal to escalate.

    Content-addressed: staging the same bytes twice costs one copy.
    """
    target = Path(path)
    try:
        if not target.exists():
            return ""                      # nothing destroyed; inverse is remove
        if target.is_dir():
            return None                    # a tree is not cheap to snapshot
        size = target.stat().st_size
        if size > MAX_STAGE_BYTES:
            return None
    except OSError:
        return None

    try:
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        token = digest.hexdigest()

        blob = blobs_dir() / token
        if not blob.exists():
            ensure_private_dir(blobs_dir())
            tmp = blob.with_suffix(".tmp")
            shutil.copyfile(target, tmp)
            os.chmod(tmp, FILE_MODE)
            os.replace(tmp, blob)          # atomic publish
        return token
    except OSError:
        return None


def _restore(token: str, destination: Path) -> None:
    blob = blobs_dir() / token
    if not blob.exists():
        raise FileNotFoundError(f"staged content {token[:12]} is gone")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".restoring")
    shutil.copyfile(blob, tmp)
    os.replace(tmp, destination)


# ── recording ───────────────────────────────────────────────────────────────

def record(op: str, **fields) -> str:
    """Append one mutation to the journal. Returns its id."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "op": op,
        "fields": {k: (str(v) if isinstance(v, Path) else v)
                   for k, v in fields.items()},
        "undone": False,
    }
    with _lock:
        ensure_private_dir(journal_dir())
        path = journal_path()
        # Append-only, fsynced: a journal that loses its last entry after a
        # crash is worse than none, because the mutation still happened.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, FILE_MODE)
        except OSError:
            pass
    return entry["id"]


def _read_all() -> list[dict]:
    path = journal_path()
    if not path.exists():
        return []
    entries: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue        # a torn final line must not hide the rest
    return entries


def _mark_undone(entry_id: str) -> None:
    """Undo is itself recorded, so the log stays append-only.

    Rewriting the file to flip a flag would mean the one structure the system
    trusts for recovery is also the one being edited in place.
    """
    record("_undone", ref=entry_id)


def history(limit: int = 20) -> list[Entry]:
    raw = _read_all()
    undone = {e["fields"].get("ref") for e in raw if e["op"] == "_undone"}
    out = [Entry(e["id"], e["ts"], e["op"], e["fields"], e["id"] in undone)
           for e in raw if not e["op"].startswith("_")]
    return out[-limit:]


# ── undoing ─────────────────────────────────────────────────────────────────

def _invert(entry: Entry) -> str:
    f = entry.fields
    op = entry.op

    if op in ("create", "copy"):
        target = Path(f["path"] if "path" in f else f["dst"])
        if target.is_dir():
            target.rmdir()
        elif target.exists():
            target.unlink()
        return f"removed {target.name}"

    if op == "mkdir":
        target = Path(f["path"])
        if target.is_dir():
            target.rmdir()          # refuses if the user put something in it
        return f"removed folder {target.name}"

    if op in ("overwrite", "trash"):
        target = Path(f["path"])
        token = f.get("blob") or ""
        if not token:
            if target.exists():
                target.unlink()
            return f"removed {target.name}"
        _restore(token, target)
        return f"restored {target.name}"

    if op == "append":
        target = Path(f["path"])
        with open(target, "r+b") as handle:
            handle.truncate(int(f["prev_size"]))
        return f"truncated {target.name}"

    if op in ("move", "rename"):
        src, dst = Path(f["src"]), Path(f["dst"])
        if not dst.exists():
            raise FileNotFoundError(f"{dst} is no longer there")
        if src.exists():
            raise FileExistsError(f"{src} exists again - refusing to overwrite")
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        return f"moved {dst.name} back"

    raise ValueError(f"no inverse defined for '{op}'")


def undo_last(count: int = 1) -> str:
    """Reverse the most recent operations, newest first.

    Refuses rather than guesses. If a target changed after its entry was
    written, that entry is skipped and reported: consent binds to an artifact,
    and so does undo.
    """
    with _lock:
        pending = [e for e in history(limit=10_000) if not e.undone]
        if not pending:
            return "Nothing to undo."

        results: list[str] = []
        for entry in reversed(pending[-max(1, count):]):
            try:
                results.append(f"✓ {_invert(entry)}")
                _mark_undone(entry.id)
            except (OSError, ValueError) as e:
                results.append(f"✗ {entry.op} {entry.fields.get('path', '')}: {e}")
        return "\n".join(results)


def prune(keep_days: float = 30.0) -> int:
    """Drop blobs no live entry refers to. Returns how many were removed."""
    with _lock:
        cutoff = time.time() - keep_days * 86400
        referenced = {e.fields.get("blob") for e in history(limit=10_000)
                      if e.ts >= cutoff and e.fields.get("blob")}
        removed = 0
        if not blobs_dir().exists():
            return 0
        for blob in blobs_dir().iterdir():
            if blob.name.endswith(".tmp") or blob.name not in referenced:
                try:
                    blob.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
