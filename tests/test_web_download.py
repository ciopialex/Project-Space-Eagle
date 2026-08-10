"""Clicking Download and actually receiving the file are different things.

The MakerWorld task — "go on makerworld and download a laptop stand for me to
print" — could not complete at all: `accept_downloads` was never set, and
nothing anywhere in the codebase waited for or saved a download. The eagle
could click the button and would then report success, because from the DOM's
point of view the click worked. The file went nowhere.

Two things this must get right, and neither is the happy path:

- **Where the file lands.** A browser-chosen temp path is useless to the user
  and unfindable by voice. It goes to the real Downloads folder, and the tool
  says the actual final path.
- **A download that never starts.** A "Download" control that opens a sign-in
  wall, or a paywall, or just does nothing, must FAIL. Reporting a file that
  does not exist is the single failure this whole session has been about.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.browser import PagePort  # noqa: E402


class _Download:
    def __init__(self, name="laptop_stand.stl", fail=None):
        self._name = name
        self._fail = fail
        self.saved_to = None

    @property
    def suggested_filename(self):
        return self._name

    def save_as(self, path):
        if self._fail:
            raise RuntimeError(self._fail)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"solid stl\n")
        self.saved_to = str(path)


class _Ctx:
    """Playwright's expect_download() context manager."""

    def __init__(self, download=None, raise_on_exit=None):
        self._d, self._raise = download, raise_on_exit

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def value(self):
        if self._raise:
            raise TimeoutError(self._raise)
        return self._d


class _Page:
    def __init__(self, ctx):
        self._ctx = ctx
        self.clicked = []

    def expect_download(self, timeout=0):
        return self._ctx

    def click(self, selector, timeout=0):
        self.clicked.append(selector)

    def eval_on_selector(self, selector, script):
        pass


def _port(page):
    return PagePort(page, call=lambda fn: fn())


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_download_is_saved_and_the_real_path_is_returned(tmp_path):
    d = _Download()
    port = _port(_Page(_Ctx(download=d)))
    result = port.download("e7", to_dir=tmp_path)
    assert result, "no path returned for a download that succeeded"
    assert Path(result).exists(), f"{result} does not exist on disk"
    assert Path(result).name == "laptop_stand.stl"


def test_the_click_still_happens(tmp_path):
    page = _Page(_Ctx(download=_Download()))
    _port(page).download("e7", to_dir=tmp_path)
    assert page.clicked, "never clicked the control"


def test_a_name_collision_does_not_overwrite(tmp_path):
    (tmp_path / "laptop_stand.stl").write_bytes(b"the one already there")
    result = _port(_Page(_Ctx(download=_Download()))).download("e7", to_dir=tmp_path)
    assert Path(result).name != "laptop_stand.stl"
    assert (tmp_path / "laptop_stand.stl").read_bytes() == b"the one already there"


# ── the failures that must not read as success ──────────────────────────────

def test_a_click_that_starts_no_download_fails(tmp_path):
    """A sign-in wall, a paywall, or a button that does nothing."""
    port = _port(_Page(_Ctx(raise_on_exit="no download started")))
    assert port.download("e7", to_dir=tmp_path) is None


def test_a_save_that_fails_returns_nothing_rather_than_a_path(tmp_path):
    d = _Download(fail="disk full")
    assert _port(_Page(_Ctx(download=d))).download("e7", to_dir=tmp_path) is None


def test_a_suggested_filename_cannot_escape_the_target_directory(tmp_path):
    """The site chooses this string. It is untrusted input."""
    d = _Download(name="../../../../etc/cron.d/pwned")
    result = _port(_Page(_Ctx(download=d))).download("e7", to_dir=tmp_path)
    if result is not None:
        assert Path(result).resolve().is_relative_to(tmp_path.resolve()), \
            f"escaped the download directory: {result}"
    assert not Path("/etc/cron.d/pwned").exists()


def test_an_empty_suggested_name_still_produces_a_file(tmp_path):
    result = _port(_Page(_Ctx(download=_Download(name="")))).download(
        "e7", to_dir=tmp_path)
    if result is not None:
        assert Path(result).exists() and Path(result).name
