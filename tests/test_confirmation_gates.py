"""Acceptance tests for BEHAVIOR_SPEC.md section 3 —
"Anything that touches the physical world asks first".

Written from the specification alone. Every test names the item it proves.
Where the specification carries a **Falsification:** line, that line is the
assertion the test is built around: the sentinel word BURNED is printed by the
program behind the gate, so asserting its ABSENCE is what proves that nothing
physical actually ran.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402
import pytest

pytestmark = pytest.mark.usefixtures("user_answers_every_question")


# A module with two gated tools that start something physical, one gated tool
# that prints what is picked on screen, and one ungated tool. The gated tools'
# prompts deliberately contain no question mark and no "Requested:" — those
# belong to the harness, so asserting them proves the harness composed them.
MANIFEST = """
key = "m"
binary = "{binary}"
description = "a test module"
output = "text"

[[tools]]
name = "burn"
description = "starts something physical"
argv = ["-c", "print('BURNED')", "{{thing}}"]
confirm = true
confirm_prompt = "This will start a real print of '{{thing}}' on printer {{printer}}. It uses filament and takes hours, and I cannot stop it remotely once it is going."

[tools.params.thing]
type = "STRING"
description = "what to print"
required = true

[tools.params.printer]
type = "STRING"
description = "which printer"
required = true

[[tools]]
name = "burn_other"
description = "also starts something physical"
argv = ["-c", "print('BURNED')", "{{thing}}"]
confirm = true
confirm_prompt = "This will start a real print of '{{thing}}' on printer {{printer}}. It uses filament and takes hours, and I cannot stop it remotely once it is going."

[tools.params.thing]
type = "STRING"
description = "what to print"
required = true

[tools.params.printer]
type = "STRING"
description = "which printer"
required = true

[[tools]]
name = "print_picks"
description = "starts the prints the user has picked on screen"
argv = ["-c", "print('BURNED')"]
confirm = true
confirm_prompt = "About to print {{title}} on {{printer}}."

[tools.params.title]
type = "STRING"
description = "the title of the picked model"
required = true

[tools.params.printer]
type = "STRING"
description = "which printer"
required = true

[[tools]]
name = "look"
description = "reads telemetry and moves nothing"
argv = ["-c", "print('LOOKED')"]

