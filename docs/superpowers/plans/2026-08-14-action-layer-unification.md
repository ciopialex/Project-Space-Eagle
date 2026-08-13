# Action Layer Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the two parallel, drifting implementations of "act on the browser a human can see" — one hardened inside the mission ladder, one weaker and reachable directly by ad-hoc voice commands — into a single implementation both paths call, and fix the three smaller bugs a live babysitting session exposed on 2026-08-13/14 (absolute-not-relative mouse movement, an AT-SPI accessibility toggle that was silently off, and blind typing with no focus verification in `computer_control`'s own direct actions).

**Architecture:** Extract the DOM-based, verified click/type/open/look logic that currently lives only inside `core/mission_runners.py`'s `_user_*` functions into a new shared module, `actions/grounding/web/user_actions.py`, that returns `ToolResult` and takes plain arguments instead of a mission `Step`. `mission_runners.py`'s `_user_click`/`_user_type`/`_user_open`/`_user_look` become thin adapters over it (Step → args → back to the `(ok, detail)` tuple the ladder expects). `actions/browser_control.py` gains three new actions — `click`, `type`, `look` — that call the *same* functions directly, so a single ad-hoc voice command gets the same DOM-exact reliability a mission step already has, without needing a mission running at all.

**Tech Stack:** Python 3.12, pytest, Playwright (via `core.session_port.SessionPort`), `pyautogui`/`mss` (via `actions/computer_control.py`), GNOME `gsettings`/AT-SPI.

## Global Constraints

- Every new/changed function that touches the world returns a `ToolResult` (`core/tool_result.py`) or, inside `mission_runners.py`'s existing `(ok, detail)` tuple contract where a function is a ladder rung — never a bare string claiming success.
- No behavior change to any *currently passing* test. Run the full suite (`python -m pytest tests/ -q`) after every task; it must stay green.
- Follow this repo's existing test-naming convention: one `test_...` function names one bug or one guarantee, with a short module-docstring context block, matching the style already in `tests/test_mission_ladder.py` and `tests/test_browser_control_contract.py`.
- Do not touch the "Dynamic Island background mode" / pill live-wiring work — that's explicitly out of scope for this plan (see the plan's closing note).

---

### Task 1: Shared user-facing browser action layer

**Files:**
- Create: `actions/grounding/web/user_actions.py`
- Create: `tests/test_user_actions.py`
- Modify: `core/mission_runners.py:92-124` (delete `_focused_text_field`/`_refuse_blind` — wait, these move to Task 4, not here; leave them for now)
- Modify: `core/mission_runners.py:193-217` (`_what_is_here`) — delete, replaced by import
- Modify: `core/mission_runners.py:220-403` (`_user_click`, `best_text_field`, `_focus_and_type`, `_web_type`, `_user_type`, `_same_page`, `_user_open`, `_user_look`) — rewritten as thin adapters
- Modify: `actions/browser_control.py:1154-1166` (`_INTERACTIVE_ACTIONS`) — add `click`, `type`, `look`
- Modify: `actions/browser_control.py:1263-1345` (the interactive-action elif chain) — add three new branches
- Modify: `main.py` — the `browser_control` tool declaration's `action` description string, to mention the three new actions

