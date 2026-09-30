"""`eagle --doctor --fix` changes the eagle's own environment freely, and the
rest of the machine only with a person's yes.

It used to run everything marked `auto` unattended -- including switching on
GNOME's accessibility for every app and rewiring the default microphone and
speakers. And every pip fix said `python -m pip install`, which fails with "No
module named pip" in the uv-built environment every real install has.
"""
from core import doctor
from core.doctor import MISSING, Check


def _checks():
    return [Check("pip thing", MISSING, fix="echo local", auto=True),
            Check("desktop thing", MISSING, fix="echo system", auto=True,
                  system=True)]


def _run(monkeypatch, answer: bool):
    ran = []
    monkeypatch.setattr(doctor, "run_checks", lambda: [])
    doctor.apply_fixes(_checks(), run=lambda cmd, **k: ran.append(cmd),
                       ask=lambda q: answer)
    return ran


def test_a_machine_wide_fix_waits_for_a_yes(monkeypatch):
    assert _run(monkeypatch, answer=False) == ["echo local"]


def test_with_a_yes_it_runs(monkeypatch):
    assert _run(monkeypatch, answer=True) == ["echo local", "echo system"]


def test_nobody_at_the_keyboard_means_no(monkeypatch):
    ran = []
    monkeypatch.setattr(doctor, "run_checks", lambda: [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    doctor.apply_fixes(_checks(), run=lambda cmd, **k: ran.append(cmd))
    assert ran == ["echo local"]


def test_pip_fixes_install_into_this_environment_without_pip(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/opt/uv" if n == "uv" else None)
    cmd = doctor._pip("playwright")
    assert cmd.startswith('"/opt/uv" pip install --python')
    assert "-m pip" not in cmd
