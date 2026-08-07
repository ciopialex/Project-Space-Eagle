"""The routing escape hatch has to be a mechanism, not a hope.

The prompt already tells the model to try a general tool when a narrow one
fails. That is advice, and it is consulted at exactly the moment the model's
judgement has already been shown to be wrong — it said "YouTube keeps that
private" about the user's own liked videos, and never tried web_agency. These
tests are about the part that does not depend on the model noticing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_fallback import (  # noqa: E402
    FALLBACKS, GENERAL, fallback_for, with_fallback_guidance)
from core.tool_result import ToolResult  # noqa: E402


def test_a_failed_narrow_tool_names_the_general_one():
    r = with_fallback_guidance(
        "youtube_video",
        ToolResult.failure("No result found for that search."))
    assert "web_agency" in r.guidance


def test_the_original_guidance_survives():
    """The tool's own advice is usually more specific than ours. Replacing it
    would trade a precise instruction for a generic one."""
    r = with_fallback_guidance(
        "youtube_video",
        ToolResult.failure("Nope.", guidance="Ask which song he meant."))
    assert "Ask which song he meant." in r.guidance
    assert "web_agency" in r.guidance


def test_a_successful_tool_is_left_alone():
    r = with_fallback_guidance("youtube_video", ToolResult.success("Playing."))
    assert not r.guidance


def test_a_general_tool_is_never_pointed_at_itself():
    """web_agency failing means the task is genuinely blocked. Telling the
    model to try web_agency next is how a retry loop starts."""
    for tool in GENERAL:
        r = with_fallback_guidance(tool, ToolResult.failure("Blocked."))
        assert tool not in r.guidance


def test_an_unmapped_tool_is_left_alone():
    r = with_fallback_guidance("reminder", ToolResult.failure("No such date."))
    assert r.guidance == ""


def test_the_hint_is_not_repeated():
    """Applied twice — a retry, a re-normalise — must not stack up."""
    r = ToolResult.failure("Nope.")
    once = with_fallback_guidance("youtube_video", r).guidance
    twice = with_fallback_guidance("youtube_video", r).guidance
    assert once == twice


def test_every_mapping_points_at_a_real_general_tool():
    """A typo here produces guidance naming a tool that does not exist, which
    is worse than no guidance: the model would report that it tried."""
    for narrow, (target, why) in FALLBACKS.items():
        assert target in GENERAL, f"{narrow} -> {target} is not a general tool"
        assert why and not why.endswith("."), f"{narrow}: reason reads oddly"
        assert narrow not in GENERAL, f"{narrow} is both narrow and general"


def test_every_mapped_tool_actually_exists_in_main():
    """The mapping is keyed on tool names the model can call. A renamed tool
    would leave a dead entry that silently never fires."""
    import main
    known = set(main.TOOL_SPECS) | {
        d.get("name") for decl in main.TOOL_DECLARATIONS
        for d in getattr(decl, "function_declarations", []) or []}
    known |= {getattr(d, "name", "") for decl in main.TOOL_DECLARATIONS
              for d in getattr(decl, "function_declarations", []) or []}
    for narrow in FALLBACKS:
        assert narrow in known, f"{narrow} is not a tool main.py declares"
    for general in GENERAL:
        assert general in known, f"{general} is not a tool main.py declares"


def test_the_youtube_failure_that_started_this_is_covered():
    """The regression, stated as itself."""
    r = with_fallback_guidance(
        "youtube_video",
        ToolResult.failure("I can't access your liked videos."))
    assert "web_agency" in r.guidance
    assert "limit" in r.guidance.lower()
