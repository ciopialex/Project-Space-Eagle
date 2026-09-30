"""The bus is only worth having if the harness actually consults it.

A discovery mechanism nothing calls is decoration. These check the three joins
into the existing system — the schema the model sees, the dispatch branch, and
the preflight — without needing a live session or an installed module.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import doctor  # noqa: E402
from core.module_bus import ModuleBus  # noqa: E402


def test_the_preflight_reports_the_bus():
    names = [c.name for c in doctor.run_checks()]
    assert "domain modules" in names


def test_no_modules_is_not_a_preflight_failure(tmp_path):
    """Space-Eagle is a complete assistant with zero modules installed. A
    preflight that went red on a machine with no 3D printer would be crying
    wolf, and a preflight people learn to ignore protects nothing."""
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: None)
    bus.discover()
    assert bus.available() == [] and bus.absent() == []
    # The check builds its own bus; assert the property it depends on instead
    # of monkeypatching one in.
    check = next(c for c in doctor.run_checks() if c.name == "domain modules")
    assert check.good


def test_main_offers_module_tools_to_the_model():
    import main
    declared = {d["name"] for d in main.TOOL_DECLARATIONS}
    offered = {d["name"] for d in main.MODULE_BUS.tool_declarations()}

    # Namespacing is what makes appending safe. If a module ever collides with
    # a core tool name, the model gets two declarations with one name and picks
    # unpredictably — so assert the sets are disjoint rather than trusting it.
    assert declared.isdisjoint(offered)


def test_every_offered_module_tool_can_be_dispatched():
    """`owns()` gates the dispatch branch. A tool offered to the model but not
    owned by the bus falls through to "Unknown tool", which the model cannot
    act on and the user hears as the eagle refusing something it just claimed."""
    import main
    for decl in main.MODULE_BUS.tool_declarations():
        assert main.MODULE_BUS.owns(decl["name"]), decl["name"]


def test_every_offered_module_tool_has_a_timeout_spec():
    """Without a spec the scheduler uses the 30s default, which is short for a
    module that goes to the network or waits on a printer."""
    import main
    for decl in main.MODULE_BUS.tool_declarations():
        spec = main.TOOL_SPECS.get(decl["name"])
        assert spec is not None, decl["name"]
        assert spec.timeout_s >= 60.0


def test_module_declarations_are_shaped_like_the_core_ones():
    """They are concatenated into one list and sent as one schema. A malformed
    entry is rejected at connect time — for the whole session, not just that
    tool — so the shape has to match exactly."""
    import main
    for decl in main.MODULE_BUS.tool_declarations():
        assert set(decl) <= {"name", "description", "parameters"}
        assert decl["name"] and decl["description"]
        assert decl["parameters"]["type"] == "OBJECT"
        for prop in decl["parameters"]["properties"].values():
            assert prop["type"] in {"STRING", "INTEGER", "NUMBER",
                                    "BOOLEAN", "ARRAY", "OBJECT"}


def test_a_broken_bus_never_stops_the_harness_booting():
    """`main` builds its bus at import. A module manifest is a file a user can
    hand-edit; a typo in one must not be the reason the eagle will not start."""
    bus = ModuleBus(manifest_dirs=["/definitely/not/a/directory/9f3a"],
                    which=lambda b: None)
    bus.discover()
    assert bus.tool_declarations() == []
    assert bus.owns("anything") is False
