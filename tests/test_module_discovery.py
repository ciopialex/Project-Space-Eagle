"""Specification section 1 — Starting up, and knowing which modules are live.

What the eagle knows about its modules when it comes up: which are declared,
which are actually installed, and what happens to the ones that are neither.

Nothing here needs a network, a browser or a real module binary. Items 1.2 to
1.5 are driven with manifests written into tmp_path, which is a module as far
as the eagle is concerned (setup note s4).

Item 1.1 is the exception and is only half reachable — see UNTESTABLE at the
foot of this file. The preflight printout has no documented entry point, so
what is asserted here is the fact the line is built from rather than the line.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402

MODULES = Path.home() / ".aethelark" / "modules"

# s1.1: the counts the preflight line reports, quoted from the item.
#: atrade gained `owners` on 2026-09-05 (SHIP_1.0 C1) -- who holds five percent
#: or more of a company, from the Schedule 13D/13G filings the module had
#: already been parsing and storing with no command to reach them.
#: a3d went 21 -> 20 on 2026-09-10 when `sing` was removed. It was not a
#: capability: its argv was ["beacon", "{printer}", "--mode=chime"], the
#: same subcommand `beacon` runs with a mode its own parameter already
#: lists. Both descriptions also claimed the literal sentence "which one is
#: CC1", with no winner named, so a small voice model was choosing between
#: two spellings of one action.
#: a3d 20 -> 21 on 2026-09-10 with `pair`: discovery can find a printer
#: that gates its LAN API behind an access code (the CC2), but there was
#: no tool to hand the code over, so a found-but-locked printer was a dead
#: end. pair takes the code the user reads off the screen and verifies it.
#: a3d 21 -> 22 on 2026-09-14 with `speed`: the print-speed mode
#: (silent/balanced/sport/ludicrous) is changed live, mid-print, the same as
#: the printer's own screen. "put the CC1 on silent mode", "set CC2 to sport".
#: a3d 22 -> 25 on 2026-09-23 with `pause`, `resume` and `stop`: a print the
#: eagle can start by voice can now be held or ended by voice.
#: atrade 11 -> 12 on 2026-09-23 with `contact`: the eagle can take the email
#: SEC requires by voice, instead of failing every filing-backed question.
DECLARED_TOOL_COUNTS = {"a3d": 30, "alaw": 3, "atrade": 12}


def _known_tools(bus):
    """Every action any module offers, read off the refusal in s2.5.

    The unknown-tool refusal names the whole set, which makes it the way to ask
    the bus what it found without reaching inside it.
    """
    guidance = bus.invoke("zzzz_no_such_tool", {}).guidance or ""
    _, _, body = guidance.partition(":")
    body = body.strip().rstrip(".")
    if body in ("", "none"):
        # what an empty machine reports: "Known module tools: none."
        return []
    return sorted(t.strip().rstrip(".") for t in body.split(",") if t.strip())


def _counts_by_module(tools):
    counts = {}
    for tool in tools:
        module, _, _ = tool.partition("_")
        counts[module] = counts.get(module, 0) + 1
    return counts


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(textwrap.dedent(body))


def _bus(tmp_path, which=lambda b: b):
    bus = ModuleBus(manifest_dirs=[tmp_path], which=which)
    bus.discover()
    return bus


GOOD = """
    key = "%s"
    binary = "%s"
    description = "a test module"
    output = "text"

    [[tools]]
    name = "go"
    description = "does the thing"
    argv = ["-c", "print('ran')"]
