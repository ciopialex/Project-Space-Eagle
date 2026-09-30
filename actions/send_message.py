from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from time import monotonic, sleep

from actions.messaging import ChatSurface, clamped_seconds, surface_for
from core import contact_aliases
from core.confirm import PARAM, Gate
from core.contact_match import Match, match, normalise
from core.tool_result import ToolResult

_TOOL = "send_message"
_POLL_S = 0.5
_LOAD_POLL_S = 1.0
_CANON = {"whatsapp": "whatsapp", "wa": "whatsapp", "whats app": "whatsapp",
          "whatsapp web": "whatsapp", "telegram": "telegram", "tg": "telegram",
          "telegram web": "telegram"}
_LABEL = {"whatsapp": "WhatsApp", "telegram": "Telegram"}
_RELINK = {
    "whatsapp": ("Tell the user to open Settings, choose Signed-in sites, press Sign in, "
                 "enter web.whatsapp.com and scan the code once."),
    "telegram": ("Tell the user to open Settings, choose Signed-in sites, press Sign in, "
                 "enter web.telegram.org and sign in once."),
}
_NOTHING = " Nothing was sent."
_CHECK_FIRST = "Tell the user to check the chat before sending again."
_SPACES = re.compile(r"\s+")

_sleep = sleep
_now = monotonic

_BUSY = threading.Lock()
_GATE = Gate()


@dataclass(frozen=True)
class _Offer:
    title: str
    spoken: str
    query: str
    again: bool
    learn: bool


_OFFERED: dict[str, _Offer] = {}
_SENT: dict[tuple[str, str, str], float] = {}
_UNSURE: dict[tuple[str, str, str], float] = {}


def _reset_for_tests() -> None:
    global _GATE
    _GATE = Gate()
    _OFFERED.clear()
    _SENT.clear()
    _UNSURE.clear()


def _flat(text: str | None) -> str:
    return _SPACES.sub(" ", text or "").strip()


