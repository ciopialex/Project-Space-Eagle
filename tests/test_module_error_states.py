"""Specification sections 2 and 12 — asking for something a module does, and
the error states that apply everywhere.

Section 2 is the contract between the eagle and a module: what comes back when
the module is fine, missing, slow, broken, lying, or asked for something that
does not exist. Section 12 is the same contract stated as invariants that hold
across every action.

None of this needs a network, a browser or a real module. Every module here is
a manifest written into tmp_path whose binary is the interpreter running a
script written a line earlier (setup note s4), which gives a real process, real
pipes and a real exit code.

Where the specification quotes fixed wording, the wording is asserted, because
these are the sentences the eagle is working from rather than anything it says
out loud.
"""

import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402

TRUNCATION_LIMIT = 16000


def _module(tmp_path, script_body, *, output="text", key="m", params=True):
    """A module whose binary is this interpreter running the given script."""
    script = tmp_path / "mod.py"
    script.write_text(textwrap.dedent(script_body))
    param_block = textwrap.dedent("""
        [tools.params.value]
        type = "STRING"
        description = "anything"
        required = true
    """) if params else ""
    argv = f'["{script}", "{{value}}"]' if params else f'["{script}"]'
    (tmp_path / f"{key}.toml").write_text(textwrap.dedent(f"""
        key = "{key}"
        binary = "{sys.executable}"
        description = "a test module"
        output = "{output}"

        [[tools]]
        name = "go"
        description = "does the thing"
        argv = {argv}
    """) + param_block)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    return bus


def _module_that_leaves_a_mark(tmp_path, marker, *, key="m"):
    """A module whose only job is to prove it ran, by creating a file.

    Asserting a word is absent from the returned message cannot show that a
    program did not run: a refusal returns its own sentence whether or not
    anything was spawned beside it. A file on disk is outside the message
    entirely, so its absence is the only honest evidence that nothing ran.
    """
    return _module(
        tmp_path,
        f"""
        from pathlib import Path
        Path({str(marker)!r}).write_text("ran")
        print("RAN")
        """,
        key=key,
    )


ABSENT = """
    key = "m"
    binary = "some-cli-that-is-not-here"
    description = "a test module"
    output = "text"

    [[tools]]
    name = "go"
    description = "does the thing"
    argv = ["go"]
"""


def _absent(tmp_path):
    (tmp_path / "m.toml").write_text(textwrap.dedent(ABSENT))
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: None)
    bus.discover()
    return bus


# --------------------------------------------------------------------------
# 2.1 the module is installed
# --------------------------------------------------------------------------

def test_an_installed_module_carries_out_the_request_and_answers(tmp_path):
    """s2.1. The eagle carries out the request and answers with the result."""
    bus = _module(tmp_path, "print('the answer')")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is True
    assert "the answer" in result.message


def test_an_ordinary_action_asks_for_no_confirmation(tmp_path):
    """s2.1. No confirmation is asked for unless section 3 applies."""
    bus = _module(tmp_path, "print('the answer')")
    result = bus.invoke("m_go", {"value": "x"})
    assert not result.data.get("needs_confirmation"), (
        "an action that touches nothing physical asked for confirmation"
    )


# --------------------------------------------------------------------------
# 2.2 the module is not installed
# --------------------------------------------------------------------------

def test_an_uninstalled_module_is_named_in_the_refusal(tmp_path):
    """s2.2. Nothing runs, and the eagle says so explicitly, naming the module.
    The fixed wording is: The <name> module is not installed on this machine,
    so <action> did nothing."""
    bus = _absent(tmp_path)
    result = bus.invoke("m_go", {})
    assert result.ok is False
    assert re.search(
        r"The m module is not installed on this machine, so .+ did nothing\.",
        result.message,
    ), f"the refusal does not carry the fixed wording: {result.message!r}"


