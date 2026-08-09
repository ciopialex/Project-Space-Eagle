"""Reading the page, not only the buttons on it.

Measured on a real product page: the collector returned 69 interactive
controls and discarded 68 text blocks — including the price. So the eagle
could click "Bambu Lab P2S 3D Printer" and could not tell you it costs €519,
and in a live session it called web_search to find that price while standing
on the page displaying it. That search cost 4541ms; collecting the text costs
12ms. The filter saved 12ms and cost 4541ms.

Text is attached to the nearest control rather than returned as its own list.
That is the difference between enriching the candidates and multiplying them —
a flat list of 137 things would make grounding worse, not better.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.page import (COLLECT_JS, MAX_CONTEXT_CHARS,  # noqa: E402
                                        WebNode, nodes_from_records)


def test_a_record_can_carry_the_text_around_it():
    node = nodes_from_records([{
        "ref": "e1", "name": "Bambu Lab P2S 3D Printer", "role": "link",
        "left": 0, "top": 0, "width": 300, "height": 300,
        "states": ["VISIBLE"], "value": "",
        "context": "From €519.00 EUR · In stock",
    }])[0]
    assert node.context == "From €519.00 EUR · In stock"


def test_a_record_without_context_still_works():
    """Every existing caller and every fake in the suite predates this field."""
    node = nodes_from_records([{
        "ref": "e1", "name": "Sign in", "role": "button",
        "left": 0, "top": 0, "width": 80, "height": 20,
        "states": ["VISIBLE"], "value": "",
    }])[0]
    assert node.context == ""


def test_the_collector_gathers_text_and_attaches_it():
    assert "context" in COLLECT_JS
    # Attached to a control, never emitted as separate nodes — a flat list of
    # every text block would double the candidates the grounder must score.
    assert "ownText" in COLLECT_JS or "nodeType === 3" in COLLECT_JS


def test_context_is_capped():
    """An article page has thousands of text blocks. Unbounded context would
    push the real controls out of the model's attention, which is the exact
    failure this is meant to prevent."""
    assert MAX_CONTEXT_CHARS <= 300
    assert str(MAX_CONTEXT_CHARS) in COLLECT_JS


def test_context_appears_in_what_the_model_is_shown():
    """Collected and then not rendered would be work done for nothing."""
    import inspect
    import actions.web_agency as W
    # `_describe` is what turns nodes into the lines the model reads.
    assert "context" in inspect.getsource(W._describe)
