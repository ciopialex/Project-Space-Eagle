import inspect

import actions.computer_control as cc
from actions.grounding.base import Element


def test_screen_find_returns_center_from_resolver(monkeypatch):
    el = Element.from_bounds("Save", "push button", 200, 500, 80, 30, "atspi")
    monkeypatch.setattr(cc, "find_element", lambda d: el)
    assert cc._screen_find("the Save button") == (240, 515)


def test_screen_find_returns_none_when_resolver_misses(monkeypatch):
    monkeypatch.setattr(cc, "find_element", lambda d: None)
    assert cc._screen_find("the Frobnicate button") is None


def test_screen_find_never_raises(monkeypatch):
    def boom(_):
        raise RuntimeError("everything is on fire")
    monkeypatch.setattr(cc, "find_element", boom)
    assert cc._screen_find("anything") is None


def test_screen_find_signature_is_unchanged():
    """The existing public contract. Every caller depends on this."""
    sig = inspect.signature(cc._screen_find)
    assert list(sig.parameters) == ["description"]


def test_screen_find_no_longer_calls_gemini_directly(monkeypatch):
    """Grounding must go through the resolver, not a hardcoded provider."""
    def fail(*a, **k):
        raise AssertionError("_screen_find built its own genai client")

    monkeypatch.setattr(cc, "_get_api_key", fail)
    monkeypatch.setattr(cc, "find_element", lambda d: None)
    assert cc._screen_find("anything") is None
