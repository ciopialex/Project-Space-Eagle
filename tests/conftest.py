"""Shared Qt/WebEngine plumbing for the tests that drive a real page.

Chromium is initialised once per process and does not survive being set up a
second time from a different test module: a second QWebEngineView built after
another one has already loaded segfaults on `load()` under pytest. That is not
a property of anything in this repo — it reproduces with two three-line test
files — but it means WebEngine has to be a session-scoped resource rather than
something each test file makes for itself.

Sharing it is also what makes these tests quick: standing Chromium up costs
about a second, and reusing one view turned a 16s file into a fraction of that.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Must be set before QApplication exists, and these tests never want a window.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")

# The suite runs against module manifests kept here as fixtures, never against
# whatever modules happen to be installed on the machine running it, and never
# against the user's own settings or keys. Set before `main` is imported,
# because the module bus is discovered at import.
_FIXTURE_MODULES = Path(__file__).resolve().parent / "fixtures" / "module_bus" / "manifests"
os.environ.setdefault("AETHELARK_MODULES_DIR", str(_FIXTURE_MODULES))
# A module counts as available only if its command exists. The suite must give
# the same answer on a machine that has never installed one, so each fixture
# module gets a stub command on PATH; nothing here runs them.
import stat as _stat
import tempfile as _tempfile
_STUBS = Path(_tempfile.mkdtemp(prefix="aethelark-test-commands-"))
for _name in ("a3d", "atrade", "alaw"):
    _cmd = _STUBS / _name
    _cmd.write_text("#!/bin/sh\nexit 0\n")
    _cmd.chmod(_cmd.stat().st_mode | _stat.S_IXUSR)
os.environ["PATH"] = str(_STUBS) + os.pathsep + os.environ.get("PATH", "")
if "AETHELARK_DATA_DIR" not in os.environ:
    import tempfile as _tempfile
    os.environ["AETHELARK_DATA_DIR"] = _tempfile.mkdtemp(prefix="aethelark-test-data-")

# Qt refuses to set up WebEngine once a QCoreApplication exists ("must be
# imported or Qt.AA_ShareOpenGLContexts must be set before a QCoreApplication
# instance is created"), so the import happens here at collection time, before
# any fixture can build one. Absence is not an error: machines without
# WebEngine skip the tests that need it.
try:                                                    # noqa: SIM105
    import PyQt6.QtWebEngineWidgets  # noqa: F401
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
PILL_HTML = REPO / "web" / "pill.html"


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="session")
def web_view(qapp):
    """One QWebEngineView for the whole run, laid out but never on screen.

    WA_DontShowOnScreen gives the page a real viewport — without one it lays
    out at zero width and every size assertion is meaningless — while never
    acquiring a GPU surface, whose loss under the offscreen platform is its own
    source of crashes.
    """
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    from PyQt6.QtCore import Qt
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView()
    view.resize(700, 560)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.show()
    return view


def pump(seconds: float) -> None:
    """Let Qt and Chromium run for a while. Transitions and timers are real."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)


def load(view, path: Path = PILL_HTML, timeout: float = 20.0) -> None:
    """Load a local page and return once it is up."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import QApplication

    done: dict = {}
    conn = view.loadFinished.connect(lambda ok: done.setdefault("ok", ok))
    view.load(QUrl.fromLocalFile(str(path)))
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while "ok" not in done and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)
    try:
        view.loadFinished.disconnect(conn)
    except (TypeError, RuntimeError):
        pass
    assert done.get("ok"), f"{path.name} did not load"


def evaluate(view, js: str, timeout: float = 5.0):
    """Run JS in the page and return its value, without a nested event loop."""
    from PyQt6.QtWidgets import QApplication

    done: dict = {}
    view.page().runJavaScript(js, lambda r: done.setdefault("r", r))
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while "r" not in done and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)
    assert "r" in done, f"JS never returned: {js[:70]}"
    return done["r"]


@pytest.fixture
def user_answers_every_question(monkeypatch):
    """The user takes a turn after each confirmation question. For tests about
    what a token is bound to, not about whether the user answered."""
    from core import confirm
    original = confirm.Gate.issue

    def issue_then_answer(self, name, args):
        token = original(self, name, args)
        confirm.note_user_turn()
        return token

    monkeypatch.setattr(confirm.Gate, "issue", issue_then_answer)
