"""How much of a page the eagle can actually see.

Success for this work is 'share of controls found structurally on a site never
seen before' — not 'number of sites supported'. The second number is the
treadmill this design exists to leave, and it is the one that will quietly pull
us back if nobody is watching the first.

This script only reads a page: navigate, collect, count, close. It never
clicks, types, or submits anything, and it never touches the user's real
Chrome profile — every run gets a fresh, throwaway profile directory that is
deleted before the process exits. Run it only against public pages you are
certain you are not logged into.

Run:  .venv/bin/python tools/web_coverage.py https://example.com [...]
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.browser import EagleBrowser      # noqa: E402
from actions.grounding.web.page import (_ACCESSIBLE_NAME_JS, MAX_NODES,  # noqa: E402
                                        collector_truncated, nodes_from_records)

# Everything a person could plausibly click or type into.
_INTERACTIVE_JS = """
(() => document.querySelectorAll(
  'a[href], button, input:not([type=hidden]), select, textarea, ' +
  '[role=button], [role=link], [role=textbox], [role=checkbox], ' +
  '[role=tab], [role=menuitem], [contenteditable=true]'
).length)()
"""

# Run *after* `page.collect()` has stamped `data-ae-ref` on everything it
# perceived. Anything matching the interactive selector above that is left
# without a ref is a control the collector missed — this reports which ones,
# and a best-effort guess at why, mirroring the skip logic in COLLECT_JS
# (actions/grounding/web/page.py): explicit role=presentation/none, no usable
# role, or no accessible name. Anything left over has a role and a name but
# still wasn't collected — the most likely explanation is that COLLECT_JS's
# own MAX_NODES budget was spent on elements earlier in DOM order.
#
# Built from `_ACCESSIBLE_NAME_JS`, the same name/role fragment `COLLECT_JS`
# and `HIT_TEST_JS` use, rather than its own copy — this diagnostic used to
# hand-maintain a second `implicitRole`/`hasName` that had already drifted
# from the real collector once (no `img`/heading/range/number/search roles,
# and its own `hasName` needed a separate fix to add the textContent
# fallback COLLECT_JS's `accName()` already had). Sharing the fragment means
# a future collector change shows up here automatically instead of needing a
# second, hand-applied patch that this measurement tool could silently miss.
_MISSED_JS = r"""
(() => {
""" + _ACCESSIBLE_NAME_JS + r"""
  const nodes = document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea, ' +
    '[role=button], [role=link], [role=textbox], [role=checkbox], ' +
    '[role=tab], [role=menuitem], [contenteditable=true]'
  );
  const missed = [];
  for (const el of nodes) {
    if (el.hasAttribute('data-ae-ref')) continue;
    const explicit = (el.getAttribute('role') || '').trim().toLowerCase();
    const role = roleOf(el);
    let reason;
    if (explicit === 'presentation' || explicit === 'none') {
      reason = 'role suppressed (presentation/none)';
    } else if (!role) {
      reason = 'no usable role (missing or generic)';
    } else if (!accName(el)) {
      // accName() is the exact function COLLECT_JS uses, textContent
      // fallback and all — reaching this branch means the real collector
      // would find no name here either, not just this diagnostic's own
      // (formerly separate, formerly out of sync) guess at one.
      reason = 'no accessible name';
    } else {
      reason = 'named + roled, but not collected (likely MAX_NODES cutoff)';
    }
    missed.push({
      tag: el.tagName.toLowerCase(),
      role: role || '(none)',
      reason: reason,
      hint: clean(el.getAttribute('aria-label') || el.innerText
                   || el.getAttribute('alt') || el.getAttribute('placeholder') || ''),
    });
  }
  return missed;
})()
"""

_MISSED_EXAMPLES_PER_BUCKET = 3

# The roles COLLECT_JS assigns to something _INTERACTIVE_JS's selector would
# also match — i.e. the roles a "control" can have. COLLECT_JS also names
# things the selector does not consider controls at all (headings, images:
# role 'heading', 'img') because it is a general-purpose accessible-name
# collector, not a controls-only one. Counting every named node as
# "perceived" against an interactive-only denominator lets the numerator
# include nodes the denominator never could — on a text-heavy page that
# produces a share over 100%, which is not a measurement, it's a bug. Filter
# to the roles that actually overlap with what was counted as interactive.
_CONTROL_ROLES = {
    "link", "button", "textbox", "checkbox", "radio", "combobox",
    "tab", "menuitem", "searchbox", "slider", "spinbutton", "password",
}


def measure(browser: EagleBrowser, url: str) -> dict:
    browser.goto(url)
    page = browser.page()
    # collect() first: it stamps data-ae-ref on everything it perceives, and
    # _MISSED_JS below relies on that stamp to know what was left out.
    records = page.collect()
    nodes = nodes_from_records(records)
    named = [n for n in nodes if n.name and n.role in _CONTROL_ROLES]
    total = browser.call(lambda p: p.evaluate(_INTERACTIVE_JS)) or 0
    missed = browser.call(lambda p: p.evaluate(_MISSED_JS)) or []
    return {
        "url": url,
        "interactive": total,
        "perceived": len(named),
        "share": (len(named) / total) if total else 0.0,
        # The collector's own word for "I stopped before I could return
        # everything" (see `collector_truncated` in page.py), not a guess
        # inferred from the node count — `len(nodes) >= MAX_NODES` used to
        # stand in for this, but that is only ever a coincidence, not a
        # signal: a page with exactly MAX_NODES named controls and nothing
        # else would trip it despite never having truncated anything.
        "hit_ceiling": collector_truncated(records),
        "missed": missed,
    }


def _print_missed(row: dict) -> None:
    missed = row["missed"]
    if not missed:
        print(f"    (nothing missed — every interactive control had a "
              f"usable accessible name)")
        return
    print(f"    missed {len(missed)} of {row['interactive']} interactive "
          f"controls, by reason:")
    # `_MISSED_JS`'s reasons are already stable, fixed strings — no
    # page-specific text baked in (the closed-<details> quoting this used to
    # do went away with task 12's `hasName()` fix, since that branch is only
    # reachable now when textContent is genuinely empty too) — so no extra
    # collapsing step is needed before tallying them.
    buckets: dict[str, list[dict]] = {}
    for m in missed:
        buckets.setdefault(m["reason"], []).append(m)
    for reason, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"      {len(items):4d}  {reason}")
        for m in items[:_MISSED_EXAMPLES_PER_BUCKET]:
            hint = f" {m['hint']!r}" if m["hint"] else ""
            print(f"             <{m['tag']} role={m['role']}>{hint}")


def main(urls: list[str]) -> int:
    # A fresh, throwaway profile every run — never the user's real Chrome
    # profile, never a persistent one that could carry a session forward
    # into the next run. Deleted before this function returns either way.
    profile_dir = Path(tempfile.mkdtemp(prefix="ae-web-coverage-"))
    browser = EagleBrowser(headless=True, profile_dir=profile_dir)
    try:
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
                if row["hit_ceiling"]:
                    print(f"    ** perceived count reached MAX_NODES="
                          f"{MAX_NODES} — the collector stopped counting "
                          f"before it reached the end of the DOM, so this "
                          f"share is a floor, not the true number. **")
                _print_missed(row)
            if rows:
                avg = sum(r["share"] for r in rows) / len(rows)
                print(f"\naverage structural coverage: {avg:.1%} "
                      f"across {len(rows)} sites")
        finally:
            browser.close()
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1:]))
