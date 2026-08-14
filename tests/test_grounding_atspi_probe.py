"""screen_click's AT-SPI tier burned ~20 real seconds across 4 calls in a
live session (5s timeout × up to 85 internal attempts, every time) because
nothing checked whether AT-SPI could answer at all before polling it
repeatedly. A single cheap probe, cached for the process, turns a doomed
20-second wait into an instant skip to the next rung (vision)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding import resolver as R  # noqa: E402


def test_atspi_unavailable_is_detected_once_and_cached(monkeypatch):
    calls = []
    def fake_probe():
        calls.append(1)
        return False
    monkeypatch.setattr(R, "_atspi_probe", fake_probe)
    monkeypatch.setattr(R, "_atspi_cache", None)
    assert R.atspi_available() is False
    assert R.atspi_available() is False
    assert len(calls) == 1, "probed more than once — should cache per process"


def test_atspi_probe_uses_the_gi_binding_not_pyatspi(monkeypatch):
    """Regression: `_atspi_probe` used to import the separate legacy
    `pyatspi` package, which isn't installed anywhere in this repo/venv —
    so the probe always fell into `except Exception: return False`, not
    because the bus was broken but because the wrong module was checked.
    This silently and permanently disabled a genuinely working AT-SPI tier.

    Mocks only `gi.repository.Atspi` (the binding `actions/grounding/atspi.py`
    actually uses, four times) healthy, with `pyatspi` left absent exactly
    as it is on a real machine, and confirms the probe reports available —
    proving it does not depend on `pyatspi` at all.

    `_ensure_bindings_once` and `atspi_enabled` are stubbed healthy so this
    stays a pure unit test, unaffected by whatever this machine's real
    GNOME toggle happens to be set to right now.
    """
    import actions.grounding.atspi as A
    monkeypatch.setattr(A, "_ensure_bindings_once", lambda: {"ok": True})
    monkeypatch.setattr(A, "atspi_enabled", lambda: True)

    fake_desktop = types.SimpleNamespace(get_child_count=lambda: 3)
    fake_atspi_module = types.SimpleNamespace(get_desktop=lambda i: fake_desktop)
    fake_gi_repository = types.SimpleNamespace(Atspi=fake_atspi_module)
    fake_gi = types.SimpleNamespace(
        require_version=lambda name, version: None,
        repository=fake_gi_repository,
    )
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_gi_repository)
    monkeypatch.setitem(sys.modules, "pyatspi", None)  # simulate "not installed"

    assert R._atspi_probe() is True


def test_atspi_probe_calls_ensure_bindings_before_importing_gi(monkeypatch):
    """Regression: every other AT-SPI entry point (`live_walker()`,
    `AtspiGrounder.find()`) calls `_ensure_bindings_once()` before touching
    `gi`, because on a fresh install the system PyGObject package is often
    sealed outside the project's venv until that one-time bootstrap symlinks
    it in. The probe skipped this and did a bare `import gi` — so on a
    machine where the bootstrap hadn't run yet, the probe would fail,
    cache `False` for the life of the process, and (because
    `_GatedAtspiTier.available()` checks the cache before ever calling the
    real grounder) the bootstrap that would have fixed it never runs.
    """
    import actions.grounding.atspi as A
    calls = []
    monkeypatch.setattr(A, "_ensure_bindings_once", lambda: calls.append(1))
    monkeypatch.setattr(A, "atspi_enabled", lambda: True)

    fake_desktop = types.SimpleNamespace(get_child_count=lambda: 3)
    fake_atspi_module = types.SimpleNamespace(get_desktop=lambda i: fake_desktop)
    fake_gi_repository = types.SimpleNamespace(Atspi=fake_atspi_module)
    fake_gi = types.SimpleNamespace(
        require_version=lambda name, version: None,
        repository=fake_gi_repository,
    )
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_gi_repository)

    assert R._atspi_probe() is True
    assert calls, "_ensure_bindings_once() was never called before the probe"


def test_atspi_probe_checks_the_gnome_toggle_not_just_the_import(monkeypatch):
    """Regression (Finding 3): Task 3's own stated purpose was catching the
    bus-up, binding-importing-fine, GNOME toggle-OFF case — but the probe as
    first written only checked whether the import succeeded, which is really
    just "is gi installed". With a healthy mocked `gi.repository.Atspi` and
    `atspi_enabled()` reporting the toggle off, the probe must still report
    unavailable — otherwise the fast-skip this task promises never engages
    in exactly the scenario it was written for.
    """
    import actions.grounding.atspi as A
    monkeypatch.setattr(A, "_ensure_bindings_once", lambda: {"ok": True})
    monkeypatch.setattr(A, "atspi_enabled", lambda: False)

    fake_desktop = types.SimpleNamespace(get_child_count=lambda: 3)
    fake_atspi_module = types.SimpleNamespace(get_desktop=lambda i: fake_desktop)
    fake_gi_repository = types.SimpleNamespace(Atspi=fake_atspi_module)
    fake_gi = types.SimpleNamespace(
        require_version=lambda name, version: None,
        repository=fake_gi_repository,
    )
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_gi_repository)

    assert R._atspi_probe() is False


def test_atspi_probe_returns_true_on_a_live_healthy_bus():
    """Regression for the exact bug found live: on a machine with a healthy
    `gi.repository.Atspi` binding and an enabled accessibility bridge, the
    probe must report available — not just refrain from crashing. A probe
    that checks the wrong package (`pyatspi`) passes a "doesn't raise" smoke
    test just as easily while still being permanently, silently wrong.

    Skipped (not asserted false) on a machine without gi or a live enabled
    bus, matching how `test_live_walker_does_not_raise` in
    tests/test_grounding_atspi.py handles the same live-environment premise.
    """
    pytest.importorskip("gi")
    from actions.grounding.atspi import atspi_enabled
    if not atspi_enabled():
        pytest.skip("toolkit-accessibility bridge not enabled on this machine")
    assert R._atspi_probe() is True


class _FakeGrounder:
    def __init__(self, available: bool) -> None:
        self.name = "atspi"
        self.cost = "fast"
        self._available = available

    def available(self) -> bool:
        return self._available

    def find(self, description: str):
        raise AssertionError("find() should not be reached by these tests")


def test_gated_atspi_tier_available_when_bus_and_grounder_both_healthy(monkeypatch):
    """The case that was silently broken: a real, working AT-SPI bus with a
    grounder that can see elements must be reachable through the tier, not
    permanently skipped."""
    monkeypatch.setattr(R, "atspi_available", lambda: True)
    tier = R._GatedAtspiTier(_FakeGrounder(available=True))
    assert tier.available() is True


def test_gated_atspi_tier_unavailable_when_bus_probe_says_no(monkeypatch):
    """The fast-skip case that was already working: when the process-wide
    probe says the bus can't answer, the tier must report unavailable
    regardless of what the wrapped grounder itself would say."""
    monkeypatch.setattr(R, "atspi_available", lambda: False)
    tier = R._GatedAtspiTier(_FakeGrounder(available=True))
    assert tier.available() is False
