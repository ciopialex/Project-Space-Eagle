"""Running out of Gemini's free allowance is a wait, not a dropped line.

Measured 2026-09-23: live sessions ended with "1011 None. Resource has been
exhausted (e.g. check quota)". The reconnect loop filed that under a dropped
connection, retried every two seconds -- spending the next minute's allowance
too -- and told the user it was "resuming". These drive the real loop against a
client that refuses to connect, and read back what it waited and what it said.
"""
import asyncio

import pytest

import main

QUOTA = "1011 None. Resource has been exhausted (e.g. check quota)."
DROPPED = "received 1011 (internal error) Internal error encountered."


class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __init__(self):
        self.logs, self.states, self.notices = [], [], []

    def write_log(self, text):
        self.logs.append(text)

    def set_state(self, state):
        self.states.append(state)

    def show_notice(self, text, action=""):
        self.notices.append((text, action))

    def __getattr__(self, name):
        return lambda *a, **k: None


class _Stop(Exception):
    pass


def _drive(monkeypatch, error: str, attempts: int):
    class _Refuses:
        async def __aenter__(self):
            raise RuntimeError(error)

        async def __aexit__(self, *exc):
            return False

    class _Live:
        def connect(self, **kwargs):
            return _Refuses()

    class _Client:
        def __init__(self, **kwargs):
            self.aio = type("Aio", (), {"live": _Live()})()

    waits = []

    async def fake_sleep(delay, *a, **k):
        waits.append(delay)
        if len(waits) >= attempts:
            raise _Stop()

    monkeypatch.setattr(main, "_get_api_key", lambda: "key")
    monkeypatch.setattr(main.genai, "Client", _Client)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.AethelarkLive, "_build_config", lambda self: None)
    ui = _UI()
    live = main.AethelarkLive(ui)
    with pytest.raises(_Stop):
        asyncio.run(live.run())
    return ui, waits


def test_an_exhausted_quota_waits_longer_each_time_and_says_why(monkeypatch):
    ui, waits = _drive(monkeypatch, QUOTA, attempts=5)
    assert waits == [30.0, 60.0, 120.0, 300.0, 300.0]
    assert ui.notices and "free limit" in ui.notices[0][0]
    # A tap on this notice opens nothing: there is no key to fix.
    assert ui.notices[0][1] == ""
    assert not any("resuming" in line for line in ui.logs)


def test_a_dropped_connection_still_reconnects_at_once(monkeypatch):
    ui, waits = _drive(monkeypatch, DROPPED, attempts=1)
    assert waits == [2.0]
    assert not ui.notices
