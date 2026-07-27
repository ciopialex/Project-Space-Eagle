"""Mission scoping, worktree lifecycle, and the no-file-paths promise.

Run:  .venv/bin/python -m pytest tests/ -q

Real git repos in tmp_path. No coding agents, no billing, no terminal windows.

THE BUG THIS FILE PINS DOWN
---------------------------
Worktrees and branches were keyed by workstream id alone ("swarm/api"), and
`ensure_worktree` returns an existing tree if it finds one. Workstream ids are
generic — almost every plan has an `api` or a `web`. Nothing in the codebase
ever removed a worktree.

So mission #2 on the same project silently inherited mission #1's branch and
its code. The first mission looked perfect; the second was quietly built on top
of the wrong thing. That is the worst shape of bug: invisible, and only in the
second demo.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.swarm_orchestrator import (  # noqa: E402
    SwarmOrchestrator, _derive_project_dir, slugify_goal,
)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    (tmp_path / "README.md").write_text("seed")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp_path, capture_output=True)
    return tmp_path


def branches(repo: Path) -> list[str]:
    r = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                       cwd=repo, capture_output=True, text=True)
    return r.stdout.split()


def worktrees(repo: Path) -> list[str]:
    r = subprocess.run(["git", "worktree", "list", "--porcelain"],
                       cwd=repo, capture_output=True, text=True)
    return [ln.split(" ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("worktree ")]


# ------------------------------------------------------- mission separation

def test_two_missions_never_share_a_branch(repo):
    """The core regression: the dental clinic must not land on the nail salon."""
    o = SwarmOrchestrator(repo)
    m1 = o.start_mission()
    b1 = o.branch_for("api")
    o.ensure_worktree("api")

    m2 = o.start_mission()
    b2 = o.branch_for("api")

    assert m1 != m2, "two missions got the same id"
    assert b1 != b2, f"mission 2 reused mission 1's branch: {b1}"
    assert m1 in b1 and m2 in b2


def test_second_mission_gets_a_clean_tree(repo):
    """Work from mission 1 must not be visible inside mission 2's worktree."""
    o = SwarmOrchestrator(repo)
    o.start_mission()
    wt1 = o.ensure_worktree("api")
    (wt1 / "nail_salon.py").write_text("# mission one")
    subprocess.run(["git", "add", "-A"], cwd=wt1, capture_output=True)
    subprocess.run(["git", "commit", "-m", "m1"], cwd=wt1, capture_output=True)

    o.start_mission()
    wt2 = o.ensure_worktree("api")

    assert wt2 != wt1
    assert not (wt2 / "nail_salon.py").exists(), \
        "mission 2 inherited mission 1's files"


def test_mission_ids_are_unique_within_the_same_second(repo):
    """Second-resolution timestamps alone would silently merge two missions."""
    o = SwarmOrchestrator(repo)
    ids = {o.start_mission() for _ in range(8)}
    assert len(ids) == 8, f"colliding mission ids: {ids}"


def test_workstreams_in_one_mission_are_isolated_from_each_other(repo):
    o = SwarmOrchestrator(repo)
    o.start_mission()
    api, web = o.ensure_worktree("api"), o.ensure_worktree("web")
    assert api != web
    assert o.branch_for("api") != o.branch_for("web")


# ------------------------------------------------------------ housekeeping

def test_old_worktrees_are_retired_not_accumulated(repo):
    """Nothing used to remove a worktree — each is a full checkout, forever."""
    o = SwarmOrchestrator(repo)
    o.start_mission()
    o.ensure_worktree("api")
    o.ensure_worktree("web")
    assert len(worktrees(repo)) == 3          # main + 2

    o.start_mission()                          # retires the previous mission
    o.ensure_worktree("api")
    assert len(worktrees(repo)) == 2, "old worktrees were not cleaned up"


def test_branches_survive_cleanup(repo):
    """Worktrees are disposable; branches are the record of what agents did.
    Deleting merged history to reclaim disk would be a bad trade."""
    o = SwarmOrchestrator(repo)
    o.start_mission()
    b = o.branch_for("api")
    o.ensure_worktree("api")
    o.start_mission()
    assert b in branches(repo), "cleanup destroyed an agent's work history"


def test_resuming_the_same_mission_reuses_its_tree(repo):
    """Within one mission, ensure_worktree must be idempotent — a restart
    mid-mission has to find the work exactly where it left it."""
    o = SwarmOrchestrator(repo)
    o.start_mission()
    first = o.ensure_worktree("api")
    (first / "wip.py").write_text("in progress")
    again = o.ensure_worktree("api")
    assert again == first
    assert (again / "wip.py").exists()


def test_mission_id_survives_a_restart(repo):
    """A fresh orchestrator object must recover the in-flight mission from the
    board, or a restart would orphan the running agents' branches."""
    o1 = SwarmOrchestrator(repo)
    mid = o1.start_mission()
    o2 = SwarmOrchestrator(repo)               # simulates a process restart
    assert o2.mission_id == mid
    assert o2.branch_for("api") == o1.branch_for("api")


# -------------------------------------------------- never ask for a path

@pytest.mark.parametrize("goal,expected", [
    # Filler ("build", "a", "professional", "website") is dropped; the words
    # that identify the project survive, in the order the user said them.
    ("Build a professional booking website for a dental clinic", "booking-dental-clinic"),
    ("make me a landing page for my nail salon", "nail-salon"),
    ("build an inventory system for my bakery", "inventory-bakery"),
    ("create a todo app", "todo"),
])
def test_goal_becomes_a_sane_folder_name(goal, expected):
    """The user described a business, not a filesystem layout."""
    assert slugify_goal(goal) == expected


def test_slug_never_empty_even_for_pure_filler():
    assert slugify_goal("build me an app please") != ""
    assert slugify_goal("") != ""


def test_slug_is_filesystem_safe():
    s = slugify_goal("Build a CRM for Bob's Café & Bar!! (v2)")
    assert s and all(c.isalnum() or c == "-" for c in s)


def test_a_goal_derives_a_project_under_home_projects():
    p = _derive_project_dir("build a booking site for a dental clinic")
    assert p == Path.home() / "Projects" / "booking-dental-clinic"


def test_no_goal_and_no_active_mission_asks_rather_than_guessing():
    """Deriving a folder from a goalless follow-up would scatter one mission
    across several directories — better to ask than to silently split it."""
    assert _derive_project_dir("") is None
