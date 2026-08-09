"""code_helper on the tool contract.

Eleven of its returns describe a failure and reached the model with no status:
a file that does not exist, a language with no interpreter, a Gemini call that
fell over. This one matters more than most because the tool WRITES FILES — a
"Could not generate code: 429" read as success is the model then telling the
user where their new script was saved, by name, when nothing was written.

Same mechanism as `file_controller`: the deciding code marks its prose with
`Failed`, the entrypoint converts. `_read_file` is the exception worth noting —
it already returned `(content, err)`, so it knew perfectly well; the error just
had nowhere to go that the caller could see.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.code_helper as C  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


def test_an_unknown_action_is_a_failure():
    r = C.code_helper({"action": "transpile"})
    assert isinstance(r, ToolResult) and r.ok is False
    assert "transpile" in r.message
    assert r.guidance


def test_writing_with_no_description_fails():
    r = C.code_helper({"action": "write"})
    assert r.ok is False
    assert r.guidance, "the model needs to know to ask what to write"


def test_editing_a_file_that_does_not_exist_fails(tmp_path):
    r = C.code_helper({"action": "edit", "file_path": str(tmp_path / "ghost.py"),
                       "description": "add a docstring"})
    assert r.ok is False
    assert "ghost.py" in r.message


def test_editing_with_no_instruction_fails(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    r = C.code_helper({"action": "edit", "file_path": str(f)})
    assert r.ok is False


def test_explaining_a_missing_file_fails(tmp_path):
    r = C.code_helper({"action": "explain", "file_path": str(tmp_path / "no.py")})
    assert r.ok is False


def test_running_a_file_with_no_known_interpreter_fails(tmp_path):
    f = tmp_path / "thing.xyzzy"
    f.write_text("nonsense")
    r = C.code_helper({"action": "run", "file_path": str(f)})
    assert r.ok is False
    assert "xyzzy" in r.message


def test_running_a_missing_file_fails(tmp_path):
    r = C.code_helper({"action": "run", "file_path": str(tmp_path / "gone.py")})
    assert r.ok is False


# ── the one that writes to disk ─────────────────────────────────────────────

def test_a_failed_generation_is_not_reported_as_a_saved_file(monkeypatch):
    """The expensive version of this bug. The model hears success and tells
    the user their script is ready — naming a path that was never written."""
    def boom(*a, **k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(C, "_write", boom)

    r = C.code_helper({"action": "write", "description": "a fizzbuzz script"})

    assert r.ok is False, "a file that was never written was reported as saved"
    assert "429" in r.message or "RESOURCE_EXHAUSTED" in r.message
    assert r.guidance


def test_a_successful_write_is_an_explicit_success(monkeypatch, tmp_path):
    target = tmp_path / "fizz.py"
    monkeypatch.setattr(C, "_write",
                        lambda *a, **k: ("print('fizz')", target))
    r = C.code_helper({"action": "write", "description": "fizzbuzz"})
    assert r.ok is True
    assert str(target) in r.message


def test_a_save_that_fails_is_not_reported_as_a_written_file(monkeypatch, tmp_path):
    """`_write` called `_save_file` and threw the answer away, so a disk that
    was full, read-only or a bad path still produced "Code written. Saved to:
    <path>" — the model then told the user exactly where to find a file that
    was never created."""
    blocked = tmp_path / "afile"
    blocked.write_text("i am a file, not a directory")

    class _Resp:
        text = "print('hello')"

    monkeypatch.setattr(C, "_get_gemini",
                        lambda *a, **k: type("M", (), {
                            "generate_content": lambda self, p: _Resp()})())
    monkeypatch.setattr(C, "_resolve_save_path",
                        lambda *a, **k: blocked / "child.py")

    r = C.code_helper({"action": "write", "description": "a hello script"})

    assert r.ok is False, "a file that was never written was reported as saved"
    assert "Saved to" not in r.message


def test_a_save_that_fails_is_a_failure(tmp_path):
    """`_save_file` swallowed the OSError into prose. A read-only target then
    read as a completed save."""
    from core.tool_result import Failed
    out = C._save_file(tmp_path / "nested" / "x.py", "code")
    assert not isinstance(out, Failed), "a normal save was marked as failed"

    blocked = tmp_path / "afile"
    blocked.write_text("i am a file, not a directory")
    out = C._save_file(blocked / "child.py", "code")
    assert isinstance(out, Failed)


# ── the regression net ──────────────────────────────────────────────────────

def test_no_refusal_reaches_the_model_as_ok(tmp_path):
    refusals = [
        {"action": "write"},
        {"action": "edit"},
        {"action": "edit", "file_path": str(tmp_path / "none.py"),
         "description": "x"},
        {"action": "explain"},
        {"action": "run"},
        {"action": "run", "file_path": str(tmp_path / "none.py")},
        {"action": "optimize"},
        {"action": "not_a_real_action"},
    ]
    for params in refusals:
        r = C.code_helper(params)
        assert r.ok is False, f"{params} came back ok=True: {r.message!r}"