"""


# --------------------------------------------------------------------------
# 1.1 what the preflight line counts
# --------------------------------------------------------------------------

def test_each_installed_module_offers_the_number_of_tools_the_item_states():
    """s1.1. Aethelark-3D reports 19; Aethelark-Trade reports 10; alaw reports
    3.

    a3d went 18 -> 19 on 2026-09-04 when `remove` was added: until then a
    printer could be added to the fleet and never taken out, so every listing
    and every fleet count was wrong from the first mistaken entry onward.

    19 -> 21 on 2026-09-05 with `quote` and `rates`. The costing arithmetic
    existed but no tool reached it, so the eagle could not be asked what to
    charge for a print -- and the one command that did reach it costed every
    job as a 30-minute run.

    The preflight line itself has no entry point (see UNTESTABLE), so the
    counts behind it are asserted instead: the line is built from these.
    """
    if not MODULES.is_dir():
        pytest.skip("no modules are installed at ~/.aethelark/modules/")
    bus = ModuleBus()
    bus.discover()
    counts = _counts_by_module(_known_tools(bus))
    wrong = {
        name: (counts.get(name), expected)
        for name, expected in DECLARED_TOOL_COUNTS.items()
        if counts.get(name) != expected
    }
    assert not wrong, (
        f"these modules offer a different number of tools than the item "
        f"states, as (found, stated): {wrong}"
    )


def test_the_modules_are_listed_alphabetically():
    """s1.1. The module names are listed alphabetically."""
    if not MODULES.is_dir():
        pytest.skip("no modules are installed at ~/.aethelark/modules/")
    bus = ModuleBus()
    bus.discover()
    listed = _known_tools(bus)
    assert listed == sorted(listed), (
        "the actions are not named in alphabetical order, so the line built "
        "from them cannot be either"
    )


# --------------------------------------------------------------------------
# 1.2 a machine with no modules at all
# --------------------------------------------------------------------------

def test_a_machine_with_no_modules_still_starts(tmp_path):
    """s1.2. The absence of modules is never reported as a fault and never
    blocks start-up."""
    bus = _bus(tmp_path)
    assert _known_tools(bus) == [], (
        "an empty machine reported modules it does not have"
    )


def test_asking_an_empty_machine_for_an_action_refuses_rather_than_crashing(tmp_path):
    """s1.2. Absence is not a fault: the eagle is still able to answer, it just
    has nothing to answer with."""
    bus = _bus(tmp_path)
    result = bus.invoke("anything_at_all", {})
    assert result.ok is False
    assert (result.message or "").strip(), "an empty machine refused silently"


# --------------------------------------------------------------------------
# 1.3 declared but not installed
# --------------------------------------------------------------------------

def test_a_declared_module_whose_program_is_absent_is_reported_not_hidden(tmp_path):
    """s1.3. A module that is described to the eagle but whose program is not
    actually installed is reported, not hidden."""
    _write(tmp_path, "ghost.toml", GOOD % ("ghost", "some-cli-that-is-not-here"))
    bus = _bus(tmp_path, which=lambda b: None)
    assert "ghost_go" in _known_tools(bus), (
        "a declared module whose program is missing was hidden entirely rather "
        "than reported"
    )


def test_an_absent_modules_action_says_it_is_not_installed(tmp_path):
    """s1.3, and s2.2's wording. Asking it to do something says so."""
    _write(tmp_path, "ghost.toml", GOOD % ("ghost", "some-cli-that-is-not-here"))
    bus = _bus(tmp_path, which=lambda b: None)
    result = bus.invoke("ghost_go", {})
    assert result.ok is False
    assert result.data["installed"] is False
    assert "not installed" in result.message


def test_an_installed_module_beside_an_absent_one_still_works(tmp_path):
    """s1.3. If some modules are installed and others are not, the installed
    ones are listed with their tool counts and the missing ones appended."""
    _write(tmp_path, "ghost.toml", GOOD % ("ghost", "some-cli-that-is-not-here"))
    _write(tmp_path, "real.toml", GOOD % ("real", sys.executable))
    bus = ModuleBus(
        manifest_dirs=[tmp_path],
        which=lambda b: None if b == "some-cli-that-is-not-here" else b,
    )
    bus.discover()
    tools = _known_tools(bus)
    assert "real_go" in tools and "ghost_go" in tools, (
        f"an absent module beside an installed one changed what was found: "
        f"{tools}"
    )
    assert bus.invoke("real_go", {}).ok is True, (
        "the installed module stopped working because an absent one was "
        "declared beside it"
    )


