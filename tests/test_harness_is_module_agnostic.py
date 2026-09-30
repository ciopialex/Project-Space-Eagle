"""The harness must not know what a domain module is called.

`core/module_bus/__init__.py` states the rule this pins:

    a3d slices and prints 3D models; alaw answers Romanian tax questions;
    neither is installed on most machines and neither belongs in this repo.

Space-Eagle is public, free, and installed by people who own none of the
modules. A module reaches it through a manifest and a subprocess — never
through an import — and that is what lets a machine with no modules boot, a
module to be written in any language, and a paid module to stay out of a free
repository.

An import also drags in what an import drags in: the module's dependency tree,
its network calls, and its release cadence, into the boot path of a voice loop
that has to answer in under a second.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Package names of the domain modules. Anything importing these has reached
#: past the bus.
MODULE_PACKAGES = {"aethelark_trade", "aethelark3d", "aethelark_law"}

#: Directories that are the harness itself.
HARNESS = ("actions", "config", "core", "memory", "tools", "web")
HARNESS_FILES = ("main.py", "aethelark_web.py", "ui.py", "web_shell.py")


def _sources() -> list[Path]:
    files = [REPO / name for name in HARNESS_FILES]
    for folder in HARNESS:
        files.extend((REPO / folder).rglob("*.py"))
    return [f for f in files if f.is_file() and "__pycache__" not in f.parts]


def _imported_packages(path: Path) -> set[str]:
    """Top-level package of every import in the file, however nested.

    Walks the AST rather than grepping, so an import inside a function or a
    `try:` — which is exactly where one gets hidden — is still found.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:                     # not ours to police
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("source", _sources(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_harness_file_imports_a_domain_module(source: Path):
    leaked = _imported_packages(source) & MODULE_PACKAGES
    assert not leaked, (
        f"{source.relative_to(REPO)} imports {', '.join(sorted(leaked))}. "
        f"Domain modules are reached through MODULE_BUS.invoke(), which works "
        f"whether or not they are installed and cannot block the turn loop "
        f"without a timeout.")


def test_the_repo_declares_no_domain_module_as_a_dependency():
    """A module in requirements.txt is the same coupling, one layer down."""
    requirements = REPO / "requirements.txt"
    if not requirements.is_file():
        pytest.skip("no requirements.txt")

    declared = requirements.read_text(encoding="utf-8").lower()
    for package in MODULE_PACKAGES:
        assert package.replace("_", "-") not in declared and package not in declared, (
            f"{package} is declared as a dependency of the harness")


# ── the other half of the boundary ──────────────────────────────────────────
#
# The import check above passes today, and has always passed, while:
#
#   * main.py declares four 3D-PRINTING tools (island_picks, print_picked,
#     island_set_printer, island_deck_move) and reaches the screen through six
#     methods that are not in core/ui_contract.py at all,
#   * web/pill.html branches on modKey === "atrade" / "a3d" / "alaw" to decide
#     how to fill a card,
#   * core/card_assembly.py is atrade's data shape keyed by stock ticker,
#   * core/ambient.py held a table of a3d's printer events.
#
# So a green "the harness is module-agnostic" was true only of the narrowest
# reading of it. Not importing a module is the cheapest half of the boundary;
# not KNOWING one is the half that matters, and nothing measured it.
#
# This is a ratchet, not a gate. It records how much module-specific code the
# harness holds right now and fails when that grows. It cannot fail the build
# for the coupling that already exists — that is what the module-protocol work
# removes, file by file — but a new `if module == "a3d"` has to be a decision
# somebody makes on purpose rather than a thing that quietly reappears.

MODULE_KEYS = ("a3d", "atrade", "alaw")

#: path -> lines of CODE (comments stripped) naming a specific module.
#: Measured 2026-09-07. Lower these as the work lands; never raise them.
KNOWN_COUPLING = {
    "web/pill.html": 12,
    "main.py": 4,
    "core/card_assembly.py": 7,
    "core/ambient.py": 8,
    "core/module_bus/__init__.py": 6,
    "core/module_bus/bus.py": 4,
    "core/module_bus/manifest.py": 4,
    "core/module_bus/bundle.py": 2,
    "aethelark_web.py": 0,
}


def _module_mentions() -> dict[str, int]:
    import re

    # `\batrade\b` does NOT match `atrade_quote` — `_` is a word character —
    # so the first version of this scan missed qualified tool names, which are
    # the commonest form coupling takes. It reported 51 lines; the corrected
    # pattern finds 68, and main.py went from 1 to 12.
    pattern = re.compile(
        r"\b(" + "|".join(MODULE_KEYS) + r")(?:_\w+)?\b", re.I)
    scanned = [REPO / f for f in ("main.py", "aethelark_web.py", "ui.py")]
    for folder in ("core", "actions", "web", "memory", "dashboard"):
        scanned.extend((REPO / folder).rglob("*.py"))
    scanned.append(REPO / "web" / "pill.html")

    found: dict[str, int] = {}
    for path in scanned:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if "manifests" in path.parts:       # a manifest is the module's own file
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Block comments span lines, and stripping them per-line counted a
        # comment that merely NAMES a module as coupling to it. Provenance —
        # "measured against a real atrade analyze" — is exactly what these
        # comments are for and must not read as a dependency. Blanked rather
        # than deleted so line numbers in any failure still line up.
        text = re.sub(r"/\*.*?\*/",
                      lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        hits = 0
        for line in text.splitlines():
            if pattern.search(re.sub(r"#.*$", "", line)):
                hits += 1
        if hits:
            found[str(path.relative_to(REPO))] = hits
    return found


def test_the_harness_does_not_learn_new_module_specific_code():
    """A ratchet. It may only ever tighten."""
    now = _module_mentions()
    worse = {
        path: (count, KNOWN_COUPLING.get(path, 0))
        for path, count in now.items()
        if count > KNOWN_COUPLING.get(path, 0)
    }
    assert not worse, (
        "new module-specific code in the harness:\n"
        + "\n".join(f"    {p}: {c} lines name a module, baseline was {b}"
                    for p, (c, b) in sorted(worse.items()))
        + "\n\nA module's name belongs in its manifest, its card HTML and its "
          "own repository. If this is deliberate, lower the boundary somewhere "
          "else first — do not raise the baseline to make this pass."
    )
