"""Containment tests for the file tool.

A language model chooses the arguments to these functions, so "the model would
not ask for that" is not a control. Every one of these pins a way out of the
allowed roots that must stay closed.

The escape in `rename` was real: the previous implementation joined new_name
onto the parent and renamed with no containment check, so a new_name of
"../../../.ssh/authorized_keys" relocated a file outside the home directory.
test_rename_cannot_escape_via_traversal is that exact case.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import file_controller as fc  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Treat tmp_path as the only allowed root, with `outside` off-limits."""
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.setattr(fc, "_SAFE_ROOTS", (home,))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home, outside


# ── the escape that existed ─────────────────────────────────────────────────

def test_rename_cannot_escape_via_traversal(sandbox):
    home, outside = sandbox
    victim = home / "Desktop" / "notes.txt"
    victim.write_text("mine")

    result = fc.rename_file("desktop", "notes.txt", "../../../outside/stolen.txt")

    assert "Access denied" in result, result
    assert victim.exists(), "the file was moved out of the allowed root"
    assert not (outside / "stolen.txt").exists()


def test_rename_rejects_a_nested_name(sandbox):
    home, _ = sandbox
    (home / "Desktop" / "a.txt").write_text("x")
    assert "Access denied" in fc.rename_file("desktop", "a.txt", "sub/dir/a.txt")


def test_rename_rejects_an_absolute_name(sandbox):
    home, outside = sandbox
    (home / "Desktop" / "a.txt").write_text("x")
    result = fc.rename_file("desktop", "a.txt", str(outside / "a.txt"))
    assert "Access denied" in result


# ── the gate, everywhere ────────────────────────────────────────────────────

@pytest.mark.parametrize("call", [
    lambda o: fc.list_files(str(o)),
    lambda o: fc.read_file(str(o), "secret.txt"),
    lambda o: fc.write_file(str(o), "x.txt", "data"),
    lambda o: fc.create_file(str(o), "x.txt"),
    lambda o: fc.create_folder(str(o), "x"),
    lambda o: fc.delete_file(str(o), "secret.txt"),
    lambda o: fc.get_file_info(str(o), "secret.txt"),
    lambda o: fc.find_files("secret", path=str(o)),
    lambda o: fc.get_largest_files(str(o)),
    lambda o: fc.get_disk_usage(str(o)),
])
def test_every_operation_refuses_a_path_outside_the_root(sandbox, call):
    _, outside = sandbox
    (outside / "secret.txt").write_text("classified")
    assert "Access denied" in call(outside)


def test_disk_usage_is_gated(sandbox):
    """It used to have no containment check at all."""
    _, outside = sandbox
    assert "Access denied" in fc.get_disk_usage(str(outside))


def test_a_name_component_cannot_be_absolute(sandbox):
    home, outside = sandbox
    (outside / "passwd").write_text("root:x:0:0")
    assert "Access denied" in fc.read_file("desktop", str(outside / "passwd"))


def test_a_name_component_cannot_traverse(sandbox):
    _, outside = sandbox
    (outside / "passwd").write_text("root:x:0:0")
    assert "Access denied" in fc.read_file("desktop", "../../outside/passwd")


def test_a_symlink_out_of_the_root_is_refused(sandbox):
    home, outside = sandbox
    (outside / "secret.txt").write_text("classified")
    link = home / "Desktop" / "escape"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    result = fc.read_file("desktop/escape", "secret.txt")
    assert "Access denied" in result
    assert "classified" not in result


# ── destructive operations ──────────────────────────────────────────────────

def test_copying_a_directory_into_itself_is_refused(sandbox):
    home, _ = sandbox
    src = home / "Desktop" / "project"
    src.mkdir()
    (src / "a.txt").write_text("x")
    assert "Access denied" in fc.copy_file("desktop", "project",
                                           "desktop/project/backup")