# --------------------------------------------------------------------------
# 1.4 a corrupt description file
# --------------------------------------------------------------------------

def test_a_corrupt_description_file_is_skipped(tmp_path):
    """s1.4. A module whose description file is corrupt is skipped."""
    _write(tmp_path, "broken.toml", 'key = "broken"\nthis is not = = valid toml [[[\n')
    bus = _bus(tmp_path)
    assert not [t for t in _known_tools(bus) if t.startswith("broken_")], (
        "a module with a corrupt description file was loaded anyway"
    )


def test_the_modules_beside_a_corrupt_one_stay_available(tmp_path):
    """s1.4. The eagle still starts, every other module still loads. Verified
    with a deliberately malformed file: the two valid modules beside it stayed
    available."""
    _write(tmp_path, "broken.toml", 'key = "broken"\nthis is not = = valid toml [[[\n')
    _write(tmp_path, "one.toml", GOOD % ("one", sys.executable))
    _write(tmp_path, "two.toml", GOOD % ("two", sys.executable))
    bus = _bus(tmp_path)
    tools = _known_tools(bus)
    assert "one_go" in tools and "two_go" in tools, (
        f"a corrupt file beside two valid modules took them down too: {tools}"
    )
    assert bus.invoke("one_go", {}).ok is True
    assert bus.invoke("two_go", {}).ok is True


# --------------------------------------------------------------------------
# 1.5 the list is read once
# --------------------------------------------------------------------------

def test_installing_a_module_does_not_change_anything_until_the_next_start(tmp_path):
    """s1.5 (inferred). The list of modules is read once, when the eagle
    starts. Installing a module does not change what the eagle can do until the
    next start-up."""
    _write(tmp_path, "one.toml", GOOD % ("one", sys.executable))
    bus = _bus(tmp_path)
    assert "one_go" in _known_tools(bus)
    _write(tmp_path, "late.toml", GOOD % ("late", sys.executable))
    assert "late_go" not in _known_tools(bus), (
        "a module installed after start-up became available without a restart"
    )


def test_a_module_installed_before_the_next_start_is_picked_up(tmp_path):
    """s1.5 (inferred). The control for the test above: the module is found on
    the next start-up, so what was proved there is the timing and not that the
    module was unreadable."""
    _write(tmp_path, "one.toml", GOOD % ("one", sys.executable))
    _bus(tmp_path)
    _write(tmp_path, "late.toml", GOOD % ("late", sys.executable))
    restarted = _bus(tmp_path)
    assert "late_go" in _known_tools(restarted), (
        "a module present at start-up was still not found"
    )


# --------------------------------------------------------------------------
# Untestable, recorded rather than written
# --------------------------------------------------------------------------

UNTESTABLE = {
    "1.1 (the printout)": (
        "the exact preflight line, 'domain modules OK — a3d (18 tools), ...'. "
        "The specification names the preflight printout as one of four "
        "surfaces a test author can observe, but neither document says how to "
        "run it, and the installed aethelark CLI offers only on, off, log, "
        "start and geo. The counts and the ordering behind the line are "
        "asserted above; the line's wording, its tick and its status word are "
        "not reachable. Needs a documented preflight entry point."
    ),
    "1.2 / 1.3 (the wording)": (
        "the details 'none declared' and 'declared but not installed: <name>' "
        "belong to the same unreachable printout. The behaviour behind both is "
        "asserted above."
    ),
    "1.4 (the console line)": (
        "'a line is written to the console naming the offending file and the "
        "reason'. The skipping is asserted above; the console line needs the "
        "same entry point."
    ),
}


def test_the_untestable_items_in_this_section_are_recorded():
    """Not a specification item. Keeps what this section cannot reach visible
    in the run rather than silently absent."""
    assert len(UNTESTABLE) == 3
