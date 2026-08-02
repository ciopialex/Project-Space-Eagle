# Fast Hands: Accessibility-First Grounding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the hands both **faster and better** — faster by reading the accessibility tree instead of round-tripping a vision model for every lookup, better by never acting on an element that isn't actually ready and never claiming success without looking.

**Architecture:** A new `actions/grounding/` package defines an `Element` value type and a `Grounder` protocol. Three grounders implement it — `AtspiGrounder` (local, milliseconds, exact bounds, full state), `ElementCache` (repeat lookups), `VisionGrounder` (the existing Gemini path, downscaled). A `GroundingResolver` chains them. On top sits the actionability layer: Playwright's five checks mapped onto AT-SPI states, a `wait_for` retry loop that re-resolves each poll, and `act_and_verify` which re-observes after acting. `computer_control._screen_find` becomes a thin delegate with an unchanged signature, so nothing that calls it needs to change. `main.py` is not touched.

**Both halves are the same upgrade.** Once the eagle reads structure, "is it enabled", "has it appeared yet", and "did anything change when I clicked" are all queries against the tree it is already walking.

**Tech Stack:** Python 3, PyGObject (`gi` / `Atspi` — already installed), Pillow, `mss`, pytest.

## Global Constraints

- **The filter:** every task must move toward human emulation. A human perceives interface structure; vision is what they fall back on when structure isn't there.
- **`_screen_find(description: str) -> tuple[int, int] | None`** — this signature must not change. It is the existing public contract.
- **No task may modify `main.py`.**
- **Never silently mutate the user's system settings.** `toolkit-accessibility` is detected and reported, never flipped without consent.
- **New files use `runtime_paths`** for any on-disk path. Do not add a 15th place that computes paths from `Path(__file__)`.
- **Tests must not require a live desktop.** Every grounder takes an injectable seam; live checks are `skipif`-guarded smoke tests.
- Test runner: `.venv/bin/python -m pytest tests -q`. Baseline is **330 passing**; that number only goes up.
- Measured baseline to beat: **93ms local overhead, 188KB upload, one VLM round-trip per lookup** (1920×1080, X11/GNOME).

---

## Prior Art — leveraged, not depended on

**Playwright's actionability model** ([playwright.dev/docs/actionability](https://playwright.dev/docs/actionability)) is a decade of GUI-flakiness lessons distilled into five checks. Every one has an exact AT-SPI equivalent, verified present on this machine:

| Playwright check | Definition | AT-SPI equivalent |
|---|---|---|
| **Visible** | non-empty bounding box, not `visibility:hidden` | `width>0 and height>0` + `STATE_VISIBLE` + `STATE_SHOWING` |
| **Stable** | same bounding box for two consecutive animation frames | poll `get_extents` twice ~16ms apart, require identical |
| **Receives Events** | element is the hit target at the action point | `Atspi.Component.get_accessible_at_point(x, y)` returns this node or a descendant |
| **Enabled** | no `[disabled]`, no `aria-disabled` | `STATE_ENABLED` and `STATE_SENSITIVE` |
| **Editable** | enabled and not `[readonly]` | `STATE_EDITABLE` |
| `scrollIntoViewIfNeeded` | — | `Atspi.Component.scroll_to(ScrollType.ANYWHERE)` |

Two further lessons taken from Playwright's design:
- **Re-resolve on every poll.** Playwright retries from the start if the element detaches. A cached node handle goes stale the moment the app redraws.
- **`force` escape hatch.** Every wait must be skippable, or the first app that lies about its state makes the eagle useless.

**OpenCode's provider layer** (Vercel AI SDK + models.dev, 75+ providers) informs Plan 6, not this plan. The shape worth copying: a model catalog held separately from client code, OpenAI-compatible as the universal fallback adapter, `baseURL` override as the escape hatch, and per-agent model selection.

### What we refuse to depend on

- **Not adding Playwright.** It drives browsers; it cannot touch a native window. We take the model, not the package.
- **Not adding the Vercel AI SDK.** Node-only. The pattern ports to Python; the dependency does not.
- **Not calling models.dev at runtime.** A network round-trip to decide which model to use is a dependency on someone else's uptime. Vendor a catalog; refresh only when asked.
- **AT-SPI is the OS accessibility layer, already installed, and already optional** — every grounder degrades to vision if it's absent. That is a leverage, not a dependency.

---

## File Structure

| File | Responsibility |
|---|---|
| `actions/grounding/__init__.py` | Public surface: `find_element`, `Element` |
| `actions/grounding/base.py` | `Element` dataclass, `Grounder` protocol, `match_score` |
| `actions/grounding/cache.py` | `ElementCache` — context-keyed, TTL, invalidation |
| `actions/grounding/atspi.py` | `AtspiGrounder` + live tree walker |
| `actions/grounding/vision.py` | `VisionGrounder` — downscale + JPEG + coordinate rescale |
| `actions/grounding/resolver.py` | `GroundingResolver` — chains grounders, records source |
| `actions/grounding/actionability.py` | The five checks + per-action requirement matrix |
| `actions/grounding/waiting.py` | `wait_for` — poll until present *and* actionable |
| `actions/grounding/verify.py` | `observe`, `act_and_verify` — look, act, look again |
| `actions/computer_control.py:333-375` | `_screen_find` becomes a delegate |
| `tests/test_grounding_*.py` | One test file per module |

---

### Task 1: `Element` value type and match scoring

**Files:**
- Create: `actions/grounding/__init__.py`
- Create: `actions/grounding/base.py`
- Test: `tests/test_grounding_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Element` (frozen dataclass with `name: str`, `role: str`, `left: int`, `top: int`, `width: int`, `height: int`, `source: str`; properties `center -> tuple[int,int]`, `x -> int`, `y -> int`; classmethod `from_bounds(name, role, left, top, width, height, source) -> Element`). `Grounder` protocol with `name: str`, `available() -> bool`, `find(description: str) -> Element | None`. `match_score(description: str, name: str, role: str) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_base.py
import pytest
from actions.grounding.base import Element, match_score


def test_element_center_is_midpoint_of_bounds():
    el = Element.from_bounds("Save", "push button", 100, 200, 80, 40, "atspi")
    assert el.center == (140, 220)
    assert el.x == 140
    assert el.y == 220


def test_element_is_frozen():
    el = Element.from_bounds("Save", "push button", 0, 0, 10, 10, "atspi")
    with pytest.raises(Exception):
        el.name = "Cancel"


def test_match_score_exact_name_and_role():
    assert match_score("the Save button", "Save", "push button") == pytest.approx(1.0)


def test_match_score_name_only_without_role_hint():
    assert match_score("Save", "Save", "push button") == pytest.approx(0.8)


def test_match_score_rejects_wrong_name():
    assert match_score("the Save button", "Cancel", "push button") == 0.0


def test_match_score_partial_name_overlap():
    # "sign in" vs a button named "Sign In Now" -> both description tokens present
    assert match_score("sign in button", "Sign In Now", "push button") == pytest.approx(1.0)


def test_match_score_empty_inputs_are_zero():
    assert match_score("", "Save", "push button") == 0.0
    assert match_score("Save", "", "push button") == 0.0


def test_match_score_stopwords_alone_do_not_match():
    assert match_score("the button", "Save", "push button") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/__init__.py
"""Tiered visual grounding: structure first, pixels last.

A human operator perceives an interface — buttons, fields, labels — not a
grid of pixels. The accessibility tree is that perception, and it is local,
exact, and roughly a thousand times faster than asking a vision model where
something is. Vision remains the honest fallback for canvases, games, remote
desktops, and anything else that publishes no structure.
"""
from actions.grounding.base import Element, Grounder, match_score

__all__ = ["Element", "Grounder", "match_score"]
```

