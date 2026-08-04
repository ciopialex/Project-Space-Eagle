# Web Agency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the eagle general perception and action inside *any* web page — read the page's own structure, address controls by name, act through the browser — instead of a hand-written scraper per site.

**Architecture:** A new `actions/grounding/web/` package. A JavaScript collector reads the live DOM and returns one record per named control (role, accessible name, viewport bounds, states) — the web's equivalent of the accessibility tree the desktop grounders already walk. Those records become the **same `UINode`** the AT-SPI, UIA and AX grounders produce, so `roles.best_match`, `actionability.check`, `wait_for` and `act_and_verify` work **unchanged**. `PageSense` decides how much to look (structure by default, screenshot on escalation). `WebGrounder` implements the existing `Grounder` protocol on top. `EagleBrowser` owns a persistent Playwright context in its own thread, so the eagle browses in the background while the user keeps their machine.

**The reuse is the point.** Every layer above `UINode` was built and tested in the fast-hands plan. This plan adds a fourth backend to it, not a fourth stack.

**Tech Stack:** Python 3.12, Playwright 1.61 (sync API, already installed in `.venv`), pytest. No new dependencies.

## Global Constraints

- **The filter:** every task must move toward human emulation. A person thrown at an unfamiliar site works out its controls; they do not need someone to have pre-written five functions for that site.
- **`Grounder` protocol is fixed.** `name: str`, `available() -> bool`, `find(description: str) -> Element | None`. `WebGrounder` conforms; do not widen the protocol.
- **`Element`, `UINode` and `actionability.check` must not be modified.** If a web concept does not fit them, the web adapter bends, not the shared types.
- **Web `Element` bounds are VIEWPORT coordinates, not screen coordinates.** Every other grounder returns absolute screen coordinates. A web `Element` must never be handed to `pyautogui`, `desktop_control`, or anything that moves the physical mouse. Web actuation goes through the browser only. Every web `Element` carries `source="web"`; that is the marker to check.
- **Grounders must never raise.** Every `available()` and `find()` returns rather than throws, per the protocol docstring.
- **Tests must not require a browser.** Every unit takes an injectable page seam. Live checks are `skipif`-guarded smoke tests, in their own file.
- **New on-disk paths go through `core/user_paths`.** Do not compute a path from `Path(__file__)`.
- **The eagle's browser is never the user's profile.** No reading the real Chrome profile, no copying cookies out of the OS keyring. One persistent context under `user_paths.user_data_dir()/browser`.
- **Nothing in v1 submits.** No `web_submit`, no form-filling heuristics, no multi-tab orchestration. Task 6 builds the refusal *before* Task 8 wires any actuation the user could point at a checkout button.
- **`actions/browser_control.py` is not modified, not deleted, and not refactored.** It keeps working. No big-bang cutover. Same for `actions/youtube_video.py`.
- Test runner: `.venv/bin/python -m pytest tests -q`. Baseline is **622 passing**; that number only goes up.
- Commit after every task. Each task ends with the full suite green.

---

## Prior art — leveraged, not depended on

**Playwright** is already a dependency (`requirements.txt`, installed at 1.61 in `.venv`, used by `actions/browser_control.py` and `actions/visual_verifier.py`). We use it for two things and no more: launching a persistent context, and actuating real input events through CDP. We do **not** use `page.aria_snapshot()` — it returns YAML with no bounds and no stable refs, which means it cannot feed `Element` and cannot feed the "receives events" check. The DOM collector in Task 2 gives us both.

**The web is a better surface than the desktop for this.** ARIA states what a control *is* directly, `document.elementFromPoint` is an exact hit-test, and `getBoundingClientRect` is exact geometry. The desktop grounders had to reconstruct all three.

### What we refuse to do

- **No per-site modules.** If a task's answer is "special-case YouTube", it is the wrong answer.
- **No cookie theft.** Seeding the eagle's profile from the user's Chrome means decrypting a keyring the user never handed over. One explicit login per site instead.
- **No CAPTCHA solving, no bot-detection evasion.** When a site asks for a human, the eagle asks for the human (Task 7). That is what a human assistant does, and it is the only approach that does not rot.

---

## File Structure

| File | Responsibility |
|---|---|
| `actions/grounding/roles.py` *(modify)* | Add the `WEB` platform and the ARIA→canonical role table. One vocabulary, now four platforms. |
| `actions/grounding/web/__init__.py` *(create)* | Package exports. |
| `actions/grounding/web/page.py` *(create)* | The seam. `PageLike` protocol, the collector JavaScript, and `nodes_from_records()` turning raw records into `UINode`s. Nothing here imports Playwright. |
| `actions/grounding/web/sense.py` *(create)* | `EscalationPolicy` and `PageSense` — how much to look, and when to escalate to pixels. |
| `actions/grounding/web/grounder.py` *(create)* | `WebGrounder` — implements `Grounder`. Matching and hit-testing. |
| `actions/grounding/web/consent.py` *(create)* | `irreversible_reason()` — the refusal that must exist before actuation does. |
| `actions/grounding/web/handoff.py` *(create)* | `looks_like_auth_wall()` and `await_human()` — the supervised handoff. |
| `actions/grounding/web/browser.py` *(create)* | `EagleBrowser` — persistent context on a dedicated thread; `PagePort` adapts a Playwright `Page` to `PageLike`. The only file that imports Playwright. |
| `core/user_paths.py` *(modify)* | `browser_profile_dir()`. |
| `actions/web_agency.py` *(create)* | The tool. Returns `ToolResult`. |
| `main.py` *(modify)* | One declaration, one dispatch branch, one `ToolSpec`. |
| `tests/test_web_roles.py` … `tests/test_web_live_smoke.py` *(create)* | One test file per unit; live tests isolated and skip-guarded. |

**Why `page.py` must not import Playwright:** it is the file every other file depends on. Keeping Playwright confined to `browser.py` is what makes Tasks 2, 3, 4, 6 and 7 testable with a twelve-line fake.

---

## Task 1: ARIA role vocabulary

The web names a button `button`; AT-SPI names it `push button`; `match_score` only understands the latter. One table closes the gap, exactly as `_WINDOWS` and `_MACOS` already do.

**Files:**
- Modify: `actions/grounding/roles.py:19-21` (add `WEB`), `:90` (register the table)
- Create: `tests/test_web_roles.py`

**Interfaces:**
- Consumes: `normalize(role, platform)`, `best_match(nodes, description, threshold, platform)` — both already exist in `actions/grounding/roles.py`.
- Produces: `roles.WEB` (the string `"web"`), and `normalize(aria_role, WEB)` returning canonical AT-SPI role names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_roles.py`:

```python
"""ARIA is a fourth accent, not a fourth language.

`match_score` understands AT-SPI role names. Windows and macOS already
normalize into them. The web now does too, so "the Save button" means the same
thing whether Save is a GTK widget or a <button>.
"""
from actions.grounding.base import UINode
from actions.grounding.roles import WEB, best_match, normalize


def test_aria_button_becomes_the_canonical_push_button():
    assert normalize("button", WEB) == "push button"


def test_the_input_family_maps_onto_text_roles():
    assert normalize("textbox", WEB) == "text"
    assert normalize("searchbox", WEB) == "text"
    assert normalize("password", WEB) == "password text"


def test_selection_controls_map_onto_their_atspi_names():
    assert normalize("checkbox", WEB) == "check box"
    assert normalize("radio", WEB) == "radio button"
    assert normalize("combobox", WEB) == "combo box"
    assert normalize("switch", WEB) == "toggle button"


def test_structure_roles_map_too():
    assert normalize("link", WEB) == "link"
    assert normalize("tab", WEB) == "page tab"
    assert normalize("menuitem", WEB) == "menu item"
    assert normalize("img", WEB) == "image"


def test_role_matching_is_case_and_whitespace_insensitive():
    assert normalize("  BUTTON ", WEB) == "push button"


def test_an_unknown_aria_role_passes_through_lowercased():
    # It still matches on its name; it just earns no role bonus.
    assert normalize("feed", WEB) == "feed"
    assert normalize("", WEB) == ""


def test_best_match_picks_the_button_over_the_link_with_the_same_name():
    nodes = [
        UINode(name="Sign in", role="link", left=0, top=0, width=60, height=20),
        UINode(name="Sign in", role="button", left=0, top=40, width=60, height=20),
    ]
    hit = best_match(nodes, "the Sign in button", platform=WEB)
    assert hit is not None and hit.role == "button"
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_roles.py -q`
Expected: FAIL — `ImportError: cannot import name 'WEB' from 'actions.grounding.roles'`

- [ ] **Step 3: Add the platform constant and the table**

In `actions/grounding/roles.py`, after the existing `MACOS = "macos"` line (currently `:21`):

```python
WEB = "web"
```

Then after the `_MACOS` table ends (currently `:88`), before `_TABLES`:

```python
#: ARIA roles -> AT-SPI canonical names. The DOM collector emits explicit
#: `role=` attributes where a page sets one and an implicit role otherwise, so
#: this table is keyed on ARIA's vocabulary plus the two implicit roles HTML
#: has and ARIA does not name: "password" and "generic".
_WEB: dict[str, str] = {
    "button":        "push button",
    "link":          "link",
    "textbox":       "text",
    "searchbox":     "text",
    "password":      "password text",
    "combobox":      "combo box",
    "listbox":       "list",
    "checkbox":      "check box",
    "radio":         "radio button",
    "switch":        "toggle button",
    "menuitem":      "menu item",
    "menuitemcheckbox": "check box",
    "menuitemradio": "radio button",
    "menu":          "menu",
    "menubar":       "menu bar",
    "tab":           "page tab",
    "tablist":       "page tab list",
    "tabpanel":      "panel",
    "option":        "list item",
    "listitem":      "list item",
    "list":          "list",
    "img":           "image",
    "image":         "image",
    "figure":        "image",
    "heading":       "heading",
    "slider":        "slider",
    "spinbutton":    "spin button",
    "progressbar":   "progress bar",
    "dialog":        "dialog",
    "alertdialog":   "dialog",
    "alert":         "notification",
    "status":        "status bar",
    "toolbar":       "tool bar",
    "table":         "table",
    "grid":          "table",
    "row":           "table row",
    "cell":          "table cell",
    "gridcell":      "table cell",
    "columnheader":  "column header",
    "rowheader":     "row header",
    "tree":          "tree",
    "treeitem":      "tree item",
    "separator":     "separator",
    "group":         "panel",
    "region":        "panel",
    "form":          "panel",
    "navigation":    "panel",
    "main":          "panel",
    "article":       "panel",
    "banner":        "panel",
    "contentinfo":   "panel",
    "complementary": "panel",
    "search":        "panel",
}
```

And change the `_TABLES` line:

```python
_TABLES = {WINDOWS: _WINDOWS, MACOS: _MACOS, WEB: _WEB}
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_roles.py -q`
Expected: PASS, 7 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 629 passed (622 + 7). If any existing role test broke, the table registration is wrong — `normalize` must still return `clean` unchanged for platforms with no table.

- [ ] **Step 6: Commit**

```bash
git add actions/grounding/roles.py tests/test_web_roles.py
git commit -m "feat(grounding): teach the shared role vocabulary to speak ARIA"
```

---

## Task 2: The page seam — DOM collector and `UINode` conversion

This is the perception itself. A JavaScript walk returns one record per named control; Python turns those records into the same `UINode` every other grounder produces. No Playwright import in this file — that is what makes everything above it testable.

**Files:**
- Create: `actions/grounding/web/__init__.py`
- Create: `actions/grounding/web/page.py`
- Create: `tests/test_web_page.py`

**Interfaces:**
- Consumes: `Element` from `actions.grounding.base`.
- Produces:
  - `COLLECT_JS: str` — the collector, an IIFE returning `list[dict]`.
  - `HIT_TEST_JS: str` — an arrow function taking `[x, y]`, returning one record dict or `None`.
  - `WebNode` — frozen dataclass: `name, role, left, top, width, height, ref, states, value`, plus `.has(state)` and `.bounds_tuple`.
  - `nodes_from_records(records: Iterable[object]) -> tuple[WebNode, ...]`
  - `element_from(node: WebNode) -> Element` — `source="web"`.
  - `ref_of(node: object) -> str` — a node's `data-ae-ref`, or `""`.
  - `PageLike` protocol: `collect() -> list[dict]`, `hit_test(x, y) -> dict | None`, `screenshot() -> bytes`, `click(ref: str) -> None`, `fill(ref: str, text: str) -> None`, `url() -> str`.

**The ref carries in `UINode.value`?** No — `value` is the control's own value and is matched against. The ref rides in the node's `name`? No. `UINode` is frozen and must not change, so `nodes_from_records` returns nodes **and** a parallel `dict[id(node), str]`? Fragile. Instead: `ref_of` reads a ref that was packed into the node's `role` field? No.

**Decision:** `nodes_from_records` returns `tuple[WebNode, ...]` where `WebNode` is a frozen dataclass that *subclasses nothing* but carries every `UINode` field plus `ref`. `roles.best_match` uses only `.name`, `.role`, `.left`, `.top`, `.width`, `.height` — it is duck-typed, never isinstance-checked. This adds a field without touching the shared type, which is exactly what the Global Constraints require.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_page.py`:

