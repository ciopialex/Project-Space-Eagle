"""The preflight must not do the thing the rest of the codebase kept doing.

A checker that reports OK for something it did not check, or reports "fixed"
without re-running the check, is worse than no checker: it converts a missing
dependency into a confident all-clear, and the user then debugs the wrong
thing. Same defect as every tool that claimed success it never verified.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.doctor import (MANUAL, MISSING, OK, UNKNOWN, Check,  # noqa: E402
                         apply_fixes, report, run_checks)


# ── it must not lie ─────────────────────────────────────────────────────────

def test_unknown_is_not_treated_as_ok():
    """A check that could not run has NOT passed."""
    assert Check("x", UNKNOWN).good is False
    assert Check("x", MISSING).good is False
    assert Check("x", MANUAL).good is False
    assert Check("x", OK).good is True


def test_fixing_rechecks_rather_than_assuming(monkeypatch):
    """The whole failure mode of this codebase in one function: `apply_fixes`
    must report the state AFTER the fix, measured, not the fix it attempted."""
    import core.doctor as D
    state = {"installed": False}

    def fake_run(cmd, shell=False, check=False):
        state["installed"] = True

    monkeypatch.setattr(D, "run_checks",
                        lambda: [Check("thing", OK if state["installed"] else MISSING,
                                       fix="install thing", auto=True)])
    before = D.run_checks()
    assert before[0].status == MISSING
    after = apply_fixes(before, run=fake_run)
    assert after[0].status == OK, "reported the old state after fixing"


def test_a_fix_that_fails_leaves_the_check_failing(monkeypatch):
    import core.doctor as D
    monkeypatch.setattr(D, "run_checks",
                        lambda: [Check("thing", MISSING, fix="nope", auto=True)])
    after = apply_fixes(D.run_checks(), run=lambda *a, **k: None)
    assert after[0].good is False, "a fix that changed nothing reported success"


def test_manual_steps_are_never_auto_run():
    """macOS Accessibility cannot be granted by a command, and pretending
    otherwise would run something that silently does nothing."""
    ran = []
    checks = [Check("mac perms", MANUAL, fix="System Settings → ...", auto=False)]
    apply_fixes(checks, run=lambda c, **k: ran.append(c))
    assert ran == [], "tried to shell out a manual instruction"


# ── the report must be actionable ───────────────────────────────────────────

def test_every_failing_check_carries_a_fix_or_says_why_not():
    for c in run_checks():
        if not c.good:
            assert c.fix or c.detail, f"{c.name} failed with no fix and no reason"


def test_the_report_names_the_fix_for_a_broken_check():
    out = report([Check("browser engine", MISSING, fix="playwright install")])
    assert "playwright install" in out
    assert "browser engine" in out


def test_a_clean_report_says_what_was_checked_not_that_all_is_well():
    out = report([Check("a", OK), Check("b", OK)])
    assert "checked" in out.lower()


def test_the_exit_code_is_nonzero_when_something_is_broken(monkeypatch):
    import core.doctor as D
    monkeypatch.setattr(D, "run_checks", lambda: [Check("x", MISSING)])
    assert D.main([]) == 1
    monkeypatch.setattr(D, "run_checks", lambda: [Check("x", OK)])
    assert D.main([]) == 0


# ── it must run on this machine, now ────────────────────────────────────────

def test_it_actually_runs_and_checks_the_real_things():
    checks = run_checks()
    names = " ".join(c.name for c in checks).lower()
    for expected in ("api key", "browser", "grounding", "permission", "audio"):
        assert expected in names, f"nothing checks {expected}"


def test_no_check_raises():
    for c in run_checks():
        assert c.status in (OK, MISSING, UNKNOWN, MANUAL), (c.name, c.status)
