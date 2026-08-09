"""Tell a rate limit apart from a broken tool.

The brain runs on a free Gemini tier - roughly 15 requests a minute - and nine
tools call Gemini inside themselves. Two of them explained a 429; the rest
surfaced the raw error, so a temporary limit read as a failed feature.

That is the same mistake this project keeps paying for: reporting the wrong
reason. "Your videos are private" for a disabled API. "Covered by something
else" for a click that was fine. A quota error dressed as a tool failure sends
the user looking for a bug in something that works, and will work again in
sixty seconds.

Applied at the single point every tool result passes through, so all nine are
covered by one change rather than nine edits that drift apart.
"""
from __future__ import annotations

#: Signatures Google actually returns. Deliberately specific - "429" alone
#: appears in ordinary text ("429 items were returned") and matching it bare
#: would relabel healthy results as quota errors.
_MARKS = (
    "resource_exhausted",
    "exceeded your current quota",
    "quota exceeded",
    "rate limit exceeded",
    "ratelimitexceeded",
    "429 resource",
    "code': 429",
    "code\": 429",
)


def looks_like_quota(text: str) -> bool:
    lowered = (text or "").lower()
    return any(mark in lowered for mark in _MARKS)


def explain_quota(result):
    """Relabel a quota failure so it reads as temporary. Otherwise untouched."""
    try:
        if result is None:
            return result
        message = str(getattr(result, "message", "") or "")
        if not looks_like_quota(message):
            return result

        from core.tool_result import ToolResult
        return ToolResult.failure(
            "The Gemini API quota is used up for the moment - this is a rate "
            "limit, not a broken tool.",
            guidance=("Tell the user plainly that the request itself was fine "
                      "and the API is rate limited; it resets within a minute "
                      "or so on the free tier. Do NOT say the feature does not "
                      "work, do not retry immediately, and do not reach for a "
                      "different tool to work around it."),
            **{k: v for k, v in (getattr(result, "data", {}) or {}).items()
               if k != "_legacy_string"})
    except Exception:
        return result