def test_an_uninstalled_module_says_how_to_install_it(tmp_path):
    """s2.2. With the follow-up: Install its CLI ('<program>') and make sure it
    is on PATH, then ask again."""
    bus = _absent(tmp_path)
    result = bus.invoke("m_go", {})
    assert re.search(
        r"Install its CLI \('some-cli-that-is-not-here'\) and make sure it is "
        r"on PATH, then ask again\.",
        result.guidance or "",
    ), f"the next step does not carry the fixed wording: {result.guidance!r}"


def test_an_uninstalled_module_never_reports_success(tmp_path):
    """s2.2. The eagle must not claim the action succeeded."""
    bus = _absent(tmp_path)
    result = bus.invoke("m_go", {})
    assert result.ok is False
    assert result.to_response()["ok"] is False, (
        "the dict handed to the model says the action succeeded"
    )


def test_an_uninstalled_module_is_not_substituted_with_another(tmp_path):
    """s2.2. The eagle must not substitute a different tool.

    A second, working module stands beside the absent one; the refusal must
    still be a refusal rather than that module's answer.
    """
    (tmp_path / "m.toml").write_text(textwrap.dedent(ABSENT))
    substituted = tmp_path / "substituted"
    other = tmp_path / "other.py"
    other.write_text(
        f"from pathlib import Path\n"
        f"Path({str(substituted)!r}).write_text('ran')\n"
        f"print('SUBSTITUTED')\n"
    )
    (tmp_path / "other.toml").write_text(textwrap.dedent(f"""
        key = "other"
        binary = "{sys.executable}"
        description = "a different module"
        output = "text"

        [[tools]]
        name = "go"
        description = "does a different thing"
        argv = ["{other}"]
    """))
    bus = ModuleBus(
        manifest_dirs=[tmp_path],
        which=lambda b: None if b == "some-cli-that-is-not-here" else b,
    )
    bus.discover()
    result = bus.invoke("m_go", {})
    assert result.ok is False
    assert "SUBSTITUTED" not in result.message, (
        "a different module's answer was returned in place of the absent one"
    )
    assert not substituted.exists(), (
        "a different module was run in place of the absent one"
    )


# --------------------------------------------------------------------------
# 2.3 / 2.4 a required detail is missing
# --------------------------------------------------------------------------

def test_a_missing_required_detail_is_refused_with_the_fixed_wording(tmp_path):
    """s2.3. The request is refused before anything runs, with the wording
    <action> needs <the missing thing>."""
    bus = _module(tmp_path, "print('RAN')")
    result = bus.invoke("m_go", {})
    assert result.ok is False
    assert result.message == "m_go needs value", (
        f"the refusal does not carry the fixed wording: {result.message!r}"
    )


def test_a_missing_required_detail_asks_for_it_back(tmp_path):
    """s2.3. The eagle is told to try again with that detail filled in."""
    bus = _module(tmp_path, "print('RAN')")
    result = bus.invoke("m_go", {})
    assert "again" in (result.guidance or "").lower(), (
        f"the refusal does not ask for the detail back: {result.guidance!r}"
    )


def test_a_missing_required_detail_runs_nothing(tmp_path):
    """s2.3. The request is refused *before anything runs*.

    The module writes a file when it runs, and the file's absence is what
    proves it did not. A refusal that returns the right sentence and starts the
    program anyway passes every other check in this file.
    """
    marker = tmp_path / "it-ran"
    bus = _module_that_leaves_a_mark(tmp_path, marker)
    bus.invoke("m_go", {})
    assert not marker.exists(), (
        "the module ran despite the request being refused before anything "
        "should have started"
    )


def test_the_mark_a_module_leaves_is_visible_when_it_does_run(tmp_path):
    """Not a specification item.

    The control for every "runs nothing" test in this file: if the marker never
    appeared even on a successful call, their absence assertions would prove
    nothing at all.
    """
    marker = tmp_path / "it-ran"
    bus = _module_that_leaves_a_mark(tmp_path, marker)
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is True
    assert marker.exists(), (
        "a module that did run left no mark, so the absence of the mark "
        "elsewhere in this file proves nothing"
    )


