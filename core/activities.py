"""Live Activities: ongoing tasks a module reports, owned and drawn by the host.

Payload contract (optional, on any ambient event):

    "activity": {"id": "CC2", "state": "active"|"ended", "leading": "CC2",
                 "trailing": "42%", "progress": 0.42, "relevance": 50,
                 "stale_after_s": 120}          (or a list of these)
    "alert": "CC2:benchy.gcode:first_layer"

An alert id is shown once; repeat it on every frame for as long as it is true.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MAX_AGE_S = 8 * 3600
DEFAULT_STALE_AFTER_S = 120.0
LEADING_MAX = 18
TRAILING_MAX = 12
MAX_ACTIVITIES = 4


def _short(value: Any, cap: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _unit(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0 or f > 1:
        return None
    return f


@dataclass
class Activity:
    module: str
    id: str
    leading: str
    trailing: str
    progress: float | None
    relevance: int
    stale_after_s: float
    started_at: float
    updated_at: float
    heard_at: float
    card: dict = field(default_factory=dict)
    image: str = ""

    @property
    def key(self) -> str:
        return f"{self.module}:{self.id}"

    def stale(self, now: float) -> bool:
        return now - self.heard_at > self.stale_after_s

    def view(self, now: float) -> dict:
        return {
            "key": self.key,
            "module": self.module,
            "leading": self.leading,
            "trailing": self.trailing,
            "progress": self.progress,
            "stale": self.stale(now),
            "card": self.card,
            "image": self.image,
        }


def _blocks(payload: Any) -> list[dict]:
    raw = payload.get("activity") if isinstance(payload, dict) else None
    items = raw if isinstance(raw, list) else [raw]
    return [b for b in items if isinstance(b, dict) and _short(b.get("id"), 64)]


_RANK = {"ended": 4, "started": 3, "updated": 2, "gone": 1}


class ActivityBoard:

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._items: dict[str, Activity] = {}
        self._said: dict[str, float] = {}

    def first_alert(self, module: str, payload: dict) -> bool:
        tag = payload.get("alert") if isinstance(payload, dict) else None
        if not tag:
            return False
        if not isinstance(tag, str):
            ids = ",".join(sorted(_short(b.get("id"), 64) for b in _blocks(payload)))
            tag = f"{payload.get('event', '')}:{ids}"
        key = f"{module}:{tag}"
        now = self._clock()
        if key in self._said:
            return False
        if len(self._said) > 512:
            for k in sorted(self._said, key=self._said.get)[:256]:
                del self._said[k]
        self._said[key] = now
        return True

    def apply(self, module: str, payload: dict) -> str | None:
        """Returns "started", "updated", "ended", "gone" (an end for something
        never seen running), or None if the payload has no activity block."""
        blocks = _blocks(payload)
        if not blocks:
            return None
        own = str(payload.get("printer") or payload.get("id") or "")
        card = {k: v for k, v in payload.items() if k != "activity"}
        kinds = [self._apply_one(module, b, card if len(blocks) == 1 or
                                 _short(b.get("id"), 64) == own else {})
                 for b in blocks]
        return max(kinds, key=_RANK.__getitem__)

    def _apply_one(self, module: str, block: dict, card: dict) -> str:
        ident = _short(block.get("id"), 64)
        key = f"{module}:{ident}"
        now = self._clock()

        if str(block.get("state") or "active").lower() == "ended":
            return "ended" if self._items.pop(key, None) is not None else "gone"

        try:
            relevance = int(block.get("relevance", 50))
        except (TypeError, ValueError):
            relevance = 50
        try:
            stale_after = float(block.get("stale_after_s") or DEFAULT_STALE_AFTER_S)
        except (TypeError, ValueError):
            stale_after = DEFAULT_STALE_AFTER_S

        existing = self._items.get(key)
        self._items[key] = Activity(
            module=module, id=ident,
            leading=_short(block.get("leading") or ident, LEADING_MAX),
            trailing=_short(block.get("trailing"), TRAILING_MAX),
            progress=_unit(block.get("progress")),
            relevance=relevance,
            stale_after_s=max(5.0, stale_after),
            started_at=existing.started_at if existing else now,
            updated_at=now, heard_at=now, card=card,
            image=_short(block.get("image"), 1024))
        return "updated" if existing else "started"

    def heard(self, module: str, payload: dict) -> None:
        now = self._clock()
        live = {_short(b.get("id"), 64) for b in _blocks(payload)
                if str(b.get("state") or "active").lower() != "ended"}
        for a in self._items.values():
            if a.module == module and a.id in live:
                a.heard_at = now

    def end_module(self, module: str) -> bool:
        gone = [k for k, a in self._items.items() if a.module == module]
        for k in gone:
            del self._items[k]
        return bool(gone)

    def visible(self) -> list[dict]:
        now = self._clock()
        for k in [k for k, a in self._items.items()
                  if now - a.started_at > MAX_AGE_S]:
            del self._items[k]
        ranked = sorted(self._items.values(),
                        key=lambda a: (-a.relevance, a.started_at))
        return [a.view(now) for a in ranked[:MAX_ACTIVITIES]]

    @staticmethod
    def signature(views: list[dict]) -> tuple:
        return tuple((v["key"], v["leading"], v["trailing"], v["progress"],
                      v["stale"]) for v in views)
