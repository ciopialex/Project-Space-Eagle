from actions.grounding.atspi import AtspiNode, bridge_status


def _nodes(n=10):
    return [AtspiNode(f"Widget{i}", "push button", i, i, 20, 20)
            for i in range(n)]


def test_populated_tree_reports_working_with_no_hint():
    s = bridge_status(enabled_fn=lambda: True, walker=lambda: _nodes())
    assert s["working"] is True
    assert s["nodes_visible"] == 10
    assert s["hint"] == ""


def test_a_populated_tree_is_working_even_when_the_setting_is_off():
    """Measured reality: the tree is usable with toolkit-accessibility false.
    Reporting 'off, therefore broken' would have been wrong."""
    s = bridge_status(enabled_fn=lambda: False, walker=lambda: _nodes())
    assert s["enabled"] is False
    assert s["working"] is True
    assert s["hint"] == ""


def test_empty_tree_is_flagged_with_the_exact_fix_command():
    s = bridge_status(enabled_fn=lambda: False, walker=lambda: [])
    assert s["working"] is False
    assert "gsettings set org.gnome.desktop.interface toolkit-accessibility true" in s["hint"]


def test_nearly_empty_tree_is_flagged():
    s = bridge_status(enabled_fn=lambda: True, walker=lambda: _nodes(2))
    assert s["working"] is False
    assert s["hint"] != ""


def test_exploding_walker_degrades_to_zero_nodes():
    def boom():
        raise RuntimeError("no display")
    s = bridge_status(enabled_fn=lambda: True, walker=boom)
    assert s["nodes_visible"] == 0
    assert s["working"] is False


def test_unnamed_and_zero_sized_nodes_do_not_count():
    junk = [AtspiNode("", "filler", 0, 0, 10, 10),
            AtspiNode("Ghost", "push button", 0, 0, 0, 0)]
    s = bridge_status(enabled_fn=lambda: True, walker=lambda: junk)
    assert s["nodes_visible"] == 0


def test_bridge_status_never_mutates_settings(monkeypatch):
    """Guard rail: this must never shell out to `gsettings set`."""
    import subprocess
    calls = []

    class FakeCompleted:
        stdout = "false"

    def spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", spy)
    bridge_status(walker=lambda: [])
    for call in calls:
        assert "set" not in list(call), f"tried to write settings: {call}"
