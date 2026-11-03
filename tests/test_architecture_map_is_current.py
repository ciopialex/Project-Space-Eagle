"""ARCHITECTURE.md is generated from the source. When the code moves and the
map does not, this fails: run `python tools/architecture.py`."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import architecture  # noqa: E402


def test_the_architecture_map_matches_the_code():
    assert (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8") == architecture.generate(), (
        "ARCHITECTURE.md is out of date: run python tools/architecture.py")
