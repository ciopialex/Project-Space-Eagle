"""The desktop sandbox, made into one.

Every test in the first section is an escape that WORKED against the shipped
`_build_sandbox()`, verified by running it. The sandbox restricted
`__builtins__` to 22 harmless names and then asked the model, in the prompt,
not to delete files or call subprocess. A prompt is not a control.

Demonstrated before this module existed:

    read /etc/passwd via the injected Path            -> allowed
    overwrite an arbitrary file                       -> allowed
    delete an arbitrary file via Path().unlink()      -> allowed
    reach subprocess.Popen via __subclasses__()       -> allowed
    invoke Popen(['id']) and read its output          -> allowed, real uid
    reach os through a module's __globals__, popen()  -> allowed

Two mechanisms close all six. Paths are resolved through the SAME containment
gate `file_controller` already uses, so generated code cannot address anything
outside the home directory; and the code is checked before it runs, rejecting
access to private attributes, which is the only reason `object.__subclasses__`
was reachable at all.

The residual risk is stated in `core/safe_exec.py` rather than hidden: this is
a desktop-automation tool and `pyautogui` still moves the real mouse.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.safe_exec import audit_code, run_sandboxed  # noqa: E402


def _run(code, roots=None):
    return run_sandboxed(code, roots=roots or (Path.home(),))


# ── the six escapes that worked ─────────────────────────────────────────────

def test_cannot_read_a_file_outside_the_allowed_roots(tmp_path):
    r = _run("print(Path('/etc/passwd').read_text()[:20])", roots=(tmp_path,))
    assert r.ok is False
    assert "/etc/passwd" in r.message or "outside" in r.message.lower()


def test_cannot_overwrite_a_file_outside_the_allowed_roots(tmp_path):
    victim = tmp_path.parent / "victim.txt"
    victim.write_text("original")
    r = _run(f"Path(r'{victim}').write_text('OVERWRITTEN')", roots=(tmp_path,))
    assert r.ok is False
    assert victim.read_text() == "original", "the file was modified"


def test_cannot_delete_anything_at_all(tmp_path):
    """Deletion is refused even INSIDE the allowed roots. The prompt already
    promised no deletion, and `file_controller` is the tool that deletes —
    through the trash, with an undo journal."""
    doomed = tmp_path / "doomed.txt"
    doomed.write_text("x")
    r = _run(f"Path(r'{doomed}').unlink()", roots=(tmp_path,))
    assert r.ok is False
    assert doomed.exists(), "the file was deleted"


def test_cannot_reach_subprocess_through_subclasses(tmp_path):
    code = "print([c for c in ().__class__.__bases__[0].__subclasses__()])"
    r = _run(code, roots=(tmp_path,))
    assert r.ok is False
    assert "__class__" in r.message or "private" in r.message.lower()


def test_cannot_invoke_popen(tmp_path):
    code = ("p=[c for c in ().__class__.__bases__[0].__subclasses__() "
            "if c.__name__=='Popen'][0]\n"
            "print(p(['id'], stdout=-1).communicate()[0])")
    r = _run(code, roots=(tmp_path,))
    assert r.ok is False


def test_cannot_reach_os_through_module_globals(tmp_path):
    code = ("for c in ().__class__.__bases__[0].__subclasses__():\n"
            "    g = getattr(getattr(c, '__init__', None), '__globals__', None)\n"
            "    if g and 'os' in g:\n"
            "        print(g['os'].popen('id').read()); break\n")
    r = _run(code, roots=(tmp_path,))
    assert r.ok is False


# ── the dynamic ways round a static check ───────────────────────────────────

def test_getattr_cannot_be_used_to_fetch_a_private_attribute(tmp_path):
    """A static check on attribute syntax is worth nothing if `getattr` can
    spell the same name at runtime."""
    r = _run("print(getattr((), '__class__'))", roots=(tmp_path,))
    assert r.ok is False


def test_a_private_name_built_from_pieces_is_still_refused(tmp_path):
    r = _run("n = '__cla' + 'ss__'\nprint(getattr((), n))", roots=(tmp_path,))
    assert r.ok is False, "assembled the attribute name at runtime and got through"


def test_import_is_refused(tmp_path):
    r = _run("import os\nprint(os.getcwd())", roots=(tmp_path,))
    assert r.ok is False


def test_exec_and_eval_are_refused(tmp_path):
    assert _run("exec('1+1')", roots=(tmp_path,)).ok is False
    assert _run("eval('1+1')", roots=(tmp_path,)).ok is False


def test_shutil_cannot_copy_from_outside_the_roots(tmp_path):
    """`shutil.copy2` takes raw strings, so containment on Path alone would
    leave a hole straight through it."""
    dest = tmp_path / "stolen"
    r = _run(f"shutil.copy2('/etc/passwd', r'{dest}')", roots=(tmp_path,))
    assert r.ok is False
    assert not dest.exists()


# ── it still has to be useful ───────────────────────────────────────────────

def test_can_list_a_directory_inside_the_roots(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    r = _run("print(sorted(p.name for p in Path(r'%s').iterdir()))" % tmp_path,
             roots=(tmp_path,))
    assert r.ok is True
    assert "a.txt" in r.message and "b.txt" in r.message


def test_can_read_a_file_inside_the_roots(tmp_path):
    (tmp_path / "notes.txt").write_text("hello there")
    r = _run(f"print(Path(r'{tmp_path / 'notes.txt'}').read_text())",
             roots=(tmp_path,))
    assert r.ok is True
    assert "hello there" in r.message


def test_can_write_a_file_inside_the_roots(tmp_path):
    target = tmp_path / "made.txt"
    r = _run(f"Path(r'{target}').write_text('written')\nprint('done')",
             roots=(tmp_path,))
    assert r.ok is True
    assert target.read_text() == "written"


def test_can_make_a_folder_and_join_paths(tmp_path):
    r = _run(f"d = Path(r'{tmp_path}') / 'Reports'\nd.mkdir()\nprint(d.name)",
             roots=(tmp_path,))
    assert r.ok is True
    assert (tmp_path / "Reports").is_dir()


def test_ordinary_python_still_works(tmp_path):
    r = _run("print(sum(x*2 for x in range(5)))", roots=(tmp_path,))
    assert r.ok is True and "20" in r.message


def test_a_crash_is_a_failure_not_a_success(tmp_path):
    r = _run("print(1/0)", roots=(tmp_path,))
    assert r.ok is False
    assert "division by zero" in r.message


# ── the checker on its own ──────────────────────────────────────────────────

def test_audit_names_what_it_refused():
    reason = audit_code("print(().__class__)")
    assert reason and "__class__" in reason


def test_audit_passes_ordinary_code():
    assert audit_code("d = Path('x') / 'y'\nprint(d.name)") is None


def test_syntax_errors_are_reported_as_such_not_executed():
    # Not "this is not python" — that PARSES, as an `is not` comparison
    # between two undefined names. Which is the whole reason the check runs
    # before execution rather than trusting the text to look wrong.
    reason = audit_code("def (:")
    assert reason and "syntax" in reason.lower()


def test_a_generation_failure_pasted_in_as_code_is_refused():
    """The original bug, now caught a second way: `_ask_gemini_for_desktop_
    action` used to return "ERROR: 429 RESOURCE_EXHAUSTED" and the caller
    compiled it."""
    reason = audit_code("ERROR: 429 RESOURCE_EXHAUSTED. Please retry in 27s")
    assert reason and "syntax" in reason.lower()
