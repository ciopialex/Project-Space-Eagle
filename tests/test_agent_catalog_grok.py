"""Grok Build (xAI's terminal agent) is one of the coding CLIs the eagle can launch (2026-11-05)."""
from core import agent_catalog as c


def test_grok_build_is_known_and_takes_the_prompt_as_its_argument():
    grok = c.catalog()["grok_build"]
    assert grok.label == "Grok Build" and grok.binary == "grok"
    assert not grok.types_prompt                                  # `grok [PROMPT]` starts with the task
    assert grok.command("fix the tests") == "grok 'fix the tests'"


def test_every_way_of_saying_grok_resolves_to_it():
    for spoken in ("grok", "Grok Build", "grok-build", "grok cli"):
        assert c.resolve(spoken) == "grok_build", spoken


def test_it_is_preferred_right_after_claude_code_and_antigravity():
    assert c.PREFERENCE[:3] == ("claude_code", "antigravity_cli", "grok_build")
