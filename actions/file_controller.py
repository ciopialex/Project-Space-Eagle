"""File operations exposed to the model as one tool.

Everything here runs on the user's real filesystem at the request of a
language model, so the containment rule is the design, not a detail: every
path is resolved and checked against the allowed roots by ONE gate, and no
operation can reach the filesystem without passing through it.

That is a structural choice rather than a stylistic one. The previous version
checked containment at each call site, which meant it could be - and was -
forgotten: `rename` validated the source but never the destination, so a
new_name of "../../../.ssh/authorized_keys" moved a file clean out of the home
directory, and `disk_usage` probed any path at all. Here a function physically
cannot obtain a Path without `_resolve` having approved it.

Resolution happens before the check, so symlinks cannot be used to step
outside: a link inside the home directory pointing at /etc resolves to /etc
and is refused. What this does NOT defend against is an attacker who can
replace a path component with a symlink in the window between the check and
the operation. Closing that properly needs openat2/O_NOFOLLOW per component,
which is out of proportion for a local assistant acting for the person who
owns the files - but it is a known limit, not an oversight.

Deletion always goes to the trash. There is no code path here that
unlinks a user file permanently.
"""
from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime
from pathlib import Path

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# The only tree the tool may touch.
_SAFE_ROOTS: tuple[Path, ...] = (Path.home(),)

# Directories that are enormous, uninteresting, and mostly machine-generated.
# Walking them turns a "find my invoice" into a minute of disk churn while the
# user waits mid-conversation.
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".cache",
    ".local", ".npm", ".cargo", ".rustup", ".gradle", ".m2", ".nuget",
    "Library", "AppData", "site-packages", ".Trash", "snap",
})

_MAX_DIRS_WALKED = 2000
_MAX_READ_CHARS = 4000


class _Denied(Exception):
    """A path resolved outside the allowed roots."""


# ── the one gate ────────────────────────────────────────────────────────────

def _xdg(var: str, default: str) -> Path:
    """A well-known user directory, honouring the XDG override on Linux."""
    if _OS == "Linux":
        configured = os.environ.get(var, "")
        if configured and Path(configured).is_dir():
            return Path(configured)
    return Path.home() / default


def _shortcuts() -> dict[str, Path]:
    # Built per call: XDG variables and even the home directory can change
    # inside a session, and a cached map would quietly serve stale paths.
    return {
        "desktop":   _xdg("XDG_DESKTOP_DIR", "Desktop"),
        "downloads": _xdg("XDG_DOWNLOAD_DIR", "Downloads"),
        "documents": _xdg("XDG_DOCUMENTS_DIR", "Documents"),
        "pictures":  _xdg("XDG_PICTURES_DIR", "Pictures"),
        "music":     _xdg("XDG_MUSIC_DIR", "Music"),
        "videos":    _xdg("XDG_VIDEOS_DIR", "Videos"),
        "home":      Path.home(),
    }


def _resolve(raw: str, *parts: str) -> Path:
    """Resolve `raw` (plus optional child components) and prove containment.

    Raises _Denied for anything outside _SAFE_ROOTS. Every filesystem access
    in this module goes through here - that is what makes the guarantee hold
    even for code added later.
    """
    base = _shortcuts().get(str(raw).strip().lower()) or Path(raw).expanduser()

    for part in parts:
        if not part:
            continue
        # An absolute component would replace the base entirely under pathlib's
        # join, so a "name" of "/etc/passwd" must never be treated as a child.
        candidate = Path(part)
        if candidate.is_absolute() or candidate.drive:
            raise _Denied(part)
        base = base / candidate

    try:
        resolved = base.expanduser().resolve()
    except (OSError, RuntimeError) as e:      # RuntimeError: symlink loop
        raise _Denied(base) from e

    for root in _SAFE_ROOTS:
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved

    raise _Denied(resolved)


def _bare_name(name: str) -> str:
    """A single filename - no separators, no traversal, no drive.

    Used where a name is written into an existing parent (rename), because
    there the parent is already approved and only the leaf may vary.
    """
    cleaned = (name or "").strip()
    if not cleaned or cleaned in {".", ".."}:
        raise _Denied(name)
    if os.sep in cleaned or (os.altsep and os.altsep in cleaned):
        raise _Denied(name)
    if Path(cleaned).is_absolute() or Path(cleaned).drive:
        raise _Denied(name)
    return cleaned