def test_move_does_not_silently_overwrite(sandbox):
    home, _ = sandbox
    (home / "Desktop" / "a.txt").write_text("new")
    target = home / "Documents"
    target.mkdir()
    (target / "a.txt").write_text("existing")

    result = fc.move_file("desktop", "a.txt", str(target))
    assert "already exists" in result
    assert (target / "a.txt").read_text() == "existing"


def test_well_known_directories_cannot_be_deleted(sandbox):
    assert "Protected directory" in fc.delete_file("desktop")


def test_deletion_never_unlinks_permanently(sandbox, monkeypatch):
    """There must be no code path that destroys a user file outright."""
    home, _ = sandbox
    victim = home / "Desktop" / "keep.txt"
    victim.write_text("important")

    monkeypatch.setattr(fc, "_SEND2TRASH", False)
    result = fc.delete_file("desktop", "keep.txt")

    assert "disabled" in result
    assert victim.exists(), "file was deleted without a trash implementation"


# ── robustness ──────────────────────────────────────────────────────────────

def test_read_output_is_bounded(sandbox):
    home, _ = sandbox
    big = home / "Desktop" / "big.txt"
    big.write_text("A" * 200_000)

    out = fc.read_file("desktop", "big.txt", max_chars=100)
    assert out.startswith("A" * 100)
    assert "Truncated" in out
    assert len(out) < 500


def test_read_never_loads_the_whole_file(sandbox, monkeypatch):
    """The output being truncated does not prove the file was not slurped.

    The previous implementation called read_text() and sliced the result, so
    pointing it at a multi-gigabyte file exhausted memory before any limit was
    applied - and a test that only checks the returned string passes either
    way. This asserts the mechanism: read_text must not be reached, and no
    more than the requested window may be pulled off the handle.
    """
    home, _ = sandbox
    big = home / "Desktop" / "big.txt"
    big.write_text("A" * 200_000)

    def _explode(*_a, **_k):
        raise AssertionError("read_file called read_text - it slurps the file")

    monkeypatch.setattr(Path, "read_text", _explode)

    reads: list[int | None] = []
    real_open = open

    def _counting_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        real_read = handle.read

        def _read(size=-1):
            reads.append(size)
            return real_read(size)

        handle.read = _read
        return handle

    monkeypatch.setattr("builtins.open", _counting_open)

    out = fc.read_file("desktop", "big.txt", max_chars=100)

    assert "Truncated" in out
    assert reads, "no bounded read happened"
    assert all(size is not None and 0 < size <= 200 for size in reads), (
        f"read sizes {reads} - an unbounded read(-1) pulls the entire file in"
    )


def test_one_unreadable_entry_does_not_abort_a_listing(sandbox):
    """A broken symlink used to raise and lose the whole directory."""
    home, _ = sandbox
    desktop = home / "Desktop"
    (desktop / "real.txt").write_text("x")
    try:
        (desktop / "broken").symlink_to(desktop / "does-not-exist")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    out = fc.list_files("desktop")
    assert "real.txt" in out


def test_noisy_directories_are_pruned_from_searches(sandbox):
    home, _ = sandbox
    junk = home / "Desktop" / "node_modules"
    junk.mkdir()
    (junk / "target.txt").write_text("x")
    (home / "Desktop" / "target.txt").write_text("x")

    out = fc.find_files("target", path="desktop")
    assert "node_modules" not in out


def test_unknown_action_lists_the_known_ones(sandbox):
    # The entrypoint now returns a ToolResult (the helpers still return prose,
    # which is what every other test in this file asserts on).
    out = fc.file_controller({"action": "explode"})
    assert "Unknown action" in out.message and "rename" in out.message


def test_malformed_parameters_are_reported_not_raised(sandbox):
    out = fc.file_controller({"action": "read", "path": "desktop",
                              "name": "a.txt", "max_chars": "not-a-number"})
    assert "Invalid parameters" in out.message
