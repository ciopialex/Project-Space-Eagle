"""Shutting the computer down cannot be taken back, so it waits for a yes.

It used to wait for `confirmed=yes`, an argument the tool's schema never
declared: a model could pass it on the first call without asking anyone. It now
goes through the same token gate as a module's print (core/confirm.py).
"""
import pytest

from actions import computer_settings as cs

pytestmark = pytest.mark.usefixtures("user_answers_every_question")


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setitem(cs.ACTION_MAP, "shutdown", lambda: calls.append("shutdown"))
    monkeypatch.setitem(cs.ACTION_MAP, "restart", lambda: calls.append("restart"))
    monkeypatch.setattr(cs, "_GATE", cs.Gate())
    return calls


def _call(**params):
    return cs.computer_settings(parameters=params)


def test_the_first_call_only_asks(ran):
    r = _call(action="shutdown")
    assert not r.ok and r.data.get("needs_confirmation") and r.data.get("confirm_token")
    assert ran == []


def test_an_invented_token_is_refused_and_the_question_asked_again(ran):
    r = _call(action="shutdown", confirm_token="yes")
    assert not r.ok and r.data.get("confirm_token") not in (None, "yes")
    assert ran == []


def test_the_issued_token_runs_it_once(ran):
    token = _call(action="shutdown").data["confirm_token"]
    _call(action="shutdown", confirm_token=token)
    assert ran == ["shutdown"]
    _call(action="shutdown", confirm_token=token)      # spent
    assert ran == ["shutdown"]


def test_a_yes_to_restart_does_not_shut_down(ran):
    token = _call(action="restart").data["confirm_token"]
    r = _call(action="shutdown", confirm_token=token)
    assert not r.ok and ran == []


def test_the_old_confirmed_argument_does_nothing(ran):
    _call(action="shutdown", confirmed="yes")
    assert ran == []
