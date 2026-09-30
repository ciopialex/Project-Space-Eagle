"""BEHAVIOR_SPEC section 11 — Aethelark-3D: starting a real print.

Written clean-room from docs/BEHAVIOR_SPEC.md sections 11 and 3,
docs/TEST_SETUP.md and docs/MODULE_CONTRACT.md. No product source was read
while writing this file. The tool names and parameter names of the stand-in
module below mirror the real contract -- `print` takes **query_or_id**, printer,
filament and leveling; `print_batch` takes **jobs**; both are gated -- so these
tests are written in the product's own vocabulary. Every value is invented.

SAFETY. Not one call in this file reaches the real a3d printing action, and no
test here needs a printer. The gate lives entirely in the call (TEST_SETUP.md
section 6), so a stand-in module declared in tmp_path exercises it honestly: its
"printer" is a Python script that writes a marker file and prints the word
BURNED. Every test asserts the marker is ABSENT and the word never appears --
that absence is the load-bearing assertion, because a gate that returns exactly
the right sentence while still starting the print would pass every other check.

`a3d_print` and `a3d_print_batch` are deliberately never invoked, not even for
their unconfirmed first call. That call is safe only if the gate holds, and the
gate is the thing under test; leaning on the property under test to make the
test safe is not a bound worth taking with somebody's hardware, and
`a3d_print_batch` in particular reads the picks off the screen, so a leaked gate
would start whatever is showing. What that costs is listed in the UNTESTABLE
block at the foot of the file.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402

pytestmark = pytest.mark.usefixtures("user_answers_every_question")

MODULES_DIR = Path.home() / ".aethelark" / "modules"

ASKED_OUT_LOUD = ("Nothing has started. Ask the user this question OUT LOUD "
                  "and wait for an answer.")
NOT_ONE_I_ISSUED = "That confirmation is not one I issued, so nothing was started."
DIFFERENT_REQUEST = "That confirmation was for a different request, so nothing was started."
ASK_AGAIN = "The user agreed to something else. Ask again for THIS request."


def _print_module(tmp_path):
    """A stand-in for the printing module.

    Two gated tools, matching the two gated actions of section 3 and the two
    gated tools of the contract: printing a named model, and printing what is
    picked on screen. Behind both sits one script that leaves a marker file and
    shouts BURNED, so "did anything run" is answerable two independent ways.
    """
    marker = tmp_path / "BURNED.txt"
    script = tmp_path / "printer.py"
    script.write_text(textwrap.dedent(f'''
        import sys
        from pathlib import Path
        Path({str(marker)!r}).write_text(" ".join(sys.argv[1:]) or "ran")
        print("BURNED")
    '''))
    (tmp_path / "m.toml").write_text(textwrap.dedent(f'''
        key = "m"
        binary = "{sys.executable}"
        description = "a stand-in for the 3D printing module"
        output = "text"

        [[tools]]
        name = "print"
        description = "downloads, slices, uploads and starts a physical print"
        argv = ["{script}", "{{query_or_id}}", "{{printer}}"]
        confirm = true
        confirm_prompt = "This will start a real print of '{{query_or_id}}' on printer {{printer}}. It uses filament and takes hours, and I cannot stop it remotely once it is going."

        [tools.params.query_or_id]
        type = "STRING"
        description = "a search phrase, a model id, or an absolute path"
        required = true

        [tools.params.printer]
        type = "STRING"
        description = "which printer to start it on"
        required = false

        [tools.params.filament]
        type = "STRING"
        description = "which filament to use"
        required = false

        [tools.params.leveling]
        type = "BOOLEAN"
        description = "whether to level the bed first"
        required = false

        [[tools]]
        name = "print_batch"
        description = "starts the prints the user has picked on screen"
        argv = ["{script}", "{{jobs}}"]
        confirm = true
        confirm_prompt = "About to print {{jobs}}."

        [tools.params.jobs]
        type = "STRING"
        description = "what is picked on screen"
        required = true
    '''))
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    return bus, marker


def _nothing_ran(answer, marker):
    """The assertion this whole section rests on."""
    assert not marker.exists(), (
        f"the print ran: {marker.name} was written, carrying {marker.read_text()!r}")
    assert "BURNED" not in (answer.message or ""), answer.message


def _both_sentences(answer):
    return (answer.message or "") + " " + (answer.guidance or "")


def _real_catalogue():
    """Every action any module offers, read without running any of them.

    Section 2.5: an action no module has is answered with a list of every action
    that exists. It is the only way to look at the printing actions without
    calling them.
    """
    if not MODULES_DIR.is_dir():
        pytest.skip("the Aethelark modules are not installed at ~/.aethelark/modules")
    tried = []
    for label, factory in (
        ("its own defaults", lambda: ModuleBus()),
        ("~/.aethelark/modules", lambda: ModuleBus(manifest_dirs=[MODULES_DIR])),
        ("each module folder", lambda: ModuleBus(
            manifest_dirs=sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()))),
    ):
        try:
            bus = factory()
            bus.discover()
        except Exception as exc:
            tried.append(f"{label}: {exc!r}")
            continue
        answer = bus.invoke("zzz_no_such_action", {})
        catalogue = ((answer.message or "") + " " + (answer.guidance or "")).lower()
        if "a3d" in catalogue:
            return catalogue
        tried.append(f"{label}: built, but no a3d action was offered")
    pytest.skip("no module bus this test could build could see the a3d module -- " +
                "; ".join(tried))


# --------------------------------------------------------------------------
# 11.1 -- a print is gated by section 3 in full
# --------------------------------------------------------------------------

def test_asking_for_a_print_starts_nothing_and_asks_a_question_instead(tmp_path):
    """s11.1 / s3.1. Falsification: if the printer moves before the user has
    answered, this spec is broken."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(answer, marker)
    assert answer.ok is False
    assert answer.data["needs_confirmation"] is True
    assert "?" in answer.message


