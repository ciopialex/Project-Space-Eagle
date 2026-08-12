"""A cheap, comparable fingerprint of where the eagle currently is.

Two bugs from the last two days share one missing idea.

A tool reported success it never had. The `ToolResult` contract exists because
of that class, and it still only ever proves *the call returned* — never *the
world changed*. And a mission reopened MakerWorld forever, because "have I
been here before?" had nothing to key on; the fix matched on the goal's TEXT,
which is a proxy for the thing actually wanted.

Both need this: something you can take before and after a step, compare, and
answer two questions with evidence rather than inference —

    did anything move?
    have I been exactly here before?

Deliberately NOT cryptographic and NOT exact. Real pages jitter: a timestamp,
a rotating advert, a live view counter. A fingerprint that changes on every
poll answers neither question. So it keeps the things that mean "somewhere
else" — the address, and the set of controls that exist — and ignores order,
which the collector does not guarantee anyway.

The `unknown` flag is the part that matters most. A read that FAILED must
never compare equal to a page that is genuinely empty, and two failed reads
must never look like the same place. Collapsing those is precisely how "I
could not look" becomes "there is nothing there" — the defect this codebase
has produced in the bot wall, the vision grounder, and every unmigrated tool.
"""
from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field

#: Never compare equal to anything, including each other.
_UNKNOWN = itertools.count()


@dataclass(frozen=True)
class Signature:
    url: str = ""
    control_count: int = 0
    controls_hash: str = ""
    unknown: bool = False
    #: Distinguishes one failed read from another. Without it two `unknown`
    #: signatures are equal, and every failure looks like a loop.
    nonce: int = field(default=0)

    def __hash__(self) -> int:
        return hash((self.url, self.control_count, self.controls_hash,
                     self.unknown, self.nonce))

    @property
    def key(self) -> str:
        """A short, stable, JSON-safe identity for this place.

        Missions are persisted across reconnects, so anything the loop check
        depends on has to survive a round trip through the store — otherwise a
        GoAway wipes the memory and the loop simply resumes.
        """
        return f"{self.url}|{self.control_count}|{self.controls_hash}"

    @property
    def worth_recording(self) -> bool:
        """A place worth remembering having been. A failed read is not one."""
        return not self.unknown

    def same_as(self, other: "Signature") -> bool:
        """Effectively the same place. False whenever either read failed —
        absence of evidence is not evidence of sameness."""
        if self.unknown or other.unknown:
            return False
        return (self.url == other.url
                and self.control_count == other.control_count
                and self.controls_hash == other.controls_hash)


def _hash_names(names) -> str:
    # Sorted: the collector does not promise an order, and a reshuffle is not
    # a change in the world.
    # Separated: without it "Home"+"Search" and "Hom"+"eSearch" hash alike.
    joined = " | ".join(sorted(n for n in names if n))
    return hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:16]


def signature_of(port) -> Signature:
    """Fingerprint whatever `port` is currently showing.

    Never raises. A port that cannot be read yields `unknown`, which is a real
    answer and a different one from "an empty page".
    """
    try:
        url = str(port.url() or "")
    except Exception:
        url = ""
    try:
        records = port.collect() or []
    except Exception:
        return Signature(url=url, unknown=True, nonce=next(_UNKNOWN))

    # Names AND values. Typing into a field changes no control's NAME, so a
    # name-only fingerprint reports "nothing changed" after every keystroke —
    # measured live: all four steps of a working mission claimed the page had
    # not reacted, which would train anyone reading it to ignore the warning.
    parts = []
    for r in records:
        try:
            get = (r.get if isinstance(r, dict)
                   else (lambda k, d=None: getattr(r, k, d)))
            name = str(get("name") or "")
            value = str(get("value") or "")
            parts.append(f"{name}={value}" if value else name)
        except Exception:
            continue
    return Signature(url=url, control_count=len(records),
                     controls_hash=_hash_names(parts))


def describe_change(before: Signature, after: Signature) -> str:
    """What moved, in words a person could check against the screen.

    Evidence, not diagnosis. It says the address changed and how many controls
    appeared; it never says WHY, because that is an inference and mixing the
    two is how a log stops being trustworthy.
    """
    if before.unknown or after.unknown:
        return "could not read the page, so no change can be claimed either way"
    parts = []
    if before.url != after.url:
        parts.append(f"address changed to {after.url}")
    delta = after.control_count - before.control_count
    if delta:
        parts.append(f"{abs(delta)} control{'s' if abs(delta) != 1 else ''} "
                     f"{'appeared' if delta > 0 else 'disappeared'}")
    elif before.controls_hash != after.controls_hash:
        parts.append("the controls on the page are different")
    if not parts:
        return "nothing on the page changed"
    return "; ".join(parts)
