"""The eagle can offer PurgeX, and a plain print never carries it.

The operator's rule: PurgeX is optional and must be mentioned; it is not part of
the basic pipeline unless the user opts in. On the harness side that means two
things about the a3d_print tool: the model CAN pass purgex when asked, and when
it does NOT, the built argv carries no PurgeX flag, so the module defaults to
standard purge.

Reading the manifest and building argv, which is what actually reaches the
module — not a mock of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus.manifest import build_argv, load_manifest

MANIFEST = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "manifests" / "a3d.toml"


def _print_tool():
    m = load_manifest(MANIFEST)
    return m, m.tool("print")


def test_a_plain_print_carries_no_purgex_flag():
    m, t = _print_tool()
    argv = build_argv(m, t, {"query_or_id": "calibration cube", "filament": "PLA"})
    assert not any("purgex" in a.lower() for a in argv), (
        f"a plain print built argv with a PurgeX flag: {argv}")


def test_opting_in_passes_purgex_through():
    m, t = _print_tool()
    argv = build_argv(m, t, {"query_or_id": "toy", "filament": "PLA", "purgex": True})
    assert any("purgex" in a.lower() and "true" in a.lower() for a in argv), (
        f"opting into PurgeX did not reach the argv: {argv}")


def test_purgex_is_declared_optional():
    _, t = _print_tool()
    purgex = next((p for p in t.params if p.name == "purgex"), None)
    assert purgex is not None, "a3d_print no longer exposes PurgeX at all"
    assert not purgex.required, "PurgeX must be optional, not a required argument"


def test_the_description_tells_the_model_to_mention_not_force_it():
    """The rule lives in the text the model reads. This is a property over that
    artifact (like a routing rule), not a mirror: it asserts the description
    carries the opt-in-and-mention instruction, which is what makes the model
    behave."""
    _, t = _print_tool()
    purgex = next(p for p in t.params if p.name == "purgex")
    desc = purgex.description.lower()
    assert "optional" in desc
    assert "mention" in desc
    assert "only pass true" in desc or "only when" in desc or "asked" in desc
