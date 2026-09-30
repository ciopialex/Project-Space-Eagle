import main
from core.tool_result import ToolResult


def _live(calls):
    live = main.AethelarkLive.__new__(main.AethelarkLive)
    live._invoke_module = lambda name, args, timeout_s: (
        calls.append((name, args)) or ToolResult.success("ok"))
    return live


def test_a_card_control_runs_only_what_the_module_declared():
    calls = []
    live = _live(calls)
    ok, _ = live.card_action("a3d", "light", {"state": "on", "printer": "CC2",
                                              "rm": "-rf /"})
    assert ok and calls == [("a3d_light", {"state": "on", "printer": "CC2"})]


def test_a_card_control_never_runs_something_that_needs_a_spoken_yes():
    calls = []
    live = _live(calls)
    ok, message = live.card_action("a3d", "stop", {"printer": "CC2"})
    assert not ok and calls == [] and "yes" in message
    ok, _ = live.card_action("a3d", "no_such_tool", {})
    assert not ok and calls == []