```python
"""The DOM, read as structure rather than pixels.

The collector runs in the page and returns one record per *named* control.
These tests own the Python half: records in, WebNodes out, with the state
vocabulary the shared actionability layer already understands.
"""
import pytest

from actions.grounding.actionability import is_editable, is_enabled, is_visible
from actions.grounding.base import Element
from actions.grounding.web.page import (COLLECT_JS, HIT_TEST_JS, WebNode,
                                        element_from, nodes_from_records,
                                        ref_of)


def _rec(**over):
    base = {"ref": "e0", "name": "Sign in", "role": "button",
            "left": 10, "top": 20, "width": 80, "height": 30,
            "states": ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"],
            "value": ""}
    base.update(over)
    return base


def test_a_record_becomes_a_node_with_its_ref_intact():
    (node,) = nodes_from_records([_rec()])
    assert isinstance(node, WebNode)
    assert node.name == "Sign in"
    assert node.role == "button"
    assert node.bounds_tuple == (10, 20, 80, 30)
    assert ref_of(node) == "e0"


def test_states_arrive_as_a_frozenset_the_shared_checks_understand():
    (node,) = nodes_from_records([_rec()])
    el = element_from(node)
    assert is_visible(el) is True
    assert is_enabled(el) is True
    assert is_editable(el) is False


def test_an_editable_field_satisfies_the_fill_checks():
    (node,) = nodes_from_records([_rec(
        role="textbox", name="Email", value="a@b.c",
        states=["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING", "EDITABLE"])])
    el = element_from(node)
    assert is_editable(el) is True
    assert el.value == "a@b.c"


def test_a_disabled_control_is_not_enabled():
    (node,) = nodes_from_records([_rec(states=["VISIBLE", "SHOWING"])])
    assert is_enabled(element_from(node)) is False


def test_elements_are_marked_web_sourced_so_they_never_reach_the_mouse():
    (node,) = nodes_from_records([_rec()])
    assert element_from(node).source == "web"


def test_garbage_records_are_dropped_rather_than_raising():
    records = [_rec(), {"nonsense": True}, None, _rec(ref="e2", name="Help")]
    nodes = nodes_from_records(records)
    assert [n.name for n in nodes] == ["Sign in", "Help"]


def test_a_record_with_no_name_is_dropped():
    assert nodes_from_records([_rec(name="")]) == ()


def test_coordinates_are_coerced_from_floats_because_the_dom_reports_them():
    (node,) = nodes_from_records([_rec(left=10.6, top=20.4, width=80.9,
                                       height=30.2)])
    assert node.bounds_tuple == (10, 20, 80, 30)


def test_the_collector_script_clears_stale_refs_before_it_walks():
    # Refs from the previous snapshot must not survive into this one, or a
    # click will land on whatever used to be at that ref.
    assert "removeAttribute('data-ae-ref')" in COLLECT_JS


def test_the_collector_and_hit_test_are_expressions_playwright_can_evaluate():
    for script in (COLLECT_JS, HIT_TEST_JS):
        assert script.strip().startswith("(")
        assert "=>" in script
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_page.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web'`

- [ ] **Step 3: Create the package**

Create `actions/grounding/web/__init__.py`:

```python
"""Perception and action inside a web page.

The same tiering as everywhere else — structure first, pixels last — pointed at
a browser instead of a desktop. Nothing here is site-specific, on purpose: a
function per site is a treadmill that scales linearly against a web of billions.
"""
```

- [ ] **Step 4: Write the collector and the conversion**

Create `actions/grounding/web/page.py`:

```python
"""The seam between a live page and the grounding types.

This file deliberately does not import Playwright. Everything above it —
tiering, matching, the refusal, the handoff — is tested against a fake page
that returns canned records, and that is only possible while the seam stays a
plain protocol.

Coordinates here are VIEWPORT coordinates. They are used for hit-testing and
for the stability check, never to move a physical mouse; web actuation goes
through the browser. `Element.source` is "web" so that rule is checkable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from actions.grounding.base import Element

# Hard ceiling. A pathological page (infinite feed, virtualised table) must not
# be able to hand back a hundred thousand records and stall the eagle.
MAX_NODES = 600

#: Walks the DOM and returns one record per *named* control.
#:
#: Named is the filter that matters. An unnamed div is not a control a person
#: could ask for, and including it would bury the ones they can.
COLLECT_JS = r"""
(() => {
  const MAX_NODES = 600;

  // Refs from the previous snapshot must not survive, or a click resolves
  // against an element that has since moved or been replaced.
  document.querySelectorAll('[data-ae-ref]')
          .forEach(e => e.removeAttribute('data-ae-ref'));

  const implicitRole = (el) => {
    const tag = el.tagName;
    if (tag === 'A') return el.hasAttribute('href') ? 'link' : null;
    if (tag === 'BUTTON' || tag === 'SUMMARY') return 'button';
    if (tag === 'SELECT') return 'combobox';
    if (tag === 'TEXTAREA') return 'textbox';
    if (tag === 'IMG') return 'img';
    if (/^H[1-6]$/.test(tag)) return 'heading';
    if (tag === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'hidden') return null;
      if (t === 'password') return 'password';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      if (t === 'number') return 'spinbutton';
      if (t === 'search') return 'searchbox';
      if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return null;
  };

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 120);

  const accName = (el) => {
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const parts = by.split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(n => n.textContent);
      const joined = clean(parts.join(' '));
      if (joined) return joined;
    }
    const label = clean(el.getAttribute('aria-label'));
    if (label) return label;
    if (el.id) {
      try {
        const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lab) { const t = clean(lab.textContent); if (t) return t; }
      } catch (e) { /* malformed id; fall through */ }
    }
    const wrapping = el.closest && el.closest('label');
    if (wrapping && wrapping !== el) {
      const t = clean(wrapping.textContent);
      if (t) return t;
    }
    if (el.tagName === 'INPUT' && el.value && /^(submit|button|reset)$/i.test(el.type || '')) {
      return clean(el.value);
    }
    return clean(el.innerText) || clean(el.getAttribute('alt'))
        || clean(el.getAttribute('placeholder')) || clean(el.getAttribute('title'))
        || clean(el.getAttribute('name'));
  };

  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll('*')) {
    if (n >= MAX_NODES) break;

    const explicit = (el.getAttribute('role') || '').trim().toLowerCase();
    if (explicit === 'presentation' || explicit === 'none') continue;
    const role = explicit || implicitRole(el);
    if (!role || role === 'generic') continue;

    const name = accName(el);
    if (!name) continue;

    const rect = el.getBoundingClientRect();
    let style;
    try { style = window.getComputedStyle(el); } catch (e) { style = null; }
    const hidden = (style && (style.visibility === 'hidden' || style.display === 'none'))
                || el.hasAttribute('hidden')
                || el.getAttribute('aria-hidden') === 'true';

    const disabled = el.disabled === true
                  || el.getAttribute('aria-disabled') === 'true';
    const readonly = el.readOnly === true
                  || el.getAttribute('aria-readonly') === 'true';
    const typable = (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
                     || el.isContentEditable === true);

    const states = [];
    if (!disabled) { states.push('ENABLED'); states.push('SENSITIVE'); }
    if (!hidden && rect.width > 0 && rect.height > 0) {
      states.push('VISIBLE'); states.push('SHOWING');
    }
    if (typable && !readonly && !disabled) states.push('EDITABLE');
    if (el === document.activeElement) states.push('FOCUSED');
    if (el.checked === true || el.getAttribute('aria-checked') === 'true') {
      states.push('CHECKED');
    }
    if (el.selected === true || el.getAttribute('aria-selected') === 'true') {
      states.push('SELECTED');
    }

    const ref = 'e' + n;
    try { el.setAttribute('data-ae-ref', ref); } catch (e) { continue; }

    out.push({
      ref: ref, name: name, role: role,
      left: rect.left, top: rect.top,
      width: rect.width, height: rect.height,
      states: states,
      value: (el.value === undefined || el.value === null) ? '' : String(el.value).slice(0, 200),
    });
    n += 1;
  }
  return out;
})()
"""

#: Given [x, y] in viewport coordinates, the record for whatever is actually
#: there — walking up to the nearest collected ancestor. This is the exact
#: equivalent of AT-SPI's get_accessible_at_point, and it is what catches the
#: cookie banner that opened over the button.
HIT_TEST_JS = r"""
((pt) => {
  const hit = document.elementFromPoint(pt[0], pt[1]);
  if (!hit) return null;
  const owner = hit.closest('[data-ae-ref]');
  if (!owner) return null;
  const rect = owner.getBoundingClientRect();
  return {
    ref: owner.getAttribute('data-ae-ref'),
    name: (owner.getAttribute('aria-label') || owner.innerText || '')
            .replace(/\s+/g, ' ').trim().slice(0, 120),
    role: (owner.getAttribute('role') || owner.tagName).toLowerCase(),
    left: rect.left, top: rect.top, width: rect.width, height: rect.height,
    states: [], value: '',
  };
})
"""


@dataclass(frozen=True)
class WebNode:
    """One control as the page reports it.

    Carries everything `UINode` does — `roles.best_match` is duck-typed and
    reads exactly these fields — plus the `ref` the browser needs to act on it.
    A separate type rather than a wider `UINode`, because the shared type is
    used by three other backends that have no concept of a ref.
    """
    name: str
    role: str
    left: int
    top: int
    width: int
    height: int
    ref: str = ""
    states: frozenset = frozenset()
    value: str = ""

    def has(self, state: str) -> bool:
        return state in self.states

    @property
    def bounds_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


def ref_of(node: object) -> str:
    """The browser-side handle for `node`, or "" if it has none."""
    return str(getattr(node, "ref", "") or "")


def nodes_from_records(records: Iterable[object]) -> tuple[WebNode, ...]:
    """Convert raw collector output into nodes. Drops anything malformed.

    The page is hostile input: a record can be missing fields, carry NaN
    geometry, or not be a dict at all. One bad record must not cost us the
    whole snapshot.
    """
    nodes: list[WebNode] = []
    for record in records or ():
        try:
            name = str(record["name"] or "").strip()      # type: ignore[index]
            if not name:
                continue
            nodes.append(WebNode(
                name=name,
                role=str(record.get("role") or ""),        # type: ignore[union-attr]
                left=int(float(record.get("left") or 0)),  # type: ignore[union-attr]
                top=int(float(record.get("top") or 0)),    # type: ignore[union-attr]
                width=int(float(record.get("width") or 0)),   # type: ignore[union-attr]
                height=int(float(record.get("height") or 0)), # type: ignore[union-attr]
                ref=str(record.get("ref") or ""),          # type: ignore[union-attr]
                states=frozenset(record.get("states") or ()),  # type: ignore[union-attr]
                value=str(record.get("value") or ""),      # type: ignore[union-attr]
            ))
        except Exception:
            continue
    return tuple(nodes)


def element_from(node: WebNode) -> Element:
    """A `WebNode` as the shared `Element` every other layer already speaks."""
    return Element.from_bounds(node.name, node.role, node.left, node.top,
                               node.width, node.height, "web",
                               states=node.states, value=node.value)


@runtime_checkable
class PageLike(Protocol):
    """What the grounder needs from a page. Implemented for real in browser.py
    and faked in one dataclass in the tests."""

    def collect(self) -> list[dict]: ...
    def hit_test(self, x: int, y: int) -> dict | None: ...
    def screenshot(self) -> bytes: ...
    def click(self, ref: str) -> None: ...
    def fill(self, ref: str, text: str) -> None: ...
    def url(self) -> str: ...
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_page.py -q`
Expected: PASS, 10 passed.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 639 passed.

