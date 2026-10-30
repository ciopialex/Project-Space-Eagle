"""A machine with no NVIDIA driver says so once, not on every metrics tick.

The metrics sampler polls every 1.5 s. The GPU probe used to retry loading
libnvidia-ml each time and print the same failure, which filled the log of
every Mac and most laptops.
"""
from __future__ import annotations

import sys

import pytest

import ui

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="posix probe")


def test_the_gpu_probe_fails_once_and_then_stays_quiet(monkeypatch, capsys):
    import ctypes
    import time

    # The import started the real sampler; let it finish its tick so it does
    # not probe concurrently with this test.
    ui._metrics._running = False
    time.sleep(1.6)

    calls = []

    def no_driver(name, *a, **k):
        calls.append(name)
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(ctypes, "CDLL", no_driver)
    monkeypatch.setattr(ui, "_PYNVML_UI", False)
    monkeypatch.setattr(ui, "_NVML_POSIX_OK", None)
    sampler = object.__new__(ui._SysMetrics)
    capsys.readouterr()   # whatever the real sampler printed before this
    for _ in range(5):
        assert sampler._get_gpu() == -1.0
    out = capsys.readouterr().out
    assert out.count("GPU load unavailable") == 1
    assert len(calls) == 1
