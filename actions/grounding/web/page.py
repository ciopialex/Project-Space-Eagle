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

# Hard ceiling on what gets shown to a language model, not just a walk bound:
# 600 named controls is already more than a model can use well. A
# pathological page (infinite feed, virtualised table) must not be able to
# hand back a hundred thousand records and stall the eagle either.
MAX_NODES = 600

#: Walks the DOM and returns one record per *named* control.
#:
#: Named is the filter that matters. An unnamed div is not a control a person
#: could ask for, and including it would bury the ones they can.
#:
#: Two things past that:
#:
#: 1. `accName()` falls back to `textContent` when `innerText` is empty.
#:    Chromium reports an empty `innerText` for content inside a *closed*
#:    `<details>` (and a few other not-currently-rendered states) even
#:    though the text is genuinely in the DOM and would render immediately
#:    if the widget opened — without this fallback, everything inside a
#:    collapsed disclosure widget is invisible to the collector. It is
#:    still collected without SHOWING/VISIBLE (see `checkVisibility()`
#:    below) — present-but-not-currently-actionable is the correct claim,
#:    not "does not exist."
#: 2. On a page with more named controls than `MAX_NODES`, document order
#:    would always keep the top of the DOM and lose whatever is near the
#:    user's current viewport. Controls that intersect the viewport are
#:    preferred when the cut has to be made, and the walk reports whether
#:    it had to truncate at all (a `{truncated: true}` record appended to
#:    the output — see `collector_truncated()`), so a caller can tell "every
#:    control" from "every control up to the point we stopped counting."
COLLECT_JS = r"""
(() => {
  const MAX_NODES = 600;
  // How many named candidates get fully evaluated (geometry, states, and a
  // spot in line for the viewport-preference cut below) before the walk
  // gives up looking for anything better. Bigger than MAX_NODES so a page
  // with more controls than that still gets to compare candidates against
  // each other instead of just keeping the first 600 in document order —
  // but still a fixed, small multiple, so a pathological page cannot make
  // this walk unbounded.
  const CANDIDATE_CAP = MAX_NODES * 3;

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
    // `innerText` is layout-dependent and correctly respects CSS visibility,
    // which is why it stays the first choice. But Chromium also reports an
    // empty `innerText` for content inside a closed <details> even though
    // the text is genuinely present and would render immediately if the
    // widget opened (this is the difference `checkVisibility()` below is
    // for: it tells the *state* apart from the *name*). `textContent` does
    // not share that blind spot, so it is the fallback rather than the
    // first choice — it also happily includes text from display:none
    // descendants, which `innerText` correctly leaves out.
    return clean(el.innerText) || clean(el.textContent) || clean(el.getAttribute('alt'))
        || clean(el.getAttribute('placeholder')) || clean(el.getAttribute('title'))
        || clean(el.getAttribute('name'));
  };

  const inViewport = (rect) => rect.bottom > 0 && rect.top < window.innerHeight
                             && rect.right > 0 && rect.left < window.innerWidth;

  // Pass 1: walk the DOM once, evaluate every named candidate fully (name,
  // geometry, states), and remember whether it currently intersects the
  // viewport. Nothing is written to the page yet — `data-ae-ref` is only
  // stamped on whatever survives the cut in pass 2, so an element that gets
  // dropped for being over MAX_NODES is left exactly as it was found.
  const candidates = [];
  let walkTruncated = false;
  let idx = 0;
  for (const el of document.querySelectorAll('*')) {
    if (candidates.length >= CANDIDATE_CAP) { walkTruncated = true; break; }

    try {
      const explicit = (el.getAttribute('role') || '').trim().toLowerCase();
      if (explicit === 'presentation' || explicit === 'none') continue;
      const role = explicit || implicitRole(el);
      if (!role || role === 'generic') continue;

      const name = accName(el);
      if (!name) continue;

      const rect = el.getBoundingClientRect();
      let style;
      try { style = window.getComputedStyle(el); } catch (e) { style = null; }
      // `checkVisibility()` (not just the element's own computed style) is
      // what actually catches a closed <details>: Chromium hides its
      // non-summary content through the rendering tree (closer to
      // content-visibility than to display:none), so the element's own
      // getComputedStyle().display never says 'none' and its rect is not
      // reliably zero either — but checkVisibility() correctly reports
      // false for it, same as a display:none ancestor. This is the signal
      // that keeps the textContent fallback above from also making the
      // control claim to be clickable.
      const checkVisible = (typeof el.checkVisibility === 'function')
                          ? el.checkVisibility() : true;
      const hidden = (style && (style.visibility === 'hidden' || style.display === 'none'))
                  || el.hasAttribute('hidden')
                  || el.getAttribute('aria-hidden') === 'true'
                  || !checkVisible;

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

      candidates.push({
        el: el, idx: idx++, inViewport: inViewport(rect),
        name: name, role: role,
        left: rect.left, top: rect.top,
        width: rect.width, height: rect.height,
        states: states,
        value: (el.value === undefined || el.value === null) ? '' : String(el.value).slice(0, 200),
      });
    } catch (e) {
      continue;
    }
  }

  // Pass 2: decide which candidates survive. Under the cap, everyone does.
  // Over it, controls intersecting the current viewport are kept first —
  // this is a hot loop over every element on the page, so "prefer the
  // viewport" has to stay this cheap: partition, keep the first MAX_NODES,
  // then put the survivors back in document order rather than reshuffling
  // the model's view of the page.
  let selected = candidates;
  let truncated = walkTruncated;
  if (candidates.length > MAX_NODES) {
    truncated = true;
    const nearby = candidates.filter(c => c.inViewport);
    const far = candidates.filter(c => !c.inViewport);
    selected = nearby.concat(far).slice(0, MAX_NODES);
    selected.sort((a, b) => a.idx - b.idx);
  }

  const out = [];
  let n = 0;
  for (const c of selected) {
    const ref = 'e' + n;
    try { c.el.setAttribute('data-ae-ref', ref); } catch (e) { continue; }
    out.push({
      ref: ref, name: c.name, role: c.role,
      left: c.left, top: c.top,
      width: c.width, height: c.height,
      states: c.states, value: c.value,
    });
    n += 1;
  }
  // A caller cannot tell "the page really only has this many controls" from
  // "we stopped counting" by looking at the array alone — this sentinel is
  // that difference. `nodes_from_records` drops it for free (it has no
  // `name`, so it hits the same except-and-continue path as any other
  // malformed record); `collector_truncated()` below is what reads it.
  if (truncated) out.push({ truncated: true });
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


def collector_truncated(records: Iterable[object]) -> bool:
    """True if `COLLECT_JS` had to stop before it could hand back every named
    control it found.

    Two distinct causes both set this: more named controls existed than
    `MAX_NODES` allows (the common case — see the viewport-preference cut
    in `COLLECT_JS`), or the raw walk itself hit its safety ceiling
    (`CANDIDATE_CAP`) before it finished the DOM at all. Either way, the
    node count `nodes_from_records` returns is a floor, not the true count —
    this is what lets a caller (`PageSense`, `tools/web_coverage.py`) say so
    instead of quietly reporting a number that means "we stopped counting."

    Reads the raw records rather than the `WebNode` tuple because the
    sentinel `{"truncated": true}` `COLLECT_JS` appends has no `name` and is
    silently dropped by `nodes_from_records` — the same fate as any other
    malformed record, which is correct for node-hood but would erase this
    flag if it were looked for after that conversion instead of before it.
    """
    for record in records or ():
        if isinstance(record, dict) and record.get("truncated"):
            return True
    return False


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
