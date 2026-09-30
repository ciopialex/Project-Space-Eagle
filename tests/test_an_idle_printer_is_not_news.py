"""An idle or unreachable machine never takes the island, even in a session
that used its module (2026-09-29: an idle Centauri Carbon 2 replaced the
camera card of a print that had just started on the other printer)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("PyQt6.QtWebEngineWidgets")
from aethelark_web import WebShellUI  # noqa: E402


def _ui(engaged):
    return SimpleNamespace(
        _ambient_engaged=set(engaged), _ambient_live_jobs=set(),
        _STATUS_ONLY_STATES=WebShellUI._STATUS_ONLY_STATES,
        _LIVE_JOB_STATES=WebShellUI._LIVE_JOB_STATES,
        _JOB_ENDED_STATES=WebShellUI._JOB_ENDED_STATES)


def _event(module, state=None, printer="Centauri Carbon 2"):
    payload = {"printer": printer}
    if state is not None:
        payload["telemetry"] = {"state": state}
    return SimpleNamespace(module=module, payload=payload)


@pytest.mark.parametrize("state", ["IDLE", "PrinterState.IDLE", "DISCONNECTED", "UPLOADING"])
def test_a_machine_sitting_there_stays_quiet_even_after_the_user_used_it(state):
    assert WebShellUI._ambient_may_speak(_ui({"a3d"}), _event("a3d", state)) is False


def test_a_running_job_may_speak():
    assert WebShellUI._ambient_may_speak(_ui(set()), _event("a3d", "PRINTING")) is True


def test_a_module_without_machine_state_speaks_once_engaged():
    assert WebShellUI._ambient_may_speak(_ui({"atrade"}), _event("atrade")) is True
    assert WebShellUI._ambient_may_speak(_ui(set()), _event("atrade")) is False
