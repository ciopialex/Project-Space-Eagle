"""The last inch: showing the user the thing they asked for.

Run:  .venv/bin/python -m pytest tests/ -q

Real servers on real ports, no agents, no browser opened.

WHY THIS EXISTS
---------------
The nail-salon mission built a complete site and a working API, and handed back
a folder path. The user had to be told a localhost URL by someone else before
they could look at their own website. They asked for a page and received a
directory — the whole product falling at the last inch.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import preview  # noqa: E402


@pytest.fixture(autouse=True)
def cleanup():
    yield
    preview.stop_all()


# ------------------------------------------------------------- detection

def test_static_site_in_public_is_found(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<h1>hi</h1>")
    kind, root = preview.detect(tmp_path)
    assert kind == "static" and root.name == "public"


@pytest.mark.parametrize("d", ["dist", "build", "site", "www"])
def test_other_conventional_build_dirs_are_found(tmp_path, d):
    (tmp_path / d).mkdir()
    (tmp_path / d / "index.html").write_text("<h1>hi</h1>")
    assert preview.detect(tmp_path)[0] == "static"


def test_index_at_the_root_is_found(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert preview.detect(tmp_path)[0] == "static"


def test_npm_start_script_wins_over_static(tmp_path):
    """A project with a server should be RUN, not served as flat files —
    otherwise the booking form silently has no API behind it."""
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node x"}}))
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<h1>hi</h1>")
    assert preview.detect(tmp_path)[0] == "node"


def test_package_without_a_start_script_is_not_treated_as_runnable(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}))
    assert preview.detect(tmp_path) is None


def test_unrecognised_project_returns_none_rather_than_guessing(tmp_path):
    """A wrong guess opens a browser at a broken page, which reads as failure.
    Staying quiet is better than being confidently wrong."""
    (tmp_path / "notes.md").write_text("just some text")
    assert preview.detect(tmp_path) is None


def test_malformed_package_json_does_not_raise(tmp_path):
    (tmp_path / "package.json").write_text("{ not json")
    assert preview.detect(tmp_path) is None


# ---------------------------------------------------------------- serving

def test_static_site_actually_serves(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<h1>Lumiere</h1>")
    pv = preview.start(tmp_path, open_browser=False)
    assert pv is not None
    body = urllib.request.urlopen(pv.url, timeout=5).read()
    assert b"Lumiere" in body


def test_ports_do_not_collide(tmp_path):
    """The dashboard is on 8000 and the user may have their own dev server —
    the OS picks a free port so a preview never fights either."""
    a, b = tmp_path / "a", tmp_path / "b"
    for p in (a, b):
        (p / "public").mkdir(parents=True)
        (p / "public" / "index.html").write_text("<h1>x</h1>")
    pa = preview.start(a, open_browser=False)
    pb = preview.start(b, open_browser=False)
    assert pa and pb and pa.url != pb.url


def test_a_second_mission_replaces_the_first_preview(tmp_path):
    """Otherwise every rebuild strands a server holding a port forever."""
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<h1>v1</h1>")
    first = preview.start(tmp_path, open_browser=False)
    second = preview.start(tmp_path, open_browser=False)
    assert first.url != second.url
    assert preview.current(tmp_path).url == second.url
    assert first.proc.poll() is not None, "old preview server was left running"


def test_current_reports_the_live_preview(tmp_path):
    (tmp_path / "index.html").write_text("<h1>x</h1>")
    pv = preview.start(tmp_path, open_browser=False)
    assert preview.current(tmp_path).url == pv.url


def test_stop_all_leaves_nothing_running(tmp_path):
    (tmp_path / "index.html").write_text("<h1>x</h1>")
    pv = preview.start(tmp_path, open_browser=False)
    assert preview.stop_all() >= 1
    assert pv.proc.poll() is not None


def test_start_returns_none_when_nothing_is_runnable(tmp_path):
    (tmp_path / "readme.txt").write_text("nothing here")
    assert preview.start(tmp_path, open_browser=False) is None


def test_a_server_that_never_binds_is_not_announced_as_ready(tmp_path, monkeypatch):
    """Opening the browser optimistically shows connection-refused, which reads
    as failure even when the server is merely slow — or never starts."""
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "false"}}))
    monkeypatch.setattr(preview, "_wait_until_serving", lambda *a, **k: False)
    assert preview.start(tmp_path, open_browser=False) is None
