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

#: How much surrounding text a single control may carry. An article page has
#: thousands of text blocks; unbounded context would push the actual controls
#: out of the model's attention, which is the failure this is meant to
#: prevent, not cause.
MAX_CONTEXT_CHARS = 160

#: The name/role derivation shared by `COLLECT_JS`, `HIT_TEST_JS`, and (via
#: `tools/web_coverage.py`'s import of this constant) the coverage
#: instrument's `_MISSED_JS`.
#:
#: This used to be three separate copies. `COLLECT_JS` computed the full
#: `accName()` chain and `implicitRole()`; `HIT_TEST_JS` computed a cut-down
#: `aria-label || innerText` for the name and `getAttribute('role') ||
#: tagName` for the role. The two never agreed except by coincidence (a bare
#: `<button>text</button>`) — every `<a href>`, every `<input>` named by
#: `label[for]`/`aria-labelledby`/a wrapping label/`alt`/`placeholder`/
#: `title`, and every checkbox named by a wrapping label came back from
#: `HIT_TEST_JS` with a *different* `(name, role)` than `COLLECT_JS` reported
#: for the exact same element. `actionability._identity` compares
#: `(name, role, bounds)` between the two, so `receives_events` could never
#: pass for any of those — every click on a link or a labelled input timed
#: out after 5s, and the model was told "it was covered by something else on
#: the page," which was false. Fixed by making both scripts call the exact
#: same functions instead of hand-maintaining two readings of "what is this
#: element called" that could drift again the moment either one changed.
#:
#: `implicitRole()` maps an element to its ARIA role when none is stated
#: explicitly. `accName()` is a deliberately simplified accessible-name
#: computation:
#:
#: 1. `accName()` falls back to `textContent` when `innerText` is empty.
#:    Chromium reports an empty `innerText` for content inside a *closed*
#:    `<details>` (and a few other not-currently-rendered states) even
#:    though the text is genuinely in the DOM and would render immediately
#:    if the widget opened — without this fallback, everything inside a
#:    collapsed disclosure widget is invisible to the collector. It is
#:    still collected without SHOWING/VISIBLE (see `checkVisibility()`
#:    in `COLLECT_JS` below) — present-but-not-currently-actionable is the
#:    correct claim, not "does not exist."
#: 2. `roleOf()` bundles the explicit-role filtering (`presentation`/`none`
#:    suppress the element; an unrecognised or `generic` role means "not a
#:    control") with the implicit-role fallback, so `COLLECT_JS`'s walk and
#:    `HIT_TEST_JS`'s point lookup make that decision identically too.
_ACCESSIBLE_NAME_JS = r"""
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

  // Explicit `role=` (filtered for presentation/none/generic), else the
  // implicit role, else null — "not a control this collector names."
  const roleOf = (el) => {
    const explicit = (el.getAttribute('role') || '').trim().toLowerCase();
    if (explicit === 'presentation' || explicit === 'none') return null;
    const role = explicit || implicitRole(el);
    if (!role || role === 'generic') return null;
    return role;
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
    const own = clean(el.innerText) || clean(el.textContent)
        || clean(el.getAttribute('alt')) || clean(el.getAttribute('placeholder'))
        || clean(el.getAttribute('title')) || clean(el.getAttribute('name'));
    if (own) return own;

    // Last resort: the name lives on a DESCENDANT, not on the element.
    // `<a href="/home"><img alt="Home"></a>` is the commonest shape on a news
    // or shop homepage, and the whole chain above misses it - `alt` is on the
    // img, and the link has no text of its own. Measured on digi24.ro, 74 of
    // 309 interactive controls were nameless for exactly this reason, and a
    // control the collector cannot name is dropped entirely: the eagle could
    // not see or click any of them, while a human clicks them by sight.
    //
    // Strictly a fallback, and only real label attributes are read - never a
    // filename, an href or a class. A decorative `alt=""` is a deliberate
    // statement that the image is not a label, so it yields nothing and the
    // control stays nameless rather than acquiring an invented one. A
    // fabricated name is worse than none: the grounder would match a
    // description to a control that does not do what the name implies.
    if (el.querySelector) {
      const inner = el.querySelector('[aria-label],[alt],[title]');
      if (inner) {
        const t = clean(inner.getAttribute('aria-label'))
            || clean(inner.getAttribute('alt'))
            || clean(inner.getAttribute('title'));
        if (t) return t;
      }
    }
    return '';
  };
"""

