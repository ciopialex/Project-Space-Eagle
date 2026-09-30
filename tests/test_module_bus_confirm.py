"""Some module tools move matter, and those need a human to say yes out loud.

`a3d print` downloads a model, slices it, uploads it and starts a physical
print: hours of machine time and a spool of filament, on hardware in the
room. It is reached through a voice loop where a server-side VAD decides when
the sentence ended, over a microphone, in a house with other people in it.

The gate is two-phase and the second phase carries a token the first phase
issued. That token exists to make one specific failure impossible: the model
answering its own question. It cannot fabricate a token, so a confirmed print
always has a first call behind it that produced a spoken question.

The token is bound to the exact arguments, which closes the other hole — a yes
for a keychain must not authorise a benchy on a different printer.
"""
from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402
import pytest

pytestmark = pytest.mark.usefixtures("user_answers_every_question")

MANIFEST = """
    key = "m"
    binary = "{binary}"
    description = "test module"
    output = "text"

    [[tools]]
    name = "go"
    description = "harmless"
    argv = ["-c", "print('ran')"]

    [[tools]]
    name = "burn"
    description = "starts something physical"
    argv = ["-c", "print('BURNED')", "{{thing}}"]
    confirm = true
    confirm_prompt = "This starts a real print on {{printer}} and uses filament."

    [tools.params.thing]
    type = "STRING"
    description = "what to print"
    required = true

    [tools.params.printer]
    type = "STRING"
    description = "which printer"

    [tools.params.leveling]
    type = "BOOLEAN"
    description = "level first"
    default = true
"""


def _bus(tmp_path) -> ModuleBus:
    (tmp_path / "m.toml").write_text(
        textwrap.dedent(MANIFEST).format(binary=sys.executable))
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    return bus


# ---------------------------------------------------------------- the gate

def test_a_guarded_tool_does_not_run_on_the_first_call(tmp_path):
    bus = _bus(tmp_path)
    result = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1"})
    assert result.ok is False
    assert "BURNED" not in result.message
    assert result.data["needs_confirmation"] is True


def test_the_refusal_is_phrased_as_a_question_to_speak(tmp_path):
    """The model has to ask the USER. A refusal that reads like an error gets
    reported as a failure instead of turned into a question."""
    bus = _bus(tmp_path)
    result = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1"})
    assert "real print on CC1" in result.message
    assert "?" in result.message
    assert "ask" in result.guidance.lower()


def test_the_question_names_the_actual_arguments(tmp_path):
    """'Shall I print something' is not a question anyone can answer."""
    bus = _bus(tmp_path)
    result = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC2"})
    assert "CC2" in result.message
    assert "benchy" in result.message


def test_an_unguarded_tool_is_untouched(tmp_path):
    bus = _bus(tmp_path)
    result = bus.invoke("m_go", {})
    assert result.ok is True
    assert "ran" in result.message


# --------------------------------------------------------------- the token

def test_the_issued_token_lets_the_same_call_through(tmp_path):
    bus = _bus(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    first = bus.invoke("m_burn", dict(args))
    token = first.data["confirm_token"]

    second = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert second.ok is True
    assert "BURNED" in second.message


def test_a_made_up_token_is_refused(tmp_path):
    """The whole point: the model cannot answer its own question."""
    bus = _bus(tmp_path)
    result = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1",
                                   "confirm_token": "yes-i-am-sure"})
    assert result.ok is False
    assert "BURNED" not in result.message


def test_a_token_does_not_authorise_different_arguments(tmp_path):
    """A yes for a keychain is not a yes for a benchy on another printer."""
    bus = _bus(tmp_path)
    first = bus.invoke("m_burn", {"thing": "keychain", "printer": "CC1"})
    token = first.data["confirm_token"]

    result = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC2",
                                   "confirm_token": token})
    assert result.ok is False
    assert "BURNED" not in result.message


def test_a_yes_survives_the_model_spelling_out_a_default(tmp_path):
    """Asked without `leveling`, confirmed with leveling=true: the same print.
    Refusing it made the user say yes a second time to an identical question."""
    bus = _bus(tmp_path)
    first = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1"})
    token = first.data["confirm_token"]
    second = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1",
                                   "leveling": True, "confirm_token": token})
    assert second.ok is True and "BURNED" in second.message


def test_a_non_default_change_still_needs_a_new_yes(tmp_path):
    bus = _bus(tmp_path)
    first = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1"})
    token = first.data["confirm_token"]
    second = bus.invoke("m_burn", {"thing": "benchy", "printer": "CC1",
                                   "leveling": False, "confirm_token": token})
    assert second.ok is False and "BURNED" not in second.message