```bash
git add actions/grounding/web/__init__.py actions/grounding/web/page.py tests/test_web_page.py
git commit -m "feat(web): read a page's controls as structure, not pixels"
```

---

## Task 3: `PageSense` — the tiered representation

Cheapest sense first. Structure by default; a screenshot only when structure came back thin, when the model asks, or when acting has failed twice. The triggers live in a policy object so they are tunable without touching perception.

**Files:**
- Create: `actions/grounding/web/sense.py`
- Create: `tests/web_fakes.py` — the fake page, shared by Tasks 3, 4 and 8
- Create: `tests/test_web_sense.py`

**Interfaces:**
- Consumes: `PageLike`, `WebNode`, `nodes_from_records` from `actions.grounding.web.page`.
- Produces:
  - `EscalationPolicy(min_nodes: int = 5, max_failures: int = 2)` — frozen dataclass.
  - `Sense(tier: str, nodes: tuple[WebNode, ...], screenshot: bytes | None, reason: str)` — frozen dataclass. `tier` is `"snapshot"` or `"screenshot"`.
  - `PageSense(policy: EscalationPolicy | None = None)` with `look(page, *, want_pixels: bool = False) -> Sense`, `note_failure() -> None`, `note_success() -> None`, and a read-only `failures` property.

- [ ] **Step 1: Write the shared fake**

`tests/` is not a package and has no `conftest.py`. The repo's convention — see `tests/test_nonblocking_tools.py:37` — is an explicit `sys.path.insert` at the top of each file that needs the project root. Follow it; do not add a `conftest.py`.

Create `tests/web_fakes.py` (a helper module, not a test file — pytest will not collect it):

```python
"""A page, without a browser.

Everything above the seam is tested against this. If a test in this area needs
a real browser to run, either the test is wrong or the seam has leaked.
"""
from __future__ import annotations


class FakePage:
    """Implements `PageLike` and nothing else."""

    def __init__(self, records=(), shot=b"PNG", url="https://example.test/"):
        self._records = list(records)
        self._shot = shot
        self._url = url
        self.shots_taken = 0
        self.collects = 0
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []

    def collect(self):
        self.collects += 1
        return list(self._records)

    def hit_test(self, x, y):
        return None

    def screenshot(self):
        self.shots_taken += 1
        return self._shot

    def click(self, ref):
        self.clicked.append(ref)

    def fill(self, ref, text):
        self.filled.append((ref, text))

    def url(self):
        return self._url


LIVE = ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"]
TYPABLE = LIVE + ["EDITABLE"]


def record(ref="e0", name="Sign in", role="button", top=0, states=None,
           **over):
    """One collector record, with sane defaults."""
    rec = {"ref": ref, "name": name, "role": role,
           "left": 0, "top": top, "width": 90, "height": 24,
           "states": list(states if states is not None else LIVE),
           "value": ""}
    rec.update(over)
    return rec


def records(n):
    """`n` distinct, ordinary controls."""
    return [record(ref=f"e{i}", name=f"Control {i}", top=i * 20)
            for i in range(n)]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_web_sense.py`:

```python
"""How much to look, and when looking harder is worth it.

A screenshot is one to two orders of magnitude more expensive than a snapshot,
in tokens and in latency. The default has to be the cheap sense; the escalation
has to be automatic when the cheap one is not answering.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.sense import (EscalationPolicy, PageSense,  # noqa: E402
                                         Sense)
from tests.web_fakes import FakePage, records as _records  # noqa: E402


def test_a_rich_page_is_read_as_structure_and_costs_no_screenshot():
    page = FakePage(_records(20))
    sense = PageSense().look(page)
    assert sense.tier == "snapshot"
    assert len(sense.nodes) == 20
    assert sense.screenshot is None
    assert page.shots_taken == 0


def test_a_thin_snapshot_escalates_to_pixels():
    page = FakePage(_records(1))
    sense = PageSense().look(page)
    assert sense.tier == "screenshot"
    assert sense.screenshot == b"PNG"
    assert "thin" in sense.reason
    # The nodes we did find are still carried — escalation adds, never replaces.
    assert len(sense.nodes) == 1


def test_an_empty_page_escalates():
    assert PageSense().look(FakePage([])).tier == "screenshot"


def test_the_model_can_ask_for_pixels_on_a_rich_page():
    page = FakePage(_records(20))
    sense = PageSense().look(page, want_pixels=True)
    assert sense.tier == "screenshot"
    assert "asked" in sense.reason


def test_two_failures_escalate_the_next_look():
    page = PageSense()
    rich = FakePage(_records(20))
    assert page.look(rich).tier == "snapshot"
    page.note_failure()
    assert page.look(rich).tier == "snapshot", "one failure is not a pattern"
    page.note_failure()
    assert page.look(rich).tier == "screenshot"
    assert "failed" in page.look(rich).reason


def test_success_clears_the_failure_count():
    sense = PageSense()
    sense.note_failure()
    sense.note_failure()
    sense.note_success()
    assert sense.failures == 0
    assert sense.look(FakePage(_records(20))).tier == "snapshot"


def test_the_policy_is_tunable_without_touching_perception():
    strict = PageSense(EscalationPolicy(min_nodes=50, max_failures=1))
    assert strict.look(FakePage(_records(20))).tier == "screenshot"


def test_a_page_that_explodes_while_collecting_degrades_to_pixels():
    class Broken(FakePage):
        def collect(self):
            raise RuntimeError("navigation in flight")

    sense = PageSense().look(Broken())
    assert sense.tier == "screenshot"
    assert sense.nodes == ()


def test_a_page_that_cannot_even_screenshot_still_returns_a_sense():
    class Dead(FakePage):
        def collect(self):
            raise RuntimeError("gone")

        def screenshot(self):
            raise RuntimeError("also gone")

    sense = PageSense().look(Dead())
    assert isinstance(sense, Sense)
    assert sense.nodes == () and sense.screenshot is None
```

- [ ] **Step 3: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_sense.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web.sense'`

- [ ] **Step 4: Write `sense.py`**

```python
"""How much of a page to look at.

The accessibility snapshot is the default sense: roughly 50-200 lines for a
complex page, local, and addressable by name. A screenshot is the escalation,
not the baseline — it costs one to two orders of magnitude more and answers
fewer questions, because a picture cannot tell you that a button is disabled.

`page.screenshot()` renders from the browser compositor rather than the
display, so the escalation tier works on a background tab, an unfocused window
or a headless browser. That is what lets the eagle work while the user keeps
their screen.
"""
from __future__ import annotations

from dataclasses import dataclass

from actions.grounding.web.page import PageLike, WebNode, nodes_from_records


@dataclass(frozen=True)
class EscalationPolicy:
    """When the cheap sense stops being good enough.

    Separate from the grounder so these can be tuned — per site, per user, from
    a config file — without anyone editing perception to do it.
    """
    #: Below this many named controls, the page is not telling us enough.
    min_nodes: int = 5
    #: Consecutive failed actions before we stop trusting structure alone.
    max_failures: int = 2


@dataclass(frozen=True)
class Sense:
    """What the eagle currently perceives of a page."""
    tier: str                       # "snapshot" | "screenshot"
    nodes: tuple[WebNode, ...]
    screenshot: bytes | None
    reason: str

    @property
    def escalated(self) -> bool:
        return self.tier == "screenshot"


class PageSense:
    """Decides how hard to look, and remembers how badly it has been going."""

    def __init__(self, policy: EscalationPolicy | None = None) -> None:
        self._policy = policy or EscalationPolicy()
        self._failures = 0

    @property
    def policy(self) -> EscalationPolicy:
        return self._policy

    @property
    def failures(self) -> int:
        return self._failures

    def note_failure(self) -> None:
        """An action did not do what it should have. Two of these and we look
        harder — the same instinct as a person leaning in."""
        self._failures += 1

    def note_success(self) -> None:
        self._failures = 0

    def _escalation_reason(self, nodes: tuple[WebNode, ...],
                           want_pixels: bool) -> str:
        if want_pixels:
            return "asked for pixels"
        if len(nodes) < self._policy.min_nodes:
            return (f"snapshot came back thin ({len(nodes)} controls, "
                    f"under {self._policy.min_nodes})")
        if self._failures >= self._policy.max_failures:
            return f"acting failed {self._failures} times in a row"
        return ""

    def look(self, page: PageLike, *, want_pixels: bool = False) -> Sense:
        """Perceive `page`. Never raises — a page mid-navigation is normal."""
        try:
            nodes = nodes_from_records(page.collect())
        except Exception:
            nodes = ()

        reason = self._escalation_reason(nodes, want_pixels)
        if not reason:
            return Sense("snapshot", nodes, None,
                         f"read {len(nodes)} controls structurally")

        try:
            shot = page.screenshot()
        except Exception:
            shot = None
        return Sense("screenshot", nodes, shot, reason)
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_sense.py -q`
Expected: PASS, 9 passed.

Note the last test asserts a `Sense` is still returned when both senses fail; `tier` will be `"screenshot"` with `screenshot=None`. That is the honest report — we tried to look harder and could not.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 648 passed.

```bash
git add actions/grounding/web/sense.py tests/web_fakes.py tests/test_web_sense.py
git commit -m "feat(web): tiered page sense - structure by default, pixels on escalation"
```

---

## Task 4: `WebGrounder`

The fourth backend. It implements the existing protocol, so `wait_for`, `act_and_verify` and the actionability checks work on it without a line changing in any of them.

**Files:**
- Create: `actions/grounding/web/grounder.py`
- Create: `tests/test_web_grounder.py`

**Interfaces:**
- Consumes: `Grounder`/`Element` (`actions.grounding.base`), `best_match`/`WEB` (`actions.grounding.roles`), `PageSense` (`actions.grounding.web.sense`), `PageLike`/`element_from`/`nodes_from_records`/`ref_of` (`actions.grounding.web.page`).
- Produces:
  - `WebGrounder(page_fn: Callable[[], PageLike | None], sense: PageSense | None = None, threshold: float = 0.5)` with class attributes `name = "web"` and `cost = "fast"`.
  - `.available() -> bool`, `.find(description) -> Element | None`
  - `.find_node(description) -> WebNode | None` — same match, ref intact. This is what actuation needs.
  - `.hit_test(x, y) -> Element | None` — passed to `actionability.check` as the `hit_test` argument.
  - `.sense: PageSense` — exposed so the tool can call `note_failure()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_grounder.py`:

