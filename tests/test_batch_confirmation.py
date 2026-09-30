"""One confirmation, bound to the whole set of picks.

This is the code path that turns a spoken sentence into hours of filament and
a spool of plastic, so the gate is the load-bearing part, not the batching.

The existing single-print gate issues a single-use token that expires in 180s
and is bound to a SHA-256 of the exact arguments. A batch keeps all of that and
binds to the ORDERED SET of (model, printer) pairs, so a yes for "stand on CC1,
dock on CC2" cannot start "dock on CC1", and a pick added after the question was
asked invalidates the token rather than riding along on it.

Every test below asserts that nothing ran, not merely that an error came back.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus.bus import ModuleBus
from core.module_bus.manifest import CONFIRM_PARAM

pytestmark = pytest.mark.usefixtures("user_answers_every_question")

JOBS = json.dumps([{"model_id": "1", "printer": "CC1"},
                   {"model_id": "2", "printer": "CC2"}])


@pytest.fixture
def bus(tmp_path, monkeypatch):
    (tmp_path / "a3d.toml").write_text(
        'key = "a3d"\nbinary = "a3d"\noutput = "json"\ndescription = "d"\n\n'
        '[[tools]]\nname = "print_batch"\ndescription = "d"\n'
        'argv = ["print-batch", "{jobs}", "--json"]\n'
        'confirm = true\nconfirm_prompt = "Start these prints?"\n\n'
        '  [tools.params.jobs]\n  type = "STRING"\n'
        '  description = "jobs"\n  required = true\n')
    b = ModuleBus([tmp_path], which=lambda n: "/bin/true").discover()
    ran = []

    class FakeProc:
        returncode = 0
        def communicate(self, timeout=None):
            ran.append(True)
            return json.dumps({"started": [], "failed": []}), ""

    monkeypatch.setattr("core.module_bus.bus.subprocess.Popen",
                        lambda *a, **k: FakeProc())
    b._ran = ran
    return b


def _ask(bus, jobs=JOBS):
    tr = bus.invoke("a3d_print_batch", {"jobs": jobs})
    return tr, tr.data.get("confirm_token")


def test_the_first_call_runs_nothing_and_returns_a_question_and_a_token(bus):
    tr, token = _ask(bus)
    assert bus._ran == [], "the first call started a print"
    assert tr.data.get("needs_confirmation") is True
    assert token, "no token was issued, so nothing could ever be confirmed"


def test_a_confirmed_call_runs(bus):
    _, token = _ask(bus)
    out = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: token})
    assert out.ok, out.message
    assert bus._ran == [True]


def test_a_token_for_a_different_set_is_refused(bus):
    """'stand on CC1, dock on CC2' must not authorise 'dock on CC1'."""
    _, token = _ask(bus)
    swapped = json.dumps([{"model_id": "2", "printer": "CC1"},
                          {"model_id": "1", "printer": "CC2"}])
    out = bus.invoke("a3d_print_batch", {"jobs": swapped, CONFIRM_PARAM: token})
    assert not out.ok
    assert "different request" in out.guidance
    assert bus._ran == []


def test_reordering_the_same_pairs_is_a_different_request(bus):
    _, token = _ask(bus)
    reordered = json.dumps(list(reversed(json.loads(JOBS))))
    out = bus.invoke("a3d_print_batch", {"jobs": reordered, CONFIRM_PARAM: token})
    assert not out.ok
    assert bus._ran == []


def test_adding_a_pick_after_the_question_invalidates_the_token(bus):
    _, token = _ask(bus)
    grown = json.dumps(json.loads(JOBS) + [{"model_id": "3", "printer": "CC1"}])
    out = bus.invoke("a3d_print_batch", {"jobs": grown, CONFIRM_PARAM: token})
    assert not out.ok
    assert bus._ran == []


def test_removing_a_pick_after_the_question_invalidates_the_token(bus):
    _, token = _ask(bus)
    shrunk = json.dumps(json.loads(JOBS)[:1])
    out = bus.invoke("a3d_print_batch", {"jobs": shrunk, CONFIRM_PARAM: token})
    assert not out.ok
    assert bus._ran == []


def test_a_token_is_single_use(bus):
    _, token = _ask(bus)
    first = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: token})
    assert first.ok
    second = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: token})
    assert not second.ok
    assert bus._ran == [True], "the token started a second batch"


def test_a_fabricated_token_runs_nothing(bus):
    """This is what stops the model answering its own question."""
    out = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: "made-up"})
    assert not out.ok
    assert "not one I issued" in out.guidance
    assert bus._ran == []


def test_an_expired_token_runs_nothing(bus, monkeypatch):
    _, token = _ask(bus)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 200)
    out = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: token})
    assert not out.ok
    assert "expired" in out.guidance
    assert bus._ran == []


def test_two_pending_questions_do_not_authorise_each_other(bus):
    """Ask about A, ask about B, then answer A with B's token."""
    other = json.dumps([{"model_id": "9", "printer": "CC1"}])
    _, token_a = _ask(bus, JOBS)
    _, token_b = _ask(bus, other)
    out = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: token_b})
    assert not out.ok
    assert bus._ran == []
    ok = bus.invoke("a3d_print_batch", {"jobs": JOBS, CONFIRM_PARAM: token_a})
    assert ok.ok


def test_the_spoken_question_names_what_will_happen(bus):
    tr, _ = _ask(bus)
    assert "Start these prints?" in tr.message


# ---- the batch is the harness's to build, never the model's -----------------

class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_the_model_cannot_call_the_internal_batch_directly(monkeypatch):
    """print_picked composes the batch from the live screen. A model that names
    the internal tool itself is refused before the module is asked anything --
    this used to be a check written for that one tool name."""
    import asyncio

    import main

    ran = []
    monkeypatch.setattr(main.MODULE_BUS, "invoke",
                        lambda *a, **k: ran.append(a))
    live = main.AethelarkLive(_UI())
    call = type("Call", (), {"name": main._PRINT_TOOL, "id": "1",
                             "args": {"jobs": JOBS}})()
    response = asyncio.run(live._execute_tool(call))
    assert ran == []
    assert response.response["ok"] is False
