"""A module that explains itself well should not have that explanation flattened.

Measured 2026-09-05. `a3d status --printer NOTAPRINTER` returns, on stdout:

    {"error": "There is no printer called 'NOTAPRINTER'.",
     "guidance": "The printers set up here are: CC1, CC2, C2, ...",
     "printers": [...]}

which is exactly what a small model needs: the problem, and the valid options.
The bus turned it into:

    message : a3d status failed: {\\n  "error": "There is no printer called ...
    guidance: Read the message -- it comes from the module itself.

So the model received raw JSON embedded in prose, and the one field written to
help it choose again was replaced by an instruction to go read the thing it was
already reading. The harness runs Gemini 2.5 Flash; this is the gap CLAUDE.md
describes as a bug -- one that needs the reasoning of the model writing the code
to cross.

A module that says nothing useful still gets the old behaviour: its raw output
is better than nothing.
"""
from __future__ import annotations

import json
import sys
import textwrap

import pytest

from core.module_bus.bus import ModuleBus


def _module_that_fails_with(tmp_path, payload: dict, exit_code: int = 1):
    """A real module process that prints `payload` and exits non-zero."""
    script = tmp_path / "run.py"
    script.write_text(
        "import json,sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n")
    (tmp_path / "m.toml").write_text(textwrap.dedent(f"""
        key = "m"
        binary = "{sys.executable}"
        description = "a module that explains itself"
        output = "json"

        [[tools]]
        name = "look"
        description = "look at something"
        argv = ["{script}"]
    """))
    return ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b).discover()


def test_the_modules_own_error_becomes_the_message(tmp_path):
    bus = _module_that_fails_with(tmp_path, {
        "error": "There is no printer called 'NOTAPRINTER'.",
        "guidance": "The printers set up here are: CC1, CC2.",
    })
    answer = bus.invoke("m_look", {})

    assert answer.ok is False
    assert "There is no printer called 'NOTAPRINTER'." in answer.message
    assert "{" not in answer.message, (
        f"raw JSON reached the model instead of the sentence inside it: "
        f"{answer.message!r}")


def test_the_modules_own_guidance_is_not_replaced_with_read_the_message(tmp_path):
    bus = _module_that_fails_with(tmp_path, {
        "error": "There is no printer called 'NOTAPRINTER'.",
        "guidance": "The printers set up here are: CC1, CC2.",
    })
    answer = bus.invoke("m_look", {})

    assert "CC1, CC2" in (answer.guidance or ""), (
        f"the module named the valid options and the bus dropped them: "
        f"{answer.guidance!r}")
    assert "Read the message" not in (answer.guidance or "")


def test_a_module_with_nothing_useful_to_say_still_reports_its_output(tmp_path):
    """The fallback must not become worse than what it replaced."""
    script = tmp_path / "run.py"
    script.write_text("import sys\nprint('something broke')\nsys.exit(2)\n")
    (tmp_path / "m.toml").write_text(textwrap.dedent(f"""
        key = "m"
        binary = "{sys.executable}"
        description = "a module that says little"
        output = "json"

        [[tools]]
        name = "look"
        description = "look"
        argv = ["{script}"]
    """))
    answer = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b).discover().invoke("m_look", {})

    assert answer.ok is False
    assert "something broke" in answer.message


def test_a_failure_still_carries_its_exit_code(tmp_path):
    bus = _module_that_fails_with(tmp_path, {"error": "no"}, exit_code=3)
    assert bus.invoke("m_look", {}).data["exit_code"] == 3
