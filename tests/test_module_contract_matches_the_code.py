"""The contract a module author reads must match the one the code enforces.

`docs/MODULE_CONTRACT.md` is the interface third-party modules are written
against, and it is the only one of these documents that is published. A stale
line in it is not a stale comment — it is a module built wrong by somebody who
had no other source.

This is a property over an artifact, not a mirror: the doc is the product for
a module author, the same way a tool description is the product for the model.
It reads text because the text IS the thing being checked, and it fails when
the code changes and the document does not.

It deliberately checks only the closed sets and the one number. Prose is not
asserted; nobody can keep a paragraph and a codebase in lockstep, and trying
is how documents start lying about the parts that are hard to check.
"""
import re
from pathlib import Path

import pytest

from core.module_bus.bus import MAX_OUTPUT_CHARS
from core.module_bus.manifest import DANGERS, RESOURCES, VALID_TYPES
from core.module_bus.validator import RESERVED_CARDS

DOC = Path(__file__).resolve().parent.parent / "docs" / "MODULE_CONTRACT.md"

pytestmark = pytest.mark.skipif(
    not DOC.is_file(), reason="MODULE_CONTRACT.md is not in this checkout")


@pytest.fixture(scope="module")
def text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_output_ceiling_is_the_number_the_bus_uses(text):
    """The rule modules get wrong most often. A wrong number here is worse
    than no number: an author would size their payload to it and still
    overflow."""
    printed = {int(n.replace(",", ""))
               for n in re.findall(r"\b(\d{1,3}(?:,\d{3})+|\d{4,})\b", text)}
    assert MAX_OUTPUT_CHARS in printed, (
        f"the doc never states the real ceiling of {MAX_OUTPUT_CHARS}")


@pytest.mark.parametrize("label,values", [
    ("resources", sorted(RESOURCES)),
    ("param types", sorted(VALID_TYPES)),
    ("danger levels", sorted(DANGERS)),
    ("reserved card names", sorted(RESERVED_CARDS)),
])
def test_every_closed_set_is_listed_in_full(text, label, values):
    """A module naming something outside one of these does not load. An author
    reading a short list would declare a hazard nothing checks, or a card name
    the eagle refuses, and find out at startup."""
    missing = [v for v in values if not re.search(rf"\b{re.escape(v)}\b", text)]
    assert not missing, f"{label}: {missing} enforced by the code, absent from the doc"


def test_the_doc_does_not_invent_resources(text):
    """The opposite drift: a doc offering a resource the scheduler cannot
    arbitrate over reads as protection and is not."""
    fenced = re.search(r"net\s+web\s+file\s+files[^\n]*", text)
    assert fenced, "the resource list is not in the doc in its expected form"
    named = set(fenced.group(0).split())
    assert named <= RESOURCES, f"doc offers resources the code rejects: {named - RESOURCES}"
