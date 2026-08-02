from actions.grounding.base import Element
from actions.grounding.verify import act_and_verify, observe

LIVE = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _el(states=LIVE, value="", left=100):
    return Element.from_bounds("Save", "push button", left, 200, 80, 40,
                               "atspi", states=states, value=value)


class ScriptedResolver:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def find(self, description):
        self.calls += 1
        return self._script[min(self.calls - 1, len(self._script) - 1)]


def test_observe_snapshots_bounds_states_and_value():
    snap = observe("Save button", ScriptedResolver([_el(value="hello")]))
    assert snap["bounds"] == (100, 200, 80, 40)
    assert "ENABLED" in snap["states"]
    assert snap["value"] == "hello"


def test_observe_returns_none_when_missing():
    assert observe("Ghost", ScriptedResolver([None])) is None


def test_reports_changed_when_state_flips_after_acting():
    r = ScriptedResolver([_el(), _el(), _el(states=LIVE | {"CHECKED"})])
    out = act_and_verify("Save button", lambda el: "clicked", resolver=r,
                         hit_test=lambda x, y: _el(), sleep=lambda s: None)
    assert out["acted"] is True
    assert out["changed"] is True
    assert out["result"] == "clicked"


def test_reports_unchanged_when_nothing_happened():
    r = ScriptedResolver([_el()])
    out = act_and_verify("Save button", lambda el: "clicked", resolver=r,
                         hit_test=lambda x, y: _el(), sleep=lambda s: None)
    assert out["acted"] is True
    assert out["changed"] is False
    assert "no observable change" in out["detail"]


def test_does_not_act_when_element_never_becomes_actionable():
    calls = []
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    r = ScriptedResolver([disabled])
    out = act_and_verify("Save button", lambda el: calls.append(el),
                         resolver=r, timeout=0.2, poll=0.05,
                         hit_test=lambda x, y: disabled, sleep=lambda s: None)
    assert out["acted"] is False
    assert calls == []
    assert "enabled" in out["detail"]


def test_value_change_counts_as_changed():
    r = ScriptedResolver([_el(value=""), _el(value=""), _el(value="typed")])
    out = act_and_verify("Search field", lambda el: None, resolver=r,
                         hit_test=lambda x, y: _el(), sleep=lambda s: None)
    assert out["changed"] is True


def test_element_disappearing_counts_as_changed():
    r = ScriptedResolver([_el(), _el(), None])
    out = act_and_verify("Save button", lambda el: None, resolver=r,
                         hit_test=lambda x, y: _el(), sleep=lambda s: None)
    assert out["changed"] is True
    assert out["after"] is None
