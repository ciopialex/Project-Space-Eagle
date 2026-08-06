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
            if match is not None and prefer is not None:
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
            if prefer(match):
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
