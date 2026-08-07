"""ToolResult — the structured contract every tool result normalizes to.

The lesson, borrowed from OpenClaw's `AgentToolResult` (their tools carry an
explicit `isError` + typed details) and adapted to OUR reality: Python, a live
Gemini function-call loop, and 27 existing string-returning tools we will NOT
rewrite at once.

Why it matters — the failure we lived this session:
  A tool returned "…couldn't confirm it sent…" and our code decided success by
  checking `"sent" in result`. The tool effectively LIED. With an explicit `ok`
  flag that class of bug is impossible.

Design for a free/weaker brain to perform like a strong one:
  The Gemini function response now carries `ok` (unambiguous success/failure) and
  an optional `guidance` (what to do next). The model stops misreading failures
  as success and stops spiralling into unrelated tools.

Non-breaking by construction:
  Tools may return a `ToolResult` OR a plain string (legacy). `normalize()` wraps
  a bare string as a success — identical to today's behaviour — so nothing breaks;
  migrate tools to real `ToolResult`s one at a time (fragile ones first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """What a tool hands back. `message` is what the model hears; `ok` is the
    truth the model (and our code) can rely on without parsing prose."""
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    # Only surfaced to the model on failure — a concrete next step ("ask the user
    # which contact", "reconnect Google", …) so it self-corrects instead of flailing.
    guidance: str = ""

    @classmethod
    def success(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=True, message=message, data=dict(data))

    @classmethod
    def failure(cls, message: str, guidance: str = "", **data: Any) -> "ToolResult":
        return cls(ok=False, message=message, guidance=guidance, data=dict(data))

    def to_response(self) -> dict[str, Any]:
        """The dict handed to Gemini as the function response.

        `result` stays the primary human-readable field (models are trained on
        it); `ok`/`guidance` augment it with an unambiguous signal.

        A tool that has NOT migrated to this contract gets no `ok` at all.
        Emitting ok=True for it was a lie with teeth: 183 legacy returns across
        this codebase describe a failure - "could not", "not found", "requires
        wmctrl" - and every one of them arrived as ok=True, while the system
        prompt instructs the model to trust `ok` over any prose. Absence sends
        the model back to reading the message, which is what these tools always
        relied on, instead of handing it a confident wrong answer.
        """
        resp: dict[str, Any] = {"result": self.message}
        if not self.data.get("_legacy_string"):
            resp["ok"] = self.ok
            if not self.ok and self.guidance:
                resp["guidance"] = self.guidance
        return resp


def normalize(raw: Any) -> ToolResult:
    """Coerce any tool's return value into a ToolResult.

    - ToolResult  → as-is (migrated tools).
    - str         → success wrapper (legacy tools; same behaviour as before).
    - None        → generic success ("Done.").
    - other       → str()-wrapped success.
    """
    if isinstance(raw, ToolResult):
        return raw
    if raw is None:
        return ToolResult.success("Done.")
    if isinstance(raw, str):
        # Legacy string tool: we can't know ok/fail from prose, so keep today's
        # behaviour (the model reads the text) but mark it legacy for telemetry.
        return ToolResult(ok=True, message=raw, data={"_legacy_string": True})
    return ToolResult.success(str(raw))