def test_an_empty_value_counts_as_missing(tmp_path):
    """s2.4. Supplying a required detail as an empty value produces exactly the
    2.3 refusal, not a run with an empty value."""
    marker = tmp_path / "it-ran"
    bus = _module_that_leaves_a_mark(tmp_path, marker)
    result = bus.invoke("m_go", {"value": ""})
    assert result.ok is False
    assert result.message == "m_go needs value", (
        f"an empty value was not treated as missing: {result.message!r}"
    )
    assert not marker.exists(), "the module ran on an empty value"


# --------------------------------------------------------------------------
# 2.5 the request names something that does not exist
# --------------------------------------------------------------------------

def test_an_unknown_action_is_named_in_the_refusal(tmp_path):
    """s2.5. Asking for an action no module has produces
    No module tool called '<name>'."""
    bus = _module(tmp_path, "print('RAN')")
    result = bus.invoke("m_nope", {})
    assert result.ok is False
    assert result.message == "No module tool called 'm_nope'.", (
        f"the refusal does not carry the fixed wording: {result.message!r}"
    )


def test_an_unknown_action_lists_every_action_on_offer(tmp_path):
    """s2.5. Plus a list of every action any module offers.

    This is also s12.3: where an input is rejected because it is not one of a
    known set, the message names the set.
    """
    bus = _module(tmp_path, "print('RAN')")
    result = bus.invoke("m_nope", {})
    assert "m_go" in (result.guidance or ""), (
        f"the refusal did not name the actions on offer: {result.guidance!r}"
    )


def test_an_unknown_action_runs_nothing(tmp_path):
    """s2.5. Nothing runs."""
    marker = tmp_path / "it-ran"
    bus = _module_that_leaves_a_mark(tmp_path, marker)
    bus.invoke("m_nope", {})
    assert not marker.exists(), (
        "asking for an action that does not exist still started a module"
    )


# --------------------------------------------------------------------------
# 2.6 the module takes too long
# --------------------------------------------------------------------------

def test_a_module_that_takes_too_long_is_stopped(tmp_path):
    """s2.6. Every action has a time limit. When it is exceeded the module is
    stopped."""
    bus = _module(tmp_path, "import time\ntime.sleep(30)")
    result = bus.invoke("m_go", {"value": "x"}, timeout_s=1.0)
    assert result.ok is False
    assert result.data["timed_out"] is True


def test_a_timeout_is_reported_with_the_fixed_wording(tmp_path):
    """s2.6. The eagle reports: The <name> module timed out after <N>s and was
    stopped."""
    bus = _module(tmp_path, "import time\ntime.sleep(30)")
    result = bus.invoke("m_go", {"value": "x"}, timeout_s=1.0)
    assert re.search(
        r"The m module timed out after [\d.]+s and was stopped\.",
        result.message,
    ), f"the timeout does not carry the fixed wording: {result.message!r}"


def test_a_timeout_says_what_to_do_next(tmp_path):
    """s2.6. With the follow-up: Try a narrower request, or run it directly to
    see where it is stuck."""
    bus = _module(tmp_path, "import time\ntime.sleep(30)")
    result = bus.invoke("m_go", {"value": "x"}, timeout_s=1.0)
    assert (
        "Try a narrower request, or run it directly to see where it is stuck."
        in (result.guidance or "")
    ), f"the timeout does not carry the fixed next step: {result.guidance!r}"


# --------------------------------------------------------------------------
# 2.7 the module fails
# --------------------------------------------------------------------------