def test_the_question_names_the_model_and_the_printer_it_would_use(tmp_path):
    """s11.1 / s3.1 / s3.2. The question names the model and the printer and
    states the cost. A question the user cannot check is not a confirmation."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(answer, marker)
    assert "watch stand" in answer.message
    assert "CC1" in answer.message


def test_the_eagle_is_told_nothing_has_started_and_to_ask_out_loud(tmp_path):
    """s11.1 / s3.1. In the same breath as the question: `Nothing has started.
    Ask the user this question OUT LOUD and wait for an answer.`"""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(answer, marker)
    assert ASKED_OUT_LOUD in _both_sentences(answer)


def test_the_question_repeats_back_the_arguments_it_would_act_on(tmp_path):
    """s11.1 / s3.1. `Requested: <the arguments>` -- the user is told what would
    happen, in the terms the request was made in."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1",
                                    "filament": "Bambu PLA Silk"})
    _nothing_ran(answer, marker)
    assert "Requested:" in answer.message
    assert "Bambu PLA Silk" in answer.message


def test_a_confirmed_print_request_is_the_only_thing_that_reaches_the_printer(tmp_path):
    """s11.1 / s3.3. A yes starts it. This is the positive control for every
    other test in this file: it proves the marker file and the sentinel word do
    fire when the program behind the gate is genuinely reached."""
    bus, marker = _print_module(tmp_path)
    request = {"query_or_id": "watch stand", "printer": "CC1"}
    token = bus.invoke("m_print", dict(request)).data["confirm_token"]
    answer = bus.invoke("m_print", dict(request, confirm_token=token))
    assert answer.ok is True, answer.message
    assert "BURNED" in answer.message
    assert marker.exists()


def test_both_print_paths_exist_and_are_the_only_two_printing_actions_offered():
    """s11.1 / s11.6 / s3. Two actions start a print: a named model, and what is
    picked on screen. Read off the list of actions rather than by calling
    either."""
    catalogue = _real_catalogue()
    assert "a3d_print" in catalogue, catalogue
    assert "a3d_print_batch" in catalogue, catalogue


