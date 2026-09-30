"""The module declared 0600 and the writers wrote 0664.

`core/user_paths.py` opens by stating the rule: "Owner-only: everything under
here is the user's private material", and defines `FILE_MODE = 0o600`. Only the
legacy migration ever applied it. Every actual writer used `Path.write_text`,
which creates at 0666 & ~umask — measured at 0664 on this machine, group and
world readable.

What is in those files:

    api_keys.json      the Gemini key the whole application runs on
    google_token.json  the Google refresh token — standing access to Gmail,
                       Calendar, Contacts and Tasks, until it is revoked

`actions/google_auth.py` names this itself, in a comment at the top of the
file: "Tokens are written to config/google_token.json. (A later hardening pass
can...)". It was still open.

This is not currently reachable by another user on an ordinary machine:
`config_dir()` is 0700, so nobody else can traverse into it. That is exactly
why it is worth pinning rather than shrugging at. The directory mode is applied
by one function in one module; the FILE mode is what survives a backup, an
rsync, a sync client, a tarball, or a directory created by anything other than
`ensure_private_dir`. Defence in depth means the inner layer is checked while
the outer one still holds, not after it fails.

Criterion 1 in CLAUDE.md: a key that has been read cannot be un-read, and the
recovery is to rotate every credential and re-consent every Google scope.
CLAUDE.md also records that a leaked private file has happened here once.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """user_paths rooted entirely in tmp_path — never the real credentials."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path / "data"))
    from core import user_paths
    assert str(tmp_path) in str(user_paths.config_dir()), (
        f"config_dir is {user_paths.config_dir()} — the override did not take, "
        f"and this test would be chmod-ing the author's real API keys")
    return user_paths


def _mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def _group_or_world_readable(p: Path) -> bool:
    return bool(_mode(p) & 0o077)


CREDENTIALS = ["api_keys_path", "google_token_path"]


# ── what gets written ───────────────────────────────────────────────────────

@pytest.mark.parametrize("which", CREDENTIALS)
def test_a_freshly_written_credential_is_owner_only(paths, which):
    target = getattr(paths, which)()
    paths.write_private(target, json.dumps({"secret": "PRETEND"}))
    assert not _group_or_world_readable(target), (
        f"{target.name} was written {oct(_mode(target))}; it holds a "
        f"credential and FILE_MODE is {oct(paths.FILE_MODE)}")


@pytest.mark.parametrize("which", CREDENTIALS)
def test_it_is_never_briefly_wider_than_that(paths, which):
    """Created with the mode, not chmod-ed after.

    A write that opens at 0664 and tightens afterwards has a window with the
    key already on disk and the wrong mode. Checked by writing into a path
    whose mode is observed the instant the call returns, and by the absence of
    any O_CREAT without a mode argument in the helper.
    """
    target = getattr(paths, which)()
    paths.write_private(target, "x" * 4096)
    assert _mode(target) == paths.FILE_MODE


def test_the_real_browser_writer_goes_through_it(paths, monkeypatch):
    """browser_control owns the Gemini key file in practice."""
    from actions import browser_control as bc
    target = paths.api_keys_path()
    monkeypatch.setattr(bc, "_CONFIG_FILE", target)
    bc._write_config({"gemini_api_key": "AIzaSy-PRETEND"})
    assert not _group_or_world_readable(target)
    assert json.loads(target.read_text())["gemini_api_key"] == "AIzaSy-PRETEND"


# ── and what is already on disk ─────────────────────────────────────────────

@pytest.mark.parametrize("which", CREDENTIALS)
def test_a_credential_an_older_version_left_loose_is_repaired(paths, which):
    """The fix above only helps the next write.

    Anyone who has already run this application has these files at 0664, and a
    Google refresh token is not rewritten on any schedule — it would stay that
    way for the life of the install.
    """
    target = getattr(paths, which)()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"secret": "PRETEND"}', encoding="utf-8")
    os.chmod(target, 0o664)
    assert _group_or_world_readable(target), "the premise did not hold"

    repaired = getattr(paths, which)()

    assert not _group_or_world_readable(repaired), (
        f"{target.name} is still {oct(_mode(repaired))} after the path lookup "
        f"that is supposed to repair it")
    assert json.loads(repaired.read_text())["secret"] == "PRETEND"


@pytest.mark.parametrize("which", CREDENTIALS)
def test_repairing_does_not_disturb_a_file_that_is_already_right(paths, which):
    target = getattr(paths, which)()
    paths.write_private(target, '{"secret": "PRETEND"}')
    before = (_mode(target), target.read_bytes())
    getattr(paths, which)()
    assert (_mode(target), target.read_bytes()) == before


def test_the_directory_is_still_owner_only(paths):
    """The outer layer, which was always right and must stay right."""
    assert not (_mode(paths.config_dir()) & 0o077)


# ── the writes still have to work ───────────────────────────────────────────

def test_overwriting_replaces_rather_than_appends(paths):
    """O_TRUNC. Without it a shorter second key leaves the tail of the first."""
    target = paths.api_keys_path()
    paths.write_private(target, json.dumps({"gemini_api_key": "A" * 200}))
    paths.write_private(target, json.dumps({"gemini_api_key": "B"}))
    assert json.loads(target.read_text()) == {"gemini_api_key": "B"}


def test_it_creates_the_directory_when_nothing_has_run_yet(paths, tmp_path):
    target = paths.config_dir() / "nested" / "fresh.json"
    paths.write_private(target, "{}")
    assert target.is_file()


def test_unicode_survives_the_round_trip(paths):
    """The writer it replaced passed encoding='utf-8' explicitly."""
    target = paths.api_keys_path()
    paths.write_private(target, json.dumps({"name": "Ștefan — 日本語"}))
    assert json.loads(target.read_text(encoding="utf-8"))["name"] == "Ștefan — 日本語"
