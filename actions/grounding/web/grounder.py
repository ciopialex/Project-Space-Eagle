"""Locate a control on a web page, by the words a person would use for it.

The whole file is thin, and that is the result worth having: matching lives in
`roles.best_match`, readiness lives in `actionability`, retrying lives in
`waiting`. This adds a fourth source of nodes to machinery that already exists,
which is why the web arrived without a single change to any of it.
"""
from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element, match_score
from actions.grounding.roles import WEB, best_match, normalize
from actions.grounding.web.page import (PageLike, WebNode, element_from,
                                        nodes_from_records)


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
        """
        page = self._page()
        if page is None:
            return None, ()
        try:
            nodes = nodes_from_records(page.collect())
        except Exception:
            return None, ()
        try:
            # `prefer` breaks ties the text score cannot. Real pages produce
            # them constantly: "the search field" on DuckDuckGo scores 0.80
            # against sixteen controls at once — the actual input, plus every
            # button, link and image whose name also contains "search". The
            # text is genuinely equally good evidence for all of them, so the
            # only honest tie-break is what the caller means to *do*: if it
            # is about to type, an editable control wins. Applied only among
            # the top scorers, never to promote a worse textual match.
            match = best_match(nodes, description,
                               threshold=self._threshold, platform=WEB)
            # Structural tie-breaks apply ALWAYS, not only when the caller
            # supplies a preference. A click passes none, which is exactly the
            # path where "Acasă" resolved to the navigation bar containing it
            # — all three candidates scored 0.8000, and the container happened
            # to come first.
            if match is not None:
                match = self._prefer_among_ties(nodes, description, match,
                                                prefer)
            return match, nodes
        except Exception:
            return None, nodes

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
