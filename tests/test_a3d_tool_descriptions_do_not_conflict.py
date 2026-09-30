"""Two tools must not claim the same sentence.

The model picks a tool by reading these descriptions, so an example phrase
appearing in two of them is a coin flip at the exact moment the user speaks.
`download` and `browse` both listed "download a watch stand" — the literal
opening line of the flow this feature exists for — and one fetches a single
model while the other fetches five and shows them.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MANIFESTS = [REPO / "tests/fixtures/module_bus/manifests/a3d.toml"]


def _descriptions(path):
    return {t["name"]: t["description"] for t in tomllib.loads(path.read_text())["tools"]}


def _quoted_examples(text):
    """The 'like this' phrases a description offers as triggers.

    Only the part before a description tells the model to use something ELSE:
    naming a phrase in order to hand it off is the opposite of claiming it.
    """
    head = re.split(r"\bcall \w+ instead\b|\bPrefer this over\b", text)[0]
    return {m.strip().lower() for m in re.findall(r"'([^']{6,})'", head)}


def test_no_example_phrase_is_claimed_by_two_tools():
    tools = _descriptions(MANIFESTS[0])
    examples = {name: _quoted_examples(desc) for name, desc in tools.items()}
    clashes = []
    names = sorted(examples)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = examples[a] & examples[b]
            if shared:
                clashes.append(f"{a} and {b} both claim {sorted(shared)}")
    assert not clashes, "; ".join(clashes)


def test_download_hands_the_open_ended_case_to_browse():
    """The literal opening line of the flow must route to browse."""
    tools = _descriptions(MANIFESTS[0])
    assert "browse" in tools["download"], \
        "download does not tell the model when to use browse instead"
    assert "download a watch stand" not in _quoted_examples(tools["download"])
    assert "download a watch stand" in _quoted_examples(tools["browse"])


def test_the_two_print_tools_say_they_start_a_machine_and_the_others_say_they_do_not():
    tools = _descriptions(MANIFESTS[0])
    for name in ("browse", "download"):
        assert "start" in tools[name].lower() and "not" in tools[name].lower(), \
            f"{name} does not say it starts no print"


