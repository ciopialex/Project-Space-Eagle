"""Tiered visual grounding: structure first, pixels last.

A human operator perceives an interface — buttons, fields, labels — not a grid
of pixels. The accessibility tree is that perception, and it is local, exact,
and roughly a hundred times faster than asking a vision model where something
is. Vision remains the honest fallback for canvases, games, remote desktops,
and anything else that publishes no structure.
"""
from actions.grounding.base import Element, Grounder, match_score
from actions.grounding.resolver import (GroundingResolver, default_resolver,
                                        find_element)

__all__ = ["Element", "Grounder", "match_score",
           "GroundingResolver", "default_resolver", "find_element"]
