from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Role words a user is likely to say, mapped to the AT-SPI role names that
# should satisfy them. Keeps "the Save button" from matching a menu item that
# happens to also be called Save.
_ROLE_WORDS: dict[str, set[str]] = {
    "button":   {"push button", "toggle button", "radio button", "check box"},
    "field":    {"text", "entry", "password text"},
    "textbox":  {"text", "entry"},
    "input":    {"text", "entry", "password text"},
    "menu":     {"menu", "menu item"},
    "link":     {"link"},
    "tab":      {"page tab"},
    "checkbox": {"check box"},
    "icon":     {"icon", "image"},
}

# Filler that never identifies anything.
_ARTICLES = {"the", "a", "an", "on", "in", "at", "of", "to", "for",
             "click", "press", "open"}

# Words that usually describe the *kind* of control rather than name it —
# stripped first so "the Save button" matches a button named "Save".
_ROLE_NOUNS = {"button", "field", "box", "icon", "input", "textbox", "menu",
               "link", "tab", "checkbox"}

_STOP = _ARTICLES | _ROLE_NOUNS


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def match_score(description: str, name: str, role: str) -> float:
    """How well does an accessible node satisfy a spoken description?

    Returns 0.0-1.0. Name overlap carries 0.8; a matching role hint adds 0.2.
    A description that shares no meaningful token with the name scores zero, so
    we never click a confidently wrong thing.
    """
    desc_tokens = _tokens(description)
    name_tokens = set(_tokens(name))
    if not desc_tokens or not name_tokens:
        return 0.0

    role_clean = (role or "").lower()
    role_hint = 0.0
    for word in desc_tokens:
        if word in _ROLE_WORDS and role_clean in _ROLE_WORDS[word]:
            role_hint = 0.2
            break

    core = [t for t in desc_tokens if t not in _STOP]
    if not core:
        # The name IS a role noun — "the Menu button", "the Search field",
        # "the Files tab". Common, and stripping it left nothing to match on,
        # so fall back to everything that isn't pure filler.
        core = [t for t in desc_tokens if t not in _ARTICLES]
    if not core:
        return 0.0

    overlap = sum(1 for t in core if t in name_tokens) / len(core)
    if overlap == 0.0:
        return 0.0
    return min(1.0, overlap * 0.8 + role_hint)


@dataclass(frozen=True)
class Element:
    """A located UI element, in absolute screen coordinates.

    `states` carries AT-SPI state names (ENABLED, SHOWING, EDITABLE, …) so
    actionability can be judged without a second tree walk. Vision-sourced
    elements leave it empty — a picture cannot tell you whether a button is
    disabled, which is precisely why structure beats pixels.
    """
    name: str
    role: str
    left: int
    top: int
    width: int
    height: int
    source: str          # "atspi" | "cache" | "vision"
    states: frozenset = frozenset()
    value: str = ""

    @classmethod
    def from_bounds(cls, name: str, role: str, left: int, top: int,
                    width: int, height: int, source: str,
                    states: frozenset = frozenset(),
                    value: str = "") -> "Element":
        return cls(name=name, role=role, left=int(left), top=int(top),
                   width=int(width), height=int(height), source=source,
                   states=states, value=value)

    def has(self, state: str) -> bool:
        return state in self.states

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)

    @property
    def x(self) -> int:
        return self.left + self.width // 2

    @property
    def y(self) -> int:
        return self.top + self.height // 2

    @property
    def center(self) -> tuple[int, int]:
        return (self.x, self.y)


@dataclass(frozen=True)
class UINode:
    """One control as the platform reports it, before interpretation.

    Shared by every structural grounder — AT-SPI on Linux, UI Automation on
    Windows, the Accessibility API on macOS. `role` is the platform's own
    vocabulary; `actions.grounding.roles.normalize` canonicalises it.
    """
    name: str
    role: str
    left: int
    top: int
    width: int
    height: int
    states: frozenset = field(default_factory=frozenset)
    value: str = ""

    def has(self, state: str) -> bool:
        return state in self.states


@runtime_checkable
class Grounder(Protocol):
    """One way of locating an element. Implementations must never raise."""
    name: str

    def available(self) -> bool: ...
    def find(self, description: str) -> Element | None: ...