def test_a_failing_module_is_reported_as_a_failure(tmp_path):
    """s2.7. When a module reports failure, the eagle reports failure. The
    eagle must never report a failed action as done."""
    bus = _module(tmp_path, textwrap.dedent("""
        import sys
        sys.stderr.write("no printer configured\\n")
        sys.exit(2)
    """))
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is False
    assert result.data["exit_code"] == 2


def test_a_failure_carries_the_modules_own_words(tmp_path):
    """s2.7. The fixed wording is <module> <action> failed: <the module's own
    message>."""
    bus = _module(tmp_path, textwrap.dedent("""
        import sys
        sys.stderr.write("no printer configured\\n")
        sys.exit(2)
    """))
    result = bus.invoke("m_go", {"value": "x"})
    assert re.search(r"m go failed: .*no printer configured", result.message), (
        f"the failure does not carry the fixed wording: {result.message!r}"
    )


def test_a_failure_says_the_message_came_from_the_module(tmp_path):
    """s2.7. With the follow-up: Read the message — it comes from the module
    itself."""
    bus = _module(tmp_path, textwrap.dedent("""
        import sys
        sys.stderr.write("no printer configured\\n")
        sys.exit(2)
    """))
    result = bus.invoke("m_go", {"value": "x"})
    assert "comes from the module itself" in (result.guidance or ""), (
        f"the failure does not carry the fixed next step: {result.guidance!r}"
    )


# --------------------------------------------------------------------------
# 2.8 the module returns something malformed
# --------------------------------------------------------------------------

def test_malformed_json_is_reported_with_the_fixed_wording(tmp_path):
    """s2.8. If a module that promises structured results emits something that
    is not, the eagle reports <module> <action> declares JSON output but did
    not emit valid JSON (<reason>)."""
    bus = _module(tmp_path, "print('not json at all')", output="json")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is False
    assert re.search(
        r"m go declares JSON output but did not emit valid JSON \(.+\)",
        result.message,
    ), f"the refusal does not carry the fixed wording: {result.message!r}"


def test_malformed_json_invents_nothing_to_fill_the_gap(tmp_path):
    """s2.8. Nothing is invented to fill the gap and no partial result is
    presented as complete."""
    bus = _module(tmp_path, "print('{\"printers\": [{\"id\": ')", output="json")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is False
    assert (result.data or {}).get("result") is None, (
        f"a partial result was presented as complete: "
        f"{(result.data or {}).get('result')!r}"
    )


# --------------------------------------------------------------------------
# 2.9 very large results are truncated, visibly
# --------------------------------------------------------------------------

def test_a_very_large_result_is_cut_at_sixteen_thousand_characters(tmp_path):
    """s2.9. A result longer than 16,000 characters is cut at 16,000."""
    bus = _module(tmp_path, "print('X' * 40000)")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is True
    assert len(result.message) < 40000, "a 40,000 character result was not cut"
    assert result.message.count("X") == TRUNCATION_LIMIT, (
        f"the result was cut to {result.message.count('X')} characters rather "
        f"than {TRUNCATION_LIMIT}"
    )


def test_a_truncated_result_says_how_much_was_cut(tmp_path):
    """s2.9. The remainder is replaced with a line reading
    … [<N> more characters truncated]."""
    bus = _module(tmp_path, "print('X' * 40000)")
    result = bus.invoke("m_go", {"value": "x"})
    assert re.search(r"… \[\d+ more characters truncated\]$", result.message), (
        f"the cut is not announced: {result.message[-80:]!r}"
    )


def test_a_result_under_the_limit_is_not_cut(tmp_path):
    """s2.9. The control: only a result *longer than* 16,000 characters is cut,
    so a shorter one must arrive whole and unannounced."""
    bus = _module(tmp_path, "print('Y' * 100)")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.message.count("Y") == 100
    assert "truncated" not in result.message


# --------------------------------------------------------------------------
# 2.12 a module crash never takes down the eagle
# --------------------------------------------------------------------------

