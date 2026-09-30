"""Undo reverses the eagle's own last file change -- and nothing the user did since.

The journal staged a copy of every file the tool overwrote or deleted and
nothing ever spent them. Wiring "undo" up is only safe if undo refuses a file
the user has touched since: the journal's docstring promised that, and the
code did not do it -- `create` was undone with unlink() whatever the file had
become. These run the real file tool in a sandbox with its own journal.
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import file_controller as fc  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(fc, "_SAFE_ROOTS", (home,))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path / "data"))
    trashed = []
    monkeypatch.setattr(fc.send2trash, "send2trash",
                        lambda p: (trashed.append(p), os.remove(p)))
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash",
                        lambda p: (trashed.append(p), os.remove(p)))
    return home / "Desktop"


def _do(**params):
    return fc.file_controller(parameters=params)


def _touch_later(path: Path, text: str) -> None:
    time.sleep(0.01)                      # a different mtime, as a person makes
    path.write_text(text)


def test_undo_puts_back_an_overwritten_file(home):
    note = home / "notes.txt"
    note.write_text("the original")
    assert _do(action="write", path=str(home), name="notes.txt", content="replaced").ok
    assert _do(action="undo").ok
    assert note.read_text() == "the original"


def test_undo_refuses_a_file_the_user_edited_since(home):
    note = home / "notes.txt"
    note.write_text("the original")
    _do(action="write", path=str(home), name="notes.txt", content="replaced")
    _touch_later(note, "an hour of the user's own work")
    r = _do(action="undo")
    assert not r.ok
    assert note.read_text() == "an hour of the user's own work"


def test_undo_brings_back_a_deleted_file(home):
    doc = home / "letter.txt"
    doc.write_text("dear")
    assert _do(action="delete", path=str(home), name="letter.txt").ok
    assert not doc.exists()
    assert _do(action="undo").ok
    assert doc.read_text() == "dear"


def test_undo_will_not_restore_over_something_new(home):
    doc = home / "letter.txt"
    doc.write_text("dear")
    _do(action="delete", path=str(home), name="letter.txt")
    doc.write_text("a new letter")
    r = _do(action="undo")
    assert not r.ok
    assert doc.read_text() == "a new letter"


def test_undoing_a_created_file_sends_it_to_the_trash_not_oblivion(home):
    assert _do(action="create_file", path=str(home), name="new.txt", content="x").ok
    assert _do(action="undo").ok
    assert not (home / "new.txt").exists()


def test_nothing_to_undo_says_so(home):
    r = _do(action="undo")
    assert r.ok and "no file change" in r.message.lower()
