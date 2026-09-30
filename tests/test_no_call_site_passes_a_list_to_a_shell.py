"""`subprocess.Popen([cmd, arg], shell=True)` runs `cmd` and throws `arg` away.

Found in `actions/dev_agent.py:_open_vscode`, which opened VS Code without the
project it had just created. The list form and the shell form mean different
things and Python does not object to being handed both:

    subprocess.run(["echo", "THE-PROJECT-PATH"], shell=True)   ->  prints ""
    subprocess.run(["echo", "THE-PROJECT-PATH"])               ->  prints it

With shell=True the first element IS the command line and everything after it
becomes the shell's $0, $1, ... So on POSIX the arguments silently vanish, and
on Windows they are joined back into one string, where an argument containing
`&` or `|` becomes a second command. One mistake, two different failures, and
neither raises.

This is a property over every call site rather than a test of one fix, which
is the same shape as `test_tool_routing_unambiguous.py`: it reads source, it
names no expected winner, and it can fail on code nobody has written yet. The
instance it was written for is already fixed; what it is for is the next one.

It is not a mirror. A mirror asserts that text you wrote says what you wrote.
This asserts a relationship between two arguments at every call site in the
repository, and it failed on real code the day it was written.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Directories that are ours to fix.
ROOTS = ["actions", "core", "dashboard", "tools", "packaging"]

RUNNERS = {"run", "Popen", "call", "check_call", "check_output"}


def _shell_is_true(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "shell":
            return isinstance(kw.value, ast.Constant) and kw.value.value is True
    return False


def _first_arg_is_a_sequence(call: ast.Call) -> bool:
    """Only a literal list/tuple counts.

    A Name could be either, and guessing would make this a source of false
    alarms — which is how a check like this gets deleted.
    """
    return bool(call.args) and isinstance(call.args[0], (ast.List, ast.Tuple))


def _subprocess_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in RUNNERS:
            yield node
        elif isinstance(fn, ast.Name) and fn.id in RUNNERS:
            yield node


def _python_files():
    for root in ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            yield path
    for name in ("main.py", "aethelark_web.py"):
        if (REPO / name).is_file():
            yield REPO / name


def test_no_call_site_hands_a_list_to_a_shell():
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for call in _subprocess_calls(tree):
            if _shell_is_true(call) and _first_arg_is_a_sequence(call):
                offenders.append(f"{path.relative_to(REPO)}:{call.lineno}")

    assert not offenders, (
        "a list was passed with shell=True. On POSIX every argument after the "
        "first is silently discarded; on Windows they are rejoined and an "
        "argument containing & or | becomes a second command:\n  "
        + "\n  ".join(offenders))


def test_the_check_can_actually_see_the_shape_it_looks_for():
    """The premise. A scanner that matches nothing passes for free.

    Two synthetic samples: one is the defect, one is the fix. If the first is
    not flagged the check is broken, and if the second IS flagged it will be
    deleted for crying wolf.
    """
    bad = ast.parse('subprocess.Popen(["code", str(p)], shell=True)')
    good = ast.parse('subprocess.Popen(["code", str(p)])')
    also_good = ast.parse('subprocess.Popen(cmdline, shell=True)')

    assert [c for c in _subprocess_calls(bad)
            if _shell_is_true(c) and _first_arg_is_a_sequence(c)], (
        "the scanner does not recognise the defect it exists to find")
    assert not [c for c in _subprocess_calls(good)
                if _shell_is_true(c) and _first_arg_is_a_sequence(c)]
    assert not [c for c in _subprocess_calls(also_good)
                if _shell_is_true(c) and _first_arg_is_a_sequence(c)], (
        "a string with shell=True is the correct form and must not be flagged")


def test_it_is_reading_a_real_number_of_files():
    """If ROOTS ever stops matching the layout, everything above passes empty."""
    files = list(_python_files())
    assert len(files) > 50, f"only found {len(files)} python files to scan"
    calls = sum(len(list(_subprocess_calls(ast.parse(p.read_text(encoding='utf-8')))))
                for p in files if p.suffix == ".py")
    assert calls > 20, f"only found {calls} subprocess call sites; the walk is wrong"