def test_a_broken_module_degrades_to_a_failed_action(tmp_path):
    """s2.12. A module that is missing, broken, hung or lying degrades to a
    failed action."""
    bus = _module(tmp_path, "import sys; sys.exit(3)")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is False, "a crashing module did not degrade to a failure"


def test_the_next_request_is_served_after_a_module_crashes(tmp_path):
    """s2.12. The session continues, and the next request is served normally."""
    crash = tmp_path / "crash.py"
    crash.write_text("import sys\nsys.exit(3)\n")
    good = tmp_path / "good.py"
    good.write_text("print('still here')\n")
    for key, script in (("bad", crash), ("good", good)):
        (tmp_path / f"{key}.toml").write_text(textwrap.dedent(f"""
            key = "{key}"
            binary = "{sys.executable}"
            description = "a test module"
            output = "text"

            [[tools]]
            name = "go"
            description = "does the thing"
            argv = ["{script}"]
        """))
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    assert bus.invoke("bad_go", {}).ok is False
    after = bus.invoke("good_go", {})
    assert after.ok is True and "still here" in after.message, (
        "a crashing module stopped the next request being served"
    )


def test_a_hung_module_does_not_stop_the_next_request(tmp_path):
    """s2.12. Hung is one of the four the item names."""
    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(30)\n")
    good = tmp_path / "good.py"
    good.write_text("print('still here')\n")
    for key, script in (("slow", hang), ("good", good)):
        (tmp_path / f"{key}.toml").write_text(textwrap.dedent(f"""
            key = "{key}"
            binary = "{sys.executable}"
            description = "a test module"
            output = "text"

            [[tools]]
            name = "go"
            description = "does the thing"
            argv = ["{script}"]
        """))
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    assert bus.invoke("slow_go", {}, timeout_s=1.0).ok is False
    after = bus.invoke("good_go", {})
    assert after.ok is True, "a hung module stopped the next request being served"


# --------------------------------------------------------------------------
# 12.1 / 12.2 — invariants across every failure this file can produce
# --------------------------------------------------------------------------

def _every_failure(tmp_path):
    """One of each failure the bus can be made to produce, as (label, result)."""
    out = []
    d = tmp_path / "absent"; d.mkdir()
    out.append(("not installed", _absent(d).invoke("m_go", {})))

    d = tmp_path / "missing"; d.mkdir()
    out.append(("required detail missing",
                _module(d, "print('RAN')").invoke("m_go", {})))

    d = tmp_path / "empty"; d.mkdir()
    out.append(("required detail empty",
                _module(d, "print('RAN')").invoke("m_go", {"value": ""})))

    d = tmp_path / "unknown"; d.mkdir()
    out.append(("unknown action",
                _module(d, "print('RAN')").invoke("m_nope", {})))

    d = tmp_path / "slow"; d.mkdir()
    out.append(("timed out",
                _module(d, "import time\ntime.sleep(30)")
                .invoke("m_go", {"value": "x"}, timeout_s=1.0)))

    d = tmp_path / "failed"; d.mkdir()
    out.append(("module failed",
                _module(d, "import sys\nsys.stderr.write('bad\\n')\nsys.exit(2)")
                .invoke("m_go", {"value": "x"})))

    d = tmp_path / "json"; d.mkdir()
    out.append(("malformed json",
                _module(d, "print('not json')", output="json")
                .invoke("m_go", {"value": "x"})))
    return out


def test_every_failure_is_reported_as_a_failure(tmp_path):
    """s12.1. Every action reports success or failure explicitly. The eagle
    must never describe a failed action as done. This is the single most
    important falsifiable property in this document."""
    wrong = [
        label for label, result in _every_failure(tmp_path)
        if result.ok is not False
    ]
    assert not wrong, f"these failures did not report themselves as one: {wrong}"


