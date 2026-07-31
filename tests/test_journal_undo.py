"""The undo journal - and the test that decides whether it can be trusted.

The design argument is that consent should be spent only on what cannot be
taken back, because attention is scarce and every needless prompt steals it
from the one prompt that mattered. That argument is only honest while undo
actually works. So test_round_trip_restores_bytes_exactly is a gate, not a
test: if it ever goes red, the premise is void and prompting has to come back
everywhere until it is green again.
"""
from __future__ import annotations

import os
import random
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import journal  # noqa: E402
from actions import file_controller as fc  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private data dir and a sandboxed home, so nothing real is touched."""
    data = tmp_path / "data"
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)

    monkeypatch.setenv("AETHELARK_DATA_DIR", str(data))
    monkeypatch.setattr(fc, "_SAFE_ROOTS", (home,))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home, data


# ── the gate ────────────────────────────────────────────────────────────────

def test_round_trip_restores_bytes_exactly(env):
    """Random mutation → undo → byte-for-byte comparison.

    If this fails, `eagle undo` is a promise the system cannot keep, and every
    "I did it, you can undo it" said on the strength of it was false.
    """
    home, _ = env
    rng = random.Random(20260731)

    for _ in range(25):
        target = home / "Desktop" / f"f{rng.randrange(5)}.txt"
        original = os.urandom(rng.randrange(0, 4096))
        target.write_bytes(original)
        before = target.read_bytes()

        token = journal.stage(target)
        assert token is not None
        target.write_bytes(os.urandom(256))
        journal.record("overwrite", path=target, blob=token)

        journal.undo_last()

        assert target.read_bytes() == before, "undo did not restore the bytes"


def test_undo_removes_a_created_file(env):
    home, _ = env
    assert "Created" in fc.create_file("desktop", "new.txt", "hello")
    assert (home / "Desktop" / "new.txt").exists()

    journal.undo_last()
    assert not (home / "Desktop" / "new.txt").exists()


def test_undo_restores_an_overwrite_through_the_tool(env):
    home, _ = env
    target = home / "Desktop" / "notes.txt"
    target.write_text("the original")

    fc.write_file("desktop", "notes.txt", "clobbered")
    assert target.read_text() == "clobbered"

    journal.undo_last()
    assert target.read_text() == "the original"


def test_undo_truncates_an_append(env):
    home, _ = env
    target = home / "Desktop" / "log.txt"
    target.write_text("line one\n")

    fc.write_file("desktop", "log.txt", "line two\n", append=True)
    assert target.read_text() == "line one\nline two\n"

    journal.undo_last()
    assert target.read_text() == "line one\n"


def test_undo_reverses_a_rename(env):
    home, _ = env
    (home / "Desktop" / "before.txt").write_text("x")

    fc.rename_file("desktop", "before.txt", "after.txt")
    assert (home / "Desktop" / "after.txt").exists()

    journal.undo_last()
    assert (home / "Desktop" / "before.txt").exists()
    assert not (home / "Desktop" / "after.txt").exists()


def test_undo_restores_a_trashed_file(env, monkeypatch):
    home, _ = env
    target = home / "Desktop" / "gone.txt"
    target.write_text("important")

    # Stand in for the system trash so the test does not touch the real one.
    # raising=False because send2trash is an optional dependency: on a machine
    # without it the module-level import failed and there is no attribute to
    # replace, which must not be mistaken for a failure of the code under test.
    monkeypatch.setattr(fc, "_SEND2TRASH", True)
    monkeypatch.setattr(fc, "send2trash",
                        type("S", (), {"send2trash": staticmethod(
                            lambda p: Path(p).unlink())})(),
                        raising=False)

    assert "Trash" in fc.delete_file("desktop", "gone.txt")
    assert not target.exists()

    journal.undo_last()
    assert target.read_text() == "important"


# ── the irreversibility classifier ──────────────────────────────────────────

def test_a_directory_cannot_be_staged(env):
    home, _ = env
    folder = home / "Desktop" / "project"
    folder.mkdir()
    assert journal.stage(folder) is None, "a tree is not cheap to snapshot"


def test_an_oversized_file_cannot_be_staged(env, monkeypatch):
    home, _ = env
    big = home / "Desktop" / "big.bin"
    big.write_bytes(b"x" * 5000)
    monkeypatch.setattr(journal, "MAX_STAGE_BYTES", 1000)
    assert journal.stage(big) is None


def test_an_unstageable_overwrite_is_refused_not_performed(env, monkeypatch):
    """The only case in the file tool that needs a human."""
    home, _ = env
    big = home / "Desktop" / "big.bin"
    big.write_bytes(b"original" * 700)
    monkeypatch.setattr(journal, "MAX_STAGE_BYTES", 100)

    result = fc.write_file("desktop", "big.bin", "clobber")

    assert "could not be undone" in result
    assert big.read_bytes().startswith(b"original"), "the file was changed anyway"


def test_a_missing_file_stages_as_empty_meaning_the_inverse_is_delete(env):
    home, _ = env
    assert journal.stage(home / "Desktop" / "nope.txt") == ""


# ── refusing rather than guessing ───────────────────────────────────────────

def test_undo_refuses_when_the_world_moved(env):
    home, _ = env
    (home / "Desktop" / "a.txt").write_text("x")
    fc.rename_file("desktop", "a.txt", "b.txt")

    # Someone recreated the original name in the meantime.
    (home / "Desktop" / "a.txt").write_text("different")

    out = journal.undo_last()
    assert "✗" in out and "exists again" in out
    assert (home / "Desktop" / "a.txt").read_text() == "different"


def test_undo_with_nothing_to_undo_is_not_an_error(env):
    assert journal.undo_last() == "Nothing to undo."


def test_undo_is_recorded_so_the_log_stays_append_only(env):
    home, _ = env
    fc.create_file("desktop", "x.txt", "1")
    journal.undo_last()
    assert journal.undo_last() == "Nothing to undo.", "an entry was undone twice"


# ── it is user data, and it obeys Amendment I ───────────────────────────────

def test_the_journal_never_lives_inside_the_repository(env):
    repo = Path(__file__).resolve().parent.parent
    assert repo not in journal.journal_dir().resolve().parents


def test_the_journal_is_owner_only(env):
    if sys.platform == "win32":
        pytest.skip("POSIX modes only")
    home, _ = env
    fc.create_file("desktop", "x.txt", "content")

    mode = stat.S_IMODE(os.stat(journal.journal_path()).st_mode)
    assert mode == 0o600, f"journal is {oct(mode)} - it holds the user's files"


def test_staged_blobs_are_owner_only(env):
    if sys.platform == "win32":
        pytest.skip("POSIX modes only")
    home, _ = env
    target = home / "Desktop" / "secret.txt"
    target.write_text("private")
    token = journal.stage(target)

    blob = journal.blobs_dir() / token
    assert stat.S_IMODE(os.stat(blob).st_mode) == 0o600


def test_prune_drops_unreferenced_blobs(env):
    home, _ = env
    target = home / "Desktop" / "a.txt"
    target.write_text("x")
    journal.stage(target)          # staged but never recorded → unreferenced

    assert journal.prune(keep_days=30) >= 1


def test_history_reads_past_a_torn_final_line(env):
    home, _ = env
    fc.create_file("desktop", "a.txt", "1")
    with open(journal.journal_path(), "a", encoding="utf-8") as handle:
        handle.write('{"id": "broken", "op": "cre')      # crash mid-write

    assert len(journal.history()) >= 1, "one torn line hid the whole journal"
