"""How often does the eagle actually succeed on a real website?

"Make web_agency reliable" is not actionable without a number. This produces
one: a fixed set of real sites, the same three things attempted on each —
open it, read it, click something real — scored on whether they worked.

Deliberately NOT a test suite. Tests pin behaviour that is already understood;
this measures behaviour on the open web, which changes underneath us. A drop
here is information (a site redesigned, a wall appeared), not a failure to fix
before merging.

Read-only by design. It clicks navigation and product links, never anything
that buys, sends or deletes — the consent gate would refuse those anyway, and
a benchmark that needs supervision will not get run.

Run:  .venv/bin/python tools/web_reliability.py
      .venv/bin/python tools/web_reliability.py --site emag
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Real sites, chosen to cover the failure modes seen in live sessions:
#: a consent wall, a single-page app, a shop with product cards, a
#: non-English page, and a site behind a bot challenge.
SITES = [
    ("gov.uk",   "https://www.gov.uk",                                    "Search"),
    ("bambu",    "https://eu.store.bambulab.com/collections/p-series",    "Bambu Lab P2S 3D Printer"),
    ("emag",     "https://www.emag.ro",                                   "Cont"),
    # Targets must be a control's WHOLE name, not a fragment. "Cont" matched
    # inside "conținutul" here and the benchmark reported a failure that was
    # its own fault - a benchmark that lies is worse than none.
    ("olx",      "https://www.olx.ro",                                    "OLX, prima pagină"),
    ("ddg",      "https://duckduckgo.com",                                "Search"),
    ("youtube",  "https://www.youtube.com",                               "Acasă"),
]


def _run_site(name: str, url: str, target: str) -> dict:
    import actions.grounding.web.browser as B
    import actions.web_agency as W

    tmp = Path(tempfile.mkdtemp(prefix=f"ae-rel-{name}-"))
    browser = B.EagleBrowser(headless=True, profile_dir=tmp / "p")
    B._DEFAULT = browser
    row = {"site": name, "open": False, "read": False, "click": False,
           "controls": 0, "ms": 0, "note": ""}
    started = time.monotonic()
    try:
        opened = W.web_agency({"url": url, "action": "open"})
        row["open"] = bool(opened.ok)
        if not opened.ok:
            row["note"] = opened.message[:70]
            return row

        looked = W.web_agency({"action": "look"})
        row["read"] = bool(looked.ok)
        try:
            row["controls"] = len(W._current_nodes(browser.page()))
        except Exception:
            pass
        # Reading means little if the page came back nearly empty.
        if row["controls"] < 5:
            row["read"] = False
            row["note"] = f"only {row['controls']} controls"

        clicked = W.web_agency({"action": "click", "description": target})
        row["click"] = bool(clicked.ok)
        if not clicked.ok:
            row["note"] = clicked.message[:70]
        return row
    except Exception as e:
        row["note"] = f"{type(e).__name__}: {e}"[:70]
        return row
    finally:
        row["ms"] = int((time.monotonic() - started) * 1000)
        try:
            browser.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="run one site by name")
    args = ap.parse_args()

    sites = [s for s in SITES if not args.site or s[0] == args.site]
    print(f"{'site':10} {'open':>5} {'read':>5} {'click':>6} {'ctrls':>6} "
          f"{'ms':>6}  note")
    rows = []
    for name, url, target in sites:
        row = _run_site(name, url, target)
        rows.append(row)
        tick = lambda b: " ok " if b else " -- "
        print(f"{row['site']:10} {tick(row['open']):>5} {tick(row['read']):>5} "
              f"{tick(row['click']):>6} {row['controls']:>6} {row['ms']:>6}  "
              f"{row['note'][:52]}")

    n = len(rows) or 1
    opened = sum(r["open"] for r in rows)
    read = sum(r["read"] for r in rows)
    clicked = sum(r["click"] for r in rows)
    print(f"\nopen  {opened}/{n} ({opened*100//n}%)   "
          f"read {read}/{n} ({read*100//n}%)   "
          f"click {clicked}/{n} ({clicked*100//n}%)")
    print(f"END TO END (all three): {sum(1 for r in rows if r['open'] and r['read'] and r['click'])}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
