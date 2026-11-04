"""The mutation journal: what makes an action undoable, and what proves it is not.

The assistant does not ask about things it can take back; it takes them back.
Destroying the last copy of something is the only truly one-way thing a file tool
does, and keeping a copy costs storage.

    token = stage(path)        # None if it cannot be cheaply staged
    if token is None:
        ...irreversible: escalate to the human
    ...perform the mutation...
    record("overwrite", path=path, blob=token)

stage() returning None is the irreversibility classifier. Not a transaction log:
undo is best-effort and refuses an entry whose file changed since.
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

# Above this size, staging is not free and the operation counts as irreversible.
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


def stage(path: Path | str) -> str | None:
    """Capture the current contents of `path`. Returns a blob token, "" when there is
    nothing to preserve (the inverse is deletion), or None when it cannot be
    captured: irreversible. Content-addressed.
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
    blob = blobs_dir() / token if token else None
    if blob is None or not blob.exists():
        raise FileNotFoundError(f"staged content {token[:12]} is gone")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".restoring")
    shutil.copyfile(blob, tmp)
    os.replace(tmp, destination)


# Operations that leave something at a path, and so have an after-state.
_LEAVES = ("create", "overwrite", "append", "copy", "move", "rename")


def _fingerprint(path) -> dict | None:
    """What a file looked like, cheaply: enough to tell it changed since."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def record(op: str, **fields) -> str:
    """Append one mutation to the journal; returns its id. Fingerprints the result so
    undo can refuse a file edited since.
    """
    if op in _LEAVES:
        target = fields.get("dst") if op in ("copy", "move", "rename") else fields.get("path")
        if target:
            fields.setdefault("after", _fingerprint(target))
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
        # Append-only and fsynced.
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
    """Undo is itself recorded, so the log stays append-only."""
    record("_undone", ref=entry_id)


def history(limit: int = 20) -> list[Entry]:
    raw = _read_all()
    undone = {e["fields"].get("ref") for e in raw if e["op"] == "_undone"}
    out = [Entry(e["id"], e["ts"], e["op"], e["fields"], e["id"] in undone)
           for e in raw if not e["op"].startswith("_")]
    return out[-limit:]


def _unchanged(entry: Entry, target: Path) -> None:
    """Raise unless `target` is exactly as this entry left it."""
    after = entry.fields.get("after")
    if after is None:
        raise ValueError(f"{target.name} was changed before undo kept track, "
                         f"so it was left alone")
    if _fingerprint(target) != after:
        raise ValueError(f"{target.name} has changed since, so it was left alone")


def _discard(target: Path) -> None:
    """Remove a file this journal created, to the trash where there is one."""
    try:
        import send2trash
        send2trash.send2trash(str(target))
    except Exception:
        target.unlink()


def _invert(entry: Entry) -> str:
    f = entry.fields
    op = entry.op

    if op in ("create", "copy"):
        target = Path(f["path"] if "path" in f else f["dst"])
        if target.is_dir():
            target.rmdir()          # refuses if anything is in it
            return f"removed {target.name}"
        if target.exists():
            _unchanged(entry, target)
            _discard(target)
        return f"removed {target.name}"

    if op == "mkdir":
        target = Path(f["path"])
        if target.is_dir():
            target.rmdir()          # refuses if the user put something in it
        return f"removed folder {target.name}"

    if op == "trash":
        target = Path(f["path"])
        if target.exists():
            raise FileExistsError(f"something new is at {target.name}; the old "
                                  f"one is still in the Trash")
        _restore(f.get("blob") or "", target)
        return f"brought {target.name} back"

    if op == "overwrite":
        target = Path(f["path"])
        _unchanged(entry, target)
        token = f.get("blob") or ""
        if not token:
            _discard(target)
            return f"removed {target.name}"
        _restore(token, target)
        return f"put back the earlier {target.name}"

    if op == "append":
        target = Path(f["path"])
        _unchanged(entry, target)
        with open(target, "r+b") as handle:
            handle.truncate(int(f["prev_size"]))
        return f"truncated {target.name}"

    if op in ("move", "rename"):
        src, dst = Path(f["src"]), Path(f["dst"])
        if not dst.exists():
            raise FileNotFoundError(f"{dst} is no longer there")
        _unchanged(entry, dst)
        if src.exists():
            raise FileExistsError(f"{src} exists again - refusing to overwrite")
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        return f"moved {dst.name} back"

    if op == "trashed_unstaged":
        raise ValueError(f"{Path(f['path']).name} is in the Trash -- it was too "
                         f"large to keep a copy of, so restore it from there")

    raise ValueError(f"no inverse defined for '{op}'")


def undo_last(count: int = 1) -> str:
    """Reverse the most recent operations, newest first. An entry whose target
    changed is skipped and reported.
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
    """Drop blobs no live entry refers to. Returns how many."""
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