```python
"""A fourth backend for the same protocol.

The value of this file is mostly what it does NOT contain: no new matching
rules, no new actionability logic, no new waiting loop. The web plugs into the
ones that already exist, and these tests prove it plugs in rather than
re-implements.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.base import Grounder            # noqa: E402
from actions.grounding.waiting import wait_for         # noqa: E402
from actions.grounding.web.grounder import WebGrounder  # noqa: E402
from actions.grounding.web.page import ref_of          # noqa: E402
from tests.web_fakes import TYPABLE, FakePage, record as _rec  # noqa: E402

PAGE = [
    _rec("e0", "Home", "link", top=0),
    _rec("e1", "Sign in", "button", top=40),
    _rec("e2", "Email", "textbox", top=80, states=TYPABLE),
    _rec("e3", "Search", "searchbox", top=120, states=TYPABLE),
    _rec("e4", "Settings", "button", top=160),
    _rec("e5", "Log out", "button", top=200),
]


def _grounder(records=PAGE, page=None):
    page = page or FakePage(records)
    return WebGrounder(lambda: page), page


def test_it_satisfies_the_grounder_protocol():
    g, _ = _grounder()
    assert isinstance(g, Grounder)
    assert g.name == "web" and g.cost == "fast"


def test_it_finds_a_control_by_the_words_a_person_would_use():
    g, _ = _grounder()
    el = g.find("the Sign in button")
    assert el is not None
    assert el.name == "Sign in"
    assert el.source == "web"


def test_it_finds_the_field_not_the_button_when_asked_for_a_field():
    g, _ = _grounder()
    el = g.find("the Email field")
    assert el is not None and el.name == "Email"


def test_a_description_matching_nothing_returns_none_rather_than_a_guess():
    g, _ = _grounder()
    assert g.find("the parachute") is None


def test_find_node_keeps_the_ref_that_actuation_needs():
    g, _ = _grounder()
    node = g.find_node("Sign in")
    assert node is not None and ref_of(node) == "e1"


def test_unavailable_when_there_is_no_page():
    g = WebGrounder(lambda: None)
    assert g.available() is False
    assert g.find("anything") is None


def test_available_when_a_page_is_open():
    g, _ = _grounder()
    assert g.available() is True


def test_a_page_that_explodes_never_raises_out_of_the_grounder():
    def boom():
        raise RuntimeError("browser died")

    g = WebGrounder(boom)
    assert g.available() is False
    assert g.find("Sign in") is None


def test_hit_test_reports_what_is_actually_at_a_point():
    page = FakePage(PAGE)
    page.hit_test = lambda x, y: _rec("e1", "Sign in", top=40)
    g = WebGrounder(lambda: page)
    hit = g.hit_test(45, 52)
    assert hit is not None and hit.name == "Sign in"


def test_hit_test_returns_none_when_nothing_is_there():
    g, _ = _grounder()
    assert g.hit_test(9999, 9999) is None


def test_it_drives_the_existing_wait_for_loop_unchanged():
    # The whole point of the protocol. wait_for was written for AT-SPI and is
    # not modified by this plan.
    page = FakePage(PAGE)
    page.hit_test = lambda x, y: _rec("e1", "Sign in", top=40)
    g = WebGrounder(lambda: page)

    result = wait_for("the Sign in button", "click", resolver=g,
                      hit_test=g.hit_test, timeout=0.2,
                      sleep=lambda _s: None)
    assert result.ok is True
    assert result.element is not None and result.element.name == "Sign in"


def test_wait_for_reports_the_real_reason_a_disabled_button_never_engages():
    greyed = _rec("e9", "Continue", top=240, states=["VISIBLE", "SHOWING"])
    page = FakePage(PAGE + [greyed])
    page.hit_test = lambda x, y: greyed
    g = WebGrounder(lambda: page)

    result = wait_for("the Continue button", "click", resolver=g,
                      hit_test=g.hit_test, timeout=0.05,
                      sleep=lambda _s: None)
    assert result.ok is False
    # Not "not_found" and not a silent timeout — the honest reason.
    assert result.failed_check == "enabled"
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_grounder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web.grounder'`

- [ ] **Step 3: Write `grounder.py`**

```python
"""Locate a control on a web page, by the words a person would use for it.

The whole file is thin, and that is the result worth having: matching lives in
`roles.best_match`, readiness lives in `actionability`, retrying lives in
`waiting`. This adds a fourth source of nodes to machinery that already exists,
which is why the web arrived without a single change to any of it.
"""
from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element
from actions.grounding.roles import WEB, best_match
from actions.grounding.web.page import (PageLike, WebNode, element_from,
                                        nodes_from_records)
from actions.grounding.web.sense import PageSense


class WebGrounder:
    """Structural grounding inside a browser page."""

    name = "web"
    cost = "fast"      # in-process CDP call, milliseconds — no network model

    def __init__(self,
                 page_fn: Callable[[], PageLike | None],
                 sense: PageSense | None = None,
                 threshold: float = 0.5) -> None:
        self._page_fn = page_fn
        self.sense = sense or PageSense()
        self._threshold = threshold

    def _page(self) -> PageLike | None:
        try:
            return self._page_fn()
        except Exception:
            return None

    def available(self) -> bool:
        return self._page() is not None

    def find_node(self, description: str) -> WebNode | None:
        """The matching node, ref intact. Actuation needs the ref; `find` does
        not expose it because `Element` has nowhere to put one."""
        page = self._page()
        if page is None:
            return None
        try:
            nodes = nodes_from_records(page.collect())
        except Exception:
            return None
        try:
            return best_match(nodes, description,
                              threshold=self._threshold, platform=WEB)
        except Exception:
            return None

    def find(self, description: str) -> Element | None:
        node = self.find_node(description)
        return None if node is None else element_from(node)

    def hit_test(self, x: int, y: int) -> Element | None:
        """What is actually at this point — the modal, if a modal opened.

        Hand this to `actionability.check` as its `hit_test`; without one the
        "receives events" requirement can never pass and every click times out.
        """
        page = self._page()
        if page is None:
            return None
        try:
            record = page.hit_test(int(x), int(y))
        except Exception:
            return None
        nodes = nodes_from_records([record] if record else [])
        return element_from(nodes[0]) if nodes else None
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_grounder.py -q`
Expected: PASS, 12 passed.

If `test_it_drives_the_existing_wait_for_loop_unchanged` fails on `receives_events`, the fake's `hit_test` record must produce an `Element` with identical `(name, role, bounds)` to the found one — `actionability._identity` compares all three.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 660 passed.

```bash
git add actions/grounding/web/grounder.py tests/test_web_grounder.py
git commit -m "feat(web): a fourth grounder for the protocol that already existed"
```

---

## Task 5: `EagleBrowser` — the eagle's own persistent context

Its own profile, logged in once per site, kept warm, able to run while the user works. This is the only file that imports Playwright.

**Files:**
- Modify: `core/user_paths.py` (add `browser_profile_dir`)
- Create: `actions/grounding/web/browser.py`
- Create: `tests/test_web_browser.py`

**Interfaces:**
- Consumes: `user_paths.user_data_dir()`, `user_paths.ensure_private_dir()`, `PageLike` from `actions.grounding.web.page`.
- Produces:
  - `user_paths.browser_profile_dir() -> Path`
  - `PagePort(page)` — adapts a Playwright `Page` to `PageLike`.
  - `EagleBrowser(headless: bool | None = None, profile_dir: Path | None = None, launcher: Callable[[Any, Path, bool], Any] | None = None, playwright_fn: Callable[[], Any] | None = None)` with `.start()`, `.goto(url) -> str`, `.page() -> PagePort | None`, `.call(fn, timeout=30.0)`, `.close()`, `.running -> bool`, `.headless -> bool`, `.last_error -> str`.
  - Both `launcher` and `playwright_fn` exist only so the tests can run without a browser. `launcher(playwright, profile, headless)` returns the page.
  - `default_browser() -> EagleBrowser` — process-wide singleton.

**Why a dedicated thread:** Playwright's sync API cannot be used from a thread running an asyncio loop, and must be used from the thread that created it. `main.py` dispatches tools onto an executor whose threads are not stable across calls. So `EagleBrowser` owns one long-lived thread and marshals work to it over a queue. `actions/browser_control.py` solved the same problem with `run_coroutine_threadsafe`; the sync API is the simpler half of that trade and keeps `WebGrounder.find` synchronous, which the `Grounder` protocol requires.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_browser.py`:

```python
"""The eagle's browser is its own, and it is not the user's.

Inheriting the user's Chrome profile would hand the eagle every session the
user has ever opened, silently, and would tie it to one visible window it has
to fight the user for. One persistent profile of its own costs one login per
site, and that login is the moment the user grants access deliberately.

These tests do not launch a browser. They pin the decisions: where the profile
lives, that it is private, that work is marshalled onto the owning thread, and
that a dead browser reports rather than raises.
"""
import threading
from pathlib import Path

import pytest

from actions.grounding.web.browser import EagleBrowser, PagePort
from core import user_paths


def test_the_profile_lives_under_the_user_data_dir_never_the_repo(tmp_path,
                                                                  monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    path = user_paths.browser_profile_dir()
    assert path == tmp_path / "browser"
    assert "Space-Eagle" not in str(path)


def test_the_profile_directory_is_created_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    path = user_paths.browser_profile_dir()
    assert path.is_dir()
    import os
    import sys
    if sys.platform != "win32":
        assert oct(os.stat(path).st_mode & 0o777) == oct(0o700)


class FakePlaywrightPage:
    """Stands in for a Playwright Page — only the methods PagePort touches."""

    def __init__(self):
        self.evaluated = []
        self.clicked = []
        self.filled = []

    def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if "elementFromPoint" in script:
            return {"ref": "e1", "name": "Sign in", "role": "button",
                    "left": 0, "top": 0, "width": 10, "height": 10,
                    "states": [], "value": ""}
        return [{"ref": "e0", "name": "Home", "role": "link", "left": 0,
                 "top": 0, "width": 10, "height": 10,
                 "states": ["ENABLED"], "value": ""}]

    def screenshot(self, **kwargs):
        return b"PNG-BYTES"

    def click(self, selector, **kwargs):
        self.clicked.append(selector)

    def fill(self, selector, value, **kwargs):
        self.filled.append((selector, value))

    @property
    def url(self):
        return "https://example.test/"


def test_pageport_actuates_by_ref_through_a_real_selector():
    raw = FakePlaywrightPage()
    port = PagePort(raw, call=lambda fn: fn())
    port.click("e7")
    assert raw.clicked == ['[data-ae-ref="e7"]']


def test_pageport_fills_by_ref():
    raw = FakePlaywrightPage()
    port = PagePort(raw, call=lambda fn: fn())
    port.fill("e2", "hello@example.test")
    assert raw.filled == [('[data-ae-ref="e2"]', "hello@example.test")]


def test_pageport_collect_returns_the_collector_records():
    port = PagePort(FakePlaywrightPage(), call=lambda fn: fn())
    assert port.collect()[0]["name"] == "Home"


def test_pageport_hit_test_passes_the_point_as_one_argument():
    raw = FakePlaywrightPage()
    port = PagePort(raw, call=lambda fn: fn())
    assert port.hit_test(4, 9)["name"] == "Sign in"
    script, arg = raw.evaluated[-1]
    assert arg == [4, 9]


def test_pageport_screenshot_comes_from_the_compositor_not_the_display():
    port = PagePort(FakePlaywrightPage(), call=lambda fn: fn())
    assert port.screenshot() == b"PNG-BYTES"


def test_every_call_is_marshalled_onto_the_owning_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    seen = {}

    def launcher(playwright, profile, headless):
        seen["thread"] = threading.current_thread().name
        return FakePlaywrightPage()

    browser = EagleBrowser(launcher=launcher, playwright_fn=lambda: object())
    browser.start()
    try:
        caller = threading.current_thread().name
        ran_on = browser.call(lambda page: threading.current_thread().name)
        assert ran_on == seen["thread"]
        assert ran_on != caller
    finally:
        browser.close()


def test_a_browser_that_fails_to_launch_reports_rather_than_raises(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))

    def launcher(playwright, profile, headless):
        raise RuntimeError("chromium is not installed")

    browser = EagleBrowser(launcher=launcher, playwright_fn=lambda: object())
    browser.start()
    try:
        assert browser.running is False
        assert browser.page() is None
        assert "chromium is not installed" in browser.last_error
    finally:
        browser.close()


def test_headless_defaults_to_visible_so_a_human_can_finish_a_login(monkeypatch):
    monkeypatch.delenv("AETHELARK_BROWSER_HEADLESS", raising=False)
    assert EagleBrowser().headless is False


def test_headless_can_be_switched_on_by_environment(monkeypatch):
    monkeypatch.setenv("AETHELARK_BROWSER_HEADLESS", "1")
    assert EagleBrowser().headless is True


def test_close_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    browser = EagleBrowser(launcher=lambda p, d, h: FakePlaywrightPage(),
                           playwright_fn=lambda: object())
    browser.start()
    browser.close()
    browser.close()
    assert browser.running is False
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_browser.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web.browser'`