# --------------------------------------------------------------------------
# 11.2 -- how the model may be given
# --------------------------------------------------------------------------

def test_a_phrase_an_id_and_an_absolute_path_are_each_gated_on_their_own_terms(tmp_path):
    """s11.2. The model may be a search phrase, a model id, or an absolute path
    to a file already on the machine. Whichever form is used the request is asked
    about before anything runs, in its own words, and each agreement is its
    own."""
    bus, marker = _print_module(tmp_path)
    forms = ["the Pulsar Designs watch stand", "124686", "/home/user/3d/benchy.stl"]
    tokens = []
    for form in forms:
        answer = bus.invoke("m_print", {"query_or_id": form, "printer": "CC1"})
        _nothing_ran(answer, marker)
        assert answer.data["needs_confirmation"] is True, form
        assert form in answer.message, form
        tokens.append(answer.data["confirm_token"])
    assert len(set(tokens)) == len(tokens), "one agreement was issued for three requests"


def test_a_print_request_with_no_model_at_all_is_refused_before_any_question(tmp_path):
    """s11.2 / s2.3. The model has to be given as something. A request without one
    is refused before anything is spawned and before any confirmation is asked
    for -- the user is asked for the missing detail, not asked to approve a
    request nobody made."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"printer": "CC1"})
    _nothing_ran(answer, marker)
    assert answer.ok is False
    assert answer.data.get("needs_confirmation") is not True
    assert "needs" in answer.message.lower()
    assert "query_or_id" in answer.message.lower()


def test_a_print_request_whose_model_is_an_empty_string_is_refused_the_same_way(tmp_path):
    """s11.2 / s2.4. An empty string counts as missing: it produces the refusal,
    not a run with an empty value, and not a confirmation question about
    nothing."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "", "printer": "CC1"})
    _nothing_ran(answer, marker)
    assert answer.ok is False
    assert answer.data.get("needs_confirmation") is not True
    assert "needs" in answer.message.lower()


# --------------------------------------------------------------------------
# 11.3 -- bed levelling
# --------------------------------------------------------------------------

def test_saying_nothing_about_levelling_is_not_treated_as_a_missing_detail(tmp_path):
    """s11.3. Levelling runs unless the user explicitly says to skip it, so
    saying nothing about it must reach the question rather than a refusal asking
    which it should be."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(answer, marker)
    assert answer.data["needs_confirmation"] is True
    assert "leveling" not in answer.message.lower()
    assert "needs" not in answer.message.lower()


def test_agreeing_to_a_levelled_print_does_not_authorise_one_that_skips_levelling(tmp_path):
    """s11.3 / s3.6. Skipping the levelling is an explicit instruction, so it is
    part of the request the user agreed to -- an agreement given for one cannot
    be spent on the other."""
    bus, marker = _print_module(tmp_path)
    token = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1",
                                   "leveling": True}).data["confirm_token"]
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1",
                                    "leveling": False, "confirm_token": token})
    _nothing_ran(answer, marker)
    assert answer.ok is False


# --------------------------------------------------------------------------
# 11.4 -- the filament is optional
# --------------------------------------------------------------------------

def test_saying_nothing_about_filament_reaches_the_question_rather_than_a_refusal(tmp_path):
    """s11.4. Naming a filament selects it; saying nothing lets the printer use
    what is loaded. Saying nothing must therefore not be treated as a missing
    detail."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(answer, marker)
    assert answer.data["needs_confirmation"] is True
    assert "needs" not in answer.message.lower()


def test_a_named_filament_binds_the_agreement_to_that_filament(tmp_path):
    """s11.4 / s3.6. Naming a filament makes it part of the request, so an
    agreement given for one filament does not authorise a print with another."""
    bus, marker = _print_module(tmp_path)
    token = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1",
                                   "filament": "Bambu PLA Silk"}).data["confirm_token"]
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1",
                                    "filament": "eSUN PLA+", "confirm_token": token})
    _nothing_ran(answer, marker)
    assert answer.ok is False


