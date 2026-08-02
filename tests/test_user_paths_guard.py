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
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "tests/", "docs/", "scratch/")):
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


def test_user_paths_is_actually_imported_by_the_app():
    """It was imported by nothing for months. Never again."""
    importers = [rel for rel, source in _sources()
                 if "from core import user_paths" in source]
    assert len(importers) >= 15, f"only {len(importers)} importers: {importers}"