- [ ] **Step 3: Add `browser_profile_dir` to `core/user_paths.py`**

Append after `google_token_path()` (currently ends at `:81`):

```python
def browser_profile_dir() -> Path:
    """The eagle's own browser profile.

    Deliberately not the user's Chrome profile. Attaching to that would give
    the eagle every session the user has open — silently, without a moment
    where they chose to grant it — and would tie it to a window it has to
    fight them for. One login per site, granted on purpose, is the trade.
    """
    return ensure_private_dir(user_data_dir() / "browser")
```

- [ ] **Step 4: Write `browser.py`**

```python
"""One browser the eagle owns, kept warm, running in its own thread.

Two facts shape this file.

Playwright's sync API must be used from the thread that created it, and cannot
be used at all from a thread running an asyncio loop. Tools are dispatched onto
an executor whose threads are not stable between calls. So the browser owns one
long-lived thread and every call is marshalled onto it.

The `Grounder` protocol is synchronous. Keeping the sync API here is what lets
`WebGrounder.find` stay a plain function instead of infecting the whole
grounding stack with async.
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from actions.grounding.web.page import COLLECT_JS, HIT_TEST_JS
from core import user_paths

# A page that has not settled in this long is not going to.
_NAV_TIMEOUT_MS = 30_000
_CALL_TIMEOUT = 30.0


def _default_playwright():
    from playwright.sync_api import sync_playwright
    return sync_playwright().start()


def _default_launcher(playwright, profile: Path, headless: bool):
    """A persistent context, so logins survive between sessions."""
    context = playwright.chromium.launch_persistent_context(
        str(profile),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    context.set_default_timeout(_NAV_TIMEOUT_MS)
    pages = context.pages
    # A persistent context already opens a tab. Adopt it rather than adding a
    # second, empty one.
    return pages[0] if pages else context.new_page()


class PagePort:
    """A Playwright `Page` as the `PageLike` the grounder wants.

    `call` marshals onto the browser thread. The tests pass `lambda fn: fn()`
    to run inline.
    """

    def __init__(self, page: Any,
                 call: Callable[[Callable[[], Any]], Any] | None = None) -> None:
        self._page = page
        self._call = call or (lambda fn: fn())

    def collect(self) -> list[dict]:
        return self._call(lambda: self._page.evaluate(COLLECT_JS)) or []

    def hit_test(self, x: int, y: int) -> dict | None:
        return self._call(
            lambda: self._page.evaluate(HIT_TEST_JS, [int(x), int(y)]))

    def screenshot(self) -> bytes:
        # From the compositor, not the display: works on a background tab.
        return self._call(lambda: self._page.screenshot(type="png"))

    def click(self, ref: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'
        self._call(lambda: self._page.click(selector))

    def fill(self, ref: str, text: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'
        self._call(lambda: self._page.fill(selector, text))

    def url(self) -> str:
        try:
            return str(self._page.url)
        except Exception:
            return ""


class EagleBrowser:
    """The eagle's browser. Started once, kept until shutdown."""

    def __init__(self,
                 headless: bool | None = None,
                 profile_dir: Path | None = None,
                 launcher: Callable[[Any, Path, bool], Any] | None = None,
                 playwright_fn: Callable[[], Any] | None = None) -> None:
        if headless is None:
            headless = os.environ.get("AETHELARK_BROWSER_HEADLESS", "") \
                         .strip().lower() in ("1", "true", "yes")
        self.headless = bool(headless)
        self._profile_dir = profile_dir
        self._launcher = launcher or _default_launcher
        self._playwright_fn = playwright_fn or _default_playwright

        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._page: Any = None
        self._playwright: Any = None
        self.last_error: str = ""

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._page is not None and bool(self._thread
                                               and self._thread.is_alive())

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="EagleBrowser")
        self._thread.start()
        self._ready.wait(timeout=60)

    def _serve(self) -> None:
        try:
            profile = self._profile_dir or user_paths.browser_profile_dir()
            self._playwright = self._playwright_fn()
            self._page = self._launcher(self._playwright, Path(profile),
                                        self.headless)
        except Exception as e:
            self.last_error = str(e)
            self._page = None
        finally:
            self._ready.set()

        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, box, done = job
            try:
                box.append(("ok", fn()))
            except Exception as e:
                box.append(("err", e))
            finally:
                done.set()

    def close(self) -> None:
        if self._thread is None:
            return
        page, self._page = self._page, None
        try:
            # Playwright pages expose their own context; the fakes do not.
            context = getattr(page, "context", None)
            if context is not None and hasattr(context, "close"):
                self._submit(lambda: context.close(), timeout=10)
            playwright = self._playwright
            if playwright is not None and hasattr(playwright, "stop"):
                self._submit(lambda: playwright.stop(), timeout=10)
        except Exception:
            pass
        self._jobs.put(None)
        self._thread.join(timeout=5)
        self._thread = None
        self._playwright = None

    # ── work ────────────────────────────────────────────────────────────────

    def _submit(self, fn: Callable[[], Any], timeout: float) -> Any:
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("browser thread is not running")
        box: list = []
        done = threading.Event()
        self._jobs.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError(f"browser call exceeded {timeout}s")
        kind, payload = box[0]
        if kind == "err":
            raise payload
        return payload

    def call(self, fn: Callable[[Any], Any],
             timeout: float = _CALL_TIMEOUT) -> Any:
        """Run `fn(page)` on the browser thread and return its result."""
        return self._submit(lambda: fn(self._page_unsafe()), timeout)

    def _page_unsafe(self) -> Any:
        return self._page

    def page(self) -> PagePort | None:
        """The current page as a `PageLike`, or None if the browser is down."""
        if not self.running:
            return None
        return PagePort(self._page,
                        call=lambda fn: self._submit(fn, _CALL_TIMEOUT))

    def goto(self, url: str) -> str:
        """Navigate. Returns the URL actually landed on."""
        def _go(page):
            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            return str(page.url)

        return self.call(_go, timeout=45.0)


_DEFAULT: EagleBrowser | None = None


def default_browser() -> EagleBrowser:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = EagleBrowser()
    return _DEFAULT
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_browser.py -q`
Expected: PASS, 12 passed.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 672 passed.

Also confirm the profile guard still holds:
Run: `.venv/bin/python -m pytest tests/test_user_paths_guard.py -q`
Expected: PASS — nothing under the repo.

```bash
git add core/user_paths.py actions/grounding/web/browser.py tests/test_web_browser.py
git commit -m "feat(web): the eagle gets its own browser, not the user's"
```

---

## Task 6: The refusal — irreversible actions

Built **before** any actuation is wired, because the spec says so and because the ordering is the whole safeguard. A capability shipped first and a guard bolted on second leaves a window where the eagle can file someone's taxes with nothing in the way — and that window is exactly when it is most tempting to test it.

**Files:**
- Create: `actions/grounding/web/consent.py`
- Create: `tests/test_web_consent.py`

**Interfaces:**
- Consumes: `WebNode` from `actions.grounding.web.page`.
- Produces: `irreversible_reason(name: str, role: str = "") -> str` — a plain-language reason, or `""` when the control is ordinary.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_consent.py`:

```python
"""What the eagle will not click on its own.

The motivating example — filing taxes — ends in an act that cannot be undone.
Every other guard in this system is about doing the right thing; this one is
about not being the thing that decides.

The list is deliberately name-based rather than structural. Basing it on "is
this a form submit" would refuse the search box on every site in the world,
which trains everyone to switch it off. Basing it on what the button SAYS
refuses the small number of controls that actually commit something.
"""
import pytest

from actions.grounding.web.consent import irreversible_reason


@pytest.mark.parametrize("name", [
    "Pay now", "Complete purchase", "Place order", "Buy it now",
    "Checkout", "Transfer funds", "Submit return", "File my taxes",
    "Confirm and pay", "Delete account", "Send payment",
    "Accept and continue", "Sign agreement", "Subscribe",
])
def test_controls_that_commit_something_are_refused(name):
    assert irreversible_reason(name, "push button") != ""


@pytest.mark.parametrize("name", [
    "Search", "Sign in", "Next", "Home", "Settings", "Play",
    "Load more", "Filter", "Sort by date", "Cancel", "Back",
    "Show password", "Add to cart",
])
def test_ordinary_navigation_is_not_refused(name):
    assert irreversible_reason(name, "push button") == ""


def test_the_reason_is_plain_language_the_user_can_act_on():
    reason = irreversible_reason("Confirm and pay", "push button")
    assert "pay" in reason.lower()
    assert reason == reason.strip() and len(reason) < 200


def test_matching_ignores_case_and_punctuation():
    assert irreversible_reason("  PAY   NOW!  ") != ""
    assert irreversible_reason("Place-Order") != ""


def test_a_word_inside_another_word_does_not_trip_it():
    # "payment history" is a page, not a payment.
    assert irreversible_reason("Payment history", "link") == ""
    assert irreversible_reason("Order history", "link") == ""
    assert irreversible_reason("Purchases", "link") == ""


def test_an_empty_name_is_refused_because_we_cannot_tell_what_it_does():
    assert irreversible_reason("", "push button") != ""


def test_links_that_merely_read_are_allowed():
    assert irreversible_reason("Your orders", "link") == ""
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_consent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web.consent'`

- [ ] **Step 3: Write `consent.py`**

```python
"""The line the eagle does not cross by itself.

v1 has no fresh-explicit-yes gate for the web. Until it does, controls that
commit something are refused outright rather than clicked and apologised for.
The Constitution already requires that irreversible decisions be paused and
escalated to the human; this is that clause, made checkable.

The list is a heuristic and will be wrong in both directions. It errs toward
refusing, because the cost of one unnecessary question is a sentence and the
cost of one unnecessary payment is a payment.
"""
from __future__ import annotations

import re

#: Phrases whose presence means the control commits something. Matched on whole
#: words, so "Payment history" is a page and "Pay now" is a payment.
_COMMITTING = {
    "pay": "it pays something",
    "paying": "it pays something",
    "purchase": "it makes a purchase",
    "buy": "it buys something",
    "checkout": "it starts a checkout",
    "order": "it places an order",
    "transfer": "it transfers something",
    "submit": "it submits something",
    "file": "it files something",
    "confirm": "it confirms something that may not be undoable",
    "delete": "it deletes something",
    "remove": "it removes something",
    "cancel-subscription": "it cancels a subscription",
    "subscribe": "it starts a subscription",
    "agree": "it agrees to something on the user's behalf",
    "accept": "it accepts something on the user's behalf",
    "sign": "it signs something",
    "send": "it sends something",
    "publish": "it publishes something",
    "post": "it posts something publicly",
    "book": "it books something",
    "apply": "it submits an application",
}

#: Words that turn a committing verb back into a noun — a page you read rather
#: than an act you take.
_READING = {"history", "histories", "details", "summary", "list", "settings",
            "preferences", "methods", "method", "receipts", "receipt",
            "status", "orders", "purchases", "payments"}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]


def irreversible_reason(name: str, role: str = "") -> str:
    """Why the eagle must not click this on its own, or "" if it may.

    Returns a phrase that slots into a sentence the user hears: the tool says
    "I stopped because <reason>".
    """
    words = _words(name)
    if not words:
        return ("it has no readable label, so there is no way to tell what it "
                "does")

    if any(w in _READING for w in words):
        return ""

    for word in words:
        reason = _COMMITTING.get(word)
        if reason:
            return reason
    return ""
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_consent.py -q`
Expected: PASS, 32 passed (the parametrised cases count individually).