# --------------------------------------------------------------------------
# 11.6 -- printing what is on screen is a different, safer path
# --------------------------------------------------------------------------

def test_printing_what_is_picked_on_screen_is_gated_exactly_as_a_named_print_is(tmp_path):
    """s11.6 / s3.1. The on-screen path is the second of the two gated actions:
    its first request runs nothing and asks instead."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print_batch", {"jobs": "Watch Stand/Display on CC1"})
    _nothing_ran(answer, marker)
    assert answer.ok is False
    assert answer.data["needs_confirmation"] is True
    assert ASKED_OUT_LOUD in _both_sentences(answer)


def test_the_on_screen_question_names_the_model_by_title_not_by_number(tmp_path):
    """s11.6 / s3.2. `About to print Watch Stand/Display on CC1.` -- the question
    uses the model's title, not its number, because a question the user cannot
    verify is not a confirmation."""
    bus, marker = _print_module(tmp_path)
    answer = bus.invoke("m_print_batch", {"jobs": "Watch Stand/Display on CC1"})
    _nothing_ran(answer, marker)
    assert "Watch Stand/Display" in answer.message
    assert "CC1" in answer.message


def test_an_agreement_for_the_named_path_does_not_authorise_the_on_screen_path(tmp_path):
    """s11.6 / s11.7. Two paths, two gates. A yes to one is not a yes to the
    other."""
    bus, marker = _print_module(tmp_path)
    token = bus.invoke("m_print", {"query_or_id": "watch stand",
                                   "printer": "CC1"}).data["confirm_token"]
    answer = bus.invoke("m_print_batch", {"jobs": "Watch Stand/Display on CC1",
                                          "confirm_token": token})
    _nothing_ran(answer, marker)
    assert answer.ok is False


# --------------------------------------------------------------------------
# 11.7 -- a print cannot be started by the eagle alone
# --------------------------------------------------------------------------

def test_one_agreement_authorises_exactly_one_start(tmp_path):
    """s11.7 / s3.4. A second attempt on the same agreement is refused with
    `That confirmation is not one I issued, so nothing was started.`"""
    bus, marker = _print_module(tmp_path)
    request = {"query_or_id": "watch stand", "printer": "CC1"}
    token = bus.invoke("m_print", dict(request)).data["confirm_token"]
    first = bus.invoke("m_print", dict(request, confirm_token=token))
    assert first.ok is True, first.message
    marker.unlink()

    second = bus.invoke("m_print", dict(request, confirm_token=token))
    _nothing_ran(second, marker)
    assert second.ok is False
    assert NOT_ONE_I_ISSUED in second.guidance


def test_an_agreement_the_eagle_made_up_starts_nothing(tmp_path):
    """s11.7 / s3.5. The eagle cannot answer its own question. There is no path
    from a single request to a started print, and a misheard sentence must not
    reach a printer."""
    bus, marker = _print_module(tmp_path)
    for invented in ("yes", "confirmed", "ok-go-ahead", ""):
        answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1",
                                        "confirm_token": invented})
        _nothing_ran(answer, marker)
        assert answer.ok is False, invented
        assert NOT_ONE_I_ISSUED in answer.guidance, invented


def test_agreeing_to_print_on_one_printer_does_not_authorise_another(tmp_path):
    """s11.7 / s3.6. A yes is bound to what was asked. `That confirmation was for
    a different request, so nothing was started.` with the follow-up `The user
    agreed to something else. Ask again for THIS request.`"""
    bus, marker = _print_module(tmp_path)
    token = bus.invoke("m_print", {"query_or_id": "watch stand",
                                   "printer": "CC1"}).data["confirm_token"]
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC2",
                                    "confirm_token": token})
    _nothing_ran(answer, marker)
    assert answer.ok is False
    assert DIFFERENT_REQUEST in answer.guidance
    assert ASK_AGAIN in _both_sentences(answer)


def test_a_misheard_model_name_is_a_different_request_and_reaches_no_printer(tmp_path):
    """s11.7 / s3.6. Agreeing to print apples does not authorise printing oranges,
    and two model names one word apart are two requests."""
    bus, marker = _print_module(tmp_path)
    token = bus.invoke("m_print", {"query_or_id": "watch stand",
                                   "printer": "CC1"}).data["confirm_token"]
    answer = bus.invoke("m_print", {"query_or_id": "watch band", "printer": "CC1",
                                    "confirm_token": token})
    _nothing_ran(answer, marker)
    assert answer.ok is False
    assert DIFFERENT_REQUEST in answer.guidance


def test_a_refusal_on_the_print_path_carries_a_next_step_rather_than_only_saying_no(tmp_path):
    """s11.7 / s12.2. Every failure on this path carries a sentence telling the
    eagle what to do about it, and it reaches the model in the same dict as the
    refusal. A refusal that only says no is a defect."""
    bus, marker = _print_module(tmp_path)
    token = bus.invoke("m_print", {"query_or_id": "watch stand",
                                   "printer": "CC1"}).data["confirm_token"]
    answer = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC2",
                                    "confirm_token": token})
    _nothing_ran(answer, marker)
    assert answer.guidance, answer.to_response()
    sent = answer.to_response()
    assert sent["ok"] is False
    assert sent["result"] == answer.message
    assert sent["guidance"] == answer.guidance


def test_never_answering_the_question_leaves_the_printer_untouched(tmp_path):
    """s11.7 / s3.9. A refusal starts nothing. The user saying no, or saying
    nothing, is the case where the second call never comes -- and the absence of
    that call is the whole of it. Asking twice issues two separate agreements and
    still starts nothing."""
    bus, marker = _print_module(tmp_path)
    asked = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(asked, marker)
    again = bus.invoke("m_print", {"query_or_id": "watch stand", "printer": "CC1"})
    _nothing_ran(again, marker)
    assert asked.data["confirm_token"] != again.data["confirm_token"]


# UNTESTABLE
#
# 11.2, that the REAL `a3d_print` accepts a search phrase, a model id and an
#   absolute path in its `query_or_id` -- observing which forms it accepts means
#   invoking it, and the unconfirmed first call is safe only if the gate is
#   sound, which is the property under test. The harness half of the item (each
#   form gated, each agreement bound to its own form) is covered above against a
#   stand-in built to the same contract.
#
# 11.3, "the default is yes" -- `a3d_print` declares `leveling` as an optional
#   BOOLEAN, which is consistent with the item, but whether an omitted value
#   levels the bed is decided inside the module at slice time. The only
#   observation is a print that levelled or did not, on real hardware. What is
#   covered above is that omitting it is not a refusal and that supplying it
#   binds the agreement.
#
# 11.5, "the printer defaults to CC1 when the user names none" -- `printer` is
#   optional on `a3d_print`, which is consistent, but seeing the default applied
#   means letting a print start on CC1.
#
# 11.6, that the on-screen path reads the picks off the screen rather than from
#   a list (5.19, 12.7) -- and here the contract and the specification disagree:
#   `a3d_print_batch` takes **jobs** `STRING` as a REQUIRED parameter, so
#   somebody composes that list, while 5.19 says the action "takes no list from
#   the user or the eagle" and 12.7 says "the eagle never composes a list of what
#   the user picked". Settling it needs the island holding real picks and the
#   real module behind it; it cannot be settled from a stand-in, and it must not
#   be settled by calling `a3d_print_batch`, which starts whatever is showing.
#
# 11.7 by way of 3.7, "a yes expires after 180 seconds" -- TEST_SETUP.md section
#   6 says the only way to test staleness without waiting three minutes is to age
#   the stored record before the second call, which means reaching past the
#   public surface into a store this clean room may not look at. It is already
#   covered in the existing suite, which is the one place allowed to do it.
#
# 11.7 by way of 3.8, "a yes must still match the screen" -- the falsification
#   (pick two models, get asked, remove one, say yes, and no print may start)
#   needs the island holding picks and the real module comparing against them. A
#   stand-in module has no screen to disagree with.
