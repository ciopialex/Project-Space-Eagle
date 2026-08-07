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
        self.closed = False

    def surface(self, visible):
        self.surfaced.append(bool(visible))
        return True

    def close(self):
        self.closed = True

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
    result = W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    assert result.ok is False, "a pending sign-in must not report success"
    assert "sign" in result.message.lower()


def test_the_window_is_left_open_while_the_user_signs_in(blocked):
    b = FakeBrowser(walls=["a sign-in wall"] * 50)
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    assert b.surfaced == [True], "the window must stay up for the user"
    assert b.visited == ["https://youtube.com"]


def test_a_second_call_confirms_and_puts_the_window_away(blocked):
    """Phase two. The user says they are done; the eagle checks rather than
    believing them, then hides the browser again."""
    b = FakeBrowser(walls=["a sign-in wall", ""])
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    result = W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    assert result.ok is True
    assert b.surfaced[-1] is False, "the browser must go back to headless"


def test_a_second_call_while_still_blocked_does_not_claim_success(blocked):
    b = FakeBrowser(walls=["a sign-in wall"] * 50)
    blocked(b)
    W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    result = W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    assert result.ok is False
    assert b.surfaced == [True], "the window must not be taken away mid-login"


def test_an_already_signed_in_site_finishes_in_one_call(blocked):
    """No handoff needed. The window must not be shown at all, and must
    certainly not be left up."""
    b = FakeBrowser(walls=[""])
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    assert result.ok is True
    assert b.surfaced[-1] is False
    assert not W._HANDOFF


def test_a_fast_sign_in_completes_within_the_grace_window(blocked):
    """If the user is quick — a remembered password, one click — waiting a
    few seconds turns two turns into one. The wait is bounded far below the
    tool budget, which is the whole point."""
    b = FakeBrowser(walls=["a sign-in wall", "a sign-in wall", ""])
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=1.0, poll=0.01, watch=False)
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
    W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
    W._sign_in(b, "https://github.com", grace=0.0, watch=False)
    assert b.visited == ["https://youtube.com", "https://github.com"]


def test_a_browser_that_will_not_surface_fails_honestly(blocked):
    class Stubborn(FakeBrowser):
        def surface(self, visible):
            self.surfaced.append(bool(visible))
            return False

    b = Stubborn(walls=["wall"] * 5)
    blocked(b)
    result = W._sign_in(b, "https://youtube.com", grace=0.0, watch=False)
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
    W._sign_in(b, "https://www.youtube.com", grace=0.0, watch=False)      # phase 1
    result = W._sign_in(b, "https://www.youtube.com", grace=0.0, watch=False)   # phase 2
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
    W._sign_in(b, "https://www.youtube.com", grace=0.0, watch=False)

    recorded = (tmp_path / P._IMPORTED_MARKER).read_text().split()
    assert set(recorded) == {"github.com", "youtube.com"}


# ── The window closing the moment the user clicks "Sign in" ────────────────
# Reported live with a screenshot: the window opened on youtube.com, the user
# pressed "Conectează-te", and the window vanished. Clicking sign-in navigates
# to accounts.google.com; mid-navigation the page has no readable controls, so
# the wall check found no wall and the handoff concluded SUCCESS — hiding the
# browser exactly when the human started typing.
#
# An unreadable page means "not yet", never "done". Success needs positive
# evidence, and so does an auth page: being ON accounts.google.com is proof the
# sign-in has NOT finished.

class _Page:
    def __init__(self, nodes, url):
        self._nodes, self._url = nodes, url


def _stub_page(monkeypatch, nodes, url):
    monkeypatch.setattr(W, "_current_nodes", lambda _p: nodes)
    monkeypatch.setattr(W, "_current_url", lambda _p: url)


def test_a_page_with_nothing_on_it_is_not_a_finished_sign_in(monkeypatch):
    """The exact frame the user lost their window on."""
    _stub_page(monkeypatch, [], "https://accounts.google.com/signin")
    assert W._wall_or_signed_out(object()), "an unreadable page read as signed in"


def test_sitting_on_the_login_page_is_not_a_finished_sign_in(monkeypatch):
    """Google's login page has no 'sign in to continue' banner to detect — it
    IS the sign-in. Nothing in the wall vocabulary matches it, so it looked
    like an ordinary signed-in page."""
    _stub_page(monkeypatch, [object()], "https://accounts.google.com/v3/signin/identifier")
    assert W._wall_or_signed_out(object())


@pytest.mark.parametrize("url", [
    "https://accounts.google.com/signin",
    "https://login.microsoftonline.com/x",
    "https://github.com/login",
    "https://signin.aws.amazon.com/",
    "https://www.facebook.com/login.php",
])
def test_known_auth_pages_all_count_as_unfinished(monkeypatch, url):
    _stub_page(monkeypatch, [object()], url)
    assert W._wall_or_signed_out(object()), url


