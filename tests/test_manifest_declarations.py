"""The seam between a module's file and a loaded module.

This is the joint every defect on 2026-09-05 lived at: two components meeting,
each assuming the other checked. A manifest is written by hand, by someone
looking at a CLI they did not write, and everything it promises is acted on by
a harness that cannot see the module's insides.

Three things are tested and nothing else:

  1. Every refusal actually refuses. A card pointing at a file nobody wrote is
     a blank card at the moment the user looks at it; a `first` naming a card
     that does not exist is a module that can never show anything. Both are
     cheap here and expensive at runtime.
  2. A broken module does not take its neighbours down. The failure mode of a
     discovery mechanism has to be "that module is missing", never "nothing
     starts".
  3. Every shipped module tool declares its own timeout. This is the guard
     against drifting back to the thing that was just deleted — twelve
     hand-written entries in `main.py` holding facts about somebody else's
     binary, where that binary could not correct them.

Not tested: that the parser reads a field it was just told to read. Writing the
text and then asserting the text says what you wrote cannot fail.
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

from core.module_bus.manifest import load_manifest, load_manifests

REPO = pathlib.Path(__file__).resolve().parent.parent
SHIPPED = REPO / "tests" / "fixtures" / "module_bus" / "manifests"

_BASE = '''
key = "toy"
binary = "toy"
[[tools]]
name = "look"
description = "look at a thing"
argv = ["look", "--json"]
seconds = 10
%s
'''

#: (what is wrong, the TOML that is wrong, a phrase the refusal must contain).
#: The phrase matters as much as the refusal: this is read out of a startup log
#: by a person deciding what to edit, so "invalid manifest" would be a failure
#: even though the module correctly did not load.
BROKEN = [
    ("template that does not exist", '''
[island]
template = "nope.html"
about = "x"
first = "small"
[island.cards.small]
size = { w = 300, h = 62 }
shows = ["x"]
''', "template nope.html is in neither"),
    ("first pointing at no card", '''
[island]
about = "x"
first = "big"
[island.cards.small]
size = { w = 300, h = 62 }
shows = ["x"]
''', "first = 'big'"),
    ("about that no card shows", '''
[island]
about = "ticker"
first = "small"
[island.cards.small]
size = { w = 300, h = 62 }
shows = ["price"]
''', "no card can be keyed by it"),
    ("card named after an eagle state", '''
[island]
about = "x"
first = "listening"
[island.cards.listening]
size = { w = 300, h = 62 }
shows = ["x"]
''', "belongs to the eagle's own state"),
    ("card with no size", '''
[island]
about = "x"
first = "small"
[island.cards.small]
shows = ["x"]
''', "needs a size"),
    ("permanent action with no question to ask",
     'danger = "permanent"', "needs a question to ask out loud"),
    ("injecting an argument argv never uses",
     'injects = ["_selection"]', "the host would fill in nothing"),
    ("a resource the scheduler cannot arbitrate",
     'writes = ["quantum_foam"]', "the harness knows"),
]


@pytest.fixture()
def toy(tmp_path):
    """A module directory with one real card template in it."""
    (tmp_path / "island").mkdir()
    (tmp_path / "island" / "template.html").write_text("<div>{x}</div>")

    def write(body: str) -> pathlib.Path:
        path = tmp_path / "toy.toml"
        path.write_text(_BASE % body)
        return path

    return write


@pytest.mark.parametrize("label,body,phrase",
                         BROKEN, ids=[b[0] for b in BROKEN])
def test_a_broken_manifest_is_refused_by_name(toy, label, body, phrase):
    with pytest.raises(ValueError) as caught:
        load_manifest(toy(body))
    assert phrase in str(caught.value), (
        f"{label}: refused, but the message would not tell anyone what to "
        f"edit.\ngot: {caught.value}")


def test_a_valid_manifest_still_loads(toy):
    manifest = load_manifest(toy('''
[island]
about = "x"
first = "small"
[island.cards.small]
size = { w = 300, h = 62 }
shows = ["x"]
'''))
    assert manifest.island is not None
    assert manifest.island.names == ("small",)


def test_one_broken_module_does_not_stop_the_others(tmp_path):
    """The whole point of skipping rather than raising.

    One user hand-editing one TOML file must not stop the eagle booting.
    """
    # The manifests and their sibling island/ folder together: a template is
    # found beside the manifest that names it, not in the harness's own tree.
    shutil.copytree(SHIPPED.parent, tmp_path / "m")
    broken = tmp_path / "m" / "manifests" / "a3d.toml"
    broken.write_text(broken.read_text() + (
        '\n[island]\ntemplate = "gone.html"\nabout = "printer"\nfirst = "small"\n'
        '[island.cards.small]\n'
        'size = { w = 300, h = 62 }\nshows = ["printer"]\n'))

    loaded = load_manifests([tmp_path / "m" / "manifests"])
    keys = {m.key for m in loaded}
    assert "a3d" not in keys, "the broken module loaded anyway"
    assert {"alaw", "atrade"} <= keys, (
        f"one bad file took its neighbours with it; loaded {keys}")


def test_every_shipped_tool_declares_its_own_timeout():
    """The guard against the harness going back to guessing.

    Twelve of these were hand-written in `main.py` — the host holding a
    measurement about a binary it cannot see, where the binary could not
    correct it. `atrade analyze` costs 34-35s and the host's 30s default
    killed it four seconds early, every time, silently.
    """
    undeclared = [
        f"{m.key}_{t.name}"
        for m in load_manifests([SHIPPED])
        for t in m.tools
        if not t.seconds
    ]
    assert not undeclared, (
        "these tools would fall back to the harness's guess: "
        + ", ".join(undeclared))
