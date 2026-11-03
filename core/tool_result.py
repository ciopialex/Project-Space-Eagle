"""ToolResult: the contract every tool result normalises to.

The function response carries `ok` (unambiguous) and, on failure, `guidance` (what
to do next), so a small model neither misreads a failure as success nor spirals
into unrelated tools. Tools may still return a plain string; normalize() keeps
those working.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """What a tool hands back. `message` is what the model hears; `ok` is the
    verdict.
    """
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    # Only sent on failure: a concrete next step.
    guidance: str = ""

    @classmethod
    def success(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=True, message=message, data=dict(data))

    @classmethod
    def failure(cls, message: str, guidance: str = "", **data: Any) -> "ToolResult":
        return cls(ok=False, message=message, guidance=guidance, data=dict(data))

    def to_response(self) -> dict[str, Any]:
        """The dict handed to Gemini as the function response. `result` stays the
        main field. A tool that has not migrated gets no `ok` at all: emitting
        ok=True for legacy prose would contradict failures the prose describes.
        """
        resp: dict[str, Any] = {"result": self.message}
        if not self.data.get("_legacy_string"):
            resp["ok"] = self.ok
            if not self.ok and self.guidance:
                resp["guidance"] = self.guidance
        return resp


class Failed(str):
    """A helper's prose that remembers it is a failure. A str subclass, so callers
    and tests are unaffected; the deciding code returns Failed(...) with its next
    step. String operations on it return plain str.
    """

    guidance: str

    def __new__(cls, text: str, guidance: str = "") -> "Failed":
        obj = super().__new__(cls, text)
        obj.guidance = guidance
        return obj


def settled(raw: Any) -> ToolResult:
    """A migrated tool's return as an explicit verdict: an unmarked string is a
    result. normalize() reads the same string as no verdict, which is right only
    for tools that have not migrated. Tests must assert on to_response().
    """
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, Failed):
        return ToolResult.failure(str(raw), guidance=raw.guidance)
    if raw is None:
        return ToolResult.success("Done.")
    return ToolResult.success(str(raw))


def normalize(raw: Any) -> ToolResult:
    """Coerce any return into a ToolResult: ToolResult as is; Failed -> failure with
    guidance; str -> legacy, no verdict; None -> "Done."; other -> str().
    """
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, Failed):
        # Before `str`: Failed is a str.
        return ToolResult.failure(str(raw), guidance=raw.guidance)
    if raw is None:
        return ToolResult.success("Done.")
    if isinstance(raw, str):
        # Legacy string: the model reads the text.
        return ToolResult(ok=True, message=raw, data={"_legacy_string": True})
    return ToolResult.success(str(raw))