def test_a_real_page_with_no_wall_is_finished(monkeypatch):
    """The other direction. If this stops being true the handoff never ends
    and the window never closes."""
    _stub_page(monkeypatch, [object(), object()], "https://www.youtube.com/playlist?list=LL")
    monkeypatch.setattr(W, "wall_reason", lambda *_a: "")
    monkeypatch.setattr(W, "signed_out_reason", lambda *_a: "")
    assert W._wall_or_signed_out(object()) == ""


def test_the_window_survives_a_click_that_navigates(monkeypatch):
    """End to end: the user clicks sign-in, the page goes blank mid-navigation,
    and the browser must still be on screen afterwards."""
    b = FakeBrowser(walls=[])
    states = iter(["a sign-in wall", "", "", "a sign-in wall"])  # blank frames in the middle
    monkeypatch.setattr(W, "_wall_or_signed_out",
                        lambda _p: next(states, "a sign-in wall"))
    monkeypatch.setattr(W, "_current_nodes", lambda _p: [])
    W._sign_in(b, "https://www.youtube.com", grace=0.0, watch=False)
    assert b.surfaced == [True], "the window was taken away mid-login"


# ── Noticing, instead of waiting to be told ────────────────────────────────
# The user signed in and the window just sat there. That was the design: phase
# two only runs when someone calls sign_in again. Correct, and wrong — nobody
# should have to announce to their assistant that they finished typing.

def test_the_watcher_closes_the_window_once_the_wall_clears(monkeypatch):
    b = FakeBrowser(walls=[])
    states = iter(["a sign-in wall", "a sign-in wall", ""])
    monkeypatch.setattr(W, "_wall_or_signed_out", lambda _p: next(states, ""))
    done = W._watch_until_signed_in(b, "https://www.youtube.com",
                                    timeout=5.0, poll=0.01)
    assert done is True
    # Stood DOWN, not merely hidden: surface(False) restarts it headless and
    # leaves a Chrome resident behind every later conversation.
    assert b.closed, "the browser was left running after the handoff"
    assert not W._HANDOFF


def test_the_watcher_gives_up_without_stranding_the_window(monkeypatch):
    """If the user wanders off, the eagle must not keep a browser on their
    screen forever — but it also must not yank it away while they might still
    be typing. It waits generously, then tidies up."""
    b = FakeBrowser(walls=[])
    monkeypatch.setattr(W, "_wall_or_signed_out", lambda _p: "still blocked")
    done = W._watch_until_signed_in(b, "https://x.test", timeout=0.05, poll=0.01)
    assert done is False
    assert b.closed, "left a browser running on the user's machine"


def test_the_watcher_records_the_site_like_a_manual_confirm(monkeypatch, tmp_path):
    import actions.grounding.web.profile_import as P
    from core import user_paths
    monkeypatch.setattr(user_paths, "browser_profile_dir", lambda: tmp_path)
    b = FakeBrowser(walls=[])
    monkeypatch.setattr(W, "_wall_or_signed_out", lambda _p: "")
    W._watch_until_signed_in(b, "https://www.youtube.com", timeout=1.0, poll=0.01)
    assert "youtube.com" in (tmp_path / P._IMPORTED_MARKER).read_text()


def test_a_vanished_browser_ends_the_watch(monkeypatch):
    """The user closed the window themselves. Polling a dead browser forever
    is how a background thread outlives the thing it was watching."""
    class Gone(FakeBrowser):
        def page(self): return None
    b = Gone(walls=[])
    assert W._watch_until_signed_in(b, "https://x.test", timeout=5.0, poll=0.01) is False


def test_the_handoff_leaves_no_browser_running(monkeypatch):
    """`surface(False)` does not stop the browser — it RESTARTS it headless.
    So a completed sign-in left a full Chrome resident for the rest of the
    session, competing with the audio threads for a laptop's CPU. Nothing
    needed it: the session is on disk, and the next web action can pay the
    ~350ms cold start."""
    closed = []

    class B(FakeBrowser):
        def close(self):
            closed.append(True)

    b = B(walls=[])
    monkeypatch.setattr(W, "_wall_or_signed_out", lambda _p: "")
    W._watch_until_signed_in(b, "https://www.youtube.com", timeout=1.0, poll=0.01)
    assert closed, "left a headless Chrome running after the handoff"


def test_giving_up_also_leaves_nothing_running(monkeypatch):
    closed = []

    class B(FakeBrowser):
        def close(self):
            closed.append(True)

    b = B(walls=[])
    monkeypatch.setattr(W, "_wall_or_signed_out", lambda _p: "blocked")
    W._watch_until_signed_in(b, "https://x.test", timeout=0.05, poll=0.01)
    assert closed, "abandoned handoff left a browser running"
