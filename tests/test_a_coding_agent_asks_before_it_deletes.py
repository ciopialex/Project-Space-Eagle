"""A coding agent asks the eagle before a command its rules do not allow, over
the same JSON stream it talks on (`--permission-prompt-tool stdio`). Ordinary
work is allowed at once; a delete waits for the user's own yes."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core import confirm, escalations  # noqa: E402
from core.agent_session import HeadlessSession  # noqa: E402

# Asks once, the way claude 2.1.316 does, then reports what it was told.
FAKE_AGENT = r'''
import json, sys
command = sys.argv[1]
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("type") == "user":
        print(json.dumps({"type": "control_request", "request_id": "r1", "request": {
            "subtype": "can_use_tool", "tool_name": "Bash",
            "input": {"command": command}, "description": "Do the thing"}}), flush=True)
    elif msg.get("type") == "control_response":
        told = msg["response"]["response"]["behavior"]
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "result": told}), flush=True)
'''


@pytest.fixture(autouse=True)
def _clean():
    escalations.clear()
    yield
    escalations.clear()


def _session(tmp_path, command):
    agent = tmp_path / "fake_claude.py"
    agent.write_text(FAKE_AGENT)
    results = []
    s = HeadlessSession("claude_code", "Claude Code", tmp_path,
                        argv=[sys.executable, str(agent), command])
    s.on_result = results.append
    s.send_line("go")
    return s, results


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_ordinary_work_is_allowed_without_asking(tmp_path):
    s, results = _session(tmp_path, "python3 hello.py")
    try:
        assert _wait(lambda: results), "the agent got an answer"
        assert results[0].text == "allow"
        assert escalations.pending() == []
    finally:
        s.close()


def test_a_delete_waits_for_the_users_own_yes(tmp_path):
    from actions.swarm_orchestrator import swarm_orchestrate
    s, results = _session(tmp_path, "rm -rf build")
    try:
        assert _wait(lambda: escalations.pending()), "the delete is held"
        assert not results, "the agent is still waiting"

        r = asyncio.run(swarm_orchestrate({"action": "authorize"}))
        assert "not said yes" in r and not results, "the model cannot approve on its own"

        confirm.note_user_turn("yes, go ahead")
        asyncio.run(swarm_orchestrate({"action": "authorize"}))
        assert _wait(lambda: results)
        assert results[0].text == "allow"
    finally:
        s.close()


def test_a_no_refuses_the_delete(tmp_path):
    from actions.swarm_orchestrator import swarm_orchestrate
    s, results = _session(tmp_path, "rm -rf build")
    try:
        assert _wait(lambda: escalations.pending())
        confirm.note_user_turn("no, don't")
        asyncio.run(swarm_orchestrate({"action": "authorize"}))
        assert _wait(lambda: results)
        assert results[0].text == "deny"
    finally:
        s.close()