def test_a_token_is_single_use(tmp_path):
    """One yes, one print. Otherwise a retry loop prints the same thing five
    times and each one costs filament."""
    bus = _bus(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    token = bus.invoke("m_burn", dict(args)).data["confirm_token"]

    assert bus.invoke("m_burn", dict(args, confirm_token=token)).ok is True
    again = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert again.ok is False
    assert "BURNED" not in again.message


def test_a_stale_token_expires(tmp_path):
    """A yes from twenty minutes ago is not consent for what is asked now."""
    bus = _bus(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    first = bus.invoke("m_burn", dict(args))
    token = first.data["confirm_token"]

    bus._confirmations[token]["expires_at"] = time.time() - 1
    result = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert result.ok is False
    assert "expired" in result.guidance.lower()


def test_the_token_never_reaches_the_module_as_an_argument(tmp_path):
    """`confirm_token` is protocol between the model and the bus. Passing it
    down would make it an unrecognised flag and fail the call."""
    bus = _bus(tmp_path)
    args = {"thing": "benchy", "printer": "CC1"}
    token = bus.invoke("m_burn", dict(args)).data["confirm_token"]
    result = bus.invoke("m_burn", dict(args, confirm_token=token))
    assert result.ok is True
    assert token not in result.message


def test_a_missing_required_argument_is_caught_before_asking(tmp_path):
    """Do not ask a human to approve a call that cannot run anyway."""
    bus = _bus(tmp_path)
    result = bus.invoke("m_burn", {"printer": "CC1"})
    assert result.ok is False
    assert result.data.get("needs_confirmation") is not True
    assert "thing" in result.message


# ------------------------------------------------------------- declaration

def test_a_guarded_tool_advertises_the_token_parameter(tmp_path):
    """The model can only complete the handshake if the schema has somewhere
    to put the token."""
    bus = _bus(tmp_path)
    decl = next(d for d in bus.tool_declarations() if d["name"] == "m_burn")
    props = decl["parameters"]["properties"]
    assert "confirm_token" in props
    assert props["confirm_token"]["type"] == "STRING"
    # Never required: the first call is supposed to arrive without it.
    assert "confirm_token" not in decl["parameters"].get("required", [])


def test_the_description_warns_that_it_is_physical(tmp_path):
    bus = _bus(tmp_path)
    decl = next(d for d in bus.tool_declarations() if d["name"] == "m_burn")
    assert "confirm" in decl["description"].lower()


def test_the_shipped_a3d_print_tool_is_guarded():
    """The real one. If this ever stops being guarded, a misheard sentence
    starts a print."""
    from core.module_bus import load_manifests
    manifests = load_manifests(
        [Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "manifests"])
    a3d = next(m for m in manifests if m.key == "a3d")
    printer = a3d.tool("print")
    assert printer is not None, "a3d print is not exposed at all"
    assert printer.confirm is True
    assert printer.confirm_prompt


def test_only_the_tools_that_start_a_machine_demand_confirmation():
    """Reading telemetry should never make the eagle ask permission — a gate
    on everything is a gate nobody reads.

    The gated tools are the ones that change a job on a machine for good:
    `print` starts one, `print_batch` starts several, and `stop` ends one --
    a stopped print cannot be resumed, so a misheard "stop" would throw away
    hours of work. `remove` forgets a printer; the model called it unasked on
    2026-09-24 and 2026-09-28, and a printer discovery cannot see from this
    network (a CC2 across Wi-Fi bands) cannot be added back without editing
    config. `pause` and `resume` are deliberately NOT gated: both can
    be undone by the other. Everything else -- search, browse, download,
    fleet, status, spool, light -- either reads, or writes a file, and asking
    about those trains the user to say yes without listening.

    Written as an exact set rather than a subset check: a new tool arriving
    with confirm = true should fail here and be argued for, and a gate quietly
    disappearing from print should fail here too.
    """
    from core.module_bus import load_manifests
    manifests = load_manifests(
        [Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "manifests"])
    a3d = next(m for m in manifests if m.key == "a3d")
    guarded = sorted(t.name for t in a3d.tools if t.confirm)
    assert guarded == ["print", "print_batch", "remove", "stop"]


def test_the_gated_tools_are_the_ones_that_reach_the_hardware():
    """Named separately from the set above so the reason survives a rename."""
    from core.module_bus import load_manifests
    manifests = load_manifests(
        [Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "manifests"])
    a3d = next(m for m in manifests if m.key == "a3d")
    by_name = {t.name: t for t in a3d.tools}
    for name in ("print", "print_batch"):
        assert by_name[name].confirm is True
        assert by_name[name].confirm_prompt, f"{name} asks nothing when it asks"
    for name in ("search", "browse", "download", "printers", "status"):
        assert not by_name[name].confirm, f"{name} should not ask permission"


def test_an_argument_the_model_does_not_know_is_asked_for_not_confirmed(tmp_path):
    """2026-09-29: a3d_print was called with filament='unknown' and the user was
    asked to approve 'a real print ... with unknown'."""
    bus = _bus(tmp_path)
    result = bus.invoke("m_burn", {"thing": "unknown", "printer": "CC1"})
    assert result.ok is False
    assert not result.data.get("confirm_token")
    assert "Ask the user" in result.guidance