If `"Add to cart"` fails, note the deny-list has no `cart` entry on purpose — a cart is reversible.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 704 passed.

```bash
git add actions/grounding/web/consent.py tests/test_web_consent.py
git commit -m "feat(web): refuse the controls that commit something, before wiring any clicks"
```

---

## Task 7: The supervised handoff

When a site asks for a human, the eagle asks for the human. No CAPTCHA solving, no evasion — that arms race is unwinnable and the wrong thing to be doing.

**Files:**
- Create: `actions/grounding/web/handoff.py`
- Create: `tests/test_web_handoff.py`

**Interfaces:**
- Consumes: `WebNode` (`actions.grounding.web.page`), `Sense` (`actions.grounding.web.sense`).
- Produces:
  - `wall_reason(nodes: Iterable[WebNode], url: str = "") -> str` — `""` when the page is ordinary.
  - `await_human(check: Callable[[], str], *, timeout: float = 300.0, poll: float = 2.0, clock=time.monotonic, sleep=time.sleep) -> bool` — polls until `check()` returns `""`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_handoff.py`:

```python
"""When a site asks for a human, ask the human.

Not because solving the challenge is impossible, but because a site asking "are
you a person" and getting a machine answer is the eagle lying on the user's
behalf. A human assistant says "I need you for this bit". That degrades
gracefully as these systems change, and nothing else does.
"""
from actions.grounding.web.handoff import await_human, wall_reason
from actions.grounding.web.page import nodes_from_records


def _nodes(*specs):
    return nodes_from_records([
        {"ref": f"e{i}", "name": n, "role": r, "left": 0, "top": i * 20,
         "width": 60, "height": 20, "states": ["ENABLED", "SENSITIVE",
                                               "VISIBLE", "SHOWING"],
         "value": ""}
        for i, (n, r) in enumerate(specs)
    ])


def test_a_password_field_is_a_login_wall():
    nodes = _nodes(("Email", "textbox"), ("Password", "password"))
    assert "sign in" in wall_reason(nodes).lower()


def test_a_verification_code_field_is_a_two_factor_wall():
    nodes = _nodes(("Verification code", "textbox"), ("Verify", "button"))
    assert "code" in wall_reason(nodes).lower()


def test_a_human_check_is_named_as_one():
    nodes = _nodes(("I am not a robot", "checkbox"))
    reason = wall_reason(nodes)
    assert reason and "human" in reason.lower()


def test_an_ordinary_page_is_not_a_wall():
    nodes = _nodes(("Search", "searchbox"), ("Home", "link"),
                   ("Settings", "button"))
    assert wall_reason(nodes) == ""


def test_a_page_with_no_controls_is_not_reported_as_a_wall():
    # Thin snapshots are the escalation trigger's problem, not the handoff's.
    assert wall_reason(()) == ""


def test_await_human_returns_true_once_the_wall_clears():
    states = ["blocked", "blocked", ""]

    def check():
        return states.pop(0)

    slept = []
    assert await_human(check, timeout=100, poll=2,
                       clock=lambda: 0.0, sleep=slept.append) is True
    assert slept == [2, 2]


def test_await_human_gives_up_and_says_so():
    ticks = iter([0, 1, 2, 400])

    assert await_human(lambda: "still blocked", timeout=300, poll=2,
                       clock=lambda: next(ticks),
                       sleep=lambda _s: None) is False


def test_await_human_survives_a_check_that_explodes():
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("page navigating")
        return ""

    assert await_human(check, timeout=100, poll=0,
                       clock=lambda: 0.0, sleep=lambda _s: None) is True
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_handoff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.grounding.web.handoff'`

- [ ] **Step 3: Write `handoff.py`**

```python
"""Hand the keyboard back when the site is asking for a person.

An auth wall is not a perception problem, and no amount of better sensing
solves it. The honest move is the one a human assistant makes: stop, say what
is being asked for, and wait.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Iterable

_CODE_WORDS = {"verification", "code", "otp", "authenticator", "passcode",
               "2fa"}
_HUMAN_WORDS = {"robot", "captcha", "human", "recaptcha", "hcaptcha"}


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w}


def wall_reason(nodes: Iterable[object], url: str = "") -> str:
    """Why this page needs the user, or "" if it does not.

    Ordered most specific first: a human-verification challenge is reported as
    such even when it sits on a login page, because it is the part the eagle
    genuinely cannot do.
    """
    names: list[set[str]] = []
    roles: list[str] = []
    for node in nodes or ():
        names.append(_words(getattr(node, "name", "")))
        roles.append(str(getattr(node, "role", "")).lower())

    if any(words & _HUMAN_WORDS for words in names):
        return ("this page is asking for a human check, which the eagle will "
                "not answer on the user's behalf")

    if any(words & _CODE_WORDS for words in names):
        return ("this page is asking for a verification code, which only the "
                "user has")

    if any(role == "password" for role in roles):
        return ("this site needs the user to sign in once; after that the "
                "eagle stays signed in")

    return ""


def await_human(check: Callable[[], str], *,
                timeout: float = 300.0,
                poll: float = 2.0,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> bool:
    """Wait for `check()` to stop reporting a reason. True if it cleared.

    Five minutes by default — long enough to find a phone, short enough that a
    forgotten handoff does not pin a browser thread forever.
    """
    start = clock()
    while True:
        try:
            if not check():
                return True
        except Exception:
            pass                      # mid-navigation; look again shortly
        if clock() - start >= timeout:
            return False
        sleep(poll)
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_handoff.py -q`
Expected: PASS, 8 passed.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 712 passed.

```bash
git add actions/grounding/web/handoff.py tests/test_web_handoff.py
git commit -m "feat(web): stop and ask when the site is asking for a person"
```

---

## Task 8: The tool — `actions/web_agency.py`

Everything above, behind one callable the model can reach. Returns `ToolResult`, so `ok` is unambiguous and `guidance` tells a weaker model exactly what to do next.

**Files:**
- Create: `actions/web_agency.py`
- Create: `tests/test_web_agency.py`

**Interfaces:**
- Consumes: `ToolResult` (`core.tool_result`), `EagleBrowser`/`default_browser` (`actions.grounding.web.browser`), `WebGrounder` (`actions.grounding.web.grounder`), `PageSense` (`actions.grounding.web.sense`), `irreversible_reason` (`actions.grounding.web.consent`), `wall_reason` (`actions.grounding.web.handoff`), `act_and_verify` (`actions.grounding.verify`), `ref_of` (`actions.grounding.web.page`).
- Produces: `web_agency(parameters: dict, player=None, browser=None) -> ToolResult`.
  Actions: `open` (needs `url`), `look` (optional `want_pixels`), `click` (needs `description`), `type` (needs `description`, `text`), `close`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_agency.py`:

```python
"""The tool the model actually calls.

The contract that matters is `ok`. A tool that says "I couldn't confirm it
clicked" and gets read as success is the exact bug ToolResult exists to make
impossible, and clicking is where it would bite hardest.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.web_agency as web_agency_module           # noqa: E402
from actions.web_agency import web_agency                # noqa: E402
from core.tool_result import ToolResult                  # noqa: E402
from tests.web_fakes import TYPABLE, FakePage, record    # noqa: E402

PAGE = [
    record("e0", "Home", "link", top=0),
    record("e1", "Sign in", "button", top=40),
    record("e2", "Search", "searchbox", top=80, states=TYPABLE),
    record("e3", "Settings", "button", top=120),
    record("e4", "Complete purchase", "button", top=160),
    record("e5", "Log out", "button", top=200),
]


@pytest.fixture(autouse=True)
def _fresh_sense():
    """The escalation counter is process-wide on purpose — a person's suspicion
    carries between actions. Tests must not inherit each other's."""
    web_agency_module._SENSE.note_success()
    yield
    web_agency_module._SENSE.note_success()


class FakeBrowser:
    def __init__(self, records=None, running=True):
        self._page = FakePage(records if records is not None else PAGE)
        # Any hit-test resolves to the element itself, so "receives events"
        # passes and we are testing the tool, not the actionability layer.
        self._page.hit_test = self._hit
        self._running = running
        self.visited = []
        self.closed = False
        self.last_error = ""

    def _hit(self, x, y):
        for rec in self._page.collect():
            if (rec["left"] <= x <= rec["left"] + rec["width"]
                    and rec["top"] <= y <= rec["top"] + rec["height"]):
                return rec
        return None

    @property
    def running(self):
        return self._running

    def start(self):
        self._running = True

    def page(self):
        return self._page if self._running else None

    def goto(self, url):
        self.visited.append(url)
        return url

    def close(self):
        self.closed = True
        self._running = False


def _call(action, browser, **params):
    return web_agency({"action": action, **params}, browser=browser)


def test_open_navigates_and_reports_what_it_found():
    b = FakeBrowser()
    result = _call("open", b, url="https://example.test/")
    assert isinstance(result, ToolResult) and result.ok is True
    assert b.visited == ["https://example.test/"]
    assert "6" in result.message or "controls" in result.message


def test_look_lists_the_controls_by_name_not_by_coordinate():
    result = _call("look", FakeBrowser())
    assert result.ok is True
    assert "Sign in" in result.message and "Search" in result.message
    # No coordinate pair anywhere in what the model is shown. A hallucinated
    # "(1420, 337)" on a YouTube page is the failure this design exists to end;
    # the model cannot invent a coordinate it was never given.
    assert re.search(r"\d+\s*,\s*\d+", result.message) is None


def test_look_on_a_thin_page_escalates_and_says_so():
    b = FakeBrowser(records=PAGE[:1])
    result = _call("look", b)
    assert result.ok is True
    assert result.data.get("tier") == "screenshot"


def test_click_acts_and_confirms():
    b = FakeBrowser()
    result = _call("click", b, description="the Sign in button")
    assert result.ok is True
    assert "Sign in" in result.message


def test_clicking_something_that_is_not_there_fails_with_guidance():
    result = _call("click", FakeBrowser(), description="the parachute")
    assert result.ok is False
    assert result.guidance      # a concrete next step, not just an apology
    assert "look" in result.guidance.lower()


def test_a_committing_control_is_refused_and_the_reason_is_returned():
    b = FakeBrowser()
    result = _call("click", b, description="Complete purchase")
    assert result.ok is False
    assert "purchase" in result.message.lower()
    assert "user" in result.guidance.lower()


def test_type_fills_a_field():
    b = FakeBrowser()
    result = _call("type", b, description="the Search field", text="eagles")
    assert result.ok is True


def test_typing_into_something_that_is_not_a_field_fails_honestly():
    b = FakeBrowser()
    result = _call("type", b, description="the Sign in button", text="x")
    assert result.ok is False
    assert "editable" in (result.message + result.guidance).lower()


def test_an_auth_wall_is_reported_rather_than_worked_around():
    b = FakeBrowser(records=[
        {"ref": "e0", "name": "Email", "role": "textbox", "left": 0, "top": 0,
         "width": 60, "height": 20, "states": ["ENABLED", "SENSITIVE",
                                               "VISIBLE", "SHOWING",
                                               "EDITABLE"], "value": ""},
        {"ref": "e1", "name": "Password", "role": "password", "left": 0,
         "top": 40, "width": 60, "height": 20,
         "states": ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING", "EDITABLE"],
         "value": ""},
    ])
    result = _call("look", b)
    assert result.data.get("needs_human")
    assert "sign in" in result.message.lower()


def test_a_browser_that_will_not_start_fails_with_a_usable_next_step():
    b = FakeBrowser(running=False)
    b.start = lambda: None          # refuses to come up
    b.last_error = "chromium is not installed"
    result = _call("look", b)
    assert result.ok is False
    assert "playwright install" in result.guidance.lower()


