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
