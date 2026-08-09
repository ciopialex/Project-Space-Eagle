"""Terminal output that says what failed and why.

The logs found nearly every real bug this week — seven of them stacked on one
sentence. What they could not show was the *reason*, because three things were
missing at exactly the moment they mattered:

  * the result was truncated at 80 characters, which cuts off the half that
    identifies the cause;
  * `guidance` — the field that carries the next step, the console link, the
    "do NOT ask them to sign in again" — was never printed at all;
  * a legacy tool that reports no status was rendered with the same green tick
    as a verified success.

So a failure read as::

    [Aethelark] 📤 youtube_api ✗ → Aethelark needs the user to sign in to Goog…

while the actual remedy sat in a field nobody printed.

One rule beyond that: arguments are logged verbatim and some tools take
credentials. A debug log that leaks a token into a terminal the user then
pastes into a chat is a worse bug than whatever was being debugged.
"""
from __future__ import annotations

#: Argument names whose VALUES must never reach the terminal. Matched as
#: substrings, because the real names vary (`api_key`, `gemini_api_key`,
#: `client_secret`). The key itself is still shown — knowing a credential was
#: passed is diagnostic; knowing what it was is a leak.
_SECRET_HINTS = ("key", "secret", "token", "password", "passwd", "credential",
                 "auth", "cookie", "session")

#: Long values are shortened, never dropped. "text was 5000 chars" is often
#: the whole answer to why something misbehaved.
_MAX_VALUE = 120


def _safe_value(name: str, value) -> str:
    lowered = str(name).lower()
    if any(hint in lowered for hint in _SECRET_HINTS):
        return "<hidden>"
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        return "<unprintable>"
    if len(text) > _MAX_VALUE:
        return f"{text[:_MAX_VALUE]}… ({len(text)} chars)"
    return text


def _safe_args(args) -> str:
    if not isinstance(args, dict) or not args:
        return "{}"
    try:
        inner = ", ".join(f"{k}={_safe_value(k, v)}" for k, v in args.items())
    except Exception:
        return "<unreadable args>"
    return "{" + inner + "}"


def tool_call(name: str, args, epoch: int = 0) -> str:
    """The line printed when a tool starts.

    First question when anything misbehaves is always "with what arguments",
    so they are here, with secrets redacted by key name rather than by hoping
    none are passed.
    """
    try:
        return f"[Tool] ▶ {name} (epoch={epoch}) {_safe_args(args)}"
    except Exception:
        return f"[Tool] ▶ {name}"


def tool_result(name: str, result, elapsed_ms: float) -> str:
    """The line printed when a tool finishes — the whole reason, never cut.

    Three outcomes, three symbols, because they are genuinely different:

        ✓  the tool asserted it worked
        ✗  the tool asserted it failed, and said what to do next
        ?  the tool said nothing either way (not yet on the contract)

    That third one used to print as ✓. It is the same lie the contract fix
    removed from the model's side, and it is worse in a log, because the log is
    what a human reads while deciding whether to trust the run.
    """
    try:
        if result is None:
            return f"[Tool] ? {name} returned nothing ({elapsed_ms:.0f}ms)"

        message = str(getattr(result, "message", result) or "").strip()
        guidance = str(getattr(result, "guidance", "") or "").strip()
        legacy = bool(getattr(result, "data", {}) .get("_legacy_string"))
        ok = bool(getattr(result, "ok", True))

        if legacy:
            head = f"[Tool] ? {name} no status reported ({elapsed_ms:.0f}ms)"
            return f"{head}\n        said: {message}" if message else head

        mark = "✓" if ok else "✗"
        head = f"[Tool] {mark} {name} ({elapsed_ms:.0f}ms)"
        lines = [head]
        if message:
            lines.append(f"        {'result' if ok else 'why'}: {message}")
        if guidance:
            lines.append(f"        next: {guidance}")
        return "\n".join(lines)
    except Exception as e:      # a logger must never be the thing that breaks
        return f"[Tool] ? {name} (log failed: {e})"


def intent_line(intent) -> str:
    """What the eagle guessed before the model answered, and how sure it was.

    Without this a wrong pre-warm is invisible: the browser starts, nothing
    explains why, and the only symptom is a process nobody asked for.
    """
    try:
        if intent is None or intent.capability is None:
            return "[Intent] no hypothesis — the model decides alone"
        warm = ", ".join(c.id for c in intent.prewarm) or "nothing"
        return (f"[Intent] {intent.capability.id} "
                f"(conf={intent.confidence:.1f}, matched={intent.matched!r}) "
                f"→ pre-warming: {warm}")
    except Exception:
        return "[Intent] <unreadable>"
