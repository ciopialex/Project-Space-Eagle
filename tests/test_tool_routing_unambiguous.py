"""No two tools may claim the same request without a stated winner.

Measured before this existed: nine tools claimed "open a website" — open_app,
send_message, youtube_video, browser_control, youtube_api, web_agency,
developer_mode, swarm_mode, a since-removed game updater. Five claimed "read
or write a file".
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

import pytest
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
    # Added 2026-09-05, after the operator said "download a watch stand from
    # makerworld for me to print" and the eagle started a browser mission --
    # opening makerworld.com and clicking through it -- instead of calling the
    # 3D module, which does the whole thing in one call. Both descriptions
    # advertised the sentence: mission's example was "download a laptop stand
    # from makerworld", and a3d_browse's was "download a watch stand".
    "download a 3D model": ("model", "download", "print", "makerworld"),
    # Added 2026-09-10. `a3d_sing` and `a3d_beacon` claimed the identical
    # sentence -- both descriptions contained the words "which one is CC1" --
    # and sing was not a different capability: its argv was
    # ["beacon", "{printer}", "--mode=chime"], the same subcommand beacon runs
    # with mode=chime, which beacon's own parameter already lists. Two tools,
    # one behaviour, one sentence, no winner named. sing was removed; this
    # keeps a replacement for it from arriving.
    # Deliberately the words for the PHYSICAL act, not "printer" or "which":
    # those two appear in every printer tool and would make this intent
    # contested by tools that are not competing for it at all.
    "make a machine announce itself": ("flash", "chime", "beep", "tone", "tell which", "machine is which"),
}

#: For each contested intent, the tool that must win — and therefore the tool
#: every other claimant has to defer to in its own description.
WINNERS = {
    "open a website":       "web_agency",
    "read or write a file": "file_controller",
    "see the screen":       "screen_process",
    "build or code":        "swarm_mode",
    # The module fetches, measures and shows the part in one call, with the
    # confirmation gate and the island card that go with it. Driving a browser
    # to do the same thing is slower, breaks when the site changes, and skips
    # both.
    "download a 3D model":  "a3d_browse",
    # One tool makes a machine announce itself, in whichever way was asked for.
    "make a machine announce itself": "a3d_beacon",
}


def _declarations(labs: bool = True) -> dict[str, str]:
    """Every description the model reads -- core tools AND module tools.

    The model is handed `declared_tools() + MODULE_BUS.tool_declarations()`.
    Auditing only the first list is how `a3d_browse` sat invisible to this
    file while `mission` advertised its ground: the two tools that collided
    lived in different lists, so nothing compared them.

    `labs` picks which built-ins are on offer: Labs tools are only declared
    when the user switches Labs on, and both configurations must route.
    Module tools come from every discovered manifest rather than only the
    available ones, so the audit does not shrink when a binary is missing.
    """
    decls = {d["name"]: (d.get("description") or "")
             for d in main.declared_tools(labs) if isinstance(d, dict)}
    for manifest in main.MODULE_BUS._manifests:
        for tool in manifest.tools:
            if not tool.internal:
                d = tool.declaration()
                decls[d["name"]] = d.get("description") or ""
    return decls


def _routing_text(labs: bool) -> str:
    """Every place the model is told which tool wins: the core prompt's
    routing block, each module's own instructions, and the Labs prompt when
    Labs is on. Module routing lives with the module, so a harness that has
    never heard of a module still routes it."""
    block = PROMPT.split("ROUTING PRECEDENCE")[1].split("\n\n")[0] \
        if "ROUTING PRECEDENCE" in PROMPT else ""
    notes = "\n".join(m.instructions for m in main.MODULE_BUS._manifests
                      if getattr(m, "instructions", ""))
    core = Path(__file__).resolve().parent.parent / "core"
    labs_text = ((core / "prompt_labs.txt").read_text() +
                 (core / "prompt_coding.txt").read_text() if labs else "")
    return "\n".join((block, notes, labs_text))


def _claimants(words, labs: bool = True) -> list[str]:
    return [name for name, desc in _declarations(labs).items()
            if sum(w in desc.lower() for w in words) >= 2]


@pytest.mark.parametrize("labs", [False, True])
def test_every_contested_intent_has_a_stated_winner(labs):
    """A tool that overlaps another must say who wins, in its own text. The
    model should never have to infer precedence from tone."""
    decls = _declarations(labs)
    unresolved = []
    for intent, words in INTENTS.items():
        winner = WINNERS[intent]
        for name in _claimants(words, labs):
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


@pytest.mark.parametrize("labs", [False, True])
def test_every_contested_tool_is_routed_in_the_prompt(labs):
    """A rule where ambiguity exists — not everywhere.

    A tool nothing else contests is already unambiguous, and a rule for it is
    pure context cost on every single turn. What must be written down is
    precedence between tools that genuinely overlap, because that is the only
    case where the model has to choose. Checked in both configurations: the
    routing a user without Labs gets must stand on its own.
    """
    contested = set()
    for intent, words in INTENTS.items():
        claimants = _claimants(words, labs)
        if len(claimants) > 1:
            contested.update(claimants)
    assert "ROUTING PRECEDENCE" in PROMPT, "the prompt has no routing section"
    routing = _routing_text(labs)
    unrouted = sorted(n for n in contested if n not in routing)
    assert unrouted == [], (
        f"these overlap another tool and have no routing rule: {unrouted}")


def test_a_tool_of_a_module_that_is_not_installed_says_so_and_what_to_do():
    import main
    for name in ("a3d_browse", "atrade_analyze", "trade_quote"):
        r = main._missing_tool_result(name)
        assert r.ok is False and "not installed" in r.message
        assert "Do not try that action again" in r.guidance


def test_a_made_up_tool_is_refused_with_a_next_step():
    import main
    r = main._missing_tool_result("made_up_tool")
    assert r.ok is False and "Do not call that name again" in r.guidance


def test_without_the_3d_module_no_printer_tool_is_offered(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    import main
    from core.module_bus import ModuleBus

    src = Path(__file__).parent / "fixtures" / "module_bus" / "manifests"
    only_trade = tmp_path / "manifests"
    only_trade.mkdir()
    shutil.copy(src / "atrade.toml", only_trade / "atrade.toml")
    monkeypatch.setattr(main, "MODULE_BUS", ModuleBus(manifest_dirs=[only_trade]))

    offered = {d["name"] for d in main.declared_tools()}
    assert not offered & set(main.MODULE_BACKED_TOOLS)
    assert not any(n.startswith("a3d_") for n in offered)
    assert not any(d["name"].startswith("a3d_") for d in main.MODULE_BUS.tool_declarations())
