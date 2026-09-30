"""Locate a control on a web page, by the words a person would use for it.

The whole file is thin, and that is the result worth having: matching lives in
`roles.best_match`, readiness lives in `actionability`, retrying lives in
`waiting`. This adds a fourth source of nodes to machinery that already exists,
which is why the web arrived without a single change to any of it.
"""
from __future__ import annotations

import time
from typing import Callable

from actions.grounding.base import Element, match_score
from actions.grounding.roles import WEB, best_match, normalize
from actions.grounding.web.page import (PageLike, WebNode, element_from,
                                        nodes_from_records)

#: The whole click-escalation path is on the voice-latency budget: a person
#: waits through it. The plan's contract is ~5s total for one `resolve_and_click`
#: call, across however many tied candidates it tries. Only the candidate-COUNT
#: half of that was ever implemented (`[:budget]`); this is the wall-clock half,
#: and it is the one that actually bounds the wait, because what a candidate
#: costs is set by Playwright's event timeouts rather than by arithmetic here.
_TOTAL_BUDGET_S = 5.0

#: Below this, a further candidate is not worth starting: the two nested event
#: expectations would get a quarter of it each, which is not a wait, and the
#: click still costs a real round trip. Stop and report honestly instead.
_MIN_TRY_MS = 500


#: Roles that WRAP controls rather than being one. Their accessible name is
#: every child's text concatenated, so they match any query their contents
#: would match - and being bigger, they often win. Live on youtube.com,
#: "Acasă" resolved to the navigation bar whose name began "Acasă Shorts
#: Abonamente Tu Istoric…" instead of the link named exactly "Acasă", and the
#: click landed on a wrapper and did nothing. Every site has these.
_CONTAINER_ROLES = frozenset({
    "navigation", "banner", "main", "region", "group", "contentinfo",
    "complementary", "list", "listitem", "article", "section", "form",
    "table", "grid", "tablist", "menubar", "toolbar", "document",
})


def prefer_visible(node) -> int:
    """Rank a control that can be seen above one that cannot.

    First, ahead of every other preference. Preferring an exact name without
    this pulled hidden controls named exactly "Search" ahead of the visible
    buttons that had been working, and click reliability across the benchmark
    fell from 66% to 33% in a single commit. Nothing about a name matters if
    the thing cannot be clicked.
    """
    try:
        return 1 if "VISIBLE" in (getattr(node, "states", None) or ()) else 0
    except Exception:
        return 1          # unknown: do not demote it


def prefer_actionable(node) -> int:
    """Rank a real control above a region that merely contains one.

    Breaks ties only - it deliberately returns the same value for every
    genuine control, because which link or button is meant is the name
    match's job, not this one's.
    """
    role = str(getattr(node, "role", "") or "").lower()
    return 0 if role in _CONTAINER_ROLES else 1


def prefer_exact(description: str, node) -> int:
    """Rank an exact name above one that merely contains the words.

    The other half of the same problem: a container is not the only thing
    that swallows a query - a long link can too.
    """
    wanted = " ".join((description or "").lower().split())
    name = " ".join(str(getattr(node, "name", "") or "").lower().split())
    if not wanted or not name:
        return 0
    if name == wanted:
        return 2
    return 1 if wanted in name else 0


def _recording(click_fn, raised: list):
    """`click_fn`, with whatever it raises written down on the way past.

    `click_with_outcome` (outcome.py) deliberately swallows everything: a
    timed-out signal is its normal "that wasn't it" case. But a click that
    RAISED is a click that never landed — a stale ref, a detached element, a
    browser call abandoned — and that is a different fact, invisible to a
    caller reading only the returned signal. Recorded here rather than
    caught: it is re-raised immediately, so the flow through
    `click_with_outcome` is exactly what it was.

    `BaseException`, not `Exception`, because the thing most worth
    recognising on this path is `asyncio.CancelledError` (see
    `_EXPECTATION_FAILED` in outcome.py), which is not an `Exception`.
    """
    def _click(ref: str) -> None:
        try:
            click_fn(ref)
        except BaseException as e:
            raised.append(e)
            raise
    return _click


