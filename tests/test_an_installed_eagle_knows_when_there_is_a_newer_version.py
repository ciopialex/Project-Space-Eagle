import subprocess
from pathlib import Path

from core.update_check import newer_version


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.org", *a],
                   check=True, capture_output=True)


def _origin_and_clone(tmp_path, marker=True):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "a.txt").write_text("1")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "one")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)],
                   check=True, capture_output=True)
    if marker:
        (clone / ".aethelark-install").write_text("")
    return origin, clone


def test_up_to_date_says_nothing(tmp_path):
    _, clone = _origin_and_clone(tmp_path)
    assert newer_version(clone) is None


def test_a_newer_commit_is_reported(tmp_path):
    origin, clone = _origin_and_clone(tmp_path)
    (origin / "a.txt").write_text("2")
    _git(origin, "commit", "-qam", "two")
    head = subprocess.run(["git", "-C", str(origin), "rev-parse", "--short=7", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert newer_version(clone) == head


def test_a_developer_checkout_is_never_checked(tmp_path):
    origin, clone = _origin_and_clone(tmp_path, marker=False)
    (origin / "a.txt").write_text("2")
    _git(origin, "commit", "-qam", "two")
    assert newer_version(clone) is None


def test_no_network_says_nothing(tmp_path):
    origin, clone = _origin_and_clone(tmp_path)
    _git(clone, "remote", "set-url", "origin", "file:///nonexistent/place")
    assert newer_version(clone) is None
