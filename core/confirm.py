"""A spoken yes, bound to exactly what it was given for.

One gate for everything that cannot be taken back: a module's print, a stop,
the computer shutting down. The first call runs nothing and gets a single-use
token bound to a digest of the request; only a second call carrying that token,
for the same request, clears. The model cannot invent a token, so a cleared
call always has a question behind it -- the one the user heard -- and a turn
of the user's after it. Without that turn the model could spend the token it
was just handed on its very next call, and the yes would be its own.

This lived inside the module bus. computer_settings grew its own gate beside
it, keyed on a `confirmed=yes` argument the tool's schema never declared: a
model could pass it on the first call without asking anyone, or not be able to
pass it at all. Two gates for one idea drift apart; this is the one.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any, Callable

#: How long a spoken "yes" stays good for. Long enough for the user to answer a
#: question and the model to call back; short enough that a yes from earlier in
#: the conversation is not still authorising something now.
TTL_S = 180.0

#: The argument the token travels on, for every gated tool.
PARAM = "confirm_token"

_turns = 0
_heard: list[str] = []
_HEARD_KEPT = 400
_turns_lock = threading.Lock()

_NO = re.compile(
    r"\b(no|nope|nah|not|don'?t|do not|stop|cancel|wait|hold on|never ?mind|"
    r"nu|nein|non)\b", re.I)
_YES = re.compile(
    r"\b(yes|yeah|yep|yup|ya|sure|ok|okay|alright|all right|go ahead|go for it|"
    r"do it|confirm|confirmed|affirmative|correct|absolutely|definitely|please do|"
    r"send it|print it|da|sigur|oui|ja|s[ií])\b", re.I)


def note_user_turn(text: str = "", *, typed: bool = False) -> None:
    """The user said or typed something. Only this can clear a token."""
    global _turns
    with _turns_lock:
        _turns += 1
        _heard.append(f" {text} " if typed else text)
        del _heard[:-_HEARD_KEPT]


def user_turns() -> int:
    return _turns


def heard_since(turn: int) -> str:
    with _turns_lock:
        missed = _turns - turn
        if missed <= 0:
            return ""
        return "".join(_heard[-missed:])


def answer(text: str) -> str:
    """'no', 'yes', or '' for whatever the user said after a question."""
    if _NO.search(text):
        return "no"
    if _YES.search(text):
        return "yes"
    return ""


class Gate:
    """Issues and checks confirmation tokens. Thread-safe."""

    def __init__(self, ttl_s: float = TTL_S,
                 clock: Callable[[], float] | None = None) -> None:
        #: token -> {digest, expires_at, tool}. Public so a test can age one.
        self.records: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_s
        # Looked up on every call rather than bound once, so the wall clock
        # the rest of the process sees is the one this gate sees too.
        self._clock = clock or (lambda: time.time())

    @staticmethod
    def digest(name: str, args: dict[str, Any]) -> str:
        """Fingerprint of exactly what was asked for.

        A yes for a keychain on CC1 cannot be replayed to start a benchy on
        CC2. Sorted and JSON-encoded so argument order never changes it.
        """
        payload = json.dumps([name, sorted(args.items())], sort_keys=True,
                             default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def check(self, name: str, args: dict[str, Any], token: str,
              had_token: bool = False) -> tuple[bool, str, str]:
        """(cleared, why a presented token was refused, a lead for the model).

        A token is spent only by the request it authorised. An expired one is
        dead either way; a live one presented for a different request is left
        where it is, because refusing AND destroying it made the corrected
        retry fail too -- blaming the user for a yes the eagle had just spent.
        """
        if not (had_token or token):
            return False, "", ""
        digest = self.digest(name, args)
        now = self._clock()
        with self._lock:
            record = self.records.get(token) if token else None
            if record is not None:
                stale = record["expires_at"] < now
                if stale or record["digest"] == digest:
                    self.records.pop(token, None)
        if record is None:
            return False, ("That confirmation is not one I issued, so nothing "
                           "was started."), ""
        if record["expires_at"] < now:
            return False, "That confirmation has expired, so nothing was started.", ""
        if record.get("turn", -1) >= user_turns():
            with self._lock:
                self.records.setdefault(token, record)
            return False, ("Nothing was started: the user has not answered yet."), (
                "Ask the user the question out loud and wait for their answer "
                "before calling again with the token.")
        if record["digest"] != digest:
            return False, ("That confirmation was for a different request, so "
                           "nothing was started."), ("The user agreed to "
                                                     "something else. Ask "
                                                     "again for THIS request.")
        said = answer(heard_since(record.get("turn", 0)))
        if said == "no":
            return False, "The user said no, so nothing was started.", (
                "Tell the user it was not done. Do not ask again unless they "
                "ask for it.")
        if said != "yes":
            with self._lock:
                self.records.setdefault(token, record)
            return False, "Nothing was started: the user has not said yes.", (
                "Ask the user the question again and wait for a clear yes or "
                "no before calling again with the token.")
        return True, "", ""

    def issue(self, name: str, args: dict[str, Any]) -> str:
        """A new single-use token for exactly this request."""
        token = secrets.token_urlsafe(16)
        now = self._clock()
        with self._lock:
            for tok in [t for t, r in self.records.items() if r["expires_at"] < now]:
                self.records.pop(tok, None)
            self.records[token] = {"digest": self.digest(name, args),
                                   "expires_at": now + self._ttl, "tool": name,
                                   "turn": user_turns()}
        return token
