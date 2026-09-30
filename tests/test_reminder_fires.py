"""A reminder is a program the operating system runs later, without the eagle.

Every reminder failed, on every OS, for weeks: the script that fires one was
built from f-strings, one of which evaluated a name that did not exist while
WRITING the file. Nothing ever ran the generated program, so nothing noticed.
These run it, with fake notifiers and a fake systemctl on PATH so the real
desktop and the real user systemd are never touched.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from actions import reminder as r

pytestmark = pytest.mark.skipif(sys.platform != "linux",
                                reason="exercises the Linux notifier and systemd")

MESSAGE = 'Call "Mum" -- it\'s {her} birthday'


def _fake(bins: Path, name: str, log: Path, code: int = 0) -> None:
    p = bins / name
    p.write_text(f"#!/bin/sh\nprintf '%s\\n' \"{name} $*\" >> '{log}'\nexit {code}\n")
    p.chmod(0o755)


@pytest.fixture
def box(tmp_path, monkeypatch):
    reminders, units, bins = (tmp_path / d for d in ("reminders", "units", "bin"))
    for d in (reminders, units, bins):
        d.mkdir()
    monkeypatch.setattr(r, "_scripts_dir", lambda: reminders)
    monkeypatch.setattr(r, "_unit_dir", lambda: units)
    monkeypatch.setenv("PATH", f"{bins}:{os.environ['PATH']}")
    return {"reminders": reminders, "units": units, "bins": bins,
            "log": tmp_path / "calls.log"}


def _run(script: Path) -> None:
    subprocess.run([sys.executable, str(script)], check=True, timeout=30,
                   env=dict(os.environ))


def test_the_script_shows_the_message_then_unregisters_itself(box):
    _fake(box["bins"], "notify-send", box["log"])
    _fake(box["bins"], "systemctl", box["log"])
    script = r._write_notify_script("t1", MESSAGE, "linux")
    record = r._write_record("t1", {"task": "t1", "scheduler": "systemd",
                                    "timer": "t1.timer", "units": [],
                                    "script": str(script)})
    _run(script)
    calls = box["log"].read_text()
    assert f"notify-send --app-name=Aethelark --urgency=critical Aethelark reminder {MESSAGE}" in calls
    assert "systemctl --user disable --now t1.timer" in calls
    assert not script.exists() and not record.exists()


def test_without_notify_send_it_asks_the_bus_directly(box):
    _fake(box["bins"], "notify-send", box["log"], code=1)
    _fake(box["bins"], "gdbus", box["log"])
    _fake(box["bins"], "systemctl", box["log"])
    script = r._write_notify_script("t2", MESSAGE, "linux")
    _run(script)
    calls = box["log"].read_text()
    assert "org.freedesktop.Notifications.Notify" in calls and MESSAGE in calls


def test_set_list_cancel_through_a_persistent_timer(box):
    _fake(box["bins"], "systemctl", box["log"])
    when = datetime.now() + timedelta(days=1)
    result = r.reminder({"date": when.strftime("%Y-%m-%d"),
                         "time": when.strftime("%H:%M"),
                         "message": "take the pills"})
    assert result.ok, result.message
    timers = list(box["units"].glob("*.timer"))
    assert len(timers) == 1
    timer = timers[0].read_text()
    # Persistent: a reminder due while the machine was off fires at next login.
    assert "Persistent=true" in timer
    assert f"OnCalendar={when.strftime('%Y-%m-%d %H:%M')}:00" in timer
    service = timers[0].with_suffix(".service").read_text()
    assert f'ExecStart="{sys.executable}"' in service

    listed = r.reminder({"action": "list"})
    assert listed.ok and "take the pills" in listed.message

    cancelled = r.reminder({"action": "cancel", "message": "pills"})
    assert cancelled.ok, cancelled.message
    assert not list(box["units"].iterdir())
    assert not list(box["reminders"].iterdir())
    assert "disable --now" in box["log"].read_text()


def test_a_scheduler_that_refuses_is_reported_as_not_set(box):
    _fake(box["bins"], "systemctl", box["log"], code=1)
    _fake(box["bins"], "at", box["log"], code=1)
    when = datetime.now() + timedelta(hours=2)
    result = r.reminder({"date": when.strftime("%Y-%m-%d"),
                         "time": when.strftime("%H:%M"), "message": "x"})
    assert not result.ok
    assert not list(box["reminders"].glob("*.json"))
