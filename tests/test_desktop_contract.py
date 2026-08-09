"""desktop_control on the tool contract — and the bug found while migrating it.

## The bug

`_ask_gemini_for_desktop_action` returns `f"ERROR: {e}"` when the call fails.
`desktop_control` passes its return value straight to `_execute_generated_code`,
which `compile()`s it. So when the brain is rate-limited — routine, the free
tier is ~15 requests a minute — the eagle tries to execute

    ERROR: 429 RESOURCE_EXHAUSTED. Please retry in 27s

as Python, gets a SyntaxError, and reports

    "Execution error: invalid syntax (<aethelark_desktop>, line 1)"

...as ok=True. The user asked for something on their desktop and was told
about a syntax error in code they never saw, for a request that would have
worked a minute later. The quota relabelling in `core/quota.py` cannot catch
this: it works on exceptions reaching the dispatcher, and this one was
stringified nine frames down.

The fix is not to parse the message — it is for the generation step to be
unable to hand a failure to the execution step at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.desktop as D  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


# ── the bug ─────────────────────────────────────────────────────────────────

def test_a_failed_generation_is_never_executed_as_code(monkeypatch):
    """The regression that matters. Whatever the brain's failure says, it must
    not reach `compile()`."""
    executed = []
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda task: D.GenerationFailed("429 RESOURCE_EXHAUSTED"))
    monkeypatch.setattr(D, "_execute_generated_code",
                        lambda code, player=None: executed.append(code) or "ran")

    r = D.desktop_control({"action": "task", "task": "line up my icons"})

    assert executed == [], f"executed the brain's error message: {executed!r}"
    assert r.ok is False


def test_the_real_generator_marks_its_own_failure_and_says_what_to_do(monkeypatch):
    """Driving the actual function, not a stand-in: a client that raises must
    produce a GenerationFailed carrying a next step."""
    import google.genai as genai

    class Boom:
        def __init__(self, **kw):
            raise RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 27s")

    monkeypatch.setattr(genai, "Client", Boom)
    monkeypatch.setattr(D, "_get_api_key", lambda: "k")

    out = D._ask_gemini_for_desktop_action("line up my icons")
    assert isinstance(out, D.GenerationFailed), "a failure that would be compiled"
    assert "RESOURCE_EXHAUSTED" in out
    assert out.guidance, "no next step for a failure that clears on its own"
    assert "quota" in out.guidance.lower() or "rate limit" in out.guidance.lower()


def test_a_rate_limit_is_reported_as_a_rate_limit_not_a_syntax_error(monkeypatch):
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda task: D.GenerationFailed(
                            "429 RESOURCE_EXHAUSTED. Please retry in 27s",
                            guidance="Say it will work again shortly."))

    r = D.desktop_control({"action": "task", "task": "line up my icons"})

    assert r.ok is False
    assert "syntax" not in r.message.lower(), "still blaming the generated code"
    assert "429" in r.message or "RESOURCE_EXHAUSTED" in r.message
    assert r.guidance, "the generator's next step was dropped on the way out"


def test_a_missing_api_key_does_not_escape_the_generator(monkeypatch):
    """The state every fresh install is in. Reading the key happened outside
    the function's own try, so a missing `api_keys.json` raised straight past
    it — the one error handler written for this was unreachable for the most
    likely cause."""
    monkeypatch.setattr(D, "_get_api_key",
                        lambda: (_ for _ in ()).throw(FileNotFoundError("api_keys.json")))

    out = D._ask_gemini_for_desktop_action("tidy up")
    assert isinstance(out, D.GenerationFailed)
    assert "api_keys.json" in out


def test_generation_failure_is_distinguishable_from_generated_code():
    """A plain string is code; a GenerationFailed is not. The caller must not
    have to tell them apart by reading the prose — which is how a model-written
    line beginning "ERROR: ..." would have been misread either way."""
    assert isinstance(D.GenerationFailed("boom"), str)
    assert not isinstance("print('hi')", D.GenerationFailed)


def test_successful_generation_still_runs(monkeypatch):
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda task: "print('tidy')")
    r = D.desktop_control({"action": "task", "task": "tidy up"})
    assert r.ok is True
    assert "tidy" in r.message


# ── the contract ────────────────────────────────────────────────────────────

def test_an_unknown_action_fails_rather_than_describing_a_failure():
    r = D.desktop_control({"action": "list_windows"})
    assert isinstance(r, ToolResult) and r.ok is False
    assert "list_windows" in r.message
    assert r.guidance


def test_no_action_at_all_fails():
    r = D.desktop_control({})
    assert r.ok is False and r.guidance


def test_a_wallpaper_with_no_path_fails():
    r = D.desktop_control({"action": "wallpaper"})
    assert r.ok is False
    assert r.guidance, "the model needs to know to ask which image"


def test_a_missing_wallpaper_image_fails(tmp_path):
    r = D.desktop_control({"action": "wallpaper",
                           "path": str(tmp_path / "nope.png")})
    assert r.ok is False
    assert "nope.png" in r.message


def test_an_unsupported_image_format_fails(tmp_path):
    f = tmp_path / "wall.tiff"
    f.write_bytes(b"II*\x00")
    r = D.desktop_control({"action": "wallpaper", "path": str(f)})
    assert r.ok is False


def test_listing_the_desktop_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "_get_desktop", lambda: tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    r = D.desktop_control({"action": "list"})
    assert r.ok is True
    assert "notes.txt" in r.message


def test_an_unsafe_task_is_refused_as_a_failure(monkeypatch):
    """UNSAFE is the model declining. That is not a completed desktop action."""
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action", lambda task: "UNSAFE")
    r = D.desktop_control({"action": "task", "task": "delete everything"})
    assert r.ok is False
    assert r.guidance


def test_code_that_raises_is_a_failure(monkeypatch):
    # Division, not `raise ValueError(...)`: the sandbox's builtins are a
    # deliberate allow-list and do not include the exception types, so that
    # would test the sandbox rather than the failure path.
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda task: "print(1 / 0)")
    r = D.desktop_control({"action": "task", "task": "do a thing"})
    assert r.ok is False
    assert "division by zero" in r.message
    assert r.guidance