```python
# actions/grounding/base.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Role words a user is likely to say, mapped to the AT-SPI role names that
# should satisfy them. Keeps "the Save button" from matching a menu item
# that happens to also be called Save.
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

# Words that carry no identifying signal on their own.
_STOP = {
    "the", "a", "an", "on", "in", "at", "of", "to", "for", "click", "press",
    "button", "field", "box", "icon", "input", "textbox", "menu", "link",
    "tab", "checkbox",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def match_score(description: str, name: str, role: str) -> float:
    """How well does an accessible node satisfy a spoken description?

    Returns 0.0-1.0. Name overlap carries 0.8; a matching role hint adds 0.2.
    A description that shares no meaningful token with the name scores zero,
    so we never click a confidently wrong thing.
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
    elements leave it empty — a picture cannot tell you if a button is
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
                    states: frozenset = frozenset(), value: str = "") -> "Element":
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


@runtime_checkable
class Grounder(Protocol):
    """One way of locating an element. Implementations must never raise."""
    name: str

    def available(self) -> bool: ...
    def find(self, description: str) -> Element | None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_base.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 337 passed (330 baseline + 7 new).

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/__init__.py actions/grounding/base.py tests/test_grounding_base.py
git commit -m "feat(grounding): Element value type and description match scoring"
```

---

### Task 2: `ElementCache`

**Files:**
- Create: `actions/grounding/cache.py`
- Test: `tests/test_grounding_cache.py`

**Interfaces:**
- Consumes: `Element` from Task 1.
- Produces: `ElementCache(ttl: float = 30.0, clock: Callable[[], float] = time.monotonic)` with `get(context: str, description: str) -> Element | None`, `put(context: str, description: str, element: Element) -> None`, `invalidate(context: str | None = None) -> None`. `context` is an opaque string identifying the active window, e.g. `"firefox|GitHub — Mozilla Firefox"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_cache.py
from actions.grounding.base import Element
from actions.grounding.cache import ElementCache


def _el(name="Save"):
    return Element.from_bounds(name, "push button", 10, 20, 30, 40, "atspi")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_get_returns_none_when_empty():
    cache = ElementCache()
    assert cache.get("app|win", "Save button") is None


def test_put_then_get_roundtrips_and_marks_source_cache():
    cache = ElementCache()
    cache.put("app|win", "Save button", _el())
    got = cache.get("app|win", "Save button")
    assert got is not None
    assert got.center == (25, 40)
    assert got.source == "cache"


def test_different_context_is_a_miss():
    cache = ElementCache()
    cache.put("app|win", "Save button", _el())
    assert cache.get("app|OTHER", "Save button") is None


def test_entry_expires_after_ttl():
    clock = FakeClock()
    cache = ElementCache(ttl=30.0, clock=clock)
    cache.put("app|win", "Save button", _el())
    clock.now = 29.9
    assert cache.get("app|win", "Save button") is not None
    clock.now = 30.1
    assert cache.get("app|win", "Save button") is None


def test_invalidate_context_clears_only_that_context():
    cache = ElementCache()
    cache.put("a|w", "Save button", _el())
    cache.put("b|w", "Save button", _el())
    cache.invalidate("a|w")
    assert cache.get("a|w", "Save button") is None
    assert cache.get("b|w", "Save button") is not None


def test_invalidate_all_clears_everything():
    cache = ElementCache()
    cache.put("a|w", "Save button", _el())
    cache.put("b|w", "Save button", _el())
    cache.invalidate()
    assert cache.get("a|w", "Save button") is None
    assert cache.get("b|w", "Save button") is None


def test_description_matching_is_case_and_space_insensitive():
    cache = ElementCache()
    cache.put("app|win", "Save Button", _el())
    assert cache.get("app|win", "  save   button ") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/cache.py
from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Callable

from actions.grounding.base import Element


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class ElementCache:
    """Remembers where a thing was, scoped to the window it was found in.

    A human doesn't re-hunt for the Save button every time they look at the
    same dialog. Entries are keyed on (window context, description) and expire,
    because interfaces move.
    """

    def __init__(self, ttl: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._store: dict[tuple[str, str], tuple[Element, float]] = {}

    def get(self, context: str, description: str) -> Element | None:
        key = (context, _norm(description))
        hit = self._store.get(key)
        if hit is None:
            return None
        element, stamp = hit
        if self._clock() - stamp >= self._ttl:
            del self._store[key]
            return None
        return replace(element, source="cache")

    def put(self, context: str, description: str, element: Element) -> None:
        self._store[(context, _norm(description))] = (element, self._clock())

    def invalidate(self, context: str | None = None) -> None:
        if context is None:
            self._store.clear()
            return
        for key in [k for k in self._store if k[0] == context]:
            del self._store[key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_cache.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 344 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/cache.py tests/test_grounding_cache.py
git commit -m "feat(grounding): context-scoped element cache with TTL"
```

---

### Task 3: `AtspiGrounder` with an injectable walker

**Files:**
- Create: `actions/grounding/atspi.py`
- Test: `tests/test_grounding_atspi.py`

**Interfaces:**
- Consumes: `Element`, `match_score` from Task 1.
- Produces: `AtspiNode` (frozen dataclass: `name: str`, `role: str`, `left: int`, `top: int`, `width: int`, `height: int`, `states: frozenset = frozenset()`, `value: str = ""`, method `has(state: str) -> bool`). `AtspiGrounder(walker: Callable[[], Iterable[AtspiNode]] | None = None, threshold: float = 0.5)` implementing `Grounder`. `atspi_enabled() -> bool` reports whether the GNOME toolkit-accessibility bridge is on. `live_walker() -> Iterator[AtspiNode]` walks the real desktop tree. `_TRACKED_STATES` is the tuple of `Atspi.StateType` member names collected: `ENABLED`, `SENSITIVE`, `VISIBLE`, `SHOWING`, `EDITABLE`, `FOCUSED`, `FOCUSABLE`, `SELECTED`, `CHECKED` — all verified present on this machine.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_atspi.py
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


