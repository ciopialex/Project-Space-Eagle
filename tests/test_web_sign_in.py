"""The sign-in handoff, which had no tests and could never succeed.

Found by the user on a live run: `web_agency action='sign_in'` sat for 90
seconds and was killed.

    [Aethelark] 🔧 web_agency  {'url': 'https://www.youtube.com', 'action': 'sign_in'}
    [Aethelark] ⚠️ Tool web_agency TIMED OUT after 90.0s

The handoff waited up to 300s for a human (`await_human`'s default) inside a
tool whose budget is 90s (`TOOL_SPECS` in main.py). Those two numbers were
written in different files by different tasks and never compared, so the
feature was structurally incapable of completing: signing in within 90 seconds
was the only way through, and 2FA on a phone rarely takes less.

The kill is external, so the `finally: browser.surface(False)` never ran
either — a killed handoff could leave the eagle's browser visible on screen.

The fix is not a bigger number. Holding a tool slot for five minutes while
someone finds their phone is the wrong shape for a voice assistant: it blocks
the batch, and the eagle cannot say anything while it waits. So the handoff
returns promptly and the sign-in is confirmed on a later turn.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import actions.web_agency as W  # noqa: E402


class FakeBrowser:
    """Records surfacing and navigation; serves whatever wall state is set."""

    def __init__(self, walls):
        # walls: list of reasons, popped one per _still_blocked() check.
        # "" means the wall has cleared.
        self.walls = list(walls)
        self.surfaced = []
        self.visited = []
        self.running = True

    def surface(self, visible):
        self.surfaced.append(bool(visible))
        return True

    def goto(self, url):
        self.visited.append(url)
        return url

    def page(self):
        return object()


@pytest.fixture(autouse=True)
def _clean_handoff():
    W._HANDOFF.clear()
    yield
    W._HANDOFF.clear()


@pytest.fixture
def blocked(monkeypatch):
    """Drive `_sign_in`'s wall check from a scripted list."""
    def install(browser):
        def _check(_page):
            return browser.walls.pop(0) if browser.walls else ""
        monkeypatch.setattr(W, "_wall_or_signed_out", _check)
    return install


def test_a_pending_handoff_returns_fast_instead_of_blocking(blocked):
    """The bug. It must not sit for minutes: the tool budget is 90s and the
    eagle is mute for the whole of a blocking wait."""
    b = FakeBrowser(walls=["a sign-in wall"] * 50)
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=0.0)
    assert result.ok is False, "a pending sign-in must not report success"
    assert "sign" in result.message.lower()


def test_the_window_is_left_open_while_the_user_signs_in(blocked):
    b = FakeBrowser(walls=["a sign-in wall"] * 50)
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0)
    assert b.surfaced == [True], "the window must stay up for the user"
    assert b.visited == ["https://youtube.com"]


def test_a_second_call_confirms_and_puts_the_window_away(blocked):
    """Phase two. The user says they are done; the eagle checks rather than
    believing them, then hides the browser again."""
    b = FakeBrowser(walls=["a sign-in wall", ""])
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0)
    result = W._sign_in(b, "https://youtube.com", grace=0.0)
    assert result.ok is True
    assert b.surfaced[-1] is False, "the browser must go back to headless"


def test_a_second_call_while_still_blocked_does_not_claim_success(blocked):
    b = FakeBrowser(walls=["a sign-in wall"] * 50)
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0)
    result = W._sign_in(b, "https://youtube.com", grace=0.0)
    assert result.ok is False
    assert b.surfaced == [True], "the window must not be taken away mid-login"


def test_an_already_signed_in_site_finishes_in_one_call(blocked):
    """No handoff needed. The window must not be shown at all, and must
    certainly not be left up."""
    b = FakeBrowser(walls=[""])
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=0.0)
    assert result.ok is True
    assert b.surfaced[-1] is False
    assert not W._HANDOFF


def test_a_fast_sign_in_completes_within_the_grace_window(blocked):
    """If the user is quick — a remembered password, one click — waiting a
    few seconds turns two turns into one. The wait is bounded far below the
    tool budget, which is the whole point."""
    b = FakeBrowser(walls=["a sign-in wall", "a sign-in wall", ""])
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=1.0, poll=0.01)
    assert result.ok is True
    assert b.surfaced[-1] is False


def test_the_grace_wait_cannot_exceed_the_tool_budget():
    """The bug, stated as an invariant. These two numbers live in different
    files and were never compared; this is what compares them."""
    import main
    budget = main.TOOL_SPECS["web_agency"].timeout_s
    assert W._SIGN_IN_GRACE_S < budget * 0.5, (
        f"grace {W._SIGN_IN_GRACE_S}s is not comfortably inside the "
        f"{budget}s tool budget")


def test_a_different_site_supersedes_a_pending_handoff(blocked):
    """Asking to sign in somewhere else must not be answered by the previous
    site's pending state."""
    b = FakeBrowser(walls=["wall"] * 50)
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0)
    W._sign_in(b, "https://github.com", grace=0.0)
    assert b.visited == ["https://youtube.com", "https://github.com"]


def test_a_browser_that_will_not_surface_fails_honestly(blocked):
    class Stubborn(FakeBrowser):
        def surface(self, visible):
            self.surfaced.append(bool(visible))
            return False

    b = Stubborn(walls=["wall"] * 5)
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=0.0)
    assert result.ok is False
    assert "screen" in result.message.lower() or "window" in result.guidance.lower()


def test_a_completed_sign_in_is_recorded_for_the_settings_panel(tmp_path, monkeypatch, blocked):
    """The user signed in through the window and Settings still showed only
    the imported sites — because the list read the IMPORT marker and nothing
    wrote to it when a human signed in by hand. The panel was reporting a
    different question than the one it asked."""
    import actions.grounding.web.profile_import as P
    from core import user_paths
    monkeypatch.setattr(user_paths, "browser_profile_dir", lambda: tmp_path)

    b = FakeBrowser(walls=["a sign-in wall", ""])
    blocked(b)
    W._sign_in(b, "https://www.youtube.com", grace=0.0)      # phase 1
    result = W._sign_in(b, "https://www.youtube.com", grace=0.0)   # phase 2
    assert result.ok

    recorded = (tmp_path / P._IMPORTED_MARKER).read_text().split()
    assert "youtube.com" in recorded


def test_recording_a_sign_in_keeps_the_sites_already_there(tmp_path, monkeypatch, blocked):
    import actions.grounding.web.profile_import as P
    from core import user_paths
    monkeypatch.setattr(user_paths, "browser_profile_dir", lambda: tmp_path)
    (tmp_path / P._IMPORTED_MARKER).write_text("github.com\n")

    b = FakeBrowser(walls=[""])
    blocked(b)
    W._sign_in(b, "https://www.youtube.com", grace=0.0)

    recorded = (tmp_path / P._IMPORTED_MARKER).read_text().split()
    assert set(recorded) == {"github.com", "youtube.com"}
