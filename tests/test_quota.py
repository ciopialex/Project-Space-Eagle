"""A rate limit is not a broken feature, and must not read like one.

The brain runs on a free Gemini tier — roughly 15 requests a minute — and nine
tools call Gemini inside themselves: web_search, file_processor, code_helper,
screen vision, flight_finder, desktop, and more. Two of the nine explained a
429; the rest surfaced the raw error or a generic failure.

That matters more here than it looks. The eagle's whole credibility problem
this week has been reporting the wrong reason: "your videos are private" for a
disabled API, "covered by something else" for a click that was fine. A quota
error dressed as a tool failure is the same mistake — the user goes looking
for a bug in something that works, and will work again in sixty seconds.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.quota import explain_quota, looks_like_quota  # noqa: E402
from core.tool_result import ToolResult, normalize  # noqa: E402


def test_the_shapes_google_actually_returns():
    for text in (
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}",
        "You exceeded your current quota, please check your plan",
        "rate limit exceeded, retry in 30s",
        "Quota exceeded for quota metric 'Generate Content API requests'",
    ):
        assert looks_like_quota(text), text


def test_an_ordinary_failure_is_not_a_quota_error():
    for text in ("File not found: /tmp/x", "No control matches 'Sign in'",
                 "429 items were returned", "connection refused"):
        assert not looks_like_quota(text), text


def test_a_quota_failure_is_relabelled_with_what_to_say():
    got = explain_quota(ToolResult.failure(
        "Summary generation failed: 429 RESOURCE_EXHAUSTED"))
    assert got.ok is False
    low = (got.message + " " + got.guidance).lower()
    assert "quota" in low or "rate limit" in low
    assert "minute" in low or "resets" in low, "did not say it is temporary"
    assert "not" in low, "should say the feature is not broken"


def test_a_legacy_string_carrying_a_quota_error_is_caught_too():
    """17 of 20 tools still return bare strings, and normalize keeps `ok` off
    them — so without this the model sees only the raw 429 text."""
    got = explain_quota(normalize("Error: 429 You exceeded your current quota"))
    assert "quota" in (got.message + got.guidance).lower()


def test_a_healthy_result_is_untouched():
    original = ToolResult.success("96 controls on the page")
    assert explain_quota(original) is original


def test_it_never_raises():
    for junk in (None, ToolResult.success(""), normalize("")):
        assert explain_quota(junk) is not None or junk is None
