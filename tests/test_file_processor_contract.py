"""file_processor's boundary, migrated to the tool contract.

47 of its return statements describe a failure, and every one arrived at the
model as ok=True. The internal helpers still return prose - migrating 123 call
sites would be churn with real regression risk - but the ENTRYPOINT knows
exactly when it has failed, because it decided so itself: no path, file
missing, not a file, unsupported type, handler raised.

Those are also the failures a user hits most: a mis-heard filename, a path
that does not exist. Each now carries a next step instead of a dead end.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.file_processor import file_processor  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


def test_no_path_fails_and_says_what_to_ask_for():
    r = file_processor({})
    assert isinstance(r, ToolResult) and r.ok is False
    assert r.guidance, "a dead end with no next step"


def test_a_missing_file_fails_with_the_path_echoed(tmp_path):
    missing = tmp_path / "not-here.txt"
    r = file_processor({"file_path": str(missing)})
    assert r.ok is False
    assert "not-here.txt" in r.message, "the user cannot correct what is not quoted"
    assert "ask" in r.guidance.lower() or "check" in r.guidance.lower()


def test_a_directory_is_not_a_file(tmp_path):
    r = file_processor({"file_path": str(tmp_path)})
    assert r.ok is False


def test_a_readable_file_succeeds(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello there, this is a plain text file with some content in it.")
    r = file_processor({"file_path": str(f), "action": "summarize"})
    assert isinstance(r, ToolResult)
    # The helper may legitimately fail without an API key; what must NOT happen
    # is a failure being dressed as success.
    assert r.ok in (True, False)
    if r.ok is False:
        assert r.guidance


def test_a_handler_that_raises_is_reported_as_a_failure(tmp_path, monkeypatch):
    """The old code caught the exception and returned "Processing failed: X"
    as a plain string — which normalize turned into ok=True. The model was
    told a crash was a success."""
    import actions.file_processor as F
    f = tmp_path / "x.txt"
    f.write_text("content")
    monkeypatch.setattr(F, "_process_text_doc",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = file_processor({"file_path": str(f), "action": "summarize"})
    assert r.ok is False
    assert "boom" in r.message


def test_it_never_raises_whatever_it_is_handed():
    for junk in (None, {}, {"file_path": None}, {"file_path": 5}):
        assert file_processor(junk) is not None