#: Walks the DOM and returns one record per *named* control.
#:
#: Named is the filter that matters. An unnamed div is not a control a person
#: could ask for, and including it would bury the ones they can.
#:
#: On a page with more named controls than `MAX_NODES`, document order would
#: always keep the top of the DOM and lose whatever is near the user's
#: current viewport. Controls that intersect the viewport are preferred when
#: the cut has to be made, and the walk reports whether it had to truncate at
#: all (a `{truncated: true}` record appended to the output — see
#: `collector_truncated()`), so a caller can tell "every control" from
#: "every control up to the point we stopped counting."
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

  // Ref strings are never reused, across this collect or any later one.
  //
  // They used to be positional — "e0", "e1", ... restarting from zero on
  // every collect — and that is a wrong-element bug, not merely a stale-ref
  // one. Any collect between resolving a control and acting on it silently
  // reassigns every ref string to whatever the walk finds *now*, so a live
  // element inherits the exact string an older, different node was holding.
  // The consent gate then approves one control and the browser actuates
  // another, with nothing raising, because the selector still matches
  // something. Review reproduced it twice on supposedly-fixed code: a gate
  // approving "Search" while the browser typed into "Message to seller",
  // and a gate approving "Continue" while the browser clicked "Complete
  // purchase".
  //
  // A monotonic counter on `window` makes that impossible by construction
  // rather than by discipline. A ref that has been re-stamped no longer
  // matches any element, so `[data-ae-ref="e7"]` finds nothing and the
  // actuation fails fast and retries against a fresh resolve — the same
  // path a genuinely stale ref already took. Every race in this class turns
  // from "acted on the wrong element" into "did not act", which is the only
  // safe direction for a gate to fail in.
  if (typeof window.__aeRefSeq !== 'number') window.__aeRefSeq = 0;
