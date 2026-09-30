"""Generated code cannot reach a private attribute by spelling it in a string.

`core/safe_exec.py` runs model-written Python. Its docstring lists six escapes
that used to work and states the rule that replaced them: generated code may
not touch any name beginning with an underscore, checked on the syntax rather
than blocked by a list of known tricks.

Two ways past that check survived, and both put the private name somewhere the
AST walk was not looking.

SPLIT STRINGS. The constant check was `startswith("__") and endswith("__")`,
so a name in two halves passed:

    getattr((), '__cla' + 'ss__')

`_safe_getattr` refuses that at run time, so it was never exploitable — but a
static check that only holds because a second check exists is not a static
check, and the next route may not go through getattr.

FORMAT STRINGS, which is the one that got through BOTH layers:

    '{0.__class__}'.format(())   ->   <class 'tuple'>

`str.format` resolves attributes itself. There is no `getattr` call to
intercept and no `ast.Attribute` node to see; the private name lives entirely
inside a string literal. It yields strings rather than objects, so it reads
rather than executes — but "may not touch the private attribute" was the
stated guarantee, and it could be touched.

These are audit-level assertions AND end-to-end ones. The audit is what the
module claims to rely on, and `run_sandboxed` is what actually protects the
machine; a fix to either alone would leave this file half-passing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.safe_exec import audit_code, run_sandboxed  # noqa: E402


#: Every route to a private name that does not appear as an attribute node.
ESCAPES = {
    "whole dunder in a string": "x = getattr((), '__class__')",
    "dunder split in two": "x = getattr((), '__cla' + 'ss__')",
    "dunder joined from a list": "x = getattr((), ''.join(['__cl', 'ass__']))",
    "format string reads an attribute": "print('{0.__class__}'.format(()))",
    "format string built at run time":
        "n = chr(95) * 2 + 'class' + chr(95) * 2\n"
        "print(('{0.' + n + '}').format(()))",
    "format_map instead of format":
        "print('{0.__class__}'.format_map({0: ()}))",
    "the full escape chain":
        "c = getattr((), '__cla' + 'ss__')\n"
        "b = getattr(c, '__bas' + 'es__')\n"
        "subs = getattr(b[0], '__subclas' + 'ses__')()\n"
        "print(len(subs))",
}


@pytest.mark.parametrize("label", sorted(ESCAPES))
def test_the_audit_refuses_it_before_anything_runs(label):
    """`audit_code` is what the module says it relies on."""
    reason = audit_code(ESCAPES[label])
    assert reason is not None, (
        f"{label}: audit_code allowed it. The private name is in a string "
        f"literal, which is exactly where the attribute walk cannot see it.")


@pytest.mark.parametrize("label", sorted(ESCAPES))
def test_nothing_reaches_a_private_attribute_when_actually_run(label):
    """And the machine is protected even if the audit is ever loosened."""
    result = run_sandboxed(ESCAPES[label])
    assert not result.ok, (
        f"{label}: this RAN. Message: {result.message[:160]!r}")


def test_the_specific_read_that_got_through_both_layers():
    """Pinned on its own, because it is the one that actually escaped.

    The others were stopped at run time by `_safe_getattr`. This one was not:
    it returned the class of a tuple as a string, through a check that was
    looking for attribute nodes and a getattr that was never called.
    """
    code = "print('{0.__class__}'.format(()))"
    result = run_sandboxed(code)
    assert not result.ok
    assert "tuple" not in (result.message or ""), (
        "the class leaked into the output anyway")


# ── the guard has to stay narrow enough to be usable ────────────────────────

def test_ordinary_generated_code_still_runs():
    """A guard that refuses real work gets removed, so this is load-bearing.

    These are the shapes `desktop_control(action="task")` actually produces:
    arithmetic, printing, an f-string with no attribute access, and a file
    written inside the allowed roots.
    """
    ok_cases = {
        "arithmetic": "print(sum(range(5)), max([1, 9, 3]))",
        "f-string without attributes": "n = 42\nprint(f'value is {n}')",
        "a file inside home":
            "p = Path('~/.aethelark_sandbox_probe.txt')\n"
            "p.write_text('hello')\n"
            "print(p.read_text())",
    }
    for label, code in ok_cases.items():
        assert audit_code(code) is None, f"{label}: audit refused ordinary code"
        result = run_sandboxed(code)
        assert result.ok, f"{label}: {result.message[:120]!r}"

    probe = Path.home() / ".aethelark_sandbox_probe.txt"
    if probe.exists():
        probe.unlink()


def test_a_string_with_a_single_underscore_is_still_allowed():
    """The rule is dunders, not underscores.

    Generated code legitimately handles filenames like `my_notes.txt`, and a
    check that refused those would be swapped out within a week.
    """
    assert audit_code("print('my_notes.txt')") is None
    assert audit_code("p = Path('~/some_folder')") is None
