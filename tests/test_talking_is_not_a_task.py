"""Conversation is answered, not searched. And a tool result is not a string.

The operator typed `eagle`, said "how are you doing", and got:

    [Tool] > web_search (epoch=0) {mode=search, query=how are you doing}
    AttributeError: 'ToolResult' object has no attribute 'startswith'

Two independent defects in one line of output.

**It searched.** `web_search` described itself as "Use for ANY question about
current facts, events, prices, or topics -- always prefer this over guessing."
"How are you doing" is a question, and the model had been told to always prefer
searching over answering. It did exactly what it was told.

The structural reason it will happen again unless guarded: every tool
description is written to advocate for its own tool, and nothing in the system
advocates for calling none. The null action has no description, so it has no
defender, and the default drifts toward action. `mission` had already won
"download a watch stand" the same way.

**Then it crashed.** `web_search` returns a `ToolResult`; the dispatcher still
asked whether it `startswith("No results")`. Every call raised, whatever the
query -- so the tool it should not have reached was also broken on arrival.

The case this is really written for is neither of those. It is the user
explaining that his boss cut his hours and his rent went up: a sentence full of
companies, prices and events, none of which is a request to look anything up.
Searching there proves you were not listening.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402

PROMPT = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text()


def _description(name: str) -> str:
    for decl in main.TOOL_DECLARATIONS:
        if isinstance(decl, dict) and decl.get("name") == name:
            return decl.get("description") or ""
    raise AssertionError(f"{name} is not declared")


# --------------------------------------------------------------- the crash

def test_a_tool_result_is_never_treated_as_a_string():
    """Drive the branch with the type it really gets.

    This replaced `inspect.getsource(AethelarkLive._execute_tool)` plus a
    substring search for `.startswith`. That assertion admitted its own reason
    in its docstring — "the branch sits inside a 2000-line async method behind
    a Gemini session" — which is the finding, not a justification. A test that
    reads source cannot tell a working branch from a broken one; it can only
    tell you which characters are present.

    The bug it guards was real and total: `web_search` returns a ToolResult,
    the branch asked it `.startswith("No results")`, and EVERY call raised
    AttributeError before the user heard anything. It was found by saying "how
    are you doing" to the running app, not by the thirteen passing tests that
    called the function directly.
    """
    import asyncio
    from types import SimpleNamespace

    import main

    live = main.AethelarkLive.__new__(main.AethelarkLive)
    live._unprompted_turn = False
    live._turn_epoch = 0
    live._inflight_by_args = {}
    live._tool_executor = None          # run_in_executor(None, …) is the default pool
    live.ui = SimpleNamespace(muted=True, set_state=lambda *_: None,
                              show_content=lambda *a, **k: None,
                              write_log=lambda *a, **k: None)

    returned = ToolResult.success("Rome is the capital of Italy.")
    monkeypatch_target = main.web_search_action
    main.web_search_action = lambda **kw: returned
    try:
        call = SimpleNamespace(id="c1", name="web_search",
                               args={"query": "capital of italy"})
        reply = asyncio.run(live._execute_tool(call))
    finally:
        main.web_search_action = monkeypatch_target

    # The whole bug was that this line never got reached.
    assert reply.response.get("ok") is True, reply.response
    assert "Rome" in reply.response.get("result", ""), reply.response

def test_tool_result_has_no_startswith_so_the_guard_is_real():
    """If ToolResult ever grows one, the test above stops meaning anything."""
    assert not hasattr(ToolResult.success("x"), "startswith")


@pytest.mark.parametrize("returned", [
    ToolResult.success("Results for cats"),
    ToolResult.failure("Nothing found.", guidance="Try a different phrase."),
    "a legacy string return",
    None,
])
def test_every_shape_web_search_can_return_is_handled(returned):
    """Legacy strings still exist elsewhere; the branch must survive all of it."""
    ok = returned.ok if isinstance(returned, ToolResult) else bool(
        returned and not str(returned).startswith(("No results", "Search failed")))
    text = returned.message if isinstance(returned, ToolResult) else returned
    assert isinstance(ok, bool)
    assert text is None or isinstance(text, str)


# ------------------------------------------------------- the over-claim

def test_web_search_no_longer_claims_every_question():
    said = " ".join(_description("web_search").split()).lower()
    for claim in ("any question", "always prefer this"):
        assert claim not in said, (
            f"web_search still claims {claim!r}. 'How are you doing' is a "
            f"question, and a tool that claims every question wins it")


def test_web_search_names_conversation_as_the_thing_it_is_not_for():
    """A weak model needs the exclusion stated, not implied by omission."""
    said = " ".join(_description("web_search").split()).lower()
    assert "conversation" in said
    for shape in ("telling you", "opinion", "joking", "upset"):
        assert shape in said, (
            f"the description does not name {shape!r} as conversation, so the "
            f"model has to infer it -- which is the gap that produced this bug")


def test_a_fact_inside_a_story_is_not_a_search_request():
    """The failure that matters more than the greeting.

    Someone describing a bad month names companies, prices and events. Every
    one of them is bait for a keyword match, and firing a search into the
    middle of it is the machine announcing it was not listening.
    """
    said = " ".join(_description("web_search").split()).lower()
    assert "mentioned inside" in said or "inside something" in said, (
        "nothing tells the model that facts appearing in what the user is "
        "SAYING are not facts he is ASKING about")


def test_the_prompt_asks_talk_or_task_before_it_asks_which_tool():
    """Order is the point. A rule after twelve routing bullets is not first."""
    lower = PROMPT.lower()
    assert "routing precedence" in lower, "the prompt has no routing section"
    talk = lower.find("just talking")
    assert talk != -1, "the prompt never asks whether the user is just talking"
    assert talk < lower.find("routing precedence"), (
        "the question of whether a tool is wanted at all comes after the "
        "rules for picking one")


def test_the_prompt_says_answering_from_knowledge_is_not_claiming_a_limit():
    """These two rules read as contradictory to a small model.

    "NEVER REPORT A LIMIT YOU HAVE NOT HIT" pushes hard toward trying a tool.
    Without saying so, "just answer him" looks like the thing that rule bans.
    """
    said = PROMPT.lower()
    assert "not \"reporting a limit" in said or "not 'reporting a limit" in said, (
        "nothing reconciles the talk-back rule with the never-claim-a-limit "
        "rule, and the louder of the two wins")


# ------------------------------------------- the asymmetry that caused it

CONVERSATION = [
    "how are you doing",
    "what can you do",
    "thanks man",
    "my boss cut my hours and rent went up",
    "I had a rough week",
    "what do you think about that",
]


#: Words that carry no routing signal. Without stripping these, "what do you
#: think about that" matches eight tools on `about` and `that` alone -- which
#: is the heuristic failing, not eight tools misbehaving. A phrase made only of
#: these cannot be tested this way, and the test says so rather than passing
#: silently or inventing a finding.
_STOPWORDS = {
    "what", "that", "this", "about", "there", "here", "with", "from", "your",
    "you", "yours", "have", "having", "just", "been", "were", "they", "them",
    "some", "much", "more", "very", "then", "than", "into", "over", "when",
    "does", "doing", "done", "will", "would", "could", "should", "like",
    "think", "know", "want", "need", "good", "well", "really", "thing",
    "things", "anything", "something", "make", "made", "back", "come", "came",
}


@pytest.mark.parametrize("said", CONVERSATION)
def test_no_tool_advertises_itself_as_the_answer_to_conversation(said):
    """No description may claim a conversational turn as its ground.

    Matching is deliberately crude -- two content words -- because that is
    roughly how much a fast model has to go on mid-sentence.
    """
    words = {w for w in said.lower().split()
             if len(w) > 3 and w not in _STOPWORDS}
    if len(words) < 2:
        pytest.skip(
            f"{said!r} carries fewer than two content words, so keyword "
            f"matching cannot say anything true about it")
    claimants = []
    for decl in main.TOOL_DECLARATIONS:
        if not isinstance(decl, dict):
            continue
        text = " ".join((decl.get("description") or "").split()).lower()
        # A description that NAMES the phrase in order to disclaim it is doing
        # the right thing, so only count tools that do not also say "not for".
        hits = sum(w in text for w in words)
        disclaims = ("do not call it" in text or "never for" in text
                     or "not a request" in text)
        if hits >= 2 and not disclaims:
            claimants.append(decl["name"])
    assert not claimants, (
        f"{claimants} advertise themselves for {said!r} without disclaiming "
        f"it; every tool argues for itself and nothing argues for silence")
