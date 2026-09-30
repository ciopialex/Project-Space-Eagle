from pathlib import Path

from core.agent_stream import RESULT, TEXT, TOOL_USE, AgyStream

FIXTURES = Path(__file__).parent / "fixtures" / "agent_stream"


def _events(name):
    parser = AgyStream()
    out = []
    for line in (FIXTURES / name).read_text().splitlines():
        out.extend(parser.feed(line))
    return out


def test_two_turns_in_one_process():
    events = [(e.kind, e.text, e.ok) for e in _events("agy_two_turns.jsonl")
              if e.kind in (TEXT, RESULT)]
    assert events == [(TEXT, "ALPHA", None), (RESULT, "ALPHA", True),
                      (TEXT, "BETA", None), (RESULT, "BETA", True)]


def test_tool_use_is_named():
    tools = [e for e in _events("agy_tool_use.jsonl") if e.kind == TOOL_USE]
    assert [t.name for t in tools] == ["run_command", "run_command"]
    assert tools[0].data["input"] == {"CommandLine": "ls -la"}
