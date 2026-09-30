"""Staged copies are bounded, because nothing was ever going to spend them.

`core/journal.py` stages a full copy of every file the file tool overwrites or
deletes, so the change can be reversed. `prune()` exists to bound that and had
no caller anywhere in the repository, so the copies accumulated for the life of
an install — every overwrite paying disk for a rollback that never came.

The other half was worse: undo_last() had no caller and file_controller had
no "undo" action, so the journal was write-only. That was decided on
2026-09-23 -- file_controller's `undo` spends the staged copies now, and
refuses anything touched since (tests/test_file_undo_never_destroys_later_work.py).
This file pins the part that holds either way: an unbounded directory is a
defect.

Every test here points AETHELARK_DATA_DIR at tmp_path. The journal otherwise
lives in the user's real ~/.local/share/aethelark, and a test that prunes THAT
is a test that deletes their undo history to prove it can.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    """The journal module, rooted entirely inside tmp_path."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path / "data"))
    from core import journal as journal_module
    assert tmp_path in journal_module.journal_dir().parents or \
        str(tmp_path) in str(journal_module.journal_dir()), (
        f"the journal is still at {journal_module.journal_dir()} — the "
        f"override did not take, and this test would prune the real one")
    return journal_module


def _staged_blob(journal, tmp_path, name: str, body: str) -> str:
    src = tmp_path / name
    src.write_text(body, encoding="utf-8")
    token = journal.stage(src)
    assert token, f"staging {name} returned nothing"
    return token


def test_staging_really_keeps_a_copy(journal, tmp_path):
    """The premise. If staging did nothing there would be nothing to bound."""
    _staged_blob(journal, tmp_path, "a.txt", "contents")
    blobs = list(journal.blobs_dir().iterdir())
    assert blobs, "nothing was staged, so the journal is not doing its job"


def test_a_blob_no_entry_refers_to_is_pruned(journal, tmp_path):
    """The leak: a staged copy that no live entry mentions is dead weight."""
    _staged_blob(journal, tmp_path, "orphan.txt", "nobody refers to me")
    before = len(list(journal.blobs_dir().iterdir()))
    assert before >= 1

    removed = journal.prune()

    assert removed >= 1, "an unreferenced blob survived a prune"
    assert len(list(journal.blobs_dir().iterdir())) < before


def test_a_blob_a_live_entry_refers_to_survives(journal, tmp_path):
    """Pruning must not eat the thing undo would need.

    A prune that removed referenced blobs would turn a dormant feature into a
    broken one the moment anybody wired it up.
    """
    token = _staged_blob(journal, tmp_path, "kept.txt", "still referenced")
    journal.record("overwrite", path=str(tmp_path / "kept.txt"), blob=token)

    journal.prune()

    survivors = {b.name for b in journal.blobs_dir().iterdir()}
    assert token in survivors, (
        "prune removed a blob that a live journal entry still refers to")


def test_a_blob_older_than_the_window_is_dropped(journal, tmp_path):
    """Retention is what makes this bounded rather than merely tidy."""
    token = _staged_blob(journal, tmp_path, "old.txt", "long ago")
    journal.record("overwrite", path=str(tmp_path / "old.txt"), blob=token)

    # Everything is now older than the window.
    journal.prune(keep_days=-1)

    assert token not in {b.name for b in journal.blobs_dir().iterdir()}


def test_half_written_staging_files_are_cleaned_up(journal, tmp_path):
    """A crash mid-stage leaves a .tmp, and nothing else would ever remove it."""
    journal.blobs_dir().mkdir(parents=True, exist_ok=True)
    stale = journal.blobs_dir() / "abcdef.tmp"
    stale.write_text("half a file", encoding="utf-8")

    journal.prune()

    assert not stale.exists(), "a .tmp left by an interrupted stage survived"


def test_pruning_an_empty_journal_is_not_an_error(journal):
    """It runs at startup, so it must be safe before anything has happened."""
    assert journal.prune() == 0


def test_it_is_called_at_startup():
    """The whole point: `prune()` existed and nothing invoked it.

    Reading the wiring rather than the behaviour, because the behaviour needs a
    running Qt shell. If this ever fails, the blobs are unbounded again and
    every assertion above is measuring a function nobody calls.
    """
    import inspect

    import aethelark_web

    assert hasattr(aethelark_web.WebShellUI, "_prune_undo_journal")
    boot = inspect.getsource(aethelark_web.WebShellUI.__init__)
    assert "_prune_undo_journal" in boot, (
        "the journal prune is no longer called during startup")
