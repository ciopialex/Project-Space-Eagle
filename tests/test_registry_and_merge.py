"""Process tracking, the kill switch, and merge-conflict resolution.

Run:  .venv/bin/python -m pytest tests/ -q

Real processes (`sleep`), real git repos. No agents, no billing, no windows.

WHAT THESE PREVENT
------------------
* A re-delegation loop left a dozen agent processes alive with no way to list
  or stop them short of `pkill claude` — which would have killed Aethelark
  itself, since it runs as claude.
* A finished mission built a complete site and a working API, and `main` stayed
  empty because nothing ever called review. To the user that is
  indistinguishable from total failure: they open the folder and it is bare.
* Every merge conflict aborted, stranding completed work on its branch forever,
  even when the plan already said who owned the file.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.swarm_reviewer import ReviewerAgent, _owner_of  # noqa: E402
from core import proc_registry  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process semantics")


@pytest.fixture(autouse=True)
def clean():
    proc_registry.clear()
    yield
    proc_registry.clear()


@pytest.fixture
def sleeper():
    procs = []

    def spawn():
        p = subprocess.Popen(["sleep", "60"], start_new_session=True)
        procs.append(p)
        return p

    yield spawn
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


# ------------------------------------------------------- process identity

def test_registered_process_is_visible(sleeper):
    p = sleeper()
    proc_registry.register("m1", "site", "claude_code", p.pid)
    live = proc_registry.running()
    assert len(live) == 1
    assert live[0].workstream == "site" and live[0].pid == p.pid


def test_identity_is_pid_plus_start_time(sleeper):
    """A recycled PID must stop matching, or a kill aims at a stranger."""
    p = sleeper()
    entry = proc_registry.register("m1", "site", "claude_code", p.pid)
    assert entry.is_same_process() is True
    entry.start_time = "999999999"          # pretend the PID was recycled
    assert entry.is_same_process() is False


def test_kill_refuses_when_identity_no_longer_matches(sleeper):
    """The safety property: never signal a process we cannot prove is ours."""
    p = sleeper()
    entry = proc_registry.register("m1", "site", "claude_code", p.pid)
    entry.start_time = "999999999"
    assert proc_registry.kill(entry) is False
    assert p.poll() is None, "killed a process it could not identify"


def test_kill_actually_kills_and_verifies(sleeper):
    p = sleeper()
    entry = proc_registry.register("m1", "site", "claude_code", p.pid)
    assert proc_registry.kill(entry) is True
    for _ in range(30):
        if p.poll() is not None:
            break
        time.sleep(0.1)
    assert p.poll() is not None, "process survived kill()"


def test_dead_processes_are_reaped_from_the_listing(sleeper):
    p = sleeper()
    proc_registry.register("m1", "site", "claude_code", p.pid)
    p.kill(); p.wait()
    assert proc_registry.running() == []


def test_kill_all_reports_what_actually_died(sleeper):
    a, b = sleeper(), sleeper()
    proc_registry.register("m1", "site", "claude_code", a.pid)
    proc_registry.register("m1", "booking", "antigravity_cli", b.pid)
    r = proc_registry.kill_all()
    assert len(r["killed"]) == 2 and not r["failed"]
    assert proc_registry.running() == []


def test_a_role_holds_one_body_at_a_time(sleeper):
    """Replacing an agent must not leave the old one tracked under the same
    role — that is how orphans accumulate invisibly."""
    a, b = sleeper(), sleeper()
    proc_registry.register("m1", "site", "claude_code", a.pid)
    proc_registry.register("m1", "site", "antigravity_cli", b.pid)
    live = proc_registry.running()
    assert len(live) == 1 and live[0].agent == "antigravity_cli"


def test_describe_is_speakable(sleeper):
    assert "Nothing is running" in proc_registry.describe()
    p = sleeper()
    proc_registry.register("m1", "site", "claude_code", p.pid)
    assert "site" in proc_registry.describe()


# --------------------------------------------------- merge by ownership

@pytest.mark.parametrize("path,expected", [
    ("public/index.html", "site"),
    ("public/assets/logo.svg", "site"),
    ("server/db.js", "booking"),
    ("package.json", "booking"),
    ("README.md", None),            # nobody owns it -> genuinely ambiguous
])
def test_ownership_lookup(path, expected):
    owners = {"site": ["public/", "public/**"],
              "booking": ["server/**", "package.json"]}
    assert _owner_of(path, owners) == expected


def test_contested_file_is_not_auto_resolved():
    """Two workstreams claiming the same path is not a decision — escalate."""
    owners = {"site": ["shared/**"], "booking": ["shared/**"]}
    assert _owner_of("shared/config.js", owners) is None


def test_bare_directory_and_glob_forms_both_match():
    """Architects write both 'server/' and 'server/**'; a plan must not fail to
    merge over glob punctuation."""
    assert _owner_of("server/a.js", {"b": ["server/"]}) == "b"
    assert _owner_of("server/a.js", {"b": ["server/**"]}) == "b"


def test_conflicting_file_owned_by_the_incoming_branch_is_resolved(tmp_path):
    """End to end on a real repo: a conflict the plan already decided must
    merge instead of aborting and stranding finished work."""
    r = tmp_path
    run = lambda *a, **k: subprocess.run(["git", *a], cwd=k.get("cwd", r),
                                         capture_output=True, text=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t.t"); run("config", "user.name", "t")
    (r / ".space_eagle").mkdir()
    (r / ".space_eagle" / "plan.json").write_text(
        '{"workstreams":[{"id":"site","owns":["public/**"]}]}')
    (r / "public").mkdir()
    (r / "public" / "index.html").write_text("<h1>base</h1>")
    run("add", "-A"); run("commit", "-m", "base")

    run("checkout", "-b", "swarm/site")
    (r / "public" / "index.html").write_text("<h1>agent version</h1>")
    run("add", "-A"); run("commit", "-m", "agent work")

    run("checkout", "main")
    (r / "public" / "index.html").write_text("<h1>conflicting main</h1>")
    run("add", "-A"); run("commit", "-m", "main edit")

    out = ReviewerAgent(r, None).merge("site", "swarm/site")
    assert out["merged"] is True, out
    assert "ownership" in out["detail"]
    assert "agent version" in (r / "public" / "index.html").read_text()


def test_conflict_nobody_owns_still_aborts_cleanly(tmp_path):
    """Ambiguity must reach a human, not be guessed at."""
    r = tmp_path
    run = lambda *a: subprocess.run(["git", *a], cwd=r, capture_output=True, text=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t.t"); run("config", "user.name", "t")
    (r / ".space_eagle").mkdir()
    (r / ".space_eagle" / "plan.json").write_text(
        '{"workstreams":[{"id":"site","owns":["public/**"]}]}')
    (r / "notes.md").write_text("base")
    run("add", "-A"); run("commit", "-m", "base")
    run("checkout", "-b", "swarm/site")
    (r / "notes.md").write_text("agent")
    run("add", "-A"); run("commit", "-m", "a")
    run("checkout", "main")
    (r / "notes.md").write_text("human")
    run("add", "-A"); run("commit", "-m", "b")

    out = ReviewerAgent(r, None).merge("site", "swarm/site")
    assert out["merged"] is False
    assert "notes.md" in out["detail"]
    assert run("status", "--porcelain").stdout.strip() == "", "left a dirty tree"