def test_an_unknown_action_is_refused_with_the_list_of_real_ones():
    result = _call("teleport", FakeBrowser())
    assert result.ok is False
    assert "look" in result.guidance


def test_close_shuts_the_browser_down():
    b = FakeBrowser()
    assert _call("close", b).ok is True
    assert b.closed is True


def test_two_failed_clicks_arm_the_escalation_for_the_next_look():
    b = FakeBrowser()
    _call("click", b, description="the parachute")
    _call("click", b, description="the submarine")
    result = _call("look", b)
    assert result.data.get("tier") == "screenshot"
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_agency.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.web_agency'`

- [ ] **Step 3: Write `web_agency.py`**

```python
"""Use a website the way a person would.

Not a function per site. One way of perceiving any page and acting inside it,
so the answer to "can you do X on this site" stops depending on whether someone
wrote X.

Everything irreversible is refused here rather than attempted and apologised
for, and everything a site wants a human for is handed back to the human.
"""
from __future__ import annotations

from typing import Any

from actions.grounding.verify import act_and_verify
from actions.grounding.web.consent import irreversible_reason
from actions.grounding.web.grounder import WebGrounder
from actions.grounding.web.handoff import wall_reason
from actions.grounding.web.page import ref_of
from actions.grounding.web.sense import PageSense
from core.tool_result import ToolResult

_ACTIONS = ("open", "look", "click", "type", "close")

_NO_BROWSER_GUIDANCE = (
    "The eagle's browser could not start. Run "
    "`.venv/bin/python -m playwright install chromium` once, then try again."
)

# One sense per process, so the failure count that drives escalation survives
# across tool calls the way a person's growing suspicion does.
_SENSE = PageSense()


def _browser(explicit):
    if explicit is not None:
        return explicit
    from actions.grounding.web.browser import default_browser
    return default_browser()


def _ready(browser) -> ToolResult | None:
    """None when the browser is usable, a failure ToolResult otherwise."""
    if not browser.running:
        try:
            browser.start()
        except Exception as e:
            return ToolResult.failure(f"The browser did not start: {e}",
                                      guidance=_NO_BROWSER_GUIDANCE)
    if not browser.running:
        detail = getattr(browser, "last_error", "") or "no further detail"
        return ToolResult.failure(f"The browser did not start: {detail}",
                                  guidance=_NO_BROWSER_GUIDANCE)
    return None


def _describe(nodes) -> str:
    return "\n".join(f"- {n.name} ({n.role})" for n in nodes[:60])


def _look(browser, want_pixels: bool) -> ToolResult:
    page = browser.page()
    sense = _SENSE.look(page, want_pixels=want_pixels)
    needs_human = wall_reason(sense.nodes, page.url() if page else "")

    lines = [f"{len(sense.nodes)} controls on {page.url() if page else 'the page'}:"]
    if sense.nodes:
        lines.append(_describe(sense.nodes))
    if sense.escalated:
        lines.append(f"(Looked closer: {sense.reason}.)")
    if needs_human:
        lines.append(f"This needs the user — {needs_human}.")

    return ToolResult.success(
        "\n".join(lines),
        tier=sense.tier,
        controls=[n.name for n in sense.nodes],
        needs_human=needs_human,
        has_screenshot=sense.screenshot is not None,
    )


def _click(browser, grounder: WebGrounder, description: str) -> ToolResult:
    node = grounder.find_node(description)
    if node is None:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"No control on this page matches '{description}'.",
            guidance=("Call web_agency action='look' to see what is actually "
                      "on the page, then use one of those names."))

    reason = irreversible_reason(node.name, node.role)
    if reason:
        return ToolResult.failure(
            f"Refused to click '{node.name}' because {reason}.",
            guidance=("Tell the user exactly what this would do and ask them "
                      "to confirm it themselves. The eagle does not take "
                      "irreversible actions on their behalf."))

    page = browser.page()
    ref = ref_of(node)
    outcome = act_and_verify(
        description,
        lambda _el: page.click(ref),
        resolver=grounder,
        action="click",
        hit_test=grounder.hit_test,
        timeout=5.0,
    )
    if not outcome["acted"]:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"Could not click '{node.name}': {outcome['detail']}",
            guidance=("The control was found but never became ready. Call "
                      "action='look' with want_pixels=true to see the page as "
                      "an image, then decide."))

    _SENSE.note_success()
    return ToolResult.success(
        f"Clicked '{node.name}' — {outcome['detail']}.",
        changed=outcome["changed"], control=node.name)


def _type(browser, grounder: WebGrounder, description: str,
          text: str) -> ToolResult:
    node = grounder.find_node(description)
    if node is None:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"No field on this page matches '{description}'.",
            guidance=("Call web_agency action='look' to see what is actually "
                      "on the page, then use one of those names."))

    page = browser.page()
    ref = ref_of(node)
    outcome = act_and_verify(
        description,
        lambda _el: page.fill(ref, text),
        resolver=grounder,
        action="fill",
        hit_test=grounder.hit_test,
        timeout=5.0,
    )
    if not outcome["acted"]:
        _SENSE.note_failure()
        return ToolResult.failure(
            f"Could not type into '{node.name}': {outcome['detail']}",
            guidance=("If the failed check was 'editable', that control is not "
                      "a text field — call action='look' and pick one whose "
                      "role is a textbox."))

    _SENSE.note_success()
    return ToolResult.success(f"Typed into '{node.name}'.", control=node.name)


def web_agency(parameters: dict | None = None, player: Any = None,
               browser: Any = None) -> ToolResult:
    """Perceive and act inside a web page. See `_ACTIONS` for the verbs."""
    params = parameters or {}
    action = str(params.get("action") or "").strip().lower()

    if action not in _ACTIONS:
        return ToolResult.failure(
            f"'{action or '(none)'}' is not something this tool does.",
            guidance=f"Use one of: {', '.join(_ACTIONS)}.")

    browser = _browser(browser)

    if action == "close":
        try:
            browser.close()
        except Exception as e:
            return ToolResult.failure(f"Could not close the browser: {e}",
                                      guidance="It may already be closed.")
        return ToolResult.success("Closed the eagle's browser.")

    not_ready = _ready(browser)
    if not_ready is not None:
        return not_ready

    if action == "open":
        url = str(params.get("url") or "").strip()
        if not url:
            return ToolResult.failure(
                "No URL to open.",
                guidance="Pass url='https://…' with action='open'.")
        if "://" not in url:
            url = "https://" + url
        try:
            browser.goto(url)
        except Exception as e:
            return ToolResult.failure(f"Could not open {url}: {e}",
                                      guidance=("Check the address, or tell "
                                                "the user the site did not "
                                                "respond."))
        return _look(browser, want_pixels=False)

    if action == "look":
        return _look(browser, want_pixels=bool(params.get("want_pixels")))

    grounder = WebGrounder(browser.page, sense=_SENSE)
    description = str(params.get("description") or "").strip()
    if not description:
        return ToolResult.failure(
            f"No control described for '{action}'.",
            guidance="Pass description='the Sign in button'.")

    if action == "click":
        return _click(browser, grounder, description)

    return _type(browser, grounder, description,
                 str(params.get("text") or ""))
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_agency.py -q`
Expected: PASS, 13 passed.

If `test_a_committing_control_is_refused` fails, check that `_click` calls `irreversible_reason` **before** `act_and_verify` and returns without acting — a refusal that fires after the click is not a refusal.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 725 passed.

```bash
git add actions/web_agency.py tests/test_web_agency.py
git commit -m "feat(web): one tool for using any website"
```

---

## Task 9: Wire it into the eagle

One declaration, one dispatch branch, one `ToolSpec`. The `ToolSpec` is the interesting line: `web_agency` does **not** write to `desktop` and is **not** exclusive, which is what makes "play a song while it does my taxes" true at the scheduler and not just in the architecture.

**Files:**
- Modify: `main.py:54` (import), `main.py:433` (end of the `browser_control` entry in `TOOL_DECLARATIONS`), `main.py:795` (`TOOL_SPECS`), `main.py:1127` (after the `browser_control` dispatch branch)
- Create: `tests/test_web_agency_wired.py`

Line numbers are from the state of `main.py` at plan time and will have shifted. Anchor on the `browser_control` occurrences instead: `grep -n "browser_control" main.py` returns exactly the four places, in the four different structures, that this task mirrors.

**Interfaces:**
- Consumes: `web_agency` from `actions.web_agency`; `ToolSpec` and `TOOL_SPECS` already in `main.py`.
- Produces: nothing new — this task only connects existing pieces.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_agency_wired.py`:

```python
"""Declared, dispatched, and scheduled as concurrent.

The last line is the one worth a test. Every other browser tool in this file
declares writes=["desktop"], because it drives the user's own browser and
therefore their screen. This one does not, and that is the difference between
an eagle that takes your machine over and one that works alongside you.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def _declaration(name):
    for tool in main.TOOL_DECLARATIONS:
        if tool.get("name") == name:
            return tool
    return None


def test_the_tool_is_declared_to_the_model():
    decl = _declaration("web_agency")
    assert decl is not None, "web_agency is not in the tool declarations"
    assert "action" in decl["parameters"]["properties"]
    assert decl["parameters"]["required"] == ["action"]


def test_the_declaration_lists_every_action_the_tool_implements():
    from actions.web_agency import _ACTIONS
    described = _declaration("web_agency")["parameters"]["properties"]["action"]
    for verb in _ACTIONS:
        assert verb in described["description"], f"'{verb}' is undocumented"


def test_it_is_not_exclusive_so_it_can_run_while_the_user_works():
    spec = main.TOOL_SPECS["web_agency"]
    assert spec.exclusive is False
    assert "desktop" not in spec.writes


def test_it_is_dispatched():
    import inspect
    source = inspect.getsource(main)
    assert 'elif name == "web_agency"' in source
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_web_agency_wired.py -q`
Expected: FAIL — `web_agency is not in the tool declarations`.

- [ ] **Step 3: Add the import**

In `main.py`, after `from actions.browser_control import browser_control` (currently `:54`):

```python
from actions.web_agency       import web_agency
```

- [ ] **Step 4: Add the declaration**

In `main.py`, immediately after the `browser_control` declaration block closes (currently `:433`):

```python
    {
        "name": "web_agency",
        "description": (
            "Uses a website the way a person would, in the eagle's OWN browser — "
            "reads what controls the page actually has, then clicks and types by "
            "name. Use this for working INSIDE a site: pressing buttons, filling "
            "fields, navigating an interface the eagle has never seen. Runs in the "
            "background, so it does not take over the user's screen. "
            "Use browser_control instead when the user just wants a page opened in "
            "THEIR browser with their own logins. "
            "The eagle's browser has its own logins: the first visit to a site may "
            "need the user to sign in once, and this tool will say so."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open (go to a url and report its controls) | look (re-read the current page) | click | type | close"},
                "url":         {"type": "STRING", "description": "URL for the open action"},
                "description": {"type": "STRING", "description": "Which control, in plain words: 'the Sign in button', 'the Email field'. Use a name from the last look."},
                "text":        {"type": "STRING", "description": "Text to type, for the type action"},
                "want_pixels": {"type": "BOOLEAN", "description": "Force a screenshot on look, when the structural read is not enough"},
            },
            "required": ["action"]
        }
    },
```

- [ ] **Step 5: Add the `ToolSpec`**

In `main.py`, after the `browser_control` entry in `TOOL_SPECS` (currently `:795`):

```python
    # Reads the web in its own browser; touches neither the user's screen nor
    # their browser. Non-exclusive on purpose — this is the tool that can run
    # while the user is doing something else.
    "web_agency": ToolSpec(reads=["web"], priority=1, timeout_s=90.0),
```

- [ ] **Step 6: Add the dispatch branch**

In `main.py`, after the `browser_control` branch (currently `:1127`):

```python
            elif name == "web_agency":
                r = await loop.run_in_executor(self._tool_executor, lambda: web_agency(parameters=args, player=self.ui))
                result = r or "Done."
```

- [ ] **Step 7: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_web_agency_wired.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 8: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 729 passed.

```bash
git add main.py tests/test_web_agency_wired.py
git commit -m "feat: wire web agency in as a tool that runs alongside the user"
```

---

## Task 10: Live smoke tests

Everything so far is proven against fakes. This proves the collector actually reads a real page — which is the one thing a fake structurally cannot tell us.

**Files:**
- Create: `tests/test_web_live_smoke.py`
- Create: `tests/fixtures/web/sample_page.html`

**Interfaces:**
- Consumes: everything built above.
- Produces: nothing importable. This file only observes.

- [ ] **Step 1: Write the fixture page**

Create `tests/fixtures/web/sample_page.html`:

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Sample</title></head>
<body>
  <nav><a href="/home">Home</a> <a href="/help">Help</a></nav>
  <h1>Sample page</h1>

  <form>
    <label for="email">Email address</label>
    <input id="email" type="email" placeholder="you@example.com">

    <label for="pw">Password</label>
    <input id="pw" type="password">

    <label><input type="checkbox" id="remember"> Remember me</label>

    <button type="submit">Sign in</button>
    <button type="button" disabled>Disabled button</button>
  </form>

  <div role="button" tabindex="0" aria-label="Custom widget">not a button tag</div>
  <div role="presentation">should never be collected</div>
  <span>bare text, no role</span>
  <button style="display:none">Hidden button</button>
</body>
</html>
```

- [ ] **Step 2: Write the live test**

Create `tests/test_web_live_smoke.py`:

```python
"""Against a real browser, on a real page.