def test_every_failure_says_so_in_the_dict_handed_to_the_model(tmp_path):
    """s12.1. The eagle works from to_response(), so that is where a failure
    has to be visible as one."""
    wrong = [
        label for label, result in _every_failure(tmp_path)
        if result.to_response().get("ok") is not False
    ]
    assert not wrong, (
        f"these failures told the model the action was done: {wrong}"
    )


def test_every_failure_comes_with_a_next_step(tmp_path):
    """s12.2. Every failure carries a sentence telling the eagle what to do
    about it. A refusal that only says no is a defect."""
    bare = [
        label for label, result in _every_failure(tmp_path)
        if not (result.guidance or "").strip()
    ]
    assert not bare, f"these failures said no and nothing else: {bare}"


def test_every_failure_carries_a_sentence_of_its_own(tmp_path):
    """s12.2, and the shape s5 of the setup note describes: a failure is a
    message plus a next step, both reachable."""
    silent = [
        label for label, result in _every_failure(tmp_path)
        if not (result.message or "").strip()
    ]
    assert not silent, f"these failures carried no sentence at all: {silent}"


def test_a_failures_next_step_reaches_the_model(tmp_path):
    """s12.2. The next step is only useful if it is handed over with the
    failure."""
    dropped = [
        label for label, result in _every_failure(tmp_path)
        if result.to_response().get("guidance") != result.guidance
    ]
    assert not dropped, (
        f"these failures kept their next step out of what the model was "
        f"handed: {dropped}"
    )


# --------------------------------------------------------------------------
# Untestable, recorded rather than written
# --------------------------------------------------------------------------

UNTESTABLE = {
    "2.6 (the limits table)": (
        "the per-action limits — 90s for browsing models, 60s for local files "
        "and printer discovery, 600s for starting prints, 5s for anything "
        "touching the island, 120s otherwise. The timeout behaviour and its "
        "wording are asserted above with a limit passed in. Which limit each "
        "real action declares is in its manifest, which is not one of the four "
        "surfaces this suite observes, and waiting out a 600s limit is not a "
        "test. Needs the limits exposed on the answer, or a documented way to "
        "read a declared limit back."
    ),
    "2.10": (
        "the on-screen copy is never truncated. The eagle's copy is cut at "
        "16,000 characters (asserted above), but the untruncated copy is not "
        "on the returned answer — .data carries only the module key. The "
        "island's copy travels a path that is not reachable from the bus "
        "result, and window.pill.set() is fed by hand from the other end, so "
        "neither surface alone can show the same result arriving whole."
    ),
    "2.11": (
        "bulk visual data never reaches the spoken answer. Same seam as 2.10: "
        "the split between what the eagle is read and what the island is given "
        "happens between the two surfaces this suite can observe."
    ),
    "2.13": (
        "rate limits are not reported as broken features. Tagged inferred. "
        "About the brain rather than a module, and reaching it means being "
        "rate-limited on purpose."
    ),
    "12.4": (
        "bad input is rejected, never sanitised. Needs an action whose "
        "parameter is one of a known set, so that an illegal value can be "
        "offered and refused. Whether a manifest can declare such a set is not "
        "stated in the setup note, and the real action that has one (the "
        "chamber light, s10.13) touches hardware."
    ),
    "12.5": (
        "a narrow tool failing is not the task being impossible. Tagged "
        "inferred. About what the eagle does next, which is the model's "
        "behaviour rather than the bus's."
    ),
    "12.6": (
        "nothing on screen means nothing on screen. About the island's "
        "selection state; belongs with section 5 and needs the on-screen "
        "selection, not the bus."
    ),
    "12.7": (
        "the eagle never composes a list of what the user picked. Falsification "
        "is that the printed set can differ from the badged set — the same "
        "selection surface as 12.6."
    ),
}


def test_the_untestable_items_in_these_sections_are_recorded():
    """Not a specification item. Keeps what these sections cannot reach visible
    in the run rather than silently absent."""
    assert len(UNTESTABLE) == 8
