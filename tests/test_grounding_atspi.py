import pytest

from actions.grounding.atspi import AtspiGrounder, AtspiNode


def _nodes():
    return [
        AtspiNode("Cancel", "push button", 100, 500, 80, 30),
        AtspiNode("Save",   "push button", 200, 500, 80, 30),
        AtspiNode("Save",   "menu item",   0,   10,  60, 20),
        AtspiNode("Search", "text",        300, 100, 200, 30),
        AtspiNode("Hidden", "push button", 0,   0,   0,  0),
    ]


def test_finds_button_by_name_and_role():
    g = AtspiGrounder(walker=lambda: _nodes())
    el = g.find("the Save button")
    assert el is not None
    assert el.center == (240, 515)
    assert el.source == "atspi"


def test_role_hint_disambiguates_same_name():
    g = AtspiGrounder(walker=lambda: _nodes())
    assert g.find("the Save menu").center == (30, 20)
    assert g.find("the Save button").center == (240, 515)


def test_returns_none_below_threshold():
    g = AtspiGrounder(walker=lambda: _nodes())
    assert g.find("the Frobnicate button") is None


def test_skips_zero_sized_nodes():
    g = AtspiGrounder(walker=lambda: _nodes())
    assert g.find("Hidden button") is None


def test_finds_text_field():
    g = AtspiGrounder(walker=lambda: _nodes())
    el = g.find("the Search field")
    assert el is not None
    assert el.center == (400, 115)


def test_never_raises_when_walker_explodes():
    def boom():
        raise RuntimeError("no display")
    g = AtspiGrounder(walker=boom)
    assert g.find("anything") is None
    assert g.available() is False


def test_available_is_true_when_walker_yields():
    g = AtspiGrounder(walker=lambda: _nodes())
    assert g.available() is True


def test_grounder_has_name():
    assert AtspiGrounder(walker=lambda: []).name == "atspi"


def test_node_states_default_empty_and_has_reports_membership():
    plain = AtspiNode("Save", "push button", 0, 0, 10, 10)
    assert plain.states == frozenset()
    assert plain.has("ENABLED") is False

    live = AtspiNode("Save", "push button", 0, 0, 10, 10,
                     states=frozenset({"ENABLED", "SENSITIVE", "SHOWING"}),
                     value="")
    assert live.has("ENABLED") is True
    assert live.has("EDITABLE") is False


def test_states_and_value_flow_through_to_the_element():
    node = AtspiNode("Search", "text", 300, 100, 200, 30,
                     states=frozenset({"ENABLED", "EDITABLE"}),
                     value="hello")
    g = AtspiGrounder(walker=lambda: [node])
    el = g.find("the Search field")
    assert el.has("EDITABLE") is True
    assert el.value == "hello"


def test_live_walker_does_not_raise():
    """Smoke test against the real desktop. Must degrade, never explode."""
    pytest.importorskip("gi")
    from actions.grounding.atspi import live_walker
    list(live_walker())


def test_live_walker_accepts_both_scopes():
    pytest.importorskip("gi")
    from actions.grounding.atspi import live_walker
    list(live_walker(scope="active"))
    list(live_walker(scope="all"))


def test_live_walker_defaults_to_active_scope():
    """Default must be the focused window, not the whole desktop.

    A desktop-wide walk is ~18x slower and reports children of unmapped
    windows at 0,0 — elements that look real but aren't on screen.
    """
    import inspect
    from actions.grounding.atspi import live_walker
    assert inspect.signature(live_walker).parameters["scope"].default == "active"


def test_rejects_int_min_coordinates():
    """AT-SPI reports INT_MIN for unmapped components. Clicking that is how
    you lose a mouse pointer. Seen live on a real desktop."""
    bogus = AtspiNode("New Tab", "push button", -2147483648, -2147483648, 36, 46)
    g = AtspiGrounder(walker=lambda: [bogus])
    assert g.find("the New Tab button") is None


def test_rejects_absurd_offscreen_coordinates():
    g = AtspiGrounder(walker=lambda: [
        AtspiNode("Save", "push button", 999999, 10, 80, 30)])
    assert g.find("the Save button") is None


def test_accepts_legitimate_negative_overlap():
    """A window scrolled partly off the left edge is still real."""
    g = AtspiGrounder(walker=lambda: [
        AtspiNode("Save", "push button", -10, 100, 80, 30)])
    assert g.find("the Save button") is not None