def _either(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


class _Lookup:
    def __init__(self, surface: ChatSurface):
        self.surface = surface
        self.titles: list[str] = []
        self.found_by: dict[str, str] = {}
        self.visible: set[str] = set()

    def run(self, query: str) -> list[str] | None:
        got = self.surface.search(query)
        if got is None:
            self.visible = set()
            return None
        rows = [t for t in got if isinstance(t, str) and t]
        self.visible = set(rows)
        for title in rows:
            self.found_by.setdefault(title, query)
        for title in dict.fromkeys(rows):
            self.titles.extend([title] * (rows.count(title) - self.titles.count(title)))
        return rows

    def reveal(self, title: str, *hints: str) -> None:
        queries = [q for q in (*hints, self.found_by.get(title), title) if q is not None]
        for query in dict.fromkeys(queries):
            if title in self.visible:
                return
            self.run(query)


def _resolve(lookup: _Lookup, who: str, aliases: dict[str, str]) -> Match:
    trusted: set[str] = set()
    known = aliases.get(normalise(who))
    if known:
        trusted.update(lookup.run(known) or ())
    listed = lookup.run(who)
    trusted.update(listed or ())
    found = match(who, lookup.titles, aliases)
    if found.kind == "none":
        lookup.run("")
        found = match(who, lookup.titles, aliases)
    if not found.certain:
        return found
    vouched = found.title in trusted and (found.kind == "alias" or listed is not None)
    return found if vouched else Match("close", found.title, found.candidates)


def _settle(surface: ChatSurface | None) -> str | None:
    if surface is None:
        return "not_loaded"
    deadline = _now() + clamped_seconds("message_load_seconds", 15.0, 1.0, 30.0)
    while True:
        state = surface.ready()
        if state != "not_loaded" or _now() >= deadline:
            return state
        _sleep(_LOAD_POLL_S)


def _recent(store: dict[tuple[str, str, str], float], key: tuple[str, str, str]) -> bool:
    window = clamped_seconds("message_dedupe_seconds", 120.0, 1.0, 3600.0)
    now = _now()
    for old in [k for k, at in store.items() if now - at >= window]:
        store.pop(old, None)
    return key in store


def _offer(platform: str, title: str, text: str, spoken: str, query: str,
           again: bool, learn: bool) -> str:
    token = _GATE.issue(_TOOL, {"platform": platform, "title": title, "text": text})
    for stale in [t for t in _OFFERED if t not in _GATE.records]:
        _OFFERED.pop(stale, None)
    _OFFERED[token] = _Offer(title, spoken, query, again, learn)
    return token


def _deliver(surface: ChatSurface, lookup: _Lookup, platform: str, title: str, text: str,
             spoken: str, again: bool, learn: bool, *hints: str) -> ToolResult:
    key = (platform, title, text)
    if not again:
        if _recent(_SENT, key):
            return ToolResult.success(f"Already sent that to {title} a moment ago.",
                                      deduped=True, title=title)
        if _recent(_UNSURE, key):
            token = _offer(platform, title, text, spoken, lookup.found_by.get(title, spoken),
                           True, learn)
            return ToolResult.failure(
                f"I tried sending that to {title} and could not confirm it arrived. "
                "Nothing new was sent.",
                guidance=("Ask the user to check the chat. If they want it sent again, call "
                          f"again with the same message and confirm_token={token}."),
                needs_confirmation=True, confirm_token=token)
    lookup.reveal(title, *hints)
    if not surface.open_chat(title):
        return ToolResult.failure(
            f"Couldn't open the chat with {title}.{_NOTHING}",
            guidance="Tell the user the chat did not open. Try once more only if they ask.")
    if not again and _flat(surface.last_outgoing()) == _flat(text):
        token = _offer(platform, title, text, spoken, lookup.found_by.get(title, spoken),
                       True, learn)
        return ToolResult.failure(
            f"The chat with {title} already ends with those words.{_NOTHING}",
            guidance=("Ask the user if they want it sent again. If yes, call again with the "
                      f"same message and confirm_token={token}."),
            needs_confirmation=True, confirm_token=token)
    if not surface.send(text):
        _UNSURE[key] = _now()
        return ToolResult.failure(
            f"Something went wrong sending to {title}, so it may not have gone.",
            guidance=_CHECK_FIRST)
    deadline = _now() + clamped_seconds("message_verify_seconds", 10.0, 1.0, 30.0)
    while _flat(surface.last_outgoing()) != _flat(text):
        if _now() >= deadline:
            _UNSURE[key] = _now()
            return ToolResult.failure(f"I typed it to {title} but can't see it sent.",
                                      guidance=_CHECK_FIRST)
        _sleep(_POLL_S)
    if surface.current_title() != title:
        _UNSURE[key] = _now()
        return ToolResult.failure(
            f"I sent it but the open chat changed before I could confirm it reached {title}. "
            "Ask the user to check.",
            guidance=_CHECK_FIRST)
    _UNSURE.pop(key, None)
    _SENT[key] = _now()
    if learn:
        contact_aliases.learn(platform, spoken, title)
    return ToolResult.success(f"Sent to {title} on {_LABEL[platform]}.",
                              title=title, platform=platform)


def _confirmed(surface: ChatSurface, platform: str, who: str, text: str,
               token: str) -> ToolResult:
    offer = _OFFERED.get(token)
    if offer is not None and normalise(who) not in {normalise(offer.spoken),
                                                     normalise(offer.title)}:
        return ToolResult.failure(
            f"That yes was for {offer.title}.{_NOTHING}",
            guidance="Ask the user who they meant, then call again without confirm_token.")
    title = offer.title if offer is not None else ""
    cleared, reason, lead = _GATE.check(
        _TOOL, {"platform": platform, "title": title, "text": text}, token, had_token=True)
    if not cleared or offer is None:
        return ToolResult.failure(
            reason or f"That confirmation did not match.{_NOTHING}",
            guidance=(lead or "Call again without confirm_token to ask the user afresh.")
            + _NOTHING)
    _OFFERED.pop(token, None)
    return _deliver(surface, _Lookup(surface), platform, title, text, offer.spoken,
                    offer.again, offer.learn, offer.query)


def _ask(platform: str, who: str, text: str, found: Match, lookup: _Lookup) -> ToolResult:
    title = found.title or ""
    token = _offer(platform, title, text, who, lookup.found_by.get(title, who), False, True)
    names = list(dict.fromkeys(found.candidates or (title,)))
    others = [n for n in names if n != title]
    guidance = (f"Ask the user if they mean {title}. If yes, call again with the same "
                f"message and confirm_token={token}.")
    if others:
        guidance += f" If not, offer {_either(others)}."
    return ToolResult.failure(f"Did you mean {title}?", guidance=guidance + _NOTHING,
                              needs_confirmation=True, confirm_token=token,
                              candidates=names)


def _unsure(who: str, label: str, found: Match) -> ToolResult:
    names = list(dict.fromkeys(found.candidates))
    if found.kind == "ambiguous":
        if len({normalise(n) for n in names}) <= 1:
            return ToolResult.failure(
                f"You have more than one chat named {names[0] if names else who}.",
                guidance=("Ask the user to tell them apart by adding a last name or "
                          "picking a different chat." + _NOTHING),
                candidates=names)
        return ToolResult.failure(
            f"There is more than one: {', '.join(names)}.",
            guidance=("Ask the user which one they mean, then call again with receiver "
                      "set to the exact name they choose." + _NOTHING),
            candidates=names)
    if names:
        return ToolResult.failure(
            f"I can't find {who} in {label}. The closest are {_either(names)}.",
            guidance=(f"Ask the user for the name exactly as it is saved, or offer these: "
                      f"{_either(names)}." + _NOTHING),
            candidates=names)
    return ToolResult.failure(
        f"I can't find {who} in {label}.",
        guidance=f"Ask the user for the name exactly as it is saved in {label}." + _NOTHING)


def _run(surface: ChatSurface | None, platform: str, who: str, text: str,
         token: str) -> ToolResult:
    label = _LABEL[platform]
    state = _settle(surface)
    if state == "signed_out":
        return ToolResult.failure(f"{label} is not signed in on the eagle's browser.",
                                  guidance=_RELINK[platform] + _NOTHING)
    if state is not None or surface is None:
        return ToolResult.failure(f"{label} did not finish loading.",
                                  guidance="Try once more in a moment." + _NOTHING)
    if token:
        return _confirmed(surface, platform, who, text, token)
    lookup = _Lookup(surface)
    found = _resolve(lookup, who, contact_aliases.load(platform))
    if found.certain and found.title:
        return _deliver(surface, lookup, platform, found.title, text, who, False, False)
    if found.kind == "close" and found.title:
        return _ask(platform, who, text, found, lookup)
    return _unsure(who, label, found)


def send_message(parameters: dict, player=None,
                 surfaces: dict[str, ChatSurface] | None = None) -> ToolResult:
    given = parameters or {}
    who = str(given.get("receiver") or "").strip()
    text = str(given.get("message_text") or "").strip()
    asked = " ".join(str(given.get("platform") or "").lower().split())
    token = str(given.get(PARAM) or "").strip()

    if not asked:
        return ToolResult.failure(
            "Which app should it go through?",
            guidance="Ask the user whether to use WhatsApp or Telegram, then call again." + _NOTHING)
    platform = _CANON.get(asked)
    if platform is None:
        return ToolResult.failure(
            "I can only send WhatsApp and Telegram messages directly.",
            guidance=("Only WhatsApp and Telegram are supported here." + _NOTHING
                      + " For any other app, do it through its website with web_agency."))
    if not who:
        return ToolResult.failure(
            "Who should the message go to?",
            guidance="Ask the user who to send it to, then call again." + _NOTHING)
    if not text:
        return ToolResult.failure(
            "What should the message say?",
            guidance="Ask the user what to write, then call again." + _NOTHING)
    if not _BUSY.acquire(blocking=False):
        return ToolResult.failure(
            "Another message is still being sent. Wait for it to finish." + _NOTHING,
            guidance="Tell the user one message is still going out; try this one after it finishes.")
    try:
        surface = surfaces.get(platform) if surfaces is not None else surface_for(platform)
        return _run(surface, platform, who, text, token)
    finally:
        _BUSY.release()
