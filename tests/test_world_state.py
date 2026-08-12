"""Did the world actually move?

Two bugs from the last two days share one missing idea.

A tool reported success it never had — the whole `ToolResult` contract exists
because of that class, and it still only proves "the call returned", never
"anything changed". And the mission reopened MakerWorld forever, because
"have I been here before?" had nothing to key on; the fix matched on goal
TEXT, which is a proxy for the thing actually wanted.

Both need the same primitive: a cheap, comparable fingerprint of what is on
screen. With it, "the step claims done but nothing changed" and "I am back
where I started" are both answerable, with evidence rather than inference.

Deliberately NOT cryptographic and NOT exact. Pages jitter — a timestamp, a
rotating advert, a live counter. The question is "am I effectively where I
was", not "is every byte identical", and a fingerprint that changes on every
poll answers neither.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.world_state import describe_change, signature_of  # noqa: E402


class _Port:
    def __init__(self, url="https://example.test/", names=()):
        self._url, self._names = url, list(names)

    def url(self):
        return self._url

    def collect(self):
        return [{"ref": f"e{i}", "name": n, "role": "link",
                 "left": 0, "top": i * 10, "width": 80, "height": 20,
                 "states": ["VISIBLE", "ENABLED"], "value": ""}
                for i, n in enumerate(self._names)]


class _Dead:
    def url(self):
        return "https://x.test/"

    def collect(self):
        raise RuntimeError("page gone")


# ── the fingerprint ─────────────────────────────────────────────────────────

def test_the_same_page_twice_is_the_same_signature():
    p = _Port(names=["Home", "Search", "Download"])
    assert signature_of(p) == signature_of(p)


def test_a_different_url_is_a_different_signature():
    a = signature_of(_Port(url="https://a.test/", names=["Home"]))
    b = signature_of(_Port(url="https://b.test/", names=["Home"]))
    assert a != b


def test_the_same_url_with_different_controls_is_a_different_signature():
    """A modal opening does not change the url, and it absolutely changes
    where you are."""
    a = signature_of(_Port(names=["Home", "Search"]))
    b = signature_of(_Port(names=["Home", "Search", "Confirm delete"]))
    assert a != b


def test_control_order_does_not_change_the_signature():
    """The same page collected twice can enumerate in a different order; that
    is not a change in the world."""
    a = signature_of(_Port(names=["Home", "Search", "Cart"]))
    b = signature_of(_Port(names=["Cart", "Home", "Search"]))
    assert a == b


def test_a_page_that_could_not_be_read_is_unknown_not_empty():
    """A failed read must not compare equal to a genuinely empty page — that
    is how "I could not look" becomes "there is nothing there"."""
    sig = signature_of(_Dead())
    assert sig.unknown is True
    assert sig != signature_of(_Port(url="https://x.test/", names=[]))


def test_an_unknown_signature_never_equals_another_unknown():
    """Two failed reads are not evidence of being in the same place."""
    assert signature_of(_Dead()) != signature_of(_Dead())


# ── did the world move ──────────────────────────────────────────────────────

def test_nothing_changed_is_reported_as_nothing_changed():
    p = _Port(names=["Home", "Search"])
    assert signature_of(p).same_as(signature_of(p)) is True


def test_a_new_control_counts_as_movement():
    before = signature_of(_Port(names=["Home"]))
    after = signature_of(_Port(names=["Home", "Results"]))
    assert before.same_as(after) is False


def test_a_failed_read_is_never_called_the_same_place():
    assert signature_of(_Port(names=["Home"])).same_as(signature_of(_Dead())) is False


def test_the_change_is_described_in_words_a_person_could_check():
    before = signature_of(_Port(url="https://a.test/", names=["Home"]))
    after = signature_of(_Port(url="https://b.test/", names=["Home", "Results"]))
    said = describe_change(before, after).lower()
    assert "b.test" in said
    assert "control" in said


def test_no_change_says_so_plainly():
    p = _Port(names=["Home"])
    assert "nothing" in describe_change(signature_of(p), signature_of(p)).lower()


def test_an_unknown_before_or_after_is_not_claimed_as_movement():
    known, unknown = signature_of(_Port(names=["a"])), signature_of(_Dead())
    assert "could not" in describe_change(known, unknown).lower()


def test_a_swap_with_the_same_count_still_reads_as_a_change():
    """Same number of controls, different controls — a page that replaced its
    contents has moved."""
    before = signature_of(_Port(names=["Home", "Search"]))
    after = signature_of(_Port(names=["Results", "Filter"]))
    assert before.same_as(after) is False
    assert "different" in describe_change(before, after).lower()


# ── being here before ───────────────────────────────────────────────────────

def test_a_signature_is_hashable_so_visits_can_be_counted():
    seen = {signature_of(_Port(names=["Home"]))}
    assert signature_of(_Port(names=["Home"])) in seen


def test_an_unknown_signature_is_not_recorded_as_a_visit():
    """Counting failed reads as visits would make everything look like a loop."""
    assert signature_of(_Dead()).worth_recording is False
    assert signature_of(_Port(names=["Home"])).worth_recording is True
