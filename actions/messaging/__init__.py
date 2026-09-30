from __future__ import annotations

import re
from typing import Any, Protocol

from config import get_config
from core.contact_match import normalise

_NUMBER = re.compile(r"\d+(\.\d+)?")


class ChatSurface(Protocol):
    platform: str

    def ready(self) -> str | None: ...

    def search(self, query: str) -> list[str] | None:
        """Filtered titles, [] when the app confirmed no match, None when unknown."""
        ...

    def open_chat(self, title: str) -> bool: ...
    def send(self, text: str) -> bool: ...
    def last_outgoing(self) -> str | None: ...
    def current_title(self) -> str | None: ...


def surface_for(platform: str) -> ChatSurface | None:
    key = (platform or "").strip().lower()
    if key in ("whatsapp", "wa", "whats app"):
        from actions.messaging.whatsapp import WhatsAppWeb
        return WhatsAppWeb()
    if key in ("telegram", "tg"):
        from actions.messaging.telegram import TelegramWeb
        return TelegramWeb()
    return None


def ensure_running(browser: Any) -> None:
    running = getattr(browser, "running", True)
    if running:
        return
    start = getattr(browser, "start", None)
    if start is None:
        return
    try:
        start()
    except Exception:
        pass


def clamped_seconds(key: str, default: float, lo: float, hi: float) -> float:
    try:
        raw = str(get_config().get(key, default)).strip()
    except Exception:
        raw = ""
    value = float(raw) if _NUMBER.fullmatch(raw) else default
    return min(max(value, lo), hi)


def relevant_titles(titles: list[str], query: str) -> list[str]:
    words = normalise(query).split()
    return [t for t in titles if all(w in normalise(t) for w in words)]
