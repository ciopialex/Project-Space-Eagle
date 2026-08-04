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
