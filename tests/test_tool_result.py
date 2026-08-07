"""The tool-result contract, and the lie that lived inside it."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_result import ToolResult, normalize  # noqa: E402



# ── The legacy lie ─────────────────────────────────────────────────────────
# `normalize` wrapped every legacy string as ok=True. Measured across the
# codebase: 183 return statements describe a FAILURE ("could not", "not
# found", "requires wmctrl") and every one of them reached the model as
# ok=True. Meanwhile core/prompt.txt instructs it: "TRUST ok over any prose —
# if ok is false, the action did NOT happen."
#
# So the model was told to trust a flag that was unconditionally true for 17
# of 20 tools. That is the exact bug class this contract was written to kill,
# living inside the contract itself.
#
# The fix is not to guess failure from prose — that is the guessing the
# contract replaced. It is to stop CLAIMING a status we do not have.

def test_a_legacy_string_does_not_claim_success():
    r = normalize("focus_window (Linux) requires wmctrl or xdotool")
    assert r.to_response().get("ok") is None, (
        "an unmigrated tool must not assert ok=True — it has not said")


def test_a_legacy_string_still_reaches_the_model_verbatim():
    """Absent `ok` means the model reads the prose, which is exactly the
    behaviour these tools had before the contract existed. No regression."""
    r = normalize("Opened in chrome: https://example.com")
    assert r.to_response()["result"] == "Opened in chrome: https://example.com"


def test_a_migrated_tool_still_asserts_its_status():
    ok = ToolResult.success("Sent.").to_response()
    bad = ToolResult.failure("No such contact.", guidance="Ask which one.").to_response()
    assert ok["ok"] is True
    assert bad["ok"] is False and bad["guidance"] == "Ask which one."


def test_the_legacy_flag_is_still_visible_for_telemetry():
    """Knowing which tools have not migrated is how the rollout gets finished
    rather than forgotten for another few months."""
    assert normalize("anything").data.get("_legacy_string") is True
    assert ToolResult.success("x").data.get("_legacy_string") is None
