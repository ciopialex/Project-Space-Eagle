"""A module key names a directory, and nothing checked what it named.

`ModuleManager` installs a module by computing `self.root / manifest.key` and
calling `shutil.rmtree` on it to replace any previous version. `remove()` does
the same. A path join has no opinion about what it is given:

    key = "/etc"     ->  destination = /etc              ->  rmtree("/etc")
    key = ".."       ->  destination = ~/.aethelark      ->  rmtree on it
    key = "../.."    ->  destination = ~                 ->  the home directory

`load_manifest` accepted any non-empty string as a key. Install is
signature-gated, so reaching it needs a bundle signed with the root key or the
`--allow-unsigned` developer flag — but a signature proves who built a bundle,
not that its manifest is sane, and `remove()` is gated by nothing at all. A
plain typo (`key = "a3d/v2"`) was a silent install into the wrong place rather
than an error.

This is the first criterion CLAUDE.md gives for earning a test: "the failure is
irreversible or physically expensive... you cannot recover by noticing later."
A recursive delete of a directory chosen by a string in a file is exactly that,
and the module factory is the stated direction — third-party manifests are the
point, so the key stops being something only this repo writes.

Two layers are pinned, because they fail differently. The parser refuses a bad
key so no manifest can carry one; the path join refuses to leave the modules
directory so a recursive delete never relies on validation that happened
somewhere else.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus.bundle import BundleError, ModuleManager  # noqa: E402
from core.module_bus.manifest import load_manifest  # noqa: E402

#: Keys that resolve outside the modules directory, or to the directory itself.
HOSTILE = [
    "..",                      # the .aethelark directory
    "../..",                   # the user's home
    "/etc",                    # an absolute path replaces the join entirely
    "../../../tmp/pwned",
    "a3d/../../..",
    ".",                       # the modules root itself
    "",
]

#: Keys that are merely wrong rather than dangerous, and must still be refused:
#: a key is also a Gemini function name and a CSS attribute value.
MALFORMED = ["a3d/v2", "a 3d", "a3d\\v2", "-leading", "a3d!", "a" * 200,
             "../a3d", "a3d\x00"]


def _write_manifest(tmp_path: Path, key: str) -> Path:
    path = tmp_path / "manifest.toml"
    path.write_text(textwrap.dedent(f'''
        key = "{key}"
        binary = "/bin/true"
        [[tools]]
        name = "noop"
        description = "does nothing"
        argv = ["noop"]
    '''), encoding="utf-8")
    return path


# ── the parser refuses it, so no manifest can carry one ─────────────────────

@pytest.mark.parametrize("key", HOSTILE + MALFORMED)
def test_a_manifest_with_a_bad_key_does_not_load(tmp_path, key):
    if not key:
        pytest.skip("an empty key was already refused, for a different reason")
    with pytest.raises(ValueError):
        load_manifest(_write_manifest(tmp_path, key))


@pytest.mark.parametrize("key", ["a3d", "atrade", "alaw", "a", "mod_2", "x-y", "A3D"])
def test_an_ordinary_key_still_loads(tmp_path, key):
    """The guard has to stay narrow enough to accept real modules."""
    manifest = load_manifest(_write_manifest(tmp_path, key))
    assert manifest.key == key


def test_the_three_shipped_manifests_still_load():
    """The ones that actually exist, not just synthetic ones."""
    root = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "manifests"
    for path in sorted(root.glob("*.toml")):
        manifest = load_manifest(path)
        assert manifest.key, f"{path.name} lost its key"


# ── and the delete refuses to leave the modules directory ───────────────────
#
# EVERY ModuleManager below is rooted in tmp_path, never on the real
# ~/.aethelark/modules, and that is not tidiness.
#
# Writing this file the other way destroyed the author's module installation.
# The mutation check that proves these assertions can fail disables the guard
# and runs the suite — and with the guard disabled, `remove("..")` on the real
# root resolves to ~/.aethelark and deletes it. The test demonstrated the bug
# by causing it.
#
# A test for a destructive operation must be unable to destroy anything when
# the code under test is broken, because a broken guard is precisely the state
# the test exists to detect.


@pytest.fixture()
def manager(tmp_path):
    """A ModuleManager whose root is a scratch directory."""
    root = tmp_path / "modules"
    root.mkdir()
    (root / "a3d").mkdir()
    return ModuleManager(root=root)


@pytest.mark.parametrize("key", HOSTILE)
def test_remove_refuses_a_key_that_escapes_the_modules_directory(manager, key):
    """`remove()` is gated by nothing else at all.

    It has no caller today, which is why this never bit — but it is public API
    on ModuleManager, and `shutil.rmtree` is two lines below the join.
    """
    with pytest.raises(BundleError):
        manager.remove(key)


@pytest.mark.parametrize("key", HOSTILE)
def test_the_install_destination_is_contained_too(manager, key):
    """Install computes the same path and rmtrees it to replace a version."""
    with pytest.raises(BundleError):
        manager._module_dir(key)


def test_nothing_outside_the_root_was_touched(manager, tmp_path):
    """The assertion that would have caught what this file once did.

    A sibling of the modules root stands in for ~/.aethelark: if a hostile key
    escapes, this is what a recursive delete reaches first.
    """
    outside = tmp_path / "please_do_not_delete"
    outside.mkdir()
    (outside / "evidence.txt").write_text("still here", encoding="utf-8")

    for key in HOSTILE:
        try:
            manager.remove(key)
        except BundleError:
            pass

    assert outside.is_dir(), "a hostile key deleted a directory outside the root"
    assert (outside / "evidence.txt").read_text(encoding="utf-8") == "still here"
    assert manager.root.is_dir(), "the modules root itself was deleted"


def test_a_real_key_resolves_inside_the_modules_directory(manager):
    resolved = manager._module_dir("a3d")
    assert resolved.is_relative_to(manager.root.resolve())
    assert resolved.name == "a3d"


def test_removing_a_module_that_is_not_installed_is_not_an_error(manager):
    """A refusal and a no-op are different answers, and callers rely on it."""
    assert manager.remove("definitely-not-installed-xyz") is False


def test_removing_a_real_module_still_works(manager):
    """The guard must not make the feature unusable."""
    assert (manager.root / "a3d").is_dir()
    assert manager.remove("a3d") is True
    assert not (manager.root / "a3d").exists()
