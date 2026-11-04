"""Locate a control on a web page by the words a person would use for it. Matching is
roles.best_match, readiness is actionability, retrying is waiting; this adds the
web as a source of nodes.
"""
from __future__ import annotations

import time
from typing import Callable

from actions.grounding.base import Element, match_score
from actions.grounding.roles import WEB, best_match, normalize
from actions.grounding.web.page import (PageLike, WebNode, element_from,
                                        nodes_from_records)

# Wall-clock budget for one resolve_and_click across all tied candidates: a person waits
# through it.
_TOTAL_BUDGET_S = 5.0

# Below this a further candidate is not worth starting.
_MIN_TRY_MS = 500


# Roles that wrap controls: their name is all their children's text, so they match
# anything inside them and must lose ties to a real control.
_CONTAINER_ROLES = frozenset({
    "navigation", "banner", "main", "region", "group", "contentinfo",
    "complementary", "list", "listitem", "article", "section", "form",
    "table", "grid", "tablist", "menubar", "toolbar", "document",
})


def prefer_visible(node) -> int:
    """Rank a visible control above a hidden one, ahead of every other preference."""
    try:
        return 1 if "VISIBLE" in (getattr(node, "states", None) or ()) else 0
    except Exception:
        return 1          # unknown: do not demote it


def prefer_actionable(node) -> int:
    """Rank a real control above a region that contains one. Breaks ties only."""
    role = str(getattr(node, "role", "") or "").lower()
    return 0 if role in _CONTAINER_ROLES else 1


def prefer_exact(description: str, node) -> int:
    """Rank an exact name above one that merely contains the words."""
    wanted = " ".join((description or "").lower().split())
    name = " ".join(str(getattr(node, "name", "") or "").lower().split())
    if not wanted or not name:
        return 0
    if name == wanted:
        return 2
    return 1 if wanted in name else 0


def _recording(click_fn, raised: list):
    """`click_fn`, recording whatever it raises before re-raising: click_with_outcome
    swallows everything, and a click that raised never landed. BaseException, to
    catch CancelledError.
    """
    def _click(ref: str) -> None:
        try:
            click_fn(ref)
        except BaseException as e:
            raised.append(e)
            raise
    return _click


class WebGrounder:
    """Structural grounding inside a browser page. Holds no escalation state:
    web_agency keeps the process-wide counter.
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
        """resolve() plus whether the read itself worked: (node, nodes, read_ok). A
        failed read must never reach the vision fallback.
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
            # The matcher failed: an error, not an invitation to guess.
            return None, nodes, False

    def resolve(self, description: str, *,
                prefer=None) -> tuple[WebNode | None, tuple]:
        """One structural read returning the match and everything it was matched
        against: (node, nodes). Both must come from the same read, since
        collecting again re-stamps every ref.

        `prefer` breaks ties the text score cannot ("the search field" ties with
        sixteen buttons): if the caller is about to type, an editable control
        wins. Applied only among the top scorers. A control beats a container and
        an exact name beats a containing one, always.
        """
        node, nodes, _read_ok = self._read(description, prefer=prefer)
        return node, nodes

    def _prefer_among_ties(self, nodes, description: str, match: WebNode,
                           prefer) -> WebNode:
        """`match`, unless another node scores the same and `prefer` likes it."""
        try:
            top = match_score(description, match.name,
                              normalize(match.role, WEB))

            # Before the caller's preference: control over container, exact over
            # containing. Ties only.
            def rank(n):
                # Visible first.
                return (prefer_visible(n), prefer_actionable(n),
                        prefer_exact(description, n))

            # Skip the scan when the match is already ideal: it can only improve a
            # match.
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
        """Resolve `description`, then click among tied candidates until one produces
        a verified outcome (outcome.py).

        Returns (node, outcome): `node` is the control actually clicked, or None
        if nothing was; `outcome` is the page-level signal, "" if none fired. Non-
        None with "" means delivered without a signal.

        gate_fn(node) runs before every click, structural or vision; if it raises,
        the whole retry stops. `total_budget_s` bounds the wall clock across
        candidates.
        """
        from actions.grounding.web.outcome import click_with_outcome
        from actions.grounding.web.page import VisionTarget, ref_of

        deadline = time.monotonic() + max(0.0, float(total_budget_s))

        def _budget_ms() -> int:
            """The smaller of this candidate's share and what is left on the clock."""
            left = int((deadline - time.monotonic()) * 1000)
            return max(_MIN_TRY_MS, min(int(timeout_ms_per_try), left))

        node, nodes, read_ok = self._read(description)

        if not read_ok:
            # The page could not be read: never escalate to vision.
            print(f"[Click] ⚠️ could not read the page for {description!r} — "
                  "not escalating to vision on a transient read failure")
            return None, ""

        if node is None:
            # No structural match at all: ask a vision model for a coordinate on a
            # screenshot of this page.
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

            # The gate applies to vision clicks too: a coordinate is the same click with
            # less known about it. VisionTarget gives gate_fn the description and an
            # "unknown" role.
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
            # A click that raised never landed.
            return (None if raised else target), outcome

        candidates = self._tied_candidates(nodes, description, node)[:budget]
        if len(candidates) > 1:
            print(f"[Click] 🔀 {len(candidates)} tied candidates for "
                  f"{description!r}: "
                  f"{[ref_of(c) for c in candidates]}")

        # The first candidate whose click was delivered without a signal.
        clicked: WebNode | None = None

        for index, candidate in enumerate(candidates):
            if index and (deadline - time.monotonic()) * 1000 < _MIN_TRY_MS:
                # Out of time, not candidates.
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
        """The matching node, ref intact. Use resolve() when the whole node list is
        needed too.
        """
        return self.resolve(description)[0]

    def find(self, description: str) -> Element | None:
        node = self.find_node(description)
        return None if node is None else element_from(node)

    def hit_test(self, x: int, y: int) -> Element | None:
        """What is actually at this point, such as a modal that opened. Pass to
        actionability.check, or receives_events never passes.
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