def test_live_walker_does_not_raise():
    """Smoke test against the real desktop. Must degrade, never explode."""
    pytest.importorskip("gi")
    from actions.grounding.atspi import live_walker
    list(live_walker())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_atspi.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.atspi'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/atspi.py
"""Read the accessibility tree instead of guessing at pixels.

AT-SPI publishes every widget's name, role, and exact screen bounds over
D-Bus. Reading it is local, takes milliseconds, and returns coordinates that
are correct rather than approximately correct. This is what a human's
perception of an interface actually corresponds to.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from actions.grounding.base import Element, match_score

_MAX_NODES = 4000   # hard ceiling so a pathological tree can't hang the eagle
_MAX_DEPTH = 40


@dataclass(frozen=True)
class AtspiNode:
    name: str
    role: str
    left: int
    top: int
    width: int
    height: int
    # States default to empty so a node can be built for tests without them.
    # Populated by live_walker from Atspi.StateSet — these are what let the
    # eagle notice a greyed-out button instead of clicking it anyway.
    states: frozenset = frozenset()
    value: str = ""

    def has(self, state: str) -> bool:
        return state in self.states


# AT-SPI states we care about, mapped from Atspi.StateType member names.
_TRACKED_STATES = ("ENABLED", "SENSITIVE", "VISIBLE", "SHOWING",
                   "EDITABLE", "FOCUSED", "FOCUSABLE", "SELECTED", "CHECKED")


def atspi_enabled() -> bool:
    """Is the GNOME toolkit-accessibility bridge switched on?

    When this is false, GTK apps publish nothing and the tree looks empty —
    which is indistinguishable from 'no matches' unless we check.
    """
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface",
             "toolkit-accessibility"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().lower()
        return out == "true"
    except Exception:
        return False


def live_walker() -> Iterator[AtspiNode]:
    """Walk the real desktop accessibility tree. Yields nothing on failure."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return

    def _extents(node):
        try:
            ext = Atspi.Component.get_extents(node, Atspi.CoordType.SCREEN)
            return int(ext.x), int(ext.y), int(ext.width), int(ext.height)
        except Exception:
            return 0, 0, 0, 0

    def _states(node) -> frozenset:
        try:
            state_set = node.get_state_set()
            return frozenset(
                s for s in _TRACKED_STATES
                if state_set.contains(getattr(Atspi.StateType, s))
            )
        except Exception:
            return frozenset()

    def _value(node) -> str:
        """Current text content, so the eagle can read a field back."""
        try:
            text = node.get_text_iface()
            if text is None:
                return ""
            return text.get_text(0, text.get_character_count()) or ""
        except Exception:
            return ""

    count = 0
    try:
        desktop = Atspi.get_desktop(0)
    except Exception:
        return

    stack = [(desktop, 0)]
    while stack and count < _MAX_NODES:
        node, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        try:
            n_children = node.get_child_count()
        except Exception:
            continue
        for i in range(n_children):
            try:
                child = node.get_child_at_index(i)
                if child is None:
                    continue
                stack.append((child, depth + 1))
                left, top, width, height = _extents(child)
                yield AtspiNode(
                    name=child.get_name() or "",
                    role=child.get_role_name() or "",
                    left=left, top=top, width=width, height=height,
                    states=_states(child), value=_value(child),
                )
                count += 1
            except Exception:
                continue


