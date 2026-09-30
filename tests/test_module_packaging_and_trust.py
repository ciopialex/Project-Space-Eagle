"""Acceptance tests for BEHAVIOR_SPEC.md section 14 — module packaging and trust.

Written clean-room from the specification. No source was read.

Section 14 carries its own warning: "No user-facing action reaches any of this
today — see Open Question 11", and Open Question 11 says the install path exists
but "there is no command, no button and no dashboard action that installs a
module package. The only callers are tests." Neither TEST_SETUP.md nor
MODULE_CONTRACT.md names that entry point, so the six items that are strictly
about unpacking a signed archive (14.1-14.5, 14.7's archive half) are recorded
at the foot of this file. Guessing an installer's import path would produce a
file of permanent skips, not a test.

What is reachable is the seam one step later: the manifest a package leaves
behind and the module the bus builds from it. Four items have an observable
half there, and those are the tests below.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import ModuleBus  # noqa: E402

import pytest  # noqa: E402


def _script(folder, name, sentinel):
    """A real program that prints a sentinel, so 'it never ran' is measurable."""
    path = folder / name
    path.write_text(textwrap.dedent(f"""
        print("{sentinel}")
    """))
    return path


def _bus(folder):
    bus = ModuleBus(manifest_dirs=[folder], which=lambda b: b)
    try:
        bus.discover()
    except Exception as exc:  # pragma: no cover - this is itself a finding
        pytest.fail(
            f"discovering the modules in {folder} raised {exc!r}; a package the "
            f"harness will not accept must be refused, not thrown"
        )
    return bus


# --------------------------------------------------------------------------
# 14.6 — a package carrying nothing that says what module it is is refused.
# --------------------------------------------------------------------------


def _offered(bus) -> str:
    """Everything the eagle is told about what it may call.

    The refusal names no tools; the list of what does exist travels in
    `guidance`. A test that reads only one of the two is measuring half the
    answer.
    """
    answer = bus.invoke("no_such_tool", {})
    return f"{answer.message}\n{answer.guidance or ''}"


def test_a_module_that_does_not_say_which_module_it_is_is_refused(tmp_path):
    """s14.6. A package carrying no identity is refused, not adopted. (inferred)"""
    named = tmp_path / "named"
    named.mkdir()
    script = _script(named, "run.py", "NAMEDRAN")
    (named / "m.toml").write_text(textwrap.dedent(f"""
        key = "m"
        binary = "{sys.executable}"
        description = "a module that says what it is"
        output = "text"

        [[tools]]
        name = "zorkmid"
        description = "does the thing"
        argv = ["{script}"]
    """))

    # Guard: prove the instrument can see an action being offered at all. If a
    # listing never names any action, the absence assertion below is worthless.
    # It reads guidance as well as message because that is where the bus puts
    # the list -- core/tool_result.py reserves `guidance` for the next step a
    # model should take, and "here is what does exist" is exactly that. Reading
    # only `message` made the assertion below vacuous: it never contains a tool
    # name, so it would have passed even while a rogue package was offered.
    listing = _offered(_bus(named))
    assert "zorkmid" in listing, (
        "an identified module's action does not appear in the list of offered "
        "actions, so this test cannot tell a refused package from a listed one"
    )

    anonymous = tmp_path / "anonymous"
    anonymous.mkdir()
    rogue = _script(anonymous, "run.py", "ANONRAN")
    (anonymous / "m.toml").write_text(textwrap.dedent(f"""
        binary = "{sys.executable}"
        description = "a module that never says what it is"
        output = "text"

        [[tools]]
        name = "zorkmid"
        description = "does the thing"
        argv = ["{rogue}"]
    """))

    bus = _bus(anonymous)
    assert "zorkmid" not in _offered(bus), (
        "an unidentified package's action is being offered to the eagle"
    )

    result = bus.invoke("m_zorkmid", {})
    assert result.ok is False
    assert "ANONRAN" not in result.message, (
        "the unidentified package's program ran"
    )


# --------------------------------------------------------------------------
# 14.7 — a package that fails any check leaves nothing behind.
# --------------------------------------------------------------------------


def test_a_module_that_fails_its_checks_leaves_no_half_installed_action_behind(tmp_path):
    """s14.7. A refused package leaves no usable remnant, and does not poison its neighbours. (inferred)"""
    good = _script(tmp_path, "good.py", "GOODRAN")
    bad = _script(tmp_path, "bad.py", "BADRAN")

    (tmp_path / "good.toml").write_text(textwrap.dedent(f"""
        key = "good"
        binary = "{sys.executable}"
        description = "a well formed module"
        output = "text"

        [[tools]]
        name = "fine"
        description = "does the thing"
        argv = ["{good}"]
    """))

    # A package the harness cannot read: the manifest is not valid TOML.
    (tmp_path / "broken.toml").write_text(textwrap.dedent(f"""
        key = "bad
        binary = "{sys.executable}"
        [[tools]
        name = "leftover"
        argv = ["{bad}"]
    """))

    bus = _bus(tmp_path)

    # Guard: the neighbour proves discovery actually ran. Without this, every
    # assertion below would pass on a bus that discovered nothing at all.
    healthy = bus.invoke("good_fine", {})
    assert healthy.ok is True, (
        f"one unreadable manifest took the healthy module down with it: "
        f"{healthy.message!r}"
    )
    assert "GOODRAN" in healthy.message

    result = bus.invoke("bad_leftover", {})
    assert result.ok is False
    assert "BADRAN" not in result.message, (
        "an action from a package that failed its checks is live"
    )


# --------------------------------------------------------------------------
# 14.8 — re-installing replaces rather than merges.
# --------------------------------------------------------------------------


def test_reinstalling_a_module_does_not_leave_the_removed_action_behind(tmp_path):
    """s14.8. A tool removed between two versions does not survive the upgrade. (inferred)"""
    stay = _script(tmp_path, "stay.py", "STAYRAN")
    legacy = _script(tmp_path, "legacy.py", "LEGACYRAN")

    manifest = tmp_path / "m.toml"
    manifest.write_text(textwrap.dedent(f"""
        key = "m"
        binary = "{sys.executable}"
        description = "version one"
        output = "text"

        [[tools]]
        name = "stay"
        description = "kept in both versions"
        argv = ["{stay}"]

        [[tools]]
        name = "legacy"
        description = "dropped in version two"
        argv = ["{legacy}"]
    """))

    bus = _bus(tmp_path)

    # Guard: the removed action must genuinely have worked before the upgrade,
    # or "it is gone afterwards" proves nothing.
    before = bus.invoke("m_legacy", {})
    assert before.ok is True, (
        f"the action this test is about was never live to begin with: "
        f"{before.message!r}"
    )
    assert "LEGACYRAN" in before.message

    manifest.write_text(textwrap.dedent(f"""
        key = "m"
        binary = "{sys.executable}"
        description = "version two"
        output = "text"

        [[tools]]
        name = "stay"
        description = "kept in both versions"
        argv = ["{stay}"]
    """))

    try:
        bus.discover()
    except Exception as exc:  # pragma: no cover - itself a finding
        pytest.fail(f"re-installing over a live module raised {exc!r}")

    upgraded = bus.invoke("m_stay", {})
    assert upgraded.ok is True, (
        f"the upgraded module does not answer at all: {upgraded.message!r}"
    )

    result = bus.invoke("m_legacy", {})
    assert result.ok is False, (
        "an action deleted in the new version survived the upgrade, so the "
        "install merged rather than replaced"
    )
    assert "LEGACYRAN" not in result.message


# --------------------------------------------------------------------------
# 14.9 — each module is isolated from every other.
#
# The dependency-version half of this item needs a real per-module environment
# and a real installer; it is at the foot of this file. The observable half —
# two modules that collide keep working independently — is here.
# --------------------------------------------------------------------------


def test_two_modules_declaring_the_same_action_both_keep_working(tmp_path):
    """s14.9. Modules are isolated from each other: a collision breaks neither. (inferred)"""
    one = _script(tmp_path, "one.py", "ONEMODULE")
    two = _script(tmp_path, "two.py", "TWOMODULE")

    for key, script in (("one", one), ("two", two)):
        (tmp_path / f"{key}.toml").write_text(textwrap.dedent(f"""
            key = "{key}"
            binary = "{sys.executable}"
            description = "module {key}"
            output = "text"

            [[tools]]
            name = "go"
            description = "an action both modules happen to name the same"
            argv = ["{script}"]
        """))

    bus = _bus(tmp_path)

    first = bus.invoke("one_go", {})
    second = bus.invoke("two_go", {})

    assert first.ok is True, f"module one stopped working: {first.message!r}"
    assert second.ok is True, f"module two stopped working: {second.message!r}"

    assert "ONEMODULE" in first.message
    assert "TWOMODULE" not in first.message, "module two answered for module one"
    assert first.data["module"] == "one"

    assert "TWOMODULE" in second.message
    assert "ONEMODULE" not in second.message, "module one answered for module two"
    assert second.data["module"] == "two"


# UNTESTABLE
#
# The shared missing seam for 14.1-14.5, 14.7's archive half and 14.10: no
# installer entry point is named anywhere in the two companion documents. §14's
# own preamble and Open Question 11 confirm the machinery exists and that "the
# only callers are tests" — but a clean-room author has no name to call, and
# inventing one (`core.module_installer.install(...)`) would produce a file that
# skips forever and certifies nothing. Each item below also names what else it
# would need beyond that entry point.
#
# 14.1 — an unsigned package is refused with an explanation naming the publisher
#     rule. Needs: the installer entry point, plus a documented way to build an
#     unsigned package. Also unstated: the exact refusal wording, so even with
#     the seam the "with an explanation that only packages signed by the
#     publisher are installed" half has no assertable target.
#
# 14.2 — a package whose signature does not verify is refused outright, and no
#     developer flag makes a tampered package installable. Needs: the installer
#     entry point, a signing key to produce a validly-signed package, and the
#     names of every developer/override flag the installer accepts — the item's
#     real content is the *absence* of a bypass, which cannot be shown without
#     the list of flags to try.
#
# 14.3 — the signature is checked before the package is unpacked. Needs: an
#     observable ordering. Even with the installer, the honest test is a package
#     whose signature fails and whose contents would be observable if unpacked
#     (a canary path in the destination). No documented way to give the
#     installer a destination or to inspect what it touched mid-run.
#
# 14.4 — with no way to check signatures, installation is refused rather than
#     allowed. Needs: a documented way to remove or break the verifier for the
#     duration of one call. This is the highest-value item in the section — a
#     missing check reading as a passed check is the classic fail-open — and it
#     is the one furthest from any documented surface.
#
# 14.5 — an entry writing outside its own folder, an absolute path, or a symlink
#     is refused. Needs: the installer entry point plus a package-building
#     helper. Note the adjacent, unspecified case: TEST_SETUP.md §7 says a
#     manifest binary written as a path is resolved inside the module's own
#     folder, so `binary = "../../../usr/bin/env"` is the same escape one layer
#     later. §14 does not say whether that is refused, so no test is written for
#     it; it wants a ruling.
#
# 14.9 (dependency half) — "each module gets its own isolated set of
#     dependencies; two modules with conflicting requirements both keep
#     working." Needs: a per-module environment created by the installer. The
#     test above covers only that two modules do not shadow each other through
#     the bus; it does not and cannot show that module A importing library v1
#     and module B importing library v2 both succeed, because nothing documented
#     creates the two environments.
#
# 14.10 — "a module whose setup did not finish is reported as not ready, and
#     asking it to do something says it is installed but not yet provisioned."
#     Needs: a documented way to create a module in the half-provisioned state.
#     A manifest whose binary is not on PATH is NOT that state — §2.2 already
#     specifies that case with different fixed wording ("The <name> module is
#     not installed on this machine..." plus "Install its CLI ... and make sure
#     it is on PATH"). With no provisioning marker to write, the two states are
#     indistinguishable from outside, and a test asserting the §14.10 wording
#     would be asserting against §2.2's scenario rather than §14.10's. See the
#     report: §14.10 and §2.2 need a ruling on which sentence a manifest-present
#     / binary-absent module gets.