[tools.params.printer]
type = "STRING"
description = "which printer"
"""


def _gated(tmp_path):
    """A real module on disk: a real process, real pipes, a real exit code."""
    (tmp_path / "m.toml").write_text(
        textwrap.dedent(MANIFEST).format(binary=sys.executable)
    )
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    return bus


def _ask(bus, **args):
    """Make the first call — the one that is supposed to start nothing."""
    return bus.invoke("m_burn", dict(args))


# --------------------------------------------------------------------------
# 3 — preamble: two actions are gated, nothing else in either module is
# --------------------------------------------------------------------------


def test_a_tool_that_moves_nothing_runs_without_asking_first(tmp_path):
    """s3 preamble. Only starting a print is gated; nothing else in either
    module is, so an ungated tool answers on the first call."""
    bus = _gated(tmp_path)
    result = bus.invoke("m_look", {"printer": "CC1"})
    assert result.ok is True
    assert "LOOKED" in result.message
    assert result.data.get("needs_confirmation") is not True


# --------------------------------------------------------------------------
# 3.1 — the first request runs nothing
# --------------------------------------------------------------------------


def test_the_first_request_for_a_print_starts_nothing(tmp_path):
    """s3.1. Falsification: if the printer moves before the user has answered,
    this spec is broken. The program behind the gate prints BURNED, so its
    absence is the proof that nothing started."""
    bus = _gated(tmp_path)
    result = _ask(bus, thing="benchy", printer="CC1")
    assert "BURNED" not in result.message
    assert result.ok is False


def test_the_first_request_returns_a_question_rather_than_a_result(tmp_path):
    """s3.1. The eagle asks a question out loud and waits, so the first call
    comes back flagged as needing confirmation and carrying a token to answer
    with."""
    bus = _gated(tmp_path)
    result = _ask(bus, thing="benchy", printer="CC1")
    assert result.data["needs_confirmation"] is True
    assert result.data["confirm_token"]


def test_the_question_names_the_model_the_printer_and_what_it_costs(tmp_path):
    """s3.1. The question names the model and the printer, and states the cost:
    filament, hours, and that it cannot be stopped remotely once going."""
    bus = _gated(tmp_path)
    message = _ask(bus, thing="benchy", printer="CC1").message
    assert (
        "This will start a real print of 'benchy' on printer CC1. It uses "
        "filament and takes hours, and I cannot stop it remotely once it is "
        "going." in message
    )


def test_the_question_repeats_back_the_arguments_and_asks_to_go_ahead(tmp_path):
    """s3.1. The fixed wording ends `Requested: <the arguments>. Shall I go
    ahead?` — the user is told exactly what they are agreeing to."""
    bus = _gated(tmp_path)
    message = _ask(bus, thing="benchy", printer="CC1").message
    assert "Requested:" in message
    assert "benchy" in message
    assert "CC1" in message
    assert "Shall I go ahead?" in message


def test_the_eagle_is_told_nothing_started_and_to_ask_the_user_out_loud(tmp_path):
    """s3.1. In the same breath the eagle is told: `Nothing has started. Ask
    the user this question OUT LOUD and wait for an answer.`"""
    bus = _gated(tmp_path)
    result = _ask(bus, thing="benchy", printer="CC1")
    assert (
        "Nothing has started. Ask the user this question OUT LOUD and wait "
        "for an answer." in result.guidance
    )


def test_a_gated_request_missing_a_required_detail_is_refused_before_it_asks(
    tmp_path,
):
    """s3.1 with s2.3. A missing required detail is refused before anything
    runs and before any confirmation is asked for, so no token is issued that
    could later authorise an incomplete print."""
    bus = _gated(tmp_path)
    result = bus.invoke("m_burn", {"printer": "CC1"})
    assert result.ok is False
    assert "BURNED" not in result.message
    assert result.data.get("needs_confirmation") is not True
    assert not result.data.get("confirm_token")


# --------------------------------------------------------------------------
# 3.2 — the question names the thing in words the user can check
# --------------------------------------------------------------------------


def test_the_question_about_screen_picks_names_the_model_by_its_title(tmp_path):
    """s3.2. When printing what is on screen the question uses the model's
    title, not its number: `About to print Watch Stand/Display on CC1.` A
    question the user cannot verify is not a confirmation."""
    bus = _gated(tmp_path)
    result = bus.invoke(
        "m_print_picks", {"title": "Watch Stand/Display", "printer": "CC1"}
    )
    assert "About to print Watch Stand/Display on CC1." in result.message
    assert result.ok is False
    assert "BURNED" not in result.message


# --------------------------------------------------------------------------
# 3.3 — a yes starts it
# --------------------------------------------------------------------------


def test_the_token_the_question_issued_lets_that_same_request_through(tmp_path):
    """s3.3. After the user agrees, the same request goes through and the print
    starts."""
    bus = _gated(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    token = _ask(bus, **args).data["confirm_token"]
    result = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert result.ok is True
    assert "BURNED" in result.message


# --------------------------------------------------------------------------
# 3.4 — a yes cannot be reused
# --------------------------------------------------------------------------


def test_one_agreement_authorises_exactly_one_start(tmp_path):
    """s3.4. Agreeing once authorises exactly one start, so a second use of the
    same agreement starts nothing."""
    bus = _gated(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    token = _ask(bus, **args).data["confirm_token"]
    first = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert first.ok is True and "BURNED" in first.message

    second = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert second.ok is False
    assert "BURNED" not in second.message


def test_a_mismatched_confirmation_does_not_spend_the_agreement(tmp_path):
    """SHIP_1.0 B2. Agreeing to one thing and being asked about another is
    correctly refused -- but the agreement was consumed on the way, so the
    corrected retry failed too, with a message blaming the user for a token the
    eagle had just destroyed. Only a successful match spends a yes."""
    bus = _gated(tmp_path)
    agreed = {"thing": "benchy", "printer": "CC1"}
    token = _ask(bus, **agreed).data["confirm_token"]

    # The model passes the right token against the wrong request.
    wrong = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC2",
                                  "confirm_token": token})
    assert wrong.ok is False
    assert "different request" in wrong.guidance, wrong.guidance
    assert "different request" not in wrong.message

    # The correction must now work. It did not: the token was already gone.
    corrected = bus.invoke("m_burn", dict(agreed, confirm_token=token))
    assert corrected.ok is True, (
        f"the agreement was spent by a request it refused, so the user has to "
        f"be asked again for something they already said yes to: "
        f"{corrected.message}")
    assert "BURNED" in corrected.message


def test_a_mismatch_does_not_start_anything_either(tmp_path):
    """Not spending the token must not become permission to run."""
    bus = _gated(tmp_path)
    token = _ask(bus, thing="benchy", printer="CC1").data["confirm_token"]
    wrong = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC2",
                                  "confirm_token": token})
    assert "BURNED" not in wrong.message


def test_the_agreement_is_still_single_use_after_a_mismatch(tmp_path):
    """The corrected call spends it, and nothing else may."""
    bus = _gated(tmp_path)
    agreed = {"thing": "benchy", "printer": "CC1"}
    token = _ask(bus, **agreed).data["confirm_token"]
    bus.invoke("m_burn", {"thing": "benchy", "printer": "CC2",
                          "confirm_token": token})
    assert bus.invoke("m_burn", dict(agreed, confirm_token=token)).ok is True
    again = bus.invoke("m_burn", dict(agreed, confirm_token=token))
    assert again.ok is False, "a spent agreement authorised a second start"


def test_a_reused_agreement_is_refused_as_one_the_eagle_did_not_issue(tmp_path):
    """s3.4. The second attempt is refused with `That confirmation is not one I
    issued, so nothing was started.`"""
    bus = _gated(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    token = _ask(bus, **args).data["confirm_token"]
    bus.invoke("m_burn", dict(args, confirm_token=token))
    second = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert (
        "That confirmation is not one I issued, so nothing was started."
        in second.guidance
    )
    assert "not one I issued" not in second.message


# --------------------------------------------------------------------------
# 3.5 — the eagle cannot answer its own question
# --------------------------------------------------------------------------


def test_a_confirmation_the_eagle_invented_starts_nothing(tmp_path):
    """s3.5. An agreement the eagle fabricates is refused: there is no path
    from a single request to a started print."""
    bus = _gated(tmp_path)
    result = bus.invoke(
        "m_burn",
        {"thing": "benchy", "printer": "CC1", "confirm_token": "yes-i-agree"},
    )
    assert result.ok is False
    assert "BURNED" not in result.message


def test_a_fabricated_confirmation_is_refused_in_the_same_words_as_a_reused_one(
    tmp_path,
):
    """s3.5. A fabricated agreement is refused with the same wording as 3.4:
    `That confirmation is not one I issued, so nothing was started.`"""
    bus = _gated(tmp_path)
    result = bus.invoke(
        "m_burn",
        {"thing": "benchy", "printer": "CC1", "confirm_token": "yes-i-agree"},
    )
    assert (
        "That confirmation is not one I issued, so nothing was started."
        in result.guidance
    )
    assert "not one I issued" not in result.message


# --------------------------------------------------------------------------
# 3.6 — a yes is bound to what was asked
# --------------------------------------------------------------------------


def test_agreeing_to_print_apples_does_not_authorise_printing_oranges(tmp_path):
    """s3.6. A yes is bound to what was asked, so a mismatched argument starts
    nothing."""
    bus = _gated(tmp_path)
    token = _ask(bus, thing="apples", printer="CC1").data["confirm_token"]
    result = bus.invoke(
        "m_burn", {"thing": "oranges", "printer": "CC1", "confirm_token": token}
    )
    assert result.ok is False
    assert "BURNED" not in result.message


def test_agreeing_to_one_printer_does_not_authorise_another_printer(tmp_path):
    """s3.6. The binding covers the whole request, the destination included: a
    yes for CC1 does not start anything on CC2."""
    bus = _gated(tmp_path)
    token = _ask(bus, thing="benchy", printer="CC1").data["confirm_token"]
    result = bus.invoke(
        "m_burn", {"thing": "benchy", "printer": "CC2", "confirm_token": token}
    )
    assert result.ok is False
    assert "BURNED" not in result.message


def test_a_mismatched_agreement_says_it_was_for_a_different_request(tmp_path):
    """s3.6. The refusal reads `That confirmation was for a different request,
    so nothing was started.` with the follow-up `The user agreed to something
    else. Ask again for THIS request.`"""
    bus = _gated(tmp_path)
    token = _ask(bus, thing="benchy", printer="CC1").data["confirm_token"]
    result = bus.invoke(
        "m_burn", {"thing": "benchy", "printer": "CC2", "confirm_token": token}
    )
    assert (
        "That confirmation was for a different request, so nothing was started."
        in result.guidance
    )
    assert (
        "The user agreed to something else. Ask again for THIS request."
        in result.guidance
    )


def test_an_agreement_for_one_gated_action_does_not_authorise_another(tmp_path):
    """s3.6. What was asked includes which action was asked about: a yes issued
    for one gated tool starts nothing on a different gated tool, even with
    identical arguments."""
    bus = _gated(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    token = _ask(bus, **args).data["confirm_token"]
    result = bus.invoke("m_burn_other", dict(args, confirm_token=token))
    assert result.ok is False
    assert "BURNED" not in result.message


# --------------------------------------------------------------------------
# 3.9 — a refusal starts nothing
# --------------------------------------------------------------------------


def test_a_question_the_user_never_answers_starts_nothing(tmp_path):
    """s3.9. If the user says no, nothing runs: an issued token that is never
    presented leaves the printer untouched, and the module is never reached."""
    bus = _gated(tmp_path)
    result = _ask(bus, thing="benchy", printer="CC1")
    assert result.data["confirm_token"]
    assert "BURNED" not in result.message
    assert result.ok is False


def test_asking_again_without_an_answer_asks_again_rather_than_proceeding(
    tmp_path,
):
    """s3.9. A repeated request with no agreement in hand is met with the
    question a second time, never with a start — an unanswered question does
    not decay into a yes."""
    bus = _gated(tmp_path)
    _ask(bus, thing="benchy", printer="CC1")
    second = _ask(bus, thing="benchy", printer="CC1")
    assert second.ok is False
    assert "BURNED" not in second.message
    assert second.data["needs_confirmation"] is True


# UNTESTABLE
#
# 3.7 — "a yes expires" (180 seconds). The only way to observe staleness
#   without waiting three minutes is to age the stored confirmation record
#   before the second call, which TEST_SETUP.md section 6 states plainly is the
#   one place a test reaches past the public surface and is not to be taken as
#   the pattern. Nothing on the public surface reports a token's age or lets a
#   clock be injected, so the quoted refusal `That confirmation has expired, so
#   nothing was started.` cannot be reached honestly from here.
#
# 3.8 — "a yes must still match the screen". The falsification is: pick two
#   models, get asked, remove one, say yes — a print must not start. There is
#   no way through ModuleBus to establish, or then change, what is picked on
#   screen; picks are made by clicking the island, and TEST_SETUP.md section 9
#   describes only registering a card and feeding it a payload, not picking
#   from it or connecting that selection to an invoke. The refusal sentence
#   `That is not what is picked on screen any more, so nothing was started.` is
#   therefore unreachable without the screen and the module wired together.
#
# 3.2, the module half. The test above proves the harness renders the title it
#   was given into a checkable sentence. It cannot prove that the real
#   Aethelark-3D picks tool passes the model's *title* rather than its id,
#   because that choice is made inside the module while reading the screen —
#   the same missing seam as 3.8.
#
# 3.9, the second half — "the eagle does not ask again unprompted". That is
#   speech, not a returned value; per TEST_SETUP.md section 5, meaning-level
#   assertions on speech are only possible where actual speech can be listened
#   to, and nothing on the module surface exposes whether the eagle re-raised
#   the question on its own.
