"""`Failed` — a helper's prose that remembers it is a failure.

The tool contract is rolled out at the BOUNDARY (see `file_processor`): the
entrypoint returns a `ToolResult`, the internal helpers keep their prose.
That works while the entrypoint is the thing that DECIDES the failure.

For most of the queue it is not. `file_controller` decides "Source not found"
eleven functions deep; `_guard` turns every containment breach and OSError
into a sentence. By the time the entrypoint sees it, the only thing left is a
string, and no amount of care at the boundary can tell it apart from a
successful one — which is why grepping prose ("if 'denied' in result") is the
bug this whole contract exists to kill.

`Failed` closes that gap without touching 123 call sites: it IS a `str`, so
every existing caller, format and assertion behaves identically, and it
carries the `guidance` the deciding code already knew. The entrypoint asks one
question — `isinstance(x, Failed)` — instead of reading tea leaves.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_result import Failed, ToolResult, normalize  # noqa: E402


def test_it_is_a_string_everywhere_a_string_was():
    """The whole point. Anything less and this is a rewrite, not a migration."""
    f = Failed("Access denied: /etc/passwd")
    assert isinstance(f, str)
    assert f == "Access denied: /etc/passwd"
    assert "denied" in f
    assert f.upper().startswith("ACCESS")
    assert f"{f}" == "Access denied: /etc/passwd"
    assert len(f) == len("Access denied: /etc/passwd")


def test_it_carries_the_next_step_the_deciding_code_knew():
    f = Failed("Source not found: notes.txt",
               guidance="Ask the user to confirm the filename.")
    assert f.guidance == "Ask the user to confirm the filename."


def test_guidance_is_optional_because_not_every_failure_has_a_next_step():
    assert Failed("It broke.").guidance == ""


def test_normalize_turns_it_into_an_explicit_failure():
    """A bare string normalizes to a legacy result with NO ok flag. A Failed
    string must normalize to ok=False — that is the entire difference."""
    r = normalize(Failed("Not a directory: /home/x", guidance="Ask which folder."))
    assert isinstance(r, ToolResult)
    assert r.ok is False
    assert r.message == "Not a directory: /home/x"
    assert r.guidance == "Ask which folder."


def test_a_plain_string_is_still_legacy_and_still_claims_nothing():
    """The non-breaking guarantee, restated as a test: migrating one tool must
    not start asserting success for the twenty that have not migrated."""
    r = normalize("Contents of Desktop/ (4 items):")
    assert r.ok is True
    assert r.data.get("_legacy_string") is True
    assert "ok" not in r.to_response(), "invented a success for a legacy tool"


def test_a_failed_string_reaches_the_model_with_ok_false_and_guidance():
    resp = normalize(Failed("Permission denied: /root",
                            guidance="Tell the user it is not yours to read.")).to_response()
    assert resp["ok"] is False
    assert resp["guidance"] == "Tell the user it is not yours to read."
    assert resp["result"] == "Permission denied: /root"


def test_it_survives_the_string_operations_helpers_actually_do():
    """Helpers join, strip and concatenate their returns. Those produce plain
    strings again — which is correct and must be deliberate, not a surprise:
    the failure marker belongs to the value the deciding code returned."""
    f = Failed("Not found: x", guidance="ask")
    assert not isinstance(f.strip(), Failed)
    assert not isinstance(f + "!", Failed)
    # ...so a helper that post-processes its own failure loses the marker, and
    # the contract degrades to today's behaviour rather than lying.
    assert normalize(f.strip()).ok is True