def _guard(fn):
    """Turn containment failures and OS errors into answers, not tracebacks.

    The model receives this string, so it has to read as an outcome. Paths are
    reported as the resolved value on purpose: when a request is refused the
    user should see exactly what it resolved to.
    """
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _Denied as e:
            return f"Access denied: {e}"
        except PermissionError as e:
            return f"Permission denied: {e.filename or ''}".strip()
        except FileNotFoundError as e:
            return f"Not found: {e.filename or ''}".strip()
        except OSError as e:
            return f"{fn.__name__} failed: {e.strerror or e}"
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ── formatting ──────────────────────────────────────────────────────────────

def _format_size(num_bytes: float) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _walk(root: Path, max_dirs: int = _MAX_DIRS_WALKED):
    """Yield files beneath `root`, bounded and pruned.

    os.walk with topdown pruning, not rglob: pruning means a skipped directory
    is never descended into at all, where rglob would enumerate every entry
    first and filter afterwards. On a home directory that is the difference
    between touching a few thousand paths and a few hundred thousand.
    """
    walked = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        walked += 1
        if walked > max_dirs:
            return
        here = Path(dirpath)
        for filename in filenames:
            yield here / filename


def _stat_or_none(path: Path):
    """One unreadable entry must not abort a whole listing."""
    try:
        return path.stat()
    except OSError:
        return None


# ── operations ──────────────────────────────────────────────────────────────

@_guard
def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    target = _resolve(path)
    if not target.is_dir():
        return f"Not a directory: {target}"

    entries = []
    for item in sorted(target.iterdir()):
        if not show_hidden and item.name.startswith("."):
            continue
        if item.is_dir():
            entries.append(f"📁 {item.name}/")
        else:
            info = _stat_or_none(item)
            size = _format_size(info.st_size) if info else "unreadable"
            entries.append(f"📄 {item.name} ({size})")

    if not entries:
        return f"Directory is empty: {target.name}/"
    return f"Contents of {target.name}/ ({len(entries)} items):\n" + "\n".join(entries)