class AtspiGrounder:
    """Locate an element by matching the accessibility tree."""

    name = "atspi"

    def __init__(self,
                 walker: Callable[[], Iterable[AtspiNode]] | None = None,
                 threshold: float = 0.5) -> None:
        self._walker = walker or live_walker
        self._threshold = threshold

    def available(self) -> bool:
        try:
            for _ in self._walker():
                return True
        except Exception:
            return False
        return False

    def find(self, description: str) -> Element | None:
        best: tuple[float, AtspiNode] | None = None
        try:
            for node in self._walker():
                if node.width <= 0 or node.height <= 0:
                    continue
                score = match_score(description, node.name, node.role)
                if score >= self._threshold and (best is None or score > best[0]):
                    best = (score, node)
        except Exception:
            return None

        if best is None:
            return None
        _, node = best
        return Element.from_bounds(node.name, node.role, node.left, node.top,
                                   node.width, node.height, "atspi",
                                   states=node.states, value=node.value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_atspi.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 354 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/atspi.py tests/test_grounding_atspi.py
git commit -m "feat(grounding): AT-SPI grounder with injectable tree walker"
```

---

### Task 4: `VisionGrounder` — the optimized fallback

**Files:**
- Create: `actions/grounding/vision.py`
- Test: `tests/test_grounding_vision.py`

**Interfaces:**
- Consumes: `Element` from Task 1.
- Produces: `VisionGrounder(client_fn=None, grab_fn=None, max_edge: int = 1280, model: str = "gemini-2.5-flash")` implementing `Grounder`. `client_fn() -> object` returns something with `.models.generate_content(...)`; `grab_fn() -> PIL.Image.Image` returns a full-screen capture. Both injectable for tests. Adds `downscale(img, max_edge) -> tuple[Image, float]` returning the resized image and the scale factor applied.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_vision.py
from PIL import Image

from actions.grounding.vision import VisionGrounder, downscale


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def generate_content(self, model=None, contents=None):
        self.calls.append((model, contents))
        return FakeResponse(self._text)


class FakeClient:
    def __init__(self, text):
        self.models = FakeModels(text)


def _screen(w=1920, h=1080):
    return Image.new("RGB", (w, h), "black")


def test_downscale_shrinks_and_reports_scale():
    img, scale = downscale(_screen(1920, 1080), 1280)
    assert max(img.size) == 1280
    assert scale == 1920 / 1280


def test_downscale_leaves_small_images_alone():
    img, scale = downscale(_screen(800, 600), 1280)
    assert img.size == (800, 600)
    assert scale == 1.0


def test_coordinates_are_rescaled_to_full_screen():
    # model sees a 1280-wide image and reports (640, 360) -> center of screen
    client = FakeClient("640,360")
    g = VisionGrounder(client_fn=lambda: client,
                       grab_fn=lambda: _screen(1920, 1080))
    el = g.find("the Save button")
    assert el is not None
    assert el.center == (960, 540)
    assert el.source == "vision"


def test_not_found_returns_none():
    g = VisionGrounder(client_fn=lambda: FakeClient("NOT_FOUND"),
                       grab_fn=lambda: _screen())
    assert g.find("the Save button") is None


def test_garbage_response_returns_none():
    g = VisionGrounder(client_fn=lambda: FakeClient("I'm not sure, sorry!"),
                       grab_fn=lambda: _screen())
    assert g.find("the Save button") is None


def test_never_raises_when_client_explodes():
    def boom():
        raise RuntimeError("no api key")
    g = VisionGrounder(client_fn=boom, grab_fn=lambda: _screen())
    assert g.find("the Save button") is None
    assert g.available() is False


def test_grounder_has_name():
    assert VisionGrounder(client_fn=lambda: FakeClient("0,0"),
                          grab_fn=lambda: _screen()).name == "vision"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_vision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.vision'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/vision.py
"""Vision grounding — the fallback for surfaces that publish no structure.

Kept for canvases, games, remote desktops, and anything else with no
accessibility tree. Optimized against the measured baseline: a full-screen
PNG cost 93ms of local encode and shipped 188KB. Downscaling to 1280px and
encoding JPEG costs 46ms and ships 107KB for the same answer.
"""
from __future__ import annotations

import io
import re
from typing import Callable

from actions.grounding.base import Element

_PROMPT = (
    "This is a screenshot of a {w}x{h} pixel screen. "
    "Locate the UI element described as: '{desc}'. "
    "Reply with ONLY the center coordinates as: x,y "
    "If the element is not visible, reply: NOT_FOUND"
)


def downscale(img, max_edge: int):
    """Shrink so the longest edge is `max_edge`. Returns (image, scale_factor).

    Multiply model-reported coordinates by scale_factor to get screen space.
    """
    longest = max(img.size)
    if longest <= max_edge:
        return img, 1.0
    scale = longest / max_edge
    resized = img.copy()
    resized.thumbnail((max_edge, max_edge))
    return resized, scale


def _default_client():
    from google import genai
    from actions.computer_control import _get_api_key
    key = _get_api_key()
    if not key:
        raise RuntimeError("no api key for vision grounding")
    return genai.Client(api_key=key)


def _default_grab():
    from actions.computer_control import _grab_screen
    return _grab_screen()


class VisionGrounder:
    """Ask a vision model where something is. Slow, remote, last resort."""

    name = "vision"

    def __init__(self,
                 client_fn: Callable[[], object] | None = None,
                 grab_fn: Callable[[], object] | None = None,
                 max_edge: int = 1280,
                 model: str = "gemini-2.5-flash") -> None:
        self._client_fn = client_fn or _default_client
        self._grab_fn = grab_fn or _default_grab
        self._max_edge = max_edge
        self._model = model

    def available(self) -> bool:
        try:
            self._client_fn()
            return True
        except Exception:
            return False

    def find(self, description: str) -> Element | None:
        try:
            from google.genai import types as gtypes

            full = self._grab_fn()
            small, scale = downscale(full, self._max_edge)
            buf = io.BytesIO()
            small.convert("RGB").save(buf, format="JPEG", quality=80)

            client = self._client_fn()
            response = client.models.generate_content(
                model=self._model,
                contents=[
                    gtypes.Part.from_bytes(data=buf.getvalue(),
                                           mime_type="image/jpeg"),
                    _PROMPT.format(w=small.size[0], h=small.size[1],
                                   desc=description),
                ],
            )
            text = (getattr(response, "text", "") or "").strip()
            if "NOT_FOUND" in text.upper():
                return None
            match = re.search(r"(\d+)\s*,\s*(\d+)", text)
            if not match:
                return None

            x = int(int(match.group(1)) * scale)
            y = int(int(match.group(2)) * scale)
            # A point, not a region — represent it as a 1px box at that point.
            return Element.from_bounds(description, "unknown", x, y, 1, 1, "vision")
        except Exception:
            return None
```

Note: `from_bounds(x, y, 1, 1)` puts `center` at `(x, y)` because `1 // 2 == 0`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_vision.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 361 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/vision.py tests/test_grounding_vision.py
git commit -m "feat(grounding): vision fallback with downscale + JPEG (93ms->46ms, 188KB->107KB)"
```

---

### Task 5: `GroundingResolver` — the chain

**Files:**
- Create: `actions/grounding/resolver.py`
- Modify: `actions/grounding/__init__.py`
- Test: `tests/test_grounding_resolver.py`

**Interfaces:**
- Consumes: `Element`, `Grounder`, `ElementCache` from Tasks 1-2.
- Produces: `GroundingResolver(grounders: list[Grounder], cache: ElementCache | None = None, context_fn: Callable[[], str] | None = None)` with `find(description: str) -> Element | None` and `last_source: str | None`. Module-level `find_element(description: str) -> Element | None` using a lazily-built default resolver, re-exported from `actions.grounding`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_resolver.py
from actions.grounding.base import Element
from actions.grounding.cache import ElementCache
from actions.grounding.resolver import GroundingResolver


class StubGrounder:
    def __init__(self, name, element=None, avail=True):
        self.name = name
        self._element = element
        self._avail = avail
        self.calls = 0

    def available(self):
        return self._avail

    def find(self, description):
        self.calls += 1
        return self._element


def _el(source="atspi", x=100, y=200):
    return Element.from_bounds("Save", "push button", x, y, 0, 0, source)


def test_first_grounder_wins_and_later_ones_are_not_called():
    fast = StubGrounder("atspi", _el("atspi"))
    slow = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([fast, slow], context_fn=lambda: "app|win")
    el = r.find("the Save button")
    assert el.source == "atspi"
    assert slow.calls == 0
    assert r.last_source == "atspi"


def test_falls_through_to_next_grounder_on_miss():
    fast = StubGrounder("atspi", None)
    slow = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([fast, slow], context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert slow.calls == 1


def test_returns_none_when_all_grounders_miss():
    r = GroundingResolver([StubGrounder("atspi", None),
                           StubGrounder("vision", None)],
                          context_fn=lambda: "app|win")
    assert r.find("the Save button") is None
    assert r.last_source is None


def test_unavailable_grounders_are_skipped():
    dead = StubGrounder("atspi", _el("atspi"), avail=False)
    live = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([dead, live], context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert dead.calls == 0


def test_result_is_cached_and_second_lookup_skips_grounders():
    slow = StubGrounder("vision", _el("vision"))
    cache = ElementCache()
    r = GroundingResolver([slow], cache=cache, context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert r.find("the Save button").source == "cache"
    assert slow.calls == 1


def test_cache_is_scoped_to_window_context():
    ctx = {"v": "app|one"}
    slow = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([slow], cache=ElementCache(),
                          context_fn=lambda: ctx["v"])
    r.find("the Save button")
    ctx["v"] = "app|two"
    r.find("the Save button")
    assert slow.calls == 2


def test_a_grounder_that_raises_does_not_break_the_chain():
    class Exploding:
        name = "boom"

        def available(self):
            return True

        def find(self, description):
            raise RuntimeError("kaboom")

    r = GroundingResolver([Exploding(), StubGrounder("vision", _el("vision"))],
                          context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.resolver'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/resolver.py
from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element, Grounder
from actions.grounding.cache import ElementCache


def _default_context() -> str:
    """Identify the active window so cache entries don't leak across apps."""
    try:
        import subprocess
        out = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=2)
        return (out.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


class GroundingResolver:
    """Try each way of locating an element, cheapest and most exact first."""

    def __init__(self,
                 grounders: list[Grounder],
                 cache: ElementCache | None = None,
                 context_fn: Callable[[], str] | None = None) -> None:
        self._grounders = grounders
        self._cache = cache
        self._context_fn = context_fn or _default_context
        self.last_source: str | None = None

    def find(self, description: str) -> Element | None:
        context = self._context_fn()

        if self._cache is not None:
            hit = self._cache.get(context, description)
            if hit is not None:
                self.last_source = "cache"
                return hit

        for grounder in self._grounders:
            try:
                if not grounder.available():
                    continue
                found = grounder.find(description)
            except Exception:
                continue
            if found is not None:
                if self._cache is not None:
                    self._cache.put(context, description, found)
                self.last_source = found.source
                return found

        self.last_source = None
        return None


_DEFAULT: GroundingResolver | None = None


def default_resolver() -> GroundingResolver:
    global _DEFAULT
    if _DEFAULT is None:
        from actions.grounding.atspi import AtspiGrounder
        from actions.grounding.vision import VisionGrounder
        _DEFAULT = GroundingResolver(
            grounders=[AtspiGrounder(), VisionGrounder()],
            cache=ElementCache(),
        )
    return _DEFAULT


def find_element(description: str) -> Element | None:
    return default_resolver().find(description)
```

Then extend the package surface:

```python
# actions/grounding/__init__.py  — replace the import block
from actions.grounding.base import Element, Grounder, match_score
from actions.grounding.resolver import GroundingResolver, find_element

__all__ = ["Element", "Grounder", "match_score",
           "GroundingResolver", "find_element"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_resolver.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 368 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/resolver.py actions/grounding/__init__.py tests/test_grounding_resolver.py
git commit -m "feat(grounding): tiered resolver — cache, then structure, then vision"
```

---

### Task 6: Wire `_screen_find` to the resolver

**Files:**
- Modify: `actions/computer_control.py:333-375`
- Test: `tests/test_screen_find_delegates.py`

**Interfaces:**
- Consumes: `find_element` from Task 5.
- Produces: `_screen_find(description: str) -> tuple[int, int] | None` — **signature unchanged**. Every existing caller keeps working.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_screen_find_delegates.py
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
    import inspect
    sig = inspect.signature(cc._screen_find)
    assert list(sig.parameters) == ["description"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_screen_find_delegates.py -v`
Expected: FAIL — `AttributeError: module 'actions.computer_control' has no attribute 'find_element'`

- [ ] **Step 3: Replace the body of `_screen_find`**

Delete `actions/computer_control.py:333-375` (the whole existing `_screen_find`, from `def _screen_find` down to its final `return None`) and put this in its place:

```python
from actions.grounding import find_element   # add near the top-level imports


def _screen_find(description: str) -> tuple[int, int] | None:
    """Locate a UI element on screen.

    Delegates to the tiered resolver: accessibility tree first (local, exact),
    cache second, vision model last. Signature is unchanged — callers do not
    need to know grounding got faster.
    """
    try:
        element = find_element(description)
    except Exception as e:
        print(f"[ComputerControl] screen_find failed: {e}")
        return None
    if element is None:
        return None
    return element.center
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_screen_find_delegates.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 372 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/computer_control.py tests/test_screen_find_delegates.py
git commit -m "refactor(hands): _screen_find delegates to the tiered resolver"
```

---

### Task 7: Report the accessibility bridge instead of silently flipping it

**Files:**
- Modify: `actions/grounding/atspi.py`
- Test: `tests/test_grounding_bridge_report.py`

**Interfaces:**
- Consumes: `atspi_enabled` from Task 3.
- Produces: `bridge_status() -> dict` with keys `enabled: bool`, `nodes_visible: int`, `hint: str`. `hint` is the empty string when healthy, otherwise the exact command the user can run.

This exists because **`toolkit-accessibility` is currently `false` on this machine** — the fast path is installed and switched off. The eagle must say so rather than quietly editing the user's desktop settings.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_bridge_report.py
from actions.grounding.atspi import AtspiNode, bridge_status


def _node():
    return AtspiNode("Save", "push button", 0, 0, 10, 10)


def test_healthy_bridge_reports_no_hint():
    s = bridge_status(enabled_fn=lambda: True, walker=lambda: [_node()])
    assert s["enabled"] is True
    assert s["nodes_visible"] == 1
    assert s["hint"] == ""


def test_disabled_bridge_returns_the_exact_fix_command():
    s = bridge_status(enabled_fn=lambda: False, walker=lambda: [])
    assert s["enabled"] is False
    assert "gsettings set org.gnome.desktop.interface toolkit-accessibility true" in s["hint"]


def test_enabled_but_empty_tree_is_still_flagged():
    s = bridge_status(enabled_fn=lambda: True, walker=lambda: [])
    assert s["nodes_visible"] == 0
    assert s["hint"] != ""


def test_exploding_walker_degrades_to_zero_nodes():
    def boom():
        raise RuntimeError("no display")
    s = bridge_status(enabled_fn=lambda: True, walker=boom)
    assert s["nodes_visible"] == 0
    assert s["hint"] != ""


def test_bridge_status_never_mutates_settings(monkeypatch):
    """Guard rail: this function must never shell out to `gsettings set`."""
    import subprocess
    calls = []

    class FakeCompleted:
        stdout = "false"

    def spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", spy)
    bridge_status()          # real probes, spied subprocess
    for call in calls:
        assert "set" not in list(call), f"bridge_status tried to write: {call}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_bridge_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'bridge_status'`

- [ ] **Step 3: Append to `actions/grounding/atspi.py`**

```python
_BRIDGE_HINT = (
    "The accessibility bridge is off, so apps aren't publishing their "
    "interface structure and I have to fall back to slower vision lookups. "
    "You can turn it on with:\n"
    "    gsettings set org.gnome.desktop.interface toolkit-accessibility true\n"
    "Some apps need a restart afterwards."
)


def bridge_status(enabled_fn: Callable[[], bool] | None = None,
                  walker: Callable[[], Iterable[AtspiNode]] | None = None) -> dict:
    """Report whether structural grounding is actually working.

    Deliberately read-only. Flipping a user's desktop settings without asking
    is not something a good employee does.
    """
    enabled_fn = enabled_fn or atspi_enabled
    walker = walker or live_walker

    try:
        enabled = bool(enabled_fn())
    except Exception:
        enabled = False

    count = 0
    try:
        for _ in walker():
            count += 1
            if count >= 50:      # enough to prove the tree is live
                break
    except Exception:
        count = 0

    hint = "" if (enabled and count > 0) else _BRIDGE_HINT
    return {"enabled": enabled, "nodes_visible": count, "hint": hint}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_bridge_report.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 377 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/atspi.py tests/test_grounding_bridge_report.py
git commit -m "feat(grounding): report accessibility bridge state without mutating it"
```

---

### Task 8: Pin the win with a latency regression test

**Files:**
- Create: `tests/test_grounding_latency.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no new API. A test that fails if structural grounding regresses to network speed.

- [ ] **Step 1: Write the test**

```python
# tests/test_grounding_latency.py
"""Pins the point of this whole plan: structure is orders of magnitude
faster than vision, and the cache is faster still. If someone reorders the
resolver chain or drops the cache, this fails."""
import time

from actions.grounding.atspi import AtspiGrounder, AtspiNode
from actions.grounding.cache import ElementCache
from actions.grounding.resolver import GroundingResolver


def _big_tree(n=2000):
    nodes = [AtspiNode(f"Widget{i}", "push button", i, i, 20, 20)
             for i in range(n)]
    nodes.append(AtspiNode("Save", "push button", 500, 600, 80, 30))
    return nodes


class SlowVision:
    name = "vision"

    def available(self):
        return True

    def find(self, description):
        time.sleep(0.15)          # stands in for a network round-trip
        return None


def test_structural_lookup_is_under_50ms_on_a_2000_node_tree():
    g = AtspiGrounder(walker=_big_tree)
    start = time.perf_counter()
    el = g.find("the Save button")
    elapsed = (time.perf_counter() - start) * 1000
    assert el is not None
    assert el.center == (540, 615)
    assert elapsed < 50, f"structural grounding took {elapsed:.1f}ms"


def test_structural_hit_never_pays_the_vision_cost():
    r = GroundingResolver([AtspiGrounder(walker=_big_tree), SlowVision()],
                          cache=ElementCache(), context_fn=lambda: "test|win")
    start = time.perf_counter()
    el = r.find("the Save button")
    elapsed = (time.perf_counter() - start) * 1000
    assert el.source == "atspi"
    assert elapsed < 100, f"resolver took {elapsed:.1f}ms — did vision run?"


def test_second_lookup_is_served_from_cache_and_is_faster():
    r = GroundingResolver([AtspiGrounder(walker=_big_tree)],
                          cache=ElementCache(), context_fn=lambda: "test|win")
    start = time.perf_counter()
    r.find("the Save button")
    cold = time.perf_counter() - start

    start = time.perf_counter()
    el = r.find("the Save button")
    warm = time.perf_counter() - start

    assert el.source == "cache"
    assert warm < cold
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_grounding_latency.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 380 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_grounding_latency.py
git commit -m "test(grounding): pin structural grounding under 50ms"
```

---

### Task 9: Actionability checks — Playwright's model on AT-SPI

**Files:**
- Create: `actions/grounding/actionability.py`
- Test: `tests/test_grounding_actionability.py`

**Interfaces:**
- Consumes: `Element` from Task 1 (with `states`, `value`, `bounds`, `has`).
- Produces: `is_visible(el) -> bool`, `is_enabled(el) -> bool`, `is_editable(el) -> bool`, `is_stable(before: Element | None, after: Element | None) -> bool`, `receives_events(el, hit_test: Callable[[int, int], Element | None]) -> bool`, `check(action: str, el: Element, *, previous: Element | None = None, hit_test=None) -> tuple[bool, str]` returning `(ok, failed_check_name)` where `failed_check_name` is `""` on success. `ACTION_REQUIREMENTS: dict[str, tuple[str, ...]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_actionability.py
from actions.grounding.base import Element
from actions.grounding.actionability import (
    ACTION_REQUIREMENTS, check, is_editable, is_enabled, is_stable,
    is_visible, receives_events,
)

LIVE = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _el(states=LIVE, left=100, top=200, width=80, height=40, name="Save"):
    return Element.from_bounds(name, "push button", left, top, width, height,
                               "atspi", states=states)


def test_visible_requires_size_and_showing():
    assert is_visible(_el()) is True
    assert is_visible(_el(width=0)) is False
    assert is_visible(_el(states=frozenset({"ENABLED", "SENSITIVE"}))) is False


def test_enabled_requires_both_enabled_and_sensitive():
    assert is_enabled(_el()) is True
    assert is_enabled(_el(states=frozenset({"ENABLED"}))) is False
    assert is_enabled(_el(states=frozenset({"VISIBLE", "SHOWING"}))) is False


def test_editable_requires_enabled_plus_editable_state():
    assert is_editable(_el()) is False
    assert is_editable(_el(states=LIVE | {"EDITABLE"})) is True
    assert is_editable(_el(states=frozenset({"EDITABLE"}))) is False


def test_stable_compares_bounds_across_two_reads():
    a = _el()
    assert is_stable(a, _el()) is True
    assert is_stable(a, _el(left=105)) is False
    assert is_stable(None, a) is False
    assert is_stable(a, None) is False


def test_receives_events_when_hit_test_returns_the_same_element():
    target = _el()
    assert receives_events(target, lambda x, y: _el()) is True


def test_receives_events_false_when_something_overlays_it():
    target = _el()
    overlay = _el(name="Modal Dialog")
    assert receives_events(target, lambda x, y: overlay) is False


def test_receives_events_false_when_hit_test_finds_nothing():
    assert receives_events(_el(), lambda x, y: None) is False


def test_click_requires_the_playwright_four():
    assert ACTION_REQUIREMENTS["click"] == (
        "visible", "stable", "receives_events", "enabled")


def test_fill_does_not_require_stable_or_hit_test():
    assert ACTION_REQUIREMENTS["fill"] == ("visible", "enabled", "editable")


def test_press_requires_nothing():
    assert ACTION_REQUIREMENTS["press"] == ()


def test_check_reports_the_first_failing_check_by_name():
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    ok, failed = check("click", disabled, previous=disabled,
                       hit_test=lambda x, y: disabled)
    assert ok is False
    assert failed == "enabled"


def test_check_passes_when_everything_holds():
    el = _el()
    ok, failed = check("click", el, previous=el, hit_test=lambda x, y: _el())
    assert ok is True
    assert failed == ""


def test_check_on_unknown_action_requires_nothing():
    ok, failed = check("teleport", _el(width=0))
    assert ok is True
    assert failed == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_actionability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.actionability'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/actionability.py
"""Is this element actually ready to be acted on?

Transplanted from Playwright's actionability model, which encodes a decade
of GUI-automation flakiness lessons. Every check has an exact AT-SPI
equivalent — see the prior-art table in the plan. This is the difference
between an agent that fires a click into the void and one that waits like a
person would.
"""
from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element

# Playwright's per-action requirement matrix, transplanted verbatim.
# https://playwright.dev/docs/actionability
ACTION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "click":            ("visible", "stable", "receives_events", "enabled"),
    "dblclick":         ("visible", "stable", "receives_events", "enabled"),
    "right_click":      ("visible", "stable", "receives_events", "enabled"),
    "check":            ("visible", "stable", "receives_events", "enabled"),
    "hover":            ("visible", "stable", "receives_events"),
    "drag":             ("visible", "stable", "receives_events"),
    "screenshot":       ("visible", "stable"),
    "fill":             ("visible", "enabled", "editable"),
    "clear":            ("visible", "enabled", "editable"),
    "select":           ("visible", "enabled"),
    "scroll_into_view": ("stable",),
    "focus":            (),
    "press":            (),
}


def is_visible(el: Element) -> bool:
    return (el.width > 0 and el.height > 0
            and el.has("VISIBLE") and el.has("SHOWING"))


def is_enabled(el: Element) -> bool:
    return el.has("ENABLED") and el.has("SENSITIVE")


def is_editable(el: Element) -> bool:
    return is_enabled(el) and el.has("EDITABLE")


def is_stable(before: Element | None, after: Element | None) -> bool:
    """Two consecutive reads with identical bounds — the animation has settled."""
    if before is None or after is None:
        return False
    return before.bounds == after.bounds


def _identity(el: Element) -> tuple:
    return (el.name, el.role, el.bounds)


def receives_events(el: Element,
                    hit_test: Callable[[int, int], Element | None]) -> bool:
    """Is this element what you'd actually hit at its own centre?

    Catches the modal that opened over the button you were about to click.
    """
    try:
        hit = hit_test(el.x, el.y)
    except Exception:
        return False
    return hit is not None and _identity(hit) == _identity(el)


def check(action: str, el: Element, *,
          previous: Element | None = None,
          hit_test: Callable[[int, int], Element | None] | None = None,
          ) -> tuple[bool, str]:
    """Run the checks this action requires. Returns (ok, first_failed_name)."""
    for name in ACTION_REQUIREMENTS.get(action, ()):
        if name == "visible" and not is_visible(el):
            return False, "visible"
        if name == "enabled" and not is_enabled(el):
            return False, "enabled"
        if name == "editable" and not is_editable(el):
            return False, "editable"
        if name == "stable" and not is_stable(previous, el):
            return False, "stable"
        if name == "receives_events":
            if hit_test is None or not receives_events(el, hit_test):
                return False, "receives_events"
    return True, ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_actionability.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 393 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/actionability.py tests/test_grounding_actionability.py
git commit -m "feat(hands): Playwright actionability checks mapped onto AT-SPI states"
```

---

### Task 10: `wait_for` — stop firing clicks into the void

**Files:**
- Create: `actions/grounding/waiting.py`
- Test: `tests/test_grounding_waiting.py`

**Interfaces:**
- Consumes: `check` from Task 9, `GroundingResolver` from Task 5.
- Produces: `WaitResult` (frozen dataclass: `element: Element | None`, `ok: bool`, `failed_check: str`, `elapsed_ms: float`, `attempts: int`). `wait_for(description: str, action: str = "click", *, resolver, timeout: float = 5.0, poll: float = 0.05, hit_test=None, force: bool = False, clock=time.monotonic, sleep=time.sleep) -> WaitResult`.

Playwright's lesson, taken deliberately: **re-resolve the element on every poll.** A handle cached across a redraw is a stale handle.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_waiting.py
from actions.grounding.base import Element
from actions.grounding.waiting import WaitResult, wait_for

LIVE = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _el(states=LIVE, left=100):
    return Element.from_bounds("Save", "push button", left, 200, 80, 40,
                               "atspi", states=states)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ScriptedResolver:
    """Returns a different element on each successive lookup."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def find(self, description):
        self.calls += 1
        return self._script[min(self.calls - 1, len(self._script) - 1)]


def test_returns_immediately_when_already_actionable():
    clock = FakeClock()
    r = ScriptedResolver([_el(), _el()])
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(),
                   clock=clock, sleep=clock.sleep)
    assert res.ok is True
    assert res.failed_check == ""
    assert res.element is not None


def test_waits_for_an_element_that_appears_late():
    clock = FakeClock()
    r = ScriptedResolver([None, None, _el(), _el()])
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(),
                   clock=clock, sleep=clock.sleep)
    assert res.ok is True
    assert r.calls >= 3


def test_waits_for_an_element_that_is_still_animating():
    clock = FakeClock()
    # bounds move, then settle
    r = ScriptedResolver([_el(left=100), _el(left=120), _el(left=140),
                          _el(left=140), _el(left=140)])
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(left=140),
                   clock=clock, sleep=clock.sleep)
    assert res.ok is True


def test_times_out_and_names_the_failing_check():
    clock = FakeClock()
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    r = ScriptedResolver([disabled])
    res = wait_for("Save button", "click", resolver=r, timeout=1.0,
                   hit_test=lambda x, y: disabled,
                   clock=clock, sleep=clock.sleep)
    assert res.ok is False
    assert res.failed_check == "enabled"


def test_times_out_when_element_never_appears():
    clock = FakeClock()
    r = ScriptedResolver([None])
    res = wait_for("Ghost button", "click", resolver=r, timeout=1.0,
                   clock=clock, sleep=clock.sleep)
    assert res.ok is False
    assert res.failed_check == "not_found"
    assert res.element is None


def test_force_skips_the_checks_but_still_needs_an_element():
    clock = FakeClock()
    disabled = _el(states=frozenset())
    r = ScriptedResolver([disabled])
    res = wait_for("Save button", "click", resolver=r, force=True,
                   clock=clock, sleep=clock.sleep)
    assert res.ok is True
    assert res.element is not None


def test_element_is_re_resolved_on_every_poll():
    clock = FakeClock()
    r = ScriptedResolver([None, None, None, _el(), _el()])
    wait_for("Save button", "click", resolver=r,
             hit_test=lambda x, y: _el(), clock=clock, sleep=clock.sleep)
    assert r.calls >= 4, "resolver must be re-queried, not cached"


def test_result_records_attempts_and_elapsed():
    clock = FakeClock()
    r = ScriptedResolver([None])
    res = wait_for("Ghost", "click", resolver=r, timeout=0.5, poll=0.1,
                   clock=clock, sleep=clock.sleep)
    assert isinstance(res, WaitResult)
    assert res.attempts >= 2
    assert res.elapsed_ms > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_waiting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.waiting'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/waiting.py
"""Wait for an element to be genuinely ready, the way a person does.

A human doesn't click where a button is about to be. They wait for the
dialog to settle, notice if it's greyed out, and see when something else
has opened on top of it. This is that, and it removes the single largest
source of flakiness in GUI automation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from actions.grounding.actionability import check
from actions.grounding.base import Element


@dataclass(frozen=True)
class WaitResult:
    element: Element | None
    ok: bool
    failed_check: str
    elapsed_ms: float
    attempts: int


def wait_for(description: str,
             action: str = "click",
             *,
             resolver,
             timeout: float = 5.0,
             poll: float = 0.05,
             hit_test: Callable[[int, int], Element | None] | None = None,
             force: bool = False,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> WaitResult:
    """Poll until `description` is present and actionable for `action`.

    Re-resolves every attempt — Playwright's lesson. A handle held across a
    redraw is a stale handle.
    """
    start = clock()
    previous: Element | None = None
    attempts = 0
    failed = "not_found"

    while True:
        attempts += 1
        element = None
        try:
            element = resolver.find(description)
        except Exception:
            element = None

        if element is not None:
            if force:
                return WaitResult(element, True, "",
                                  (clock() - start) * 1000, attempts)
            ok, failed_check = check(action, element,
                                     previous=previous, hit_test=hit_test)
            if ok:
                return WaitResult(element, True, "",
                                  (clock() - start) * 1000, attempts)
            failed = failed_check
        else:
            failed = "not_found"

        previous = element

        if clock() - start >= timeout:
            return WaitResult(element, False, failed,
                              (clock() - start) * 1000, attempts)
        sleep(poll)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_waiting.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 401 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/waiting.py tests/test_grounding_waiting.py
git commit -m "feat(hands): wait_for — poll until actionable, re-resolving each attempt"
```

---

### Task 11: Act, then look again

**Files:**
- Create: `actions/grounding/verify.py`
- Test: `tests/test_grounding_verify.py`

**Interfaces:**
- Consumes: `Element` from Task 1, `wait_for` from Task 10.
- Produces: `observe(description: str, resolver) -> dict | None` returning `{"bounds", "states", "value"}`. `act_and_verify(description: str, act: Callable[[Element], object], *, resolver, action: str = "click", settle: float = 0.15, sleep=time.sleep, **wait_kwargs) -> dict` returning `{"acted", "changed", "before", "after", "detail", "result"}`.

A human glances at the screen after they click. Returning "clicked" when nothing happened is the lie this task removes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_verify.py
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
    r = ScriptedResolver([_el(value="hello")])
    snap = observe("Save button", r)
    assert snap["bounds"] == (100, 200, 80, 40)
    assert "ENABLED" in snap["states"]
    assert snap["value"] == "hello"


def test_observe_returns_none_when_missing():
    assert observe("Ghost", ScriptedResolver([None])) is None


def test_reports_changed_when_state_flips_after_acting():
    after = _el(states=LIVE | {"CHECKED"})
    r = ScriptedResolver([_el(), _el(), after])
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'actions.grounding.verify'`

- [ ] **Step 3: Write minimal implementation**

```python
# actions/grounding/verify.py
"""Look, act, look again.

Reporting "clicked" without checking whether anything happened is the most
common way an agent lies to its operator. A person glances at the screen
after they act; so does the eagle.
"""
from __future__ import annotations

import time
from typing import Callable

from actions.grounding.base import Element
from actions.grounding.waiting import wait_for


def observe(description: str, resolver) -> dict | None:
    """Everything the eagle can currently perceive about an element."""
    try:
        el = resolver.find(description)
    except Exception:
        return None
    if el is None:
        return None
    return {"bounds": el.bounds, "states": set(el.states), "value": el.value}


def act_and_verify(description: str,
                   act: Callable[[Element], object],
                   *,
                   resolver,
                   action: str = "click",
                   settle: float = 0.15,
                   sleep: Callable[[float], None] = time.sleep,
                   **wait_kwargs) -> dict:
    """Wait until actionable, act, then re-observe and report the truth."""
    waited = wait_for(description, action, resolver=resolver,
                      sleep=sleep, **wait_kwargs)
    if not waited.ok or waited.element is None:
        return {
            "acted": False, "changed": False,
            "before": None, "after": None, "result": None,
            "detail": (f"never became actionable for {action}: "
                       f"{waited.failed_check} (after {waited.elapsed_ms:.0f}ms, "
                       f"{waited.attempts} attempts)"),
        }

    element = waited.element
    before = {"bounds": element.bounds,
              "states": set(element.states),
              "value": element.value}

    result = act(element)
    sleep(settle)
    after = observe(description, resolver)

    changed = (after is None) or (after != before)
    detail = ("observed a change after acting" if changed
              else "acted, but no observable change — it may not have worked")

    return {"acted": True, "changed": changed, "before": before,
            "after": after, "result": result, "detail": detail}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_verify.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 408 passed.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/verify.py tests/test_grounding_verify.py
git commit -m "feat(hands): act-and-verify — report what actually happened, not what was attempted"
```

---

## Acceptance Gate — before merging to `main`

- [ ] `.venv/bin/python -m pytest tests -q` → **408 passed**, zero failures.
- [ ] `_screen_find` signature unchanged; every existing caller untouched.
- [ ] `main.py` shows no diff: `git diff main --stat -- main.py` is empty.
- [ ] Manual smoke on the real desktop, bridge ON:
  ```bash
  gsettings set org.gnome.desktop.interface toolkit-accessibility true
  .venv/bin/python -c "
  from actions.grounding.atspi import bridge_status
  from actions.grounding import find_element
  import time
  print(bridge_status())
  t=time.perf_counter(); el=find_element('the Files button')
  print(el, f'{(time.perf_counter()-t)*1000:.1f}ms')"
  ```
  Expected: a real `Element` with `source='atspi'` in well under a second, versus the current ~1-3s VLM round-trip.
- [ ] Manual smoke with the bridge OFF: `find_element` still returns a result via vision. Degradation works.

## Merge

```bash
git checkout main
git merge --no-ff <branch> -m "feat(hands): accessibility-first grounding"
.venv/bin/python -m pytest tests -q     # green on main before anything else starts
```

Plan 2 does not begin until this is merged.
