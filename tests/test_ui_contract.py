"""Conformance tests for the human-showcase boundary.

These are the tests that turn "find out at runtime, for one user, on one code
path, months later" into "the suite fails now".
"""
import re
from pathlib import Path

import pytest

from core.ui_contract import (REQUIRED_MEMBERS, AethelarkUI, conforms,
                              missing_members)

ROOT = Path(__file__).resolve().parent.parent


class MinimalUI:
    """The smallest thing that satisfies the contract."""
    muted = False
    current_file = None
    assistant_name = "Aethelark"
    on_text_command = None
    on_interrupt = None
    on_remote_clicked = None

    def write_log(self, text): pass
    def set_state(self, state): pass
    def set_audio_level(self, level): pass
    def show_content(self, title, text): pass
    def start_camera_stream(self): pass
    def stop_camera_stream(self): pass
    def prompt_reconfig(self): pass
    def reconfig_complete(self): return True
    def request_shutdown(self): pass
    def notify_phone_connected(self): pass


def test_minimal_ui_conforms():
    assert conforms(MinimalUI) is True
    assert missing_members(MinimalUI) == []


def test_missing_members_names_what_is_absent():
    class Incomplete:
        muted = False
        current_file = None

        def write_log(self, text): pass

    gaps = missing_members(Incomplete)
    assert "set_state" in gaps
    assert "request_shutdown" in gaps
    assert "write_log" not in gaps
    assert conforms(Incomplete) is False


def test_protocol_is_runtime_checkable_for_instances():
    assert isinstance(MinimalUI(), AethelarkUI)


def test_contract_covers_every_ui_call_the_backend_makes():
    """The contract must not drift behind main.py.

    Any `self.ui.X` in main.py that isn't in REQUIRED_MEMBERS means a UI
    surface can be written that satisfies the Protocol and still crashes.
    """
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    used = set(re.findall(r"self\.ui\.([A-Za-z_][A-Za-z0-9_]*)", source))
    undeclared = sorted(used - set(REQUIRED_MEMBERS))
    assert undeclared == [], (
        f"main.py touches UI members that the contract does not declare: "
        f"{undeclared}")


def test_backend_no_longer_reaches_into_private_ui_internals():
    """`ui._win._ready` and `ui.root.quit()` forced every surface to fabricate
    Qt-shaped objects — aethelark_web.py literally carries a `_WinShim` for it."""
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "self.ui._win" not in source
    assert "self.ui.root" not in source


@pytest.mark.parametrize("module,cls_name", [
    ("ui", "AethelarkUI"),
    ("aethelark_web", "WebShellUI"),
])
def test_shipped_uis_conform(module, cls_name):
    """Both real surfaces satisfy the contract, statically."""
    pytest.importorskip("PyQt6")
    mod = pytest.importorskip(module)
    cls = getattr(mod, cls_name)
    assert missing_members(cls) == [], (
        f"{cls_name} is missing: {missing_members(cls)}")


def test_recording_ui_conforms():
    from core.ui_recorder import RecordingUI
    assert missing_members(RecordingUI) == []
    assert isinstance(RecordingUI(), AethelarkUI)


def test_recording_ui_captures_what_the_backend_showed():
    from core.ui_recorder import RecordingUI
    ui = RecordingUI()
    ui.set_state("THINKING")
    ui.write_log("SYS: Interrupted — listening...")
    ui.set_state("LISTENING")
    ui.show_content("Result", "42")
    ui.notify_phone_connected()
    ui.request_shutdown()

    assert ui.states == ["THINKING", "LISTENING"]
    assert ui.last_state == "LISTENING"
    assert ui.logged("Interrupted")
    assert ui.content == [("Result", "42")]
    assert ui.phone_notifications == 1
    assert ui.shutdown_requested is True
    assert "set_state" in ui.call_names()


def test_recording_ui_can_simulate_an_unfinished_reconfig():
    from core.ui_recorder import RecordingUI
    ui = RecordingUI(reconfig_done=False)
    ui.prompt_reconfig()
    assert ui.reconfig_prompts == 1
    assert ui.reconfig_complete() is False