class WebGrounder:
    """Structural grounding inside a browser page.

    Deliberately holds no escalation state. An earlier version took a
    `PageSense` and stored it as `self.sense`, which nothing ever read —
    `actions/web_agency.py` keeps the one process-wide counter, because the
    signal it carries ("acting has failed twice, look harder") belongs to the
    operator across tool calls rather than to any one short-lived grounder.
    The parameter is gone rather than left dangling: a constructor argument
    that implies shared state which does not exist is worse than none.
    """

    name = "web"
    cost = "fast"      # in-process CDP call, milliseconds — no network model

    def __init__(self,
                 page_fn: Callable[[], PageLike | None],
                 threshold: float = 0.5) -> None:
        self._page_fn = page_fn
        self._threshold = threshold

    def _page(self) -> PageLike | None:
        try:
            return self._page_fn()
        except Exception:
            return None

    def available(self) -> bool:
        return self._page() is not None

    def _read(self, description: str, *,
              prefer=None) -> tuple[WebNode | None, tuple, bool]:
        """`resolve()`, plus the one thing `resolve()` cannot say: whether
        the READ itself worked — `(node, nodes, read_ok)`.

        `resolve()` answers `(None, ())` both when the page genuinely has
        nothing matching AND when `page.collect()` threw (a page mid-
        navigation, a browser call that timed out, a context that went away).
        Those are opposite situations for `resolve_and_click`: the first is
        the case its vision fallback exists for, and the second must NEVER
        reach it, because vision would answer a transient read failure by
        clicking a raw screen coordinate — actuating on a page nobody could
        read. `read_ok=False` means "do not escalate, this is an error."
        """
        page = self._page()
        if page is None:
            return None, (), False
        try:
            nodes = nodes_from_records(page.collect())
        except Exception:
            return None, (), False
        try:
            match = best_match(nodes, description,
                               threshold=self._threshold, platform=WEB)
            if match is not None:
                match = self._prefer_among_ties(nodes, description, match,
                                                prefer)
            return match, nodes, True
        except Exception:
            # The matching machinery itself failed. Nodes were read, so this
            # is not "the page has nothing like that" either — same reasoning
            # as a failed collect: an error, not an invitation to guess.
            return None, nodes, False

    def resolve(self, description: str, *,
                prefer=None) -> tuple[WebNode | None, tuple]:
        """One structural read, returning both the match and everything it
        was matched against: `(node, nodes)`.

        Callers that need the whole node list *and* a match must have both
        from the same read. Collecting twice re-stamps every `data-ae-ref`
        (see `page.py`), so a match resolved by one collect and a node list
        gathered by another describe two different snapshots — and the ref
        the caller is about to act on belongs to the older one. That is
        exactly how the consent gate came to approve one control while the
        browser actuated another: the gate's own `wall_reason` check
        re-collected between the resolve and the fill.

        `prefer` breaks ties the text score cannot. Real pages produce them
        constantly: "the search field" on DuckDuckGo scores 0.80 against
        sixteen controls at once — the actual input, plus every button, link
        and image whose name also contains "search". The text is genuinely
        equally good evidence for all of them, so the only honest tie-break
        is what the caller means to *do*: if it is about to type, an editable
        control wins. Applied only among the top scorers, never to promote a
        worse textual match. Structural tie-breaks (a control beats a
        container, an exact name beats a containing one) apply ALWAYS, not
        only when the caller supplies a preference — a click passes none, and
        that is exactly the path where "Acasă" resolved to the navigation bar
        containing it.
        """
        node, nodes, _read_ok = self._read(description, prefer=prefer)
        return node, nodes

    def _prefer_among_ties(self, nodes, description: str, match: WebNode,
                           prefer) -> WebNode:
        """`match`, unless another node scores the same and `prefer` likes it."""
        try:
            top = match_score(description, match.name,
                              normalize(match.role, WEB))

            # Before the caller's own preference: a control beats a container,
            # and an exact name beats one that merely contains the words. Both
            # only ever break a TIE in the name score, so a better-matching
            # node is never displaced by a role.
            def rank(n):
                # Visible first: a name is irrelevant on something unclickable.
                return (prefer_visible(n), prefer_actionable(n),
                        prefer_exact(description, n))

            # Skip the scan entirely when the match is already ideal. The
            # scan re-scores every node, which on a 2000-node tree blew the
            # 50ms structural-lookup budget - and it can only ever IMPROVE a
            # match, so there is nothing to look for once it is perfect.
            if rank(match) == (1, 1, 2):
                return match

            best = match
            for node in nodes:
                if node is match:
                    continue
                try:
                    tied = abs(match_score(description, node.name,
                                           normalize(node.role, WEB)) - top) < 1e-9
                except Exception:
                    continue
                if tied and rank(node) > rank(best):
                    best = node
            if best is not match:
                match = best

            if prefer is not None and prefer(match):
                return match
            if prefer is None:
                return match
            for node in nodes:
                if node is match or not prefer(node):
                    continue
                if abs(match_score(description, node.name,
                                   normalize(node.role, WEB)) - top) < 1e-9:
                    return node
        except Exception:
            pass
        return match

    def _tied_candidates(self, nodes, description: str, match: WebNode) -> list:
        """Every node scoring the same as `match`, `match` itself first."""
        top = match_score(description, match.name,
                          normalize(match.role, WEB))
        tied = [match]
        for node in nodes:
            if node is match:
                continue
            try:
                score = match_score(description, node.name,
                                    normalize(node.role, WEB))
            except Exception:
                continue
            if abs(score - top) < 1e-9:
                tied.append(node)
        return tied

    def resolve_and_click(self, description: str, *, page, click_fn,
                          gate_fn, budget: int = 3,
                          timeout_ms_per_try: int = 1500,
                          total_budget_s: float = _TOTAL_BUDGET_S):
        """Resolve `description`, then click among tied candidates until
        one produces a real, verified outcome (see outcome.py).

        Returns `(node, outcome)`, and the pair carries two independent
        facts — which is the whole point of its shape:

          * `node` is the control that was actually CLICKED, or None if
            nothing ever was. Non-None with an empty `outcome` means the
            click was genuinely delivered and simply produced no signal;
            `(None, "")` — and only that — means nothing matched, or
            nothing could be clicked at all. Conflating the two is how
            `user_click` came to answer "no control matching 'Accept terms'
            — the page has: Accept terms" while the checkbox it had just
            ticked sat there ticked: the tool denying an action it had
            performed, in a sentence naming the control it performed it on.
          * `outcome` is the verified page-level signal, "" when none fired.

        `gate_fn(node)` is the consent/irreversible-action check — called
        before EVERY click attempted, structural or vision, never once and
        never skipped. Left to propagate if it raises: a refused candidate
        stops the whole retry, it is never silently passed over in favour of
        the next one.

        `total_budget_s` bounds the wall clock across all candidates, not
        just their number. A person is waiting through this.
        """
        from actions.grounding.web.outcome import click_with_outcome
        from actions.grounding.web.page import VisionTarget, ref_of

        deadline = time.monotonic() + max(0.0, float(total_budget_s))

        def _budget_ms() -> int:
            """What this candidate may spend: the smaller of its own share
            and everything left on the clock."""
            left = int((deadline - time.monotonic()) * 1000)
            return max(_MIN_TRY_MS, min(int(timeout_ms_per_try), left))

        node, nodes, read_ok = self._read(description)

        if not read_ok:
            # The page could not be read, which is not the same as "the page
            # does not have that". Escalating to vision here would answer a
            # transient failure by clicking a raw coordinate on a page nobody
            # could read — see `_read`.
            print(f"[Click] ⚠️ could not read the page for {description!r} — "
                  "not escalating to vision on a transient read failure")
            return None, ""

        if node is None:
            # No structural match at all — not merely a tied-and-exhausted
            # retry. Live case: the "Open in Bambu Studio" dropdown item had
            # no ARIA role and no accessible name, so collect() never saw it
            # as a candidate to begin with. Last resort: ask a vision model
            # for a coordinate on a screenshot of this same page.
            print(f"[Click] 🔍 no structural match for {description!r} — "
                  "asking vision")
            from actions.grounding.web.vision_click import page_vision_grounder
            vision = page_vision_grounder(page)
            element = vision.find(description)
            if element is None:
                print(f"[Click] ⛔ vision found nothing for {description!r} "
                      f"either — giving up (last_error={vision.last_error!r})")
                return None, ""
            print(f"[Click] 🔍 vision found {description!r} at "
                  f"({element.left:.0f},{element.top:.0f}) "
                  f"{element.width:.0f}x{element.height:.0f}")

            # THE GATE APPLIES HERE TOO. It did not, and that was a live
            # breach of this path's one non-negotiable rule: reproduced
            # through `web_agency._click` with `confirmed=False`, a control
            # described as "Complete purchase" that structural matching
            # happened to miss was clicked outright, `ok=True`, with no
            # consent check ever running — the eagle taking an irreversible
            # action on someone's behalf because the page failed to name a
            # button. A coordinate is not less dangerous than a ref; it is
            # the same click with less known about it. `VisionTarget`
            # (page.py) is what `gate_fn` needs to judge it: the description
            # the caller asked for, and an honest "unknown" role.
            target = VisionTarget(name=description)
            gate_fn(target)
            print(f"[Click] ✅ consent gate passed for vision target "
                  f"{description!r} — clicking")

            x = int(element.left + element.width / 2)
            y = int(element.top + element.height / 2)
            raised: list[BaseException] = []
            outcome = click_with_outcome(
                page, "", _recording(lambda _ref: page.mouse_click(x, y),
                                     raised),
                timeout_ms=_budget_ms())
            if raised:
                print(f"[Click] ⛔ vision click on {description!r} raised: "
                      f"{raised[0]!r}")
            else:
                print(f"[Click] {'✅' if outcome else '❔'} vision click on "
                      f"{description!r} at ({x},{y}) -> "
                      f"outcome={outcome or '(no signal)'!r}")
            # A click that RAISED never landed, so there is nothing to report
            # having clicked — `(None, "")`, the same as never finding it.
            return (None if raised else target), outcome

        candidates = self._tied_candidates(nodes, description, node)[:budget]
        if len(candidates) > 1:
            print(f"[Click] 🔀 {len(candidates)} tied candidates for "
                  f"{description!r}: "
                  f"{[ref_of(c) for c in candidates]}")

        #: The first candidate whose click was genuinely delivered without
        #: raising, even though no signal followed. Tied candidates all share
        #: the matched name, so which of them is named back is not a
        #: meaningful choice — that there WAS one is.
        clicked: WebNode | None = None

        for index, candidate in enumerate(candidates):
            if index and (deadline - time.monotonic()) * 1000 < _MIN_TRY_MS:
                # Out of time, not out of candidates. Whatever has already
                # been clicked is still reported honestly below.
                print(f"[Click] ⏱️ budget exhausted after {index}/"
                      f"{len(candidates)} candidates for {description!r}")
                break
            gate_fn(candidate)
            raised = []
            outcome = click_with_outcome(
                page, ref_of(candidate), _recording(click_fn, raised),
                timeout_ms=_budget_ms())
            if len(candidates) > 1 or not outcome:
                status = ("raised: " + repr(raised[0]) if raised
                          else f"outcome={outcome or '(no signal)'!r}")
                print(f"[Click]   [{index+1}/{len(candidates)}] "
                      f"{ref_of(candidate)} -> {status}")
            if outcome:
                return candidate, outcome
            if clicked is None and not raised:
                clicked = candidate
        if len(candidates) > 1:
            print(f"[Click] {'✅' if clicked else '⛔'} tied-candidate retry "
                  f"for {description!r} ended: "
                  f"{'delivered, no signal' if clicked else 'nothing worked'}")
        return clicked, ""

    def find_node(self, description: str) -> WebNode | None:
        """The matching node, ref intact. Actuation needs the ref; `find` does
        not expose it because `Element` has nowhere to put one.

        Use `resolve()` instead when the whole node list is needed too.
        """
        return self.resolve(description)[0]

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