@_guard
def create_file(path: str, name: str = "", content: str = "") -> str:
    target = _resolve(path, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    return f"{'Overwrote' if existed else 'Created'} file: {target.name}"


@_guard
def create_folder(path: str, name: str = "") -> str:
    target = _resolve(path, name)
    target.mkdir(parents=True, exist_ok=True)
    return f"Folder created: {target.name}"


@_guard
def delete_file(path: str, name: str = "") -> str:
    target = _resolve(path, name)
    if not target.exists():
        return f"Not found: {target.name}"

    # The well-known directories themselves are never deletable, however the
    # request is phrased.
    protected = {p.resolve() for p in _shortcuts().values()}
    if target in protected:
        return f"Protected directory, cannot delete: {target.name}"

    if not _SEND2TRASH:
        return ("send2trash is not installed, so deletion is disabled - "
                "nothing here removes a file permanently. "
                "Run: pip install send2trash")
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


def _destination_for(src: Path, destination: str) -> Path:
    """Approve a destination and expand a directory target to a full path."""
    dst = _resolve(destination)
    if dst.is_dir():
        dst = _resolve(str(dst), src.name)
    if src == dst:
        raise _Denied(f"{dst} (source and destination are the same)")
    # Copying or moving a directory into itself walks the growing target
    # forever. Cheap to check, unpleasant to hit.
    if src.is_dir() and dst.is_relative_to(src):
        raise _Denied(f"{dst} (inside the source directory)")
    return dst


@_guard
def move_file(path: str, name: str = "", destination: str = "") -> str:
    if not destination:
        return "No destination specified."
    src = _resolve(path, name)
    if not src.exists():
        return f"Source not found: {src.name}"

    dst = _destination_for(src, destination)
    if dst.exists():
        return f"'{dst.name}' already exists in {dst.parent.name}/ - not overwriting."

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved: {src.name} → {dst.parent.name}/"


@_guard
def copy_file(path: str, name: str = "", destination: str = "") -> str:
    if not destination:
        return "No destination specified."
    src = _resolve(path, name)
    if not src.exists():
        return f"Source not found: {src.name}"

    dst = _destination_for(src, destination)
    if dst.exists():
        return f"'{dst.name}' already exists in {dst.parent.name}/ - not overwriting."

    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(str(src), str(dst))
    else:
        shutil.copy2(str(src), str(dst))
    return f"Copied: {src.name} → {dst.parent.name}/"


@_guard
def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    target = _resolve(path, name)
    if not target.exists():
        return f"Not found: {target.name}"
    if not new_name:
        return "No new name provided."

    # THE fix. The old version joined new_name onto the parent and renamed
    # with no containment check at all, so "../../.ssh/authorized_keys"
    # relocated the file outside the home directory entirely. Two gates now:
    # the leaf must be a bare filename, and the result is resolved and checked
    # like any other path.
    leaf = _bare_name(new_name)
    new_path = _resolve(str(target.parent), leaf)
    if new_path.exists():
        return f"A file named '{leaf}' already exists here."

    target.rename(new_path)
    return f"Renamed: {target.name} → {leaf}"


@_guard
def read_file(path: str, name: str = "", max_chars: int = _MAX_READ_CHARS) -> str:
    target = _resolve(path, name)
    if not target.exists():
        return f"File not found: {target.name}"
    if not target.is_file():
        return f"Not a file: {target.name}"

    limit = max(1, int(max_chars))
    # Read one character past the limit rather than slurping the file and
    # slicing. The old version called read_text() first, so pointing this at a
    # multi-gigabyte file took the whole process down with it - and the model
    # only ever sees the first few thousand characters anyway.
    with open(target, "r", encoding="utf-8", errors="replace") as handle:
        chunk = handle.read(limit + 1)

    if len(chunk) > limit:
        size = _stat_or_none(target)
        total = f" of {_format_size(size.st_size)}" if size else ""
        return chunk[:limit] + f"\n\n[Truncated - first {limit} characters{total}]"
    return chunk


@_guard
def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    target = _resolve(path, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a" if append else "w", encoding="utf-8") as handle:
        handle.write(content)
    return f"{'Appended to' if append else 'Written to'}: {target.name}"


@_guard
def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    root = _resolve(path)
    if not root.is_dir():
        return f"Search path not found: {root}"

    wanted_ext = extension.lower().lstrip("*") if extension else ""
    if wanted_ext and not wanted_ext.startswith("."):
        wanted_ext = "." + wanted_ext
    needle = name.lower()
    limit = max(1, min(int(max_results), 50))

    results = []
    for item in _walk(root):
        if wanted_ext and item.suffix.lower() != wanted_ext:
            continue
        if needle and needle not in item.name.lower():
            continue
        info = _stat_or_none(item)
        size = _format_size(info.st_size) if info else "unreadable"
        results.append(f"📄 {item.name} ({size}) — {item.parent}")
        if len(results) >= limit:
            break

    if not results:
        return f"No {name or extension or 'files'} found in {root.name}/"
    return f"Found {len(results)} file(s):\n" + "\n".join(results)


@_guard
def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    root = _resolve(path)
    if not root.is_dir():
        return f"Path not found: {root}"

    limit = max(1, min(int(count), 50))

    # Only the top N are ever shown, so only the top N are kept. The old
    # version accumulated every file in the tree and sorted the lot - on a
    # home directory that is a list of hundreds of thousands of tuples held in
    # memory to print ten lines.
    import heapq
    largest: list[tuple[int, str, str]] = []
    for item in _walk(root):
        info = _stat_or_none(item)
        if info is None:
            continue
        entry = (info.st_size, item.name, str(item.parent))
        if len(largest) < limit:
            heapq.heappush(largest, entry)
        elif entry[0] > largest[0][0]:
            heapq.heapreplace(largest, entry)

    if not largest:
        return "No files found."

    lines = [f"Top {len(largest)} largest files in {root.name}/:"]
    for size, filename, parent in sorted(largest, reverse=True):
        lines.append(f"  {_format_size(size):>10}  {filename}  ({parent})")
    return "\n".join(lines)


@_guard
def get_disk_usage(path: str = "home") -> str:
    # This used to skip the containment check entirely, which let it probe any
    # mount point on the machine.
    target = _resolve(path)
    usage = shutil.disk_usage(target)
    percent = (usage.used / usage.total * 100) if usage.total else 0.0
    return (f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({percent:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}")


_TYPE_FOLDERS: dict[str, frozenset[str]] = {
    "Images":    frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
                            ".svg", ".ico", ".heic"}),
    "Documents": frozenset({".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                            ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"}),
    "Videos":    frozenset({".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
                            ".webm", ".m4v"}),
    "Music":     frozenset({".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma",
                            ".m4a"}),
    "Archives":  frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}),
    "Code":      frozenset({".py", ".js", ".ts", ".html", ".css", ".json",
                            ".xml", ".cpp", ".java", ".cs", ".go", ".rs", ".sh"}),
}

