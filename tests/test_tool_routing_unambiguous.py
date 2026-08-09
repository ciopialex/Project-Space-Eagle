"""No two tools may claim the same request without a stated winner.

Measured before this existed: nine tools claimed "open a website" — open_app,
send_message, youtube_video, browser_control, youtube_api, web_agency,
developer_mode, swarm_mode, game_updater. Five claimed "read or write a file".
Twenty of thirty tools had no routing rule at all and were picked from their
description alone.

That is not decoding, it is a weighted coin flip, and it is the exact shape of
the original failure: `youtube_video` captured "show me my liked videos"
because its description claimed YouTube ground it could not actually deliver,
and the user was told his own videos were private.

The rule this file enforces: when several tools plausibly answer the same
utterance, exactly one must be named the winner IN THE OTHERS' TEXT. Ambiguity
is allowed to exist — the tools really do overlap — but it must be resolved in
writing rather than left to the model.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

PROMPT = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text()

#: Things a person actually says, and the words a tool uses to claim them.
INTENTS = {
    "open a website":     ("website", "url", "browser", "page", "web"),
    "read or write a file": ("file", "read", "write", "folder"),
    "see the screen":     ("screenshot", "screen", "capture"),
    "build or code":      ("code", "build", "project", "repo"),
}

#: For each contested intent, the tool that must win — and therefore the tool
#: every other claimant has to defer to in its own description.
WINNERS = {
    "open a website":       "web_agency",
    "read or write a file": "file_controller",
    "see the screen":       "screen_process",
    "build or code":        "swarm_mode",
}


def _declarations() -> dict[str, str]:
    return {d["name"]: (d.get("description") or "")
            for d in main.TOOL_DECLARATIONS if isinstance(d, dict)}


def _claimants(words) -> list[str]:
    return [name for name, desc in _declarations().items()
            if sum(w in desc.lower() for w in words) >= 2]


def test_every_contested_intent_has_a_stated_winner():
    """A tool that overlaps another must say who wins, in its own text. The
    model should never have to infer precedence from tone."""
    decls = _declarations()
    unresolved = []
    for intent, words in INTENTS.items():
        winner = WINNERS[intent]
        for name in _claimants(words):
            if name == winner:
                continue
            if winner not in decls[name]:
                unresolved.append(f"{name} contests '{intent}' without deferring to {winner}")
    assert unresolved == [], (
        "these overlap with no stated precedence:\n  " + "\n  ".join(unresolved))


def test_the_winner_actually_claims_its_own_ground():
    decls = _declarations()
    for intent, winner in WINNERS.items():
        assert winner in decls, f"{winner} is not declared at all"
        assert winner in _claimants(INTENTS[intent]), (
            f"{winner} is supposed to win '{intent}' but does not describe it")


def test_every_contested_tool_is_routed_in_the_prompt():
    """A rule where ambiguity exists — not everywhere.

    The first version of this demanded a prompt rule for all 30 tools. That is
    the wrong bar: a tool nothing else contests is already unambiguous, and a
    rule for it is pure context cost on every single turn. The prompt was
    deliberately trimmed for latency two days ago; re-inflating it with 20
    rules nobody needs would undo that for no routing benefit.

    What must be written down is precedence between tools that genuinely
    overlap, because that is the only case where the model has to choose."""
    contested = set()
    for intent, words in INTENTS.items():
        claimants = _claimants(words)
        if len(claimants) > 1:
            contested.update(claimants)

    # Looked for "toolname:" before, which only matched the per-tool entries
    # and missed precedence written as prose ("→ web_agency", "browser_control
    # only OPENS"). The property is that the PRECEDENCE block names the tool,
    # not that it is formatted a particular way.
    block = PROMPT.split("ROUTING PRECEDENCE")[1].split("\n\n")[0] \
        if "ROUTING PRECEDENCE" in PROMPT else ""
    assert block, "the prompt has no ROUTING PRECEDENCE section"
    unrouted = sorted(n for n in contested
                      if n not in block and f"{n}:" not in PROMPT)
    assert unrouted == [], (
        f"these overlap another tool and have no rule in prompt.txt: {unrouted}")