**Interfaces:**
- Produces (used by Task 1's own callers, and by nothing outside this task):
  - `user_actions.user_click(description: str) -> ToolResult`
  - `user_actions.user_type(description: str | None, text: str) -> ToolResult`
  - `user_actions.user_open(url: str) -> ToolResult`
  - `user_actions.user_look(limit: int = 40) -> ToolResult`
  - `user_actions.best_text_field(nodes) -> WebNode | None` (moved as-is from `mission_runners.py`)
  - `user_actions.focus_and_type(port, grounder, text: str) -> tuple[bool, str]` (moved as-is, renamed from `_focus_and_type` since it's now a public shared helper)
  - `user_actions.what_is_here(port, limit: int = 12) -> str` (moved as-is, renamed from `_what_is_here`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_user_actions.py
"""The one implementation "click/type/open/look on the visible browser"
now uses, whether the caller is a mission step or a single ad-hoc
browser_control action call.

Before this file existed, this logic lived only inside
core/mission_runners.py's _user_click/_user_type/_user_open/_user_look,
reachable only from inside a running mission. A live babysitting session
on 2026-08-13/14 showed the cost: a standalone "click the search bar"
voice command had no DOM-exact option at all, and fell through to
computer_control's pixel/AT-SPI path — unrelated code, its own bugs
(broken AT-SPI here), no relation to what the model was actually looking
at.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web import user_actions as UA  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


class _Node:
    def __init__(self, name, role="button", states=frozenset({"EDITABLE", "VISIBLE"}),
                width=100):
        self.name, self.role, self.states, self.width = name, role, states, width


class _Grounder:
    def __init__(self, node=None):
        self._node = node

    def find_node(self, description):
        return self._node


class _Port:
    def __init__(self, nodes=(), typed_into=""):
        self._nodes = nodes
        self.clicked_ref = None
        self.filled = None
        self._typed_into = typed_into

    def collect(self):
        return [{"ref": f"e{i}", "name": n.name, "role": n.role,
                 "states": list(n.states), "left": 0, "top": 0,
                 "width": n.width, "height": 20}
                for i, n in enumerate(self._nodes)]

    def click(self, ref):
        self.clicked_ref = ref

    def fill(self, ref, text):
        self.filled = (ref, text)

    def type_into_focused(self, text):
        return self._typed_into


def test_user_click_with_no_window_open_is_an_explicit_failure(monkeypatch):
    monkeypatch.setattr(UA, "_user_window", lambda: (None, None))
    r = UA.user_click("the Search button")
    assert isinstance(r, ToolResult) and r.ok is False
    assert "no browser window" in r.message


def test_user_click_finds_and_clicks_the_named_control(monkeypatch):
    node = _Node("Search")
    port = _Port()
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(node)))
    r = UA.user_click("Search")
    assert r.ok is True
    assert port.clicked_ref == "e0"


def test_user_click_names_what_is_actually_on_the_page_when_nothing_matches(monkeypatch):
    port = _Port(nodes=[_Node("Home"), _Node("Download")])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(None)))
    r = UA.user_click("Search")
    assert r.ok is False
    assert "Home" in r.message and "Download" in r.message


def test_user_type_with_no_description_types_into_whatever_is_focused(monkeypatch):
    port = _Port(typed_into="Search field")
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(None)))
    r = UA.user_type(None, "watch stand")
    assert r.ok is True
    assert "Search field" in r.message


def test_user_type_with_a_description_fills_that_named_field(monkeypatch):
    node = _Node("Search input", role="textbox")
    port = _Port(nodes=[node])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(node)))
    r = UA.user_type("Search input", "watch stand")
    assert r.ok is True
    assert port.filled == ("e0", "watch stand")


def test_user_look_reports_how_many_controls_are_on_the_page(monkeypatch):
    port = _Port(nodes=[_Node("A"), _Node("B"), _Node("C")])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, None))
    r = UA.user_look()
    assert r.ok is True
    assert "3" in r.message


def test_user_open_with_no_window_is_an_explicit_failure(monkeypatch):
    monkeypatch.setattr(UA, "_user_window", lambda create=False: (None, None))
    r = UA.user_open("https://example.test")
    assert r.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_actions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web.user_actions'`

- [ ] **Step 3: Create `actions/grounding/web/user_actions.py`**

```python
"""Act on the browser the eagle opened FOR THE USER — one implementation,
reached by two callers: a mission step, and a single ad-hoc voice command.

Before this file existed, everything here lived only inside
core/mission_runners.py's _user_click/_user_type/_user_open/_user_look,
private to that module and only reachable from inside a running mission.
A live babysitting session (2026-08-13/14) showed the cost of that: asked
to "click the search bar" as a single command, the model had no DOM-exact
option and fell to computer_control's pixel/AT-SPI path instead — a
completely different, less reliable implementation with its own bugs
(AT-SPI was structurally broken in that environment), unrelated to the
page the model was actually looking at.

`actions/browser_control.py` now exposes these directly as its own
`click`/`type`/`look` actions; `core/mission_runners.py`'s `_user_*`
functions became thin adapters that call the same code. There is exactly
one implementation of "find a control on the visible page and act on it"
from here on.
"""
from __future__ import annotations

from core.tool_result import ToolResult


def _user_window(create: bool = False):
    from core.session_port import user_window
    return user_window(create=create)


def what_is_here(port, limit: int = 12) -> str:
    """The names actually on the page, for a failure that could not find its
    own — "no control matches" alone makes the model guess again."""
    try:
        from actions.grounding.web.page import nodes_from_records
        from actions.web_agency import _spread
        names = []
        # Spread across the page, not the first N — taking the front returned
        # "Home; All Models; Following; MakerLab" on a page of search
        # results (the sidebar, in document order), the same positional
        # bias hidden content everywhere else in this codebase.
        for n in _spread(nodes_from_records(port.collect()), budget=60, run=6):
            nm = str(getattr(n, "name", "") or "").strip()
            if nm and nm not in names:
                names.append(nm)
            if len(names) >= limit:
                break
        return "; ".join(names)
    except Exception:
        return ""


#: Roles you can type into. `searchbox` first: typing a search query into a
#: newsletter signup is the failure this ordering exists to avoid.
_FIELD_ROLES = ("searchbox", "textbox")


def best_text_field(nodes):
    """The field a person would type in, or None.

    "Search for X" is two actions wearing one step — focus a field, then
    type — and a person does not need to be told which field; they look
    for the one they can type in. None is a real answer: guessing at a
    control that is not editable is how `Page.fill: Element is not ...`
    happened live, and how text ends up somewhere nobody asked for.
    """
    best, best_rank = None, ()
    for n in nodes or ():
        role = str(getattr(n, "role", "") or "").lower()
        states = getattr(n, "states", frozenset()) or frozenset()
        if role not in _FIELD_ROLES:
            continue
        if "EDITABLE" not in states or "VISIBLE" not in states:
            continue
        name = str(getattr(n, "name", "") or "")
        rank = (
            _FIELD_ROLES.index(role) == 0,
            "search" in name.lower(),
            bool(name),
            int(getattr(n, "width", 0) or 0),
        )
        if best is None or rank > best_rank:
            best, best_rank = n, rank
    return best


def focus_and_type(port, grounder, text: str) -> tuple[bool, str]:
    """Put the cursor in the page's text field, then type. Exact, not blind."""
    try:
        where = port.type_into_focused(text)
        if where:
            return True, f"typed into the focused field ({where})"
    except Exception:
        pass
    try:
        from actions.grounding.web.page import nodes_from_records, ref_of
        field = best_text_field(nodes_from_records(port.collect()))
        if field is None:
            return False, ("no text field on this page to type into — the "
                           "page may not have loaded, or the field may be "
                           "behind a button that opens it")
        port.click(ref_of(field))
        typed = port.type_into_focused(text)
        if typed:
            return True, f"clicked {field.name or 'the search field'!r} and typed"
        port.fill(ref_of(field), text)
        return True, f"typed into {field.name or 'the search field'!r}"
    except Exception as e:
        return False, f"could not type into the page's text field: {e}"


def _same_page(a: str, b: str) -> bool:
    """Close enough that navigating again would only cost a reload."""
    def norm(u):
        u = (u or "").strip().lower().rstrip("/")
        for pfx in ("https://", "http://", "www."):
            if u.startswith(pfx):
                u = u[len(pfx):]
        return u
    x, y = norm(a), norm(b)
    return bool(x) and bool(y) and (x == y or x.startswith(y) or y.startswith(x))


def user_click(description: str) -> ToolResult:
    port, grounder = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    node = grounder.find_node(description)
    if node is None:
        here = what_is_here(port)
        return ToolResult.failure(
            f"no control matching {description!r}"
            + (f" — the page has: {here}" if here else ""),
            guidance="Call browser_control action='look' and use one of "
                     "the names it returns.")
    from actions.grounding.web.page import ref_of
    port.click(ref_of(node))
    return ToolResult.success(f"clicked {node.name!r} in the user's window")


def user_type(description: str | None, text: str) -> ToolResult:
    port, grounder = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    if not text:
        return ToolResult.failure("nothing to type",
                                  guidance="Pass text='...'.")

    # "Type motherboard" names no control, because a person does not name
    # one — they just clicked the field. With no explicit description, type
    # into whatever the PAGE reports as focused: exact, because the browser
    # knows, and it is the field a previous click landed on.
    if not description:
        ok, detail = focus_and_type(port, grounder, text)
        if ok:
            return ToolResult.success(detail)

    node = grounder.find_node(description or "")
    if node is None:
        return ToolResult.failure(
            f"no field matching {description!r} in the user's window",
            guidance="Call browser_control action='look' and use one of "
                     "the names it returns.")
    from actions.grounding.web.page import ref_of
    port.fill(ref_of(node), text)
    return ToolResult.success(f"typed into {node.name!r} in the user's window")


def user_open(url: str) -> ToolResult:
    port, _ = _user_window(create=True)   # the one call that may open one
    if not url:
        return ToolResult.failure("no url to open",
                                  guidance="Pass url='https://...'.")
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Retrying will not help by itself — the launch "
                     "budget may be spent, or the session could not start.")
    here = port.url()
    if _same_page(here, url):
        return ToolResult.success(f"already on {here}")
    port.goto(url)
    return ToolResult.success(f"navigated the user's window to {url}")


def user_look(limit: int = 40) -> ToolResult:
    port, _ = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    from actions.grounding.web.page import nodes_from_records
    nodes = nodes_from_records(port.collect())
    names = what_is_here(port, limit=limit)
    return ToolResult.success(
        f"{len(nodes)} controls in the user's window"
        + (f": {names}" if names else ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_actions.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Rewrite `core/mission_runners.py`'s `_user_*` functions as thin adapters**

Replace lines 193-217 (`_what_is_here`), 244-256 (`_user_click`), 260-320 (`best_text_field`, `_focus_and_type`), 341-365 (`_user_type`), 368-377 (`_same_page`), 380-402 (`_user_open`, `_user_look`) with:

```python
from actions.grounding.web.user_actions import (  # add near the top imports
    best_text_field, focus_and_type, user_click as _ua_click,
    user_look as _ua_look, user_open as _ua_open, user_type as _ua_type,
    what_is_here,
)


def _user_click(step: Step) -> tuple[bool, str]:
    r = _ua_click(step.target or step.intent)
    return r.ok, r.message


def _user_type(step: Step) -> tuple[bool, str]:
    r = _ua_type(step.target or None, step.text)
    return r.ok, r.message


def _user_open(step: Step) -> tuple[bool, str]:
    why = _needs_url(step)
    if why:
        return False, why
    r = _ua_open(step.url)
    return r.ok, r.message


def _user_look(step: Step) -> tuple[bool, str]:
    r = _ua_look()
    return r.ok, r.message
```

Leave `_web_type` where it is, but change its own call from `_focus_and_type(page, None, step.text)` to `focus_and_type(page, None, step.text)` (now imported, not module-private).

- [ ] **Step 6: Run the full mission_runners test suite**

Run: `.venv/bin/python -m pytest tests/test_mission_ladder.py tests/test_mission_tool.py tests/test_no_ghost_browsers.py tests/test_mission_no_loop.py tests/test_the_blackboard_reaches_the_next_step.py -v`
Expected: PASS, no regressions — these tests mock `M._runners`/`R._user_window`, not the internals being moved, so behavior is unchanged.

- [ ] **Step 7: Add `click`, `type`, `look` to `browser_control`**

In `actions/browser_control.py`, extend `_INTERACTIVE_ACTIONS` (around line 1162):

```python
_INTERACTIVE_ACTIONS = frozenset({
    "click", "type", "scroll", "fill_form", "smart_click", "smart_type",
    "get_text", "get_url", "press", "close_tab", "screenshot", "back",
    "forward", "reload", "look",
})
```

Note `"click"` and `"type"` already exist in this set — they currently mean "the raw pixel-based click/type on browser_control's own session object." Rename the OLD behavior's dispatch keys to avoid collision: in the elif chain (~line 1300), the existing `action == "click"` / `action == "type"` branches call `sess.click(...)`/`sess.type_text(...)` (browser_control's own lower-level session methods, unrelated to the eagle's DOM grounder). Leave those branches as-is — they still work and nothing else in this plan removes them — but add the new, DOM-based path as a distinct pair of branches checked FIRST, using a different action name so both remain callable: `smart_click_dom` collides with nothing existing, but the clean fix is to make `click`/`type` themselves DOM-first with the old pixel behavior as an internal fallback. Do that:

```python
        if action == "click":
            from actions.grounding.web.user_actions import user_click
            r = user_click(params.get("description", ""))
            if r.ok:
                _log(player, r.message)
                return r
            # Fall back to the session's own low-level click only if the
            # DOM lookup found no window at all (not if it found a window
            # and just could not match the description — that is a real
            # "no such control" the caller should hear about, not paper over).
            if "no browser window is open" not in r.message:
                _log(player, r.message)
                return r
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            from actions.grounding.web.user_actions import user_type
            r = user_type(params.get("description"), params.get("text", ""))
            if r.ok or "no browser window is open" not in r.message:
                _log(player, r.message)
                return r
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "look":
            from actions.grounding.web.user_actions import user_look
            r = user_look()
            _log(player, r.message)
            return r
        elif action == "scroll":
```

This replaces the existing `if action == "click": ... elif action == "type": ...` pair (find them in the elif chain) — change the first `if` to stay `if`, and change what follows from `elif action == "scroll":` onward to chain from the new `elif action == "look":` block above. Every other existing `elif` in that chain (fill_form, smart_click, etc.) is untouched — only insert before `scroll`.

- [ ] **Step 8: Write the browser_control-level tests**

Add to `tests/test_browser_control_contract.py`:

```python
def test_click_action_uses_the_dom_grounder_when_a_window_is_open(monkeypatch):
    from core.tool_result import ToolResult
    called = {}
    def fake_user_click(description):
        called["description"] = description
        return ToolResult.success(f"clicked {description!r} in the user's window")
    import actions.grounding.web.user_actions as UA
    monkeypatch.setattr(UA, "user_click", fake_user_click)
    _fake_registry(monkeypatch)
    r = BC.browser_control({"action": "click", "description": "Search"})
    assert r.ok is True
    assert called["description"] == "Search"


def test_type_action_uses_the_dom_grounder_when_a_window_is_open(monkeypatch):
    from core.tool_result import ToolResult
    called = {}
    def fake_user_type(description, text):
        called["args"] = (description, text)
        return ToolResult.success("typed into 'Search' in the user's window")
    import actions.grounding.web.user_actions as UA
    monkeypatch.setattr(UA, "user_type", fake_user_type)
    _fake_registry(monkeypatch)
    r = BC.browser_control({"action": "type", "description": "Search", "text": "watch stand"})
    assert r.ok is True
    assert called["args"] == ("Search", "watch stand")


def test_look_action_reports_whats_on_the_page(monkeypatch):
    from core.tool_result import ToolResult
    import actions.grounding.web.user_actions as UA
    monkeypatch.setattr(UA, "user_look", lambda: ToolResult.success("3 controls: Home; Search; Download"))
    _fake_registry(monkeypatch)
    r = BC.browser_control({"action": "look"})
    assert r.ok is True
    assert "Home" in r.message
```

Note: `import actions.grounding.web.user_actions as UA; monkeypatch.setattr(UA, ...)` patches the module attribute; since `browser_control.py`'s new branches do `from actions.grounding.web.user_actions import user_click` *inside* the function body (a fresh lookup on every call, matching this codebase's existing lazy-import convention), the monkeypatch is picked up correctly — confirm this pattern already works by checking `test_browser_control_contract.py`'s existing `_fake_registry` tests, which rely on the same lazy-import-inside-function style for `_registry`.

- [ ] **Step 9: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_browser_control_contract.py -v`
Expected: PASS (11 tests total: 8 existing + 3 new)

- [ ] **Step 10: Update the `browser_control` tool declaration in `main.py`**

Find the `browser_control` entry in `TOOL_DECLARATIONS` and extend its `action` parameter description to mention the three new actions, e.g. append: `"click/type/look now use the same exact DOM lookup the mission tool uses — prefer these over computer_control's screen_click/type for anything inside a browser window."` Exact wording is free; the requirement is that `click`, `type`, and `look` are named in that description string (a docs/declaration-sync test may check this — see Task 5).

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all tests pass, no regressions.

- [ ] **Step 12: Commit**

```bash
git add actions/grounding/web/user_actions.py tests/test_user_actions.py \
       core/mission_runners.py actions/browser_control.py \
       tests/test_browser_control_contract.py main.py
git commit -m "$(cat <<'EOF'
feat(web): one DOM-based click/type/look, reachable with or without a mission

_user_click/_user_type/_user_open/_user_look lived only inside the mission
ladder, private to core/mission_runners.py. A live babysitting session
(2026-08-13/14) showed the cost: a standalone "click the search bar" voice
command had no DOM-exact option and fell to computer_control's pixel/AT-SPI
path instead — a different implementation, unrelated bugs, no relation to
the page the model was actually looking at.

Extracted the logic into actions/grounding/web/user_actions.py, callable
directly. mission_runners.py's _user_* functions are now thin adapters over
it. browser_control gains click/type/look actions using the same code.
EOF
)"
```

---

### Task 2: Directional, relative mouse movement

**Files:**
- Modify: `actions/computer_control.py:378-381` (`_move`) and `:720-721` (dispatch)
- Test: `tests/test_computer_control_move.py` (new)

**Interfaces:**
- Produces: `_move(x=None, y=None, direction=None, amount=100, duration=0.3) -> str` — either absolute `(x, y)` (existing behavior, unchanged) or a named `direction` (`up`/`down`/`left`/`right`) moved by `amount` pixels from the CURRENT cursor position (new).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_computer_control_move.py
"""move() used to only accept an absolute (x, y) target the model had to
guess. A live babysitting session (2026-08-13/14) asked it to move the
cursor in one direction and it moved on two axes at once — because the
model was supplying both coordinates from a guess, not a delta from where
the cursor actually was. One of those guesses drove the cursor into a
screen corner and tripped PyAutoGUI's fail-safe abort.

direction/amount mirrors scroll's own interface exactly (already correct,
already used successfully all through that same session).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.computer_control as CC  # noqa: E402


def test_direction_up_moves_only_the_y_axis(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    monkeypatch.setattr(CC.pyautogui, "position", lambda: (500, 500))
    calls = []
    monkeypatch.setattr(CC.pyautogui, "moveTo",
                        lambda x, y, duration=0.3: calls.append((x, y)))
    CC._move(direction="up", amount=100)
    assert calls == [(500, 400)]


def test_direction_right_moves_only_the_x_axis(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    monkeypatch.setattr(CC.pyautogui, "position", lambda: (500, 500))
    calls = []
    monkeypatch.setattr(CC.pyautogui, "moveTo",
                        lambda x, y, duration=0.3: calls.append((x, y)))
    CC._move(direction="right", amount=50)
    assert calls == [(550, 500)]


def test_explicit_x_y_still_means_absolute_as_before(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    calls = []
    monkeypatch.setattr(CC.pyautogui, "moveTo",
                        lambda x, y, duration=0.3: calls.append((x, y)))
    CC._move(x=700, y=200)
    assert calls == [(700, 200)]


def test_neither_direction_nor_coordinates_is_a_clean_failure(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    from core.tool_result import Failed
    result = CC._move()
    assert isinstance(result, Failed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_computer_control_move.py -v`
Expected: FAIL — current `_move(x, y, duration=0.3)` has no `direction`/`amount` parameters and no no-args failure path.

- [ ] **Step 3: Rewrite `_move`**

Replace `actions/computer_control.py:378-381`:

```python
_DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def _move(x: int | None = None, y: int | None = None, direction: str | None = None,
          amount: int = 100, duration: float = 0.3):
    _require_pyautogui()
    if direction:
        dx, dy = _DIRECTIONS.get(direction.lower(), (0, 0))
        if dx == 0 and dy == 0:
            return Failed(f"'{direction}' is not a direction.",
                         guidance="Use one of: up, down, left, right.")
        cx, cy = pyautogui.position()
        x, y = cx + dx * amount, cy + dy * amount
    elif x is None or y is None:
        return Failed("move needs either 'direction' or both 'x' and 'y'.",
                     guidance="e.g. direction='up', amount=100 — or x=700, y=200 for an absolute position.")
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse → ({x}, {y})"
```

Add `from core.tool_result import Failed` to the top imports if not already present (check — `Failed` is already imported elsewhere in this file for other actions; if so, this is a no-op).

- [ ] **Step 4: Update the dispatch site**

Replace `actions/computer_control.py:720-721`:

```python
        if action == "move":
            return _move(
                x=int(params["x"]) if "x" in params else None,
                y=int(params["y"]) if "y" in params else None,
                direction=params.get("direction"),
                amount=int(params.get("amount", 100)),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_computer_control_move.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Update the `computer_control` tool declaration in `main.py`**

Find the `move` mention in the `computer_control` tool's parameter descriptions (likely under a generic `x`/`y`/`direction`/`amount` set shared with `scroll`) and add `direction` (`up|down|left|right`) and `amount` as valid params for `move`, matching how `scroll` is already documented.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add actions/computer_control.py tests/test_computer_control_move.py main.py
git commit -m "$(cat <<'EOF'
fix(hands): move() takes a direction, not just a guessed absolute position

Live, 2026-08-13/14: asked to move the cursor in one direction, the model
supplied both x and y as an absolute-position guess, moving both axes at
once. One guess drove the cursor into a screen corner and tripped
PyAutoGUI's fail-safe abort. direction+amount mirrors scroll's own
interface, which the same session used correctly throughout.
EOF
)"
```

---

### Task 3: The AT-SPI accessibility toggle — extend the existing doctor check, and fail fast when it's off

**Files:**
- Modify: `core/doctor.py:66-96` (`_structural_grounding`) — the binding-import check already there is necessary but not sufficient; add the GNOME toggle check inside it
- Modify: `actions/grounding/resolver.py` — probe once, cache, skip the AT-SPI tier fast when it's unavailable
- Test: `tests/test_doctor.py`, `tests/test_grounding_atspi_probe.py` (new)

**Interfaces:**
- Produces: `actions/grounding/resolver.py` gains `atspi_available() -> bool`, cached per-process.

- [ ] **Step 1: Confirm the root cause is what it looks like, and that the fix is durable**

Found live: `_structural_grounding()` (`core/doctor.py:66-96`) only checks that `python3-gi`/`Atspi` *imports* — it already passes on this machine. The actual failure was one layer up: `gsettings get org.gnome.desktop.interface toolkit-accessibility` returned `false`, despite `at-spi-bus-launcher`, its `dbus-daemon`, and `at-spi2-registryd` all running. The bus existed; GNOME's own toggle was gating whether any app — including Chrome, even with `--force-renderer-accessibility` (already passed in `browser_control.py`'s launch flags) — publishes to it at all. That's *why* the existing check reported OK while `screen_click` failed every time: it was checking the binding, not the toggle.

Setting it to `true` live (`gsettings set org.gnome.desktop.interface toolkit-accessibility true`) is unverified beyond that one flip. The next real run of `eagle` with a browser open is the actual test of whether `screen_click` stops hitting `AtSpiAdaptor::applicationInterface does not implement "GetApplicationBusAddress"`. If a fresh live test still shows the error after the toggle is on, stop and re-investigate — the toggle may only take effect for apps started *after* it flips, meaning `eagle` and any open browser need restarting, not just the setting changed.

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_doctor.py
def test_structural_grounding_also_checks_the_gnome_accessibility_toggle(monkeypatch):
    """The existing check only verified the AT-SPI python binding imports —
    that already passed on the machine where this was found, while every
    screen_click still failed, because GNOME's own toolkit-accessibility
    toggle (a layer up from the binding) was off. The bus and both its
    daemons were running; nothing published to it."""
    import core.doctor as D
    monkeypatch.setattr(D, "_plat", lambda: "linux")
    monkeypatch.setattr(
        D.subprocess, "run",
        lambda cmd, **kw: type("R", (), {"stdout": "false\n", "returncode": 0})())
    c = D._structural_grounding()
    assert c.status == D.MISSING
    assert "toolkit-accessibility" in c.detail
    assert c.fix == ("gsettings set org.gnome.desktop.interface "
                     "toolkit-accessibility true")
    assert c.auto is True


def test_structural_grounding_passes_when_the_toggle_is_on(monkeypatch):
    import core.doctor as D
    monkeypatch.setattr(D, "_plat", lambda: "linux")
    monkeypatch.setattr(
        D.subprocess, "run",
        lambda cmd, **kw: type("R", (), {"stdout": "true\n", "returncode": 0})())
    c = D._structural_grounding()
    assert c.status == D.OK
```

Note: on the machine these are written against, `import gi; gi.require_version(...)` genuinely succeeds, so these tests exercise the real import path plus a mocked `gsettings` call — they do not need to mock the `gi` import itself.

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -v -k structural_grounding`
Expected: FAIL — `_structural_grounding()` currently returns `OK` right after the import succeeds, never calling `subprocess.run` at all, so `c.detail`/`c.fix` are empty and the first test's assertions fail.

- [ ] **Step 4: Extend `_structural_grounding()` in `core/doctor.py`**

Replace `core/doctor.py:66-80` (the `if plat == "linux":` branch only — leave the `windows`/macOS branches at lines 81-96 untouched):

```python
    if plat == "linux":
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: F401
        except Exception as e:
            return Check(
                "structural grounding (AT-SPI)", MISSING, str(e)[:70],
                fix="sudo apt install python3-gi gir1.2-atspi-2.0 "
                    "&& sudo systemctl --user restart at-spi-dbus-bus")
        # The binding importing is necessary but not sufficient. GNOME's own
        # toggle gates whether ANY app publishes to the bus at all — found
        # live: the bus and both its daemons were running, this import
        # succeeded, and every screen_click still failed, because only this
        # one setting was off.
        try:
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface",
                 "toolkit-accessibility"],
                capture_output=True, text=True, timeout=3)
            if out.stdout.strip() != "true":
                return Check(
                    "structural grounding (AT-SPI)", MISSING,
                    "toolkit-accessibility is off — the bus runs but "
                    "nothing publishes to it",
                    fix="gsettings set org.gnome.desktop.interface "
                        "toolkit-accessibility true", auto=True)
        except Exception:
            pass   # not a GNOME desktop (no gsettings) — the import already
                   # proved the binding works; do not fail a check this
                   # function cannot honestly evaluate here
        return Check("structural grounding (AT-SPI)", OK)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -v`
Expected: PASS, no regressions in existing doctor tests (`_structural_grounding` is called from `run_checks()`, which other doctor tests may already exercise end-to-end — confirm those still pass with the real `gsettings` call now happening; they should, since the toggle is genuinely `true` after Step-0's live fix).

- [ ] **Step 6: Write the failing test for the fast-fail probe**

```python
# tests/test_grounding_atspi_probe.py
"""screen_click's AT-SPI tier burned ~20 real seconds across 4 calls in a
live session (5s timeout × up to 85 internal attempts, every time) because
nothing checked whether AT-SPI could answer at all before polling it
repeatedly. A single cheap probe, cached for the process, turns a doomed
20-second wait into an instant skip to the next rung (vision)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding import resolver as R  # noqa: E402


def test_atspi_unavailable_is_detected_once_and_cached(monkeypatch):
    calls = []
    def fake_probe():
        calls.append(1)
        return False
    monkeypatch.setattr(R, "_atspi_probe", fake_probe)
    monkeypatch.setattr(R, "_atspi_cache", None)
    assert R.atspi_available() is False
    assert R.atspi_available() is False
    assert len(calls) == 1, "probed more than once — should cache per process"
```

- [ ] **Step 7: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_grounding_atspi_probe.py -v`
Expected: FAIL — `atspi_available`/`_atspi_probe`/`_atspi_cache` don't exist yet.

- [ ] **Step 8: Add the probe to `actions/grounding/resolver.py`**

```python
_atspi_cache: bool | None = None


def _atspi_probe() -> bool:
    """A cheap, real check — not a guess. Tries to enumerate the AT-SPI
    accessible registry root; a broken bus raises or returns nothing."""
    try:
        import pyatspi
        return pyatspi.Registry.getDesktop(0).childCount >= 0
    except Exception:
        return False


def atspi_available() -> bool:
    """Whether the AT-SPI tier can answer at all, checked once per process.

    Before this existed, every screen_click call spent its full 5s timeout
    (up to 85 internal polling attempts) against a bus that was never going
    to answer, every single time, in a session where it failed 4/4 calls.
    """
    global _atspi_cache
    if _atspi_cache is None:
        _atspi_cache = _atspi_probe()
    return _atspi_cache
```

Find wherever `actions/grounding/resolver.py`'s tiered resolver currently adds the AT-SPI/structural tier (search for `structural_grounder` — already referenced from `core/mission_runners.py`) and gate it: skip straight to the next tier when `atspi_available()` is `False`, instead of adding it to the tier list at all.

- [ ] **Step 9: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_grounding_atspi_probe.py -v`
Expected: PASS

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass. Pay particular attention to any existing AT-SPI/structural-grounder tests (`tests/test_grounding_atspi.py` exists per an earlier grep this session) — they likely mock the structural layer directly and should be unaffected, but confirm.

- [ ] **Step 11: Commit**

```bash
git add core/doctor.py actions/grounding/resolver.py tests/test_doctor.py tests/test_grounding_atspi_probe.py
git commit -m "$(cat <<'EOF'
fix(hands): stop paying the full AT-SPI timeout when the bus can't answer

Live, 2026-08-13/14: gsettings org.gnome.desktop.interface
toolkit-accessibility was false despite the AT-SPI bus and its daemons
all running — every screen_click call burned its full 5s timeout (up to
85 internal attempts) against a tier that could never answer. Doctor now
checks and can auto-fix the toggle; the resolver probes once per process
and skips the tier instead of polling it to death when it's unavailable.
EOF
)"
```

---

### Task 4: Blind typing, refused in `computer_control`'s own direct actions

**Files:**
- Modify: `core/mission_runners.py:92-124` — move `_focused_text_field`/`_refuse_blind` out
- Create: `actions/grounding/focus.py`
- Modify: `actions/computer_control.py:288-311` (`_type`, `_smart_type`) and the `type_text` action if it exists as its own function
- Test: `tests/test_computer_control_no_blind_typing.py` (new)

**Interfaces:**
- Produces: `actions.grounding.focus.focused_editable_name() -> str | None` (moved from `core/mission_runners.py`'s `_focused_text_field`, renamed, made importable from a module that doesn't itself depend on `core.mission`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_computer_control_no_blind_typing.py
"""Live, 2026-08-13/14: computer_control's own type/type_text/smart_type
actions reported success ("Typed: watch stand") with no verification the
text landed anywhere — and per the human watching, it didn't, until they
clicked the field themselves. The same guard mission_runners.py already
has (_refuse_blind, built earlier the same week) never covered
computer_control's own direct actions — this closes that gap by sharing
one focus-check both paths call.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.computer_control as CC  # noqa: E402
from core.tool_result import Failed  # noqa: E402


def test_type_refuses_when_nothing_has_focus(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    import actions.grounding.focus as F
    monkeypatch.setattr(F, "focused_editable_name", lambda: None)
    result = CC._type("watch stand")
    assert isinstance(result, Failed)
    assert "focus" in str(result).lower()


def test_type_proceeds_when_something_has_focus(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    typed = []
    monkeypatch.setattr(CC.pyautogui, "typewrite",
                        lambda text, interval=0.03: typed.append(text))
    import actions.grounding.focus as F
    monkeypatch.setattr(F, "focused_editable_name", lambda: "Search field")
    result = CC._type("watch stand")
    assert not isinstance(result, Failed)
    assert typed == ["watch stand"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_computer_control_no_blind_typing.py -v`
Expected: FAIL — `_type` currently types unconditionally.

- [ ] **Step 3: Create `actions/grounding/focus.py`**

```python
"""Whether keyboard focus is known — moved out of core/mission_runners.py
so both the mission ladder and computer_control's own direct actions
share the exact same check, rather than one having it and the other not.

A mission typed "motherboard" into the user's terminal while Claude Code
was running in it, and reported success. The same defect is documented at
the top of actions/whatsapp_web.py from months earlier. computer_control's
own type/type_text/smart_type actions never got this guard — a live
babysitting session (2026-08-13/14) hit exactly the same bug through them.
"""
from __future__ import annotations


def focused_editable_name() -> str | None:
    """The name of the text field that currently has keyboard focus, or
    None. None means "I do not know where typing would land" — the only
    honest answer when the accessibility layer cannot see a focused
    editable control. It is NOT the same as "there is no field", and must
    never be read as permission.
    """
    try:
        from actions.grounding.resolver import structural_grounder
        g = structural_grounder()
        if g is None:
            return None
        for node in (g.nodes() if hasattr(g, "nodes") else []):
            states = getattr(node, "states", frozenset())
            if "FOCUSED" in states and "EDITABLE" in states:
                return str(getattr(node, "name", "") or "a text field")
    except Exception:
        return None
    return None
```

- [ ] **Step 4: Update `core/mission_runners.py` to import from the new location**

Delete lines 92-118 (`_focused_text_field`'s body) and replace its call sites (`_refuse_blind`'s caller, `_runners_screen_type`) with an import: `from actions.grounding.focus import focused_editable_name`, calling `focused_editable_name()` wherever `_focused_text_field()` was called. Keep `_refuse_blind` itself in `mission_runners.py` (it's mission-specific phrasing, not shared logic).

- [ ] **Step 5: Add the guard to `computer_control._type`/`_smart_type`**

Replace `actions/computer_control.py:288-311`:

```python
def _type(text: str, interval: float = 0.03):
    _require_pyautogui()
    from actions.grounding.focus import focused_editable_name
    where = focused_editable_name()
    if not where:
        return Failed(
            "refusing to type: nothing identifiable has keyboard focus, "
            "so the text could land anywhere — a terminal, a chat, "
            "someone's half-filled form.",
            guidance="Click the field first (screen_click, or browser_control "
                     "action='click' if it's in a browser window), then type.")
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''} (into {where!r})"


def _smart_type(text: str, clear_first: bool = True):
    _require_pyautogui()
    from actions.grounding.focus import focused_editable_name
    where = focused_editable_name()
    if not where:
        return Failed(
            "refusing to type: nothing identifiable has keyboard focus, "
            "so the text could land anywhere — a terminal, a chat, "
            "someone's half-filled form.",
            guidance="Click the field first (screen_click, or browser_control "
                     "action='click' if it's in a browser window), then type.")
    if clear_first:
        _clear_field()
        time.sleep(0.1)
    # ... existing body unchanged from here ...
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_computer_control_no_blind_typing.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite, paying attention to anything exercising real typing**

Run: `.venv/bin/python -m pytest tests/ -q -k "computer_control or mission"`
Expected: all pass. `focused_editable_name()` depends on `atspi_available()` from Task 3 — with AT-SPI unavailable, this guard now fails CLOSED (refuses to type) rather than typing blind, which is the correct, safe direction; confirm no existing test assumed the old blind-typing behavior was desired.

Then: `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 8: Commit**

```bash
git add actions/grounding/focus.py core/mission_runners.py actions/computer_control.py tests/test_computer_control_no_blind_typing.py
git commit -m "$(cat <<'EOF'
fix(hands): computer_control's own type/smart_type refuse to type blind

Live, 2026-08-13/14: both reported success ("Typed: watch stand") with no
focus check at all, and the text landed nowhere until the human clicked
the field themselves. mission_runners.py already had this guard
(_refuse_blind); computer_control's own direct actions never did. Moved
the check to actions/grounding/focus.py so both paths share it.
EOF
)"
```

---

### Task 5: Route single ad-hoc browser commands to the new DOM-based actions

**Files:**
- Modify: `core/prompt.txt`
- Test: `tests/test_prompt_routes_browser_actions.py` (new)

**Interfaces:**
- Consumes: nothing new — this is a prompt-text change plus a test that greps for it, matching the existing pattern in `tests/test_mission_wired.py` (`test_the_prompt_routes_multi_action_requests_to_it`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_routes_browser_actions.py
"""Live, 2026-08-13/14: given a single command like "click the search bar,"
the model reached for computer_control's pixel path or web_agency's
DISCONNECTED hidden browser — never the DOM-exact browser_control
click/type/look added in this same plan — because nothing told it to.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_prompt_prefers_browser_control_dom_actions_for_visible_browser_commands():
    prompt = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text().lower()
    assert "browser_control" in prompt
    # The guidance must actually connect a single click/type command aimed
    # at what the user can see to browser_control's click/type/look, not
    # just mention both tools somewhere unrelated.
    assert "computer_control" in prompt
```

(This test intentionally only pins that the concepts are present and connected in prose — see Step 2 for the actual wording the assertion should tighten around once it's written, matching this repo's existing loose prompt-content tests like `test_the_prompt_says_not_to_narrate_every_step`.)

- [ ] **Step 2: Add the routing guidance to `core/prompt.txt`**

Find the existing section that discusses `web_agency` vs `browser_control` vs `computer_control` (search `grep -n "web_agency\|browser_control\|computer_control" core/prompt.txt`) and add, near the existing single-action guidance at line 34:

```
When a single command names something on the browser the user can already see — "click the search bar," "type X into it," "what's on this page" — call browser_control action='click'/'type'/'look', not computer_control's screen_click/type. It uses the same exact DOM lookup the mission tool uses, is faster, and does not require the accessibility bus. Reach for computer_control only for things OUTSIDE any browser window — a native app, the desktop itself — or once browser_control itself reports no window is open.
```

- [ ] **Step 3: Tighten the test to check for this specific connective language**

Update the test written in Step 1 to assert on a phrase actually present after Step 2, e.g. `assert "same exact dom lookup" in prompt` (adjust to match the exact wording written).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_prompt_routes_browser_actions.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite one final time**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass — this should be the full count from before this plan, plus every test added across all 5 tasks.

- [ ] **Step 6: Commit**

```bash
git add core/prompt.txt tests/test_prompt_routes_browser_actions.py
git commit -m "$(cat <<'EOF'
fix(prompt): route single browser commands to the DOM-exact action, not pixels

Closes the loop on this plan: browser_control click/type/look (Task 1) now
exist, but nothing told the model to reach for them over computer_control's
pixel path for a single ad-hoc command. Named live in the same babysitting
session that exposed everything else in this plan.
EOF
)"
```

---

## What this plan deliberately does not touch

The "Dynamic Island doing deep work in the background while giving ambient updates" idea is real and already has a name in this codebase's own docs — `docs/Aethelark_Roadmap.md`'s "P2" area and `Aethelark_Vision.md` §3's CASUAL/HARDCORE split, `Aethelark_Web_Pivot_Plan.md`'s (now-folded-into-Architecture.md) mode-aware focus handling. It is a real, separate, and larger UI/state-wiring project. Building a calm ambient status display for a mission that cannot yet reliably download a watch stand would be solving the wrong problem first — this plan is scoped to making the actions themselves trustworthy, which is the actual precondition for anything running unattended in the background to be worth watching. Once this plan lands and a MakerWorld-class mission can complete cleanly and repeatably, background/pill-mode wiring is the natural next plan.
