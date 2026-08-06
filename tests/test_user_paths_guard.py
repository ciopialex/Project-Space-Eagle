"""Stops the path bug from regrowing, and proves the ship blocker is fixed.

`user_paths.py` was written to solve this and then sat imported by nothing
while 37 call sites computed their own paths. A module nobody imports is a
module that silently stops being true. This test is the reason it stays wired.
"""
import re
import sys
from pathlib import Path

import pytest

from core import user_paths as rp

ROOT = Path(__file__).resolve().parent.parent

#: Files the user owns. Computing these from __file__ or sys.executable is the
#: bug: in a frozen bundle both point somewhere read-only.
USER_DATA = ("api_keys.json", "long_term.json", "google_token.json")

#: Legitimate exceptions, each for a stated reason.
ALLOWED = {
    "core/user_paths.py",            # defines the paths
    "memory/memory_manager.py",      # owns the memory store path
    "core/journal.py",               # owns the journal path
    "core/capability/keys.py",          # SCANS other tools' credential files
}


def _sources():
    """Every .py file that is part of THIS checkout, and nothing else.

    Dot-directories are skipped wholesale rather than named one at a time.
    A git worktree lives at `.claude/worktrees/<branch>/` and contains a
    complete second copy of the repository — so an rglob from the root walked
    into it and flagged `.claude/worktrees/x/core/user_paths.py`, which the
    allowlist could not match because it is keyed on `core/user_paths.py`.
    The guard was reporting a checkout it does not govern. The same trap is
    set by `.venv`, `.git`, and any future tooling scratch directory, so the
    rule is the category, not the instance.
    """
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if any(part.startswith(".") for part in rel.split("/")[:-1]):
            continue
        if rel.startswith(("tests/", "docs/", "scratch/")):
            continue
        if "__pycache__" in rel or rel in ALLOWED:
            continue
        yield rel, path.read_text(encoding="utf-8", errors="ignore")


def test_no_module_computes_a_user_data_path_by_hand():
    """Every user-owned path goes through user_paths, or a frozen build
    writes into Program Files and loses the user's API key."""
    offenders = []
    for rel, source in _sources():
        for line_no, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            if not any(name in line for name in USER_DATA):
                continue
            if "user_paths" in line:
                continue
            # A path expression, not prose in a message or docstring.
            if re.search(r'/\s*"[^"]*\.json"', line) or re.search(
                    r'Path\([^)]*\)\s*/', line):
                offenders.append(f"{rel}:{line_no}: {stripped[:70]}")

    assert offenders == [], (
        "these compute a user-data path by hand instead of via user_paths:\n"
        + "\n".join(offenders))


def test_a_nested_checkout_is_not_scanned(tmp_path, monkeypatch):
    """A git worktree puts a whole second copy of the repo under `.claude/`.

    Scanning it made this guard fail against code it does not govern, and the
    allowlist could not rescue it: the exemptions are keyed on `core/x.py`,
    never `.claude/worktrees/b/core/x.py`. Guarded here because the failure
    only appears when a worktree happens to exist, so it is invisible on a
    clean clone and lands on whoever is mid-branch.
    """
    import test_user_paths_guard as guard

    nested = tmp_path / ".claude" / "worktrees" / "b" / "core"
    nested.mkdir(parents=True)
    (nested / "user_paths.py").write_text('P = config_dir() / "api_keys.json"\n')
    (tmp_path / "real.py").write_text("from core import user_paths\n")

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    scanned = dict(guard._sources())

    assert "real.py" in scanned, "the real tree must still be scanned"
    assert not [r for r in scanned if r.startswith(".")], (
        f"scanned a nested checkout: {sorted(scanned)}")


def test_the_guard_still_catches_a_real_offender(tmp_path, monkeypatch):
    """A skip rule that quietly empties the scan would make this file pass by
    checking nothing. Prove the detector still fires."""
    import test_user_paths_guard as guard

    (tmp_path / "sloppy.py").write_text(
        'KEY_FILE = Path.home() / ".aethelark" / "api_keys.json"\n')
    monkeypatch.setattr(guard, "ROOT", tmp_path)

    with pytest.raises(AssertionError, match="sloppy.py"):
        guard.test_no_module_computes_a_user_data_path_by_hand()


def test_user_paths_is_actually_imported_by_the_app():
    """It was imported by nothing for months. Never again."""
    importers = [rel for rel, source in _sources()
                 if "from core import user_paths" in source]
    assert len(importers) >= 15, f"only {len(importers)} importers: {importers}"