""" + _ACCESSIBLE_NAME_JS + r"""
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
      const role = roleOf(el);
      if (!role) continue;

      let name = accName(el);
      if (!name) {
        // A control you can TYPE INTO is a control, named or not. Measured on
        // makerworld.com: the search bar is a visible <input type="text">,
        // 870px wide, with no placeholder, no aria-label and no <label for> —
        // so it had no accessible name, so `if (!name) continue` deleted it.
        // The page reported 269 controls and ZERO editable, and every rung
        // below then failed honestly about the wrong thing.
        //
        // Scoped to typable roles on purpose. Keeping every unnamed element
        // would flood the model's line budget with things it cannot act on,
        // which is a different way of being blind.
        const TYPABLE_ROLES = ['textbox', 'searchbox', 'password', 'spinbutton'];
        if (TYPABLE_ROLES.indexOf(role) === -1) continue;
        name = role === 'searchbox' ? 'search field' : 'text field';
      }

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
    // Monotonic and never reset — see the note at the top of this script for
    // why reusing "e0", "e1", ... across collects is a wrong-element bug.
    const ref = 'e' + (window.__aeRefSeq++);
    try { c.el.setAttribute('data-ae-ref', ref); } catch (e) { continue; }
    out.push({
      ref: ref, name: c.name, role: c.role,
      left: c.left, top: c.top,
      width: c.width, height: c.height,
      states: c.states, value: c.value,
      context: '',
    });
    n += 1;
  }
  // ── What a person reads, as opposed to what they can click ──────────────
  //
  // Everything above collects CONTROLS. Measured on a real product page that
  // was 69 controls and 68 discarded text blocks - including the price. The
  // eagle could click "Bambu Lab P2S 3D Printer" and could not say it costs
  // EUR 519, so in a live session it ran a web search for that price while
  // sitting on the page displaying it. The search cost 4541ms. Collecting
  // this costs about 12ms.
  //
  // Attached to a control rather than emitted as separate nodes. A flat list
  // of every text block would double what the grounder has to score, making
  // matching worse in the act of making reading better.
  try {
    const MAX_CONTEXT_CHARS = 160;
    const owners = out.map(o => ({
      o: o, cx: o.left + o.width / 2, cy: o.top + o.height / 2,
      bits: [],
    }));
    if (owners.length) {
      for (const el of document.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) continue;
        // Only the element's OWN text, so a wrapper does not repeat every
        // word its children already contributed.
        let own = '';
        for (const kid of el.childNodes) {
          if (kid.nodeType === 3) own += ' ' + kid.textContent;
        }
        own = own.replace(/\s+/g, ' ').trim();
        if (!own || own.length > 120) continue;
        if (el.hasAttribute('data-ae-ref')) continue;   // already a control

        // Nearest control by centre distance. Spatial, because that is how a
        // person decides a price belongs to the product above it - the DOM
        // often puts them in sibling containers with no shared control.
        let best = null, bestD = Infinity;
        const tx = r.left + r.width / 2, ty = r.top + r.height / 2;
        for (const w of owners) {
          const d = Math.abs(w.cx - tx) + Math.abs(w.cy - ty);
          if (d < bestD) { bestD = d; best = w; }
        }
        if (best && bestD < 400) best.bits.push(own);
      }
      for (const w of owners) {
        if (w.bits.length) {
          // Prices arrive as separate text nodes - "EUR", "519", ".00" -
          // so a bullet between every fragment is noise. Join with spaces,
          // then close up the gaps punctuation leaves behind.
          w.o.context = w.bits.join(' ')
            .replace(/\s+([.,:%])/g, '$1')
            .replace(/([\u20AC$\u00A3])\s+/g, '$1')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, MAX_CONTEXT_CHARS);
        }
      }
    }
  } catch (e) { /* context is a bonus; never lose the controls over it */ }

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
#:
#: `name`/`role` come from the exact same `accName()`/`roleOf()` used by
#: `COLLECT_JS` — see `_ACCESSIBLE_NAME_JS`'s docstring for why this used to
#: be a second, disagreeing reading and what that broke (`receives_events`,
#: and therefore every click, for anything but a bare `<button>`). `roleOf()`
#: can return `null` here in principle (the page mutated `role=` between the
#: collect that stamped `owner`'s ref and this hit-test), so this falls back
#: to the element's own tag name in that case — the same fallback the old,
#: pre-fix code used unconditionally — rather than reporting no role at all.
HIT_TEST_JS = r"""
(() => {
""" + _ACCESSIBLE_NAME_JS + r"""
  return (pt) => {
    const hit = document.elementFromPoint(pt[0], pt[1]);
    if (!hit) return null;
    // Report the control a click here would ACTIVATE, not the innermost thing
    // under the cursor. A product card is a link wrapping an image, and the
    // collector stamps both - so `closest()` alone returned the IMAGE, whose
    // identity never matches the LINK the caller resolved. Live, that made a
    // perfectly clickable product unreachable: 5009ms across 76 tries,
    // reported as "covered by something else" with nothing covering it.
    //
    // Walk up and prefer the nearest ancestor-or-self that is genuinely
    // clickable. A modal over a button is NOT an ancestor of that button, so
    // it still wins the hit test and still blocks - which is the case this
    // check exists for.
    let owner = null;
    for (let node = hit; node; node = node.parentElement) {
      if (!node.getAttribute || !node.getAttribute('data-ae-ref')) continue;
      if (owner === null) owner = node;          // innermost, as a fallback
      const tag = node.tagName;
      const role = (node.getAttribute('role') || '').toLowerCase();
      if (tag === 'A' || tag === 'BUTTON' || role === 'link' || role === 'button') {
        owner = node;
        break;
      }
    }
    if (!owner) return null;
    const rect = owner.getBoundingClientRect();
    return {
      ref: owner.getAttribute('data-ae-ref'),
      name: accName(owner),
      role: roleOf(owner) || (owner.tagName || '').toLowerCase(),
      left: rect.left, top: rect.top, width: rect.width, height: rect.height,
      states: [], value: '',
    };
  };
})()
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
    #: Text sitting with this control that is not itself clickable - a price,
    #: a stock line, a heading. Attached HERE rather than returned as separate
    #: nodes: a flat list of every text block would double what the grounder
    #: must score, making matching worse while trying to make reading better.
    context: str = ""

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
                value=str(record.get("value") or ""),
                context=str(record.get("context") or ""),  # type: ignore[union-attr]
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
