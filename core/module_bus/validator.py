"""Everything a manifest can promise and not deliver.

The parser catches what is malformed. This catches what is *inconsistent* — a
card pointing at an HTML file nobody wrote, a first card that does not exist,
a tool that starts a physical print with no question in front of it.

Every one of these is a blank card, a dead module, or a real-world action with
no confirmation, discovered at the moment the user is looking at it. Found here
instead, the module simply does not load, `load_manifests` logs which file and
which field, and the other modules load normally.

The failure mode of a discovery mechanism has to be "that module is missing",
never "nothing starts". So nothing here raises: it returns a list of sentences,
and `load_manifest` turns a non-empty list into the ValueError that skips the
module.

Ordering is deliberate. The most concrete problem is reported first, because a
person reading a startup log fixes the first line and re-runs.
"""
from __future__ import annotations

import re

from pathlib import Path

from .manifest import PERMANENT, IslandFace, ModuleManifest

#: Names the harness uses for what the eagle is doing. A module card sharing
#: one of these would shadow it the moment the two are keyed by name — and the
#: half of the codebase that already conflates the two rows is the reason this
#: check exists at all.
RESERVED_CARDS = frozenset({"idle", "listening", "thinking", "speaking", "none"})


def island_dirs(manifest: ModuleManifest) -> list[Path]:
    """Where this module's card files could live, relative to its manifest.

    Installed, the manifest sits beside its own `island/`. In a flat directory
    of several manifests, the cards are filed by key in a sibling `island/`.
    A validator that knew only one layout would refuse every module in
    whichever tree it had not been taught.
    """
    from .bus import island_dirs as _dirs
    return _dirs(manifest.source, manifest.key)


def resolve(manifest: ModuleManifest, name: str) -> Path | None:
    """A declared file name, wherever it really is. None when it is nowhere."""
    if not name:
        return None
    candidates = [d / name for d in island_dirs(manifest)]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0] if candidates else None


def _missing(manifest: ModuleManifest, name: str) -> bool:
    """Declared, and not present in either layout."""
    if not name:
        return False
    return not any((d / name).is_file() for d in island_dirs(manifest))


def problems(manifest: ModuleManifest) -> list[str]:
    """Every reason this module should not load. Empty means it may."""
    found: list[str] = []
    found += _tool_problems(manifest)
    if manifest.island is not None:
        found += _island_problems(manifest, manifest.island)
    if manifest.deck is not None:
        found += _deck_problems(manifest)
    for account in manifest.accounts:
        found += _account_problems(account)
    return found


_SITE_RE = re.compile(r"^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _account_problems(account) -> list[str]:
    """An account the host cannot hand over is worse than none: the user
    signs in and the module still says they are not signed in."""
    where = f"account {account.site or '?'!r}"
    found = []
    if not _SITE_RE.match(account.site or ""):
        found.append(f"{where}: site must be a bare domain like 'example.com'")
    if not account.proof:
        found.append(f"{where}: no proof cookie, so a signed-out browser "
                     f"could not be told from a signed-in one")
    if not account.receive:
        found.append(f"{where}: no receive command to hand the session to")
    return found


def _deck_problems(manifest: ModuleManifest) -> list[str]:
    """A deck refiner must name a real tool and a parameter it takes: the host
    calls it in the background, where nobody would see it fail."""
    deck = manifest.deck
    tool = next((t for t in manifest.tools if t.name == deck.refine), None)
    if tool is None:
        return [f"deck.refine names {deck.refine!r}, which is not one of its tools"]
    if deck.param not in {p.name for p in tool.params}:
        return [f"deck.refine_param {deck.param!r} is not a parameter of "
                f"tool {deck.refine!r}"]
    if tool.danger == PERMANENT or tool.confirm:
        return [f"deck.refine {deck.refine!r} needs a confirmation, and the "
                f"host runs it unasked"]
    return []


# ─────────────────────────────────────────────────────────── tools

def _tool_problems(manifest: ModuleManifest) -> list[str]:
    found: list[str] = []
    for tool in manifest.tools:
        where = f"tool '{tool.name}'"

        # A tool that does something to the physical world, with nothing for
        # the eagle to say out loud first. The two halves of a gate must not be
        # able to drift apart, so one implies the other.
        if tool.danger == PERMANENT and not tool.confirm_prompt:
            found.append(
                f"{where}: danger = \"permanent\" but no confirm_prompt — "
                f"a permanent action needs a question to ask out loud first")

        # `injects` is the host filling an argument the model cannot supply.
        # An injected name that never appears in argv is a silent no-op — the
        # tool runs without the thing it was written to act on.
        for name in tool.injects:
            if not any("{" + name + "}" in part for part in tool.argv):
                found.append(
                    f"{where}: injects {name!r} but no argv entry uses "
                    f"{{{name}}} — the host would fill in nothing")

    return found


# ─────────────────────────────────────────────────────────── island

def _island_problems(manifest: ModuleManifest, face: IslandFace) -> list[str]:
    found: list[str] = []
    names = set(face.names)
    looked = " or ".join(str(d) for d in island_dirs(manifest))

    # A template that is not there draws nothing, and the island templates
    # have lived outside any repository before now — one `rm` from gone, with
    # every test that touched them skipping silently.
    if _missing(manifest, manifest.template):
        found.append(
            f"[island]: template {manifest.template} is in neither {looked}")

    for card in face.cards:
        if card.name in RESERVED_CARDS:
            found.append(
                f"card '{card.name}': that name belongs to the eagle's own "
                f"state — pick another")
        for tool in card.prefetch:
            if manifest.tool(tool) is None:
                found.append(
                    f"card '{card.name}': prefetch {tool!r} is not a tool "
                    f"this module declares")

    # The card the module lands on when it first takes the screen. Without it
    # the module can be invoked and can never show anything.
    if not face.first:
        found.append("[island]: no 'first' — nothing says which card to open")
    elif face.first not in names:
        found.append(
            f"[island]: first = {face.first!r} is not one of {_listing(names)}")

    # What makes two answers the same card. Without it a price answer and a
    # deep answer about the same company cannot be recognised as one thing, and
    # the second view draws a card of em dashes — which is exactly what
    # happened before `card_assembly.py` existed to paper over it.
    if not face.about:
        found.append(
            "[island]: no 'about' — nothing says what makes two answers the "
            "same card")
    elif face.cards and not any(face.about in c.shows for c in face.cards):
        found.append(
            f"[island]: about = {face.about!r} is not in any card's 'shows', "
            f"so no card can be keyed by it")

    if _missing(manifest, face.css):
        found.append(f"[island]: {face.css} is in neither {looked}")

    return found


def _listing(names) -> str:
    return ", ".join(repr(n) for n in sorted(names)) or "(none)"