Everything else in this plan is tested against a fake page, which proves the
Python is right and proves nothing at all about the JavaScript. This file is
the only thing that can catch a collector that does not actually collect.

Skipped when Playwright's browsers are not installed, so CI and a fresh
checkout stay green:
    .venv/bin/python -m playwright install chromium
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="playwright not installed")

from actions.grounding.web.browser import EagleBrowser        # noqa: E402
from actions.grounding.web.grounder import WebGrounder        # noqa: E402
from actions.grounding.web.handoff import wall_reason         # noqa: E402
from actions.grounding.web.page import nodes_from_records     # noqa: E402

PAGE_URL = (Path(__file__).parent / "fixtures" / "web"
            / "sample_page.html").resolve().as_uri()


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    b = EagleBrowser(headless=True,
                     profile_dir=tmp_path_factory.mktemp("eagle-profile"))
    b.start()
    if not b.running:
        pytest.skip(f"could not launch chromium: {b.last_error}")
    try:
        b.goto(PAGE_URL)
        yield b
    finally:
        b.close()


def test_the_collector_reads_the_real_dom(browser):
    nodes = nodes_from_records(browser.page().collect())
    names = {n.name for n in nodes}
    assert "Sign in" in names
    assert "Home" in names
    assert "Custom widget" in names, "explicit role= was ignored"


def test_labels_become_accessible_names(browser):
    nodes = nodes_from_records(browser.page().collect())
    assert any(n.name == "Email address" and n.role == "textbox"
               for n in nodes), "a <label for=…> did not become the name"


def test_presentational_and_unnamed_elements_are_left_out(browser):
    nodes = nodes_from_records(browser.page().collect())
    names = {n.name for n in nodes}
    assert "should never be collected" not in names
    assert "bare text, no role" not in names


def test_a_disabled_button_reports_as_disabled(browser):
    nodes = nodes_from_records(browser.page().collect())
    disabled = next(n for n in nodes if n.name == "Disabled button")
    assert "ENABLED" not in disabled.states


def test_a_hidden_button_reports_as_not_showing(browser):
    nodes = nodes_from_records(browser.page().collect())
    hidden = next(n for n in nodes if n.name == "Hidden button")
    assert "SHOWING" not in hidden.states


def test_a_password_field_is_recognised_as_an_auth_wall(browser):
    nodes = nodes_from_records(browser.page().collect())
    assert wall_reason(nodes) != ""


def test_the_grounder_finds_a_control_by_plain_words(browser):
    g = WebGrounder(browser.page)
    el = g.find("the Sign in button")
    assert el is not None and el.name == "Sign in"
    assert el.width > 0 and el.height > 0, "no real geometry came back"


def test_hit_testing_returns_the_control_at_its_own_centre(browser):
    g = WebGrounder(browser.page)
    el = g.find("the Sign in button")
    hit = g.hit_test(el.x, el.y)
    assert hit is not None and hit.name == "Sign in"


def test_typing_into_a_real_field_changes_its_value(browser):
    g = WebGrounder(browser.page)
    node = g.find_node("the Email address field")
    assert node is not None
    from actions.grounding.web.page import ref_of
    browser.page().fill(ref_of(node), "eagle@example.test")

    after = g.find("the Email address field")
    assert after is not None and after.value == "eagle@example.test"


def test_clicking_a_real_checkbox_checks_it(browser):
    g = WebGrounder(browser.page)
    from actions.grounding.web.page import ref_of
    node = g.find_node("the Remember me checkbox")
    assert node is not None
    browser.page().click(ref_of(node))

    after = g.find("the Remember me checkbox")
    assert after is not None and after.has("CHECKED")


def test_a_screenshot_comes_back_from_a_headless_browser(browser):
    shot = browser.page().screenshot()
    assert shot[:4] == b"\x89PNG", "not a PNG — the compositor read failed"
```

- [ ] **Step 3: Install the browser binary and run**

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/test_web_live_smoke.py -q
```

Expected: PASS, 11 passed.

**This is the step where the collector's JavaScript gets debugged.** Expect at least one failure on the first run — most likely accessible-name extraction, where a wrapping `<label>` returns the input's own text alongside the label's. Fix the JavaScript in `page.py`, not the test. To see what the page is actually returning:

```bash
.venv/bin/python -c "
from pathlib import Path
from actions.grounding.web.browser import EagleBrowser
b = EagleBrowser(headless=True, profile_dir=Path('/tmp/eagle-debug'))
b.start()
b.goto(Path('tests/fixtures/web/sample_page.html').resolve().as_uri())
for r in b.page().collect():
    print(r)
b.close()
"
```

- [ ] **Step 4: Verify it skips cleanly without a browser binary**

Run: `PLAYWRIGHT_BROWSERS_PATH=/nonexistent .venv/bin/python -m pytest tests/test_web_live_smoke.py -q`
Expected: 11 skipped, 0 failed. If it errors instead of skipping, the `browser` fixture's skip guard is not catching the launch failure.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 740 passed.

```bash
git add tests/test_web_live_smoke.py tests/fixtures/web/sample_page.html
git commit -m "test(web): prove the collector against a real browser"
```

---

## Task 11: Measure it, and say so honestly

The spec names a success metric that is deliberately not "number of sites supported". Measuring it is what stops the treadmill quietly reappearing.

**Files:**
- Create: `tools/web_coverage.py`
- Modify: `docs/superpowers/specs/2026-08-04-web-agency-design.md` (status line)

**Interfaces:**
- Consumes: `EagleBrowser`, `nodes_from_records`.
- Produces: a script, run by hand. Not a test — it needs the network.

- [ ] **Step 1: Write the measurement script**

Create `tools/web_coverage.py`:

```python
"""How much of a page the eagle can actually see.

Success for this work is 'share of controls found structurally on a site never
seen before' — not 'number of sites supported'. The second number is the
treadmill this design exists to leave, and it is the one that will quietly pull
us back if nobody is watching the first.

Run:  .venv/bin/python tools/web_coverage.py https://example.com [...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.browser import EagleBrowser      # noqa: E402
from actions.grounding.web.page import nodes_from_records   # noqa: E402

# Everything a person could plausibly click or type into.
_INTERACTIVE_JS = """
(() => document.querySelectorAll(
  'a[href], button, input:not([type=hidden]), select, textarea, ' +
  '[role=button], [role=link], [role=textbox], [role=checkbox], ' +
  '[role=tab], [role=menuitem], [contenteditable=true]'
).length)()
"""


def measure(browser: EagleBrowser, url: str) -> dict:
    browser.goto(url)
    page = browser.page()
    total = browser.call(lambda p: p.evaluate(_INTERACTIVE_JS)) or 0
    nodes = nodes_from_records(page.collect())
    named = [n for n in nodes if n.name]
    return {
        "url": url,
        "interactive": total,
        "perceived": len(named),
        "share": (len(named) / total) if total else 0.0,
    }


def main(urls: list[str]) -> int:
    browser = EagleBrowser(headless=True)
    browser.start()
    if not browser.running:
        print(f"could not launch: {browser.last_error}")
        return 1
    try:
        rows = []
        for url in urls:
            try:
                rows.append(measure(browser, url))
            except Exception as e:
                print(f"{url}: FAILED ({e})")
        for row in rows:
            print(f"{row['share']:6.1%}  {row['perceived']:4d}/"
                  f"{row['interactive']:<4d}  {row['url']}")
        if rows:
            avg = sum(r["share"] for r in rows) / len(rows)
            print(f"\naverage structural coverage: {avg:.1%} "
                  f"across {len(rows)} sites")
    finally:
        browser.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 2: Run it against sites nobody tuned it for**

```bash
.venv/bin/python tools/web_coverage.py \
  https://en.wikipedia.org/wiki/Bird_of_prey \
  https://news.ycombinator.com \
  https://www.youtube.com \
  https://developer.mozilla.org
```

Record the real numbers. There is no pass mark to hit here — the number is the baseline the next plan has to beat, and writing down a bad one honestly is worth more than tuning until it looks good.

- [ ] **Step 3: Record the result in the spec**

Change the status line at `docs/superpowers/specs/2026-08-04-web-agency-design.md:4`:

```markdown
**Status:** v1 implemented 2026-08-__ — see `docs/superpowers/plans/2026-08-04-web-agency.md`

**Measured structural coverage at v1:** __% across N sites
(`tools/web_coverage.py`). Automatisms and the submission gate remain unbuilt.
```

- [ ] **Step 4: Commit**

```bash
git add tools/web_coverage.py docs/superpowers/specs/2026-08-04-web-agency-design.md
git commit -m "test(web): measure structural coverage, and write the real number down"
```

---

## Not in this plan

**Automatisms.** The spec designs them and the design holds — semantic steps, never coordinates, `act_and_verify` as the invalidation trigger, a fast path that falls back to perception rather than replacing it. They are v2 and get their own plan, because v1 has to prove that perceiving a page works before there is any point remembering how a page went. Nothing here forecloses them: `find_node` already returns a named, role-typed step, which is exactly what a recorder would capture.

**The submission gate.** v1 refuses (Task 6). v2 replaces the refusal with a fresh-explicit-yes gate and only then wires anything that submits. The order is not negotiable — the guard exists first.

**Multi-tab orchestration and form-filling heuristics.** Out of scope per the spec.

**Retiring the scrapers.** `actions/youtube_video.py` and `actions/browser_control.py` keep working, untouched, until the general path demonstrably covers each case. The coverage number from Task 11 is what that argument will be made from.

---

## Definition of Done

- [ ] Full suite green: **740 passing**, up from 622.
- [ ] `tests/test_web_live_smoke.py` passes with chromium installed, and skips cleanly without it.
- [ ] `tools/web_coverage.py` has been run against at least four sites and the real number is written into the spec.
- [ ] `actions/browser_control.py` and `actions/youtube_video.py` are unmodified: `git diff main --stat` shows neither.
- [ ] No web `Element` reaches `pyautogui`: `grep -rn "source == \"web\"" actions/` shows the guard, and nothing in `actions/computer_control.py` or `actions/desktop.py` consumes a `WebGrounder`.
- [ ] `main` is shippable, and the branch is merged with the suite green.
