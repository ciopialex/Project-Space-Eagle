"""A page, without a browser.

Everything above the seam is tested against this. If a test in this area needs
a real browser to run, either the test is wrong or the seam has leaked.
"""
from __future__ import annotations


class FakePage:
    """Implements `PageLike` and nothing else."""

    def __init__(self, records=(), shot=b"PNG", url="https://example.test/"):
        self._records = list(records)
        self._shot = shot
        self._url = url
        self.shots_taken = 0
        self.collects = 0
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []

    def collect(self):
        self.collects += 1
        return list(self._records)

    def hit_test(self, x, y):
        return None

    def screenshot(self):
        self.shots_taken += 1
        return self._shot

    def click(self, ref):
        self.clicked.append(ref)

    def fill(self, ref, text):
        self.filled.append((ref, text))

    def url(self):
        return self._url


LIVE = ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"]
TYPABLE = LIVE + ["EDITABLE"]


def record(ref="e0", name="Sign in", role="button", top=0, states=None,
           **over):
    """One collector record, with sane defaults."""
    rec = {"ref": ref, "name": name, "role": role,
           "left": 0, "top": top, "width": 90, "height": 24,
           "states": list(states if states is not None else LIVE),
           "value": ""}
    rec.update(over)
    return rec


def records(n):
    """`n` distinct, ordinary controls."""
    return [record(ref=f"e{i}", name=f"Control {i}", top=i * 20)
            for i in range(n)]