# Reversed once at import: one dict lookup per file instead of scanning six
# sets for every item on the desktop.
_EXT_TO_FOLDER: dict[str, str] = {
    ext: folder for folder, exts in _TYPE_FOLDERS.items() for ext in exts
}


@_guard
def organize_desktop() -> str:
    desktop = _resolve("desktop")
    if not desktop.is_dir():
        return f"No desktop directory at {desktop}"

    moved: list[str] = []
    skipped = 0

    for item in sorted(desktop.iterdir()):
        if item.is_dir() or item.name.startswith("."):
            continue

        folder = _EXT_TO_FOLDER.get(item.suffix.lower(), "Others")
        target_dir = desktop / folder
        destination = target_dir / item.name

        if destination.exists():
            skipped += 1
            continue

        target_dir.mkdir(exist_ok=True)
        shutil.move(str(item), str(destination))
        moved.append(f"{item.name} → {folder}/")

    result = f"Desktop organized: {len(moved)} files moved."
    if moved:
        result += "\n" + "\n".join(moved[:8])
        if len(moved) > 8:
            result += f"\n... and {len(moved) - 8} more."
    if skipped:
        result += f"\n{skipped} file(s) skipped (name conflict)."
    return result


@_guard
def get_file_info(path: str, name: str = "") -> str:
    target = _resolve(path, name)
    info = _stat_or_none(target)
    if info is None:
        return f"Not found: {target.name}"

    fields = {
        "Name":      target.name,
        "Type":      "Folder" if target.is_dir() else "File",
        "Size":      _format_size(info.st_size),
        "Location":  str(target.parent),
        "Created":   datetime.fromtimestamp(info.st_ctime).strftime("%Y-%m-%d %H:%M"),
        "Modified":  datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "Extension": target.suffix or "—",
    }
    return "\n".join(f"  {k}: {v}" for k, v in fields.items())


# ── tool entry point ────────────────────────────────────────────────────────

def file_controller(parameters: dict | None = None, response=None,
                    player=None, session_memory=None) -> str:
    """Dispatch one file action. Called from main.py's tool executor."""
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()
    path = params.get("path", "desktop")
    name = params.get("name", "")

    if player:
        try:
            player.write_log(f"[file] {action} {name or path}")
        except Exception:
            pass   # a UI that cannot log must not fail the file operation

    handlers = {
        "list": lambda: list_files(path, bool(params.get("show_hidden", False))),
        "create_file": lambda: create_file(path, name, params.get("content", "")),
        "create_folder": lambda: create_folder(path, name),
        "delete": lambda: delete_file(path, name),
        "move": lambda: move_file(path, name, params.get("destination", "")),
        "copy": lambda: copy_file(path, name, params.get("destination", "")),
        "rename": lambda: rename_file(path, name, params.get("new_name", "")),
        "read": lambda: read_file(path, name,
                                  int(params.get("max_chars", _MAX_READ_CHARS))),
        "write": lambda: write_file(path, name, params.get("content", ""),
                                    bool(params.get("append", False))),
        "find": lambda: find_files(name, params.get("extension", ""), path,
                                   int(params.get("max_results", 20))),
        "largest": lambda: get_largest_files(path, int(params.get("count", 10))),
        "disk_usage": lambda: get_disk_usage(path),
        "organize_desktop": organize_desktop,
        "info": lambda: get_file_info(path, name),
    }

    handler = handlers.get(action)
    if handler is None:
        return f"Unknown action: '{action}'. Known: {', '.join(sorted(handlers))}"

    try:
        return handler()
    except (TypeError, ValueError) as e:
        # A malformed argument from the model is a bad request, not a crash.
        return f"Invalid parameters for '{action}': {e}"
