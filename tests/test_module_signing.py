"""Proving a bundle came from Aethelark, not merely that it arrived intact.

The SHA-256 shipped beside a `.aem` is an integrity check: it catches a
truncated download. It proves nothing about authorship, because whoever wrote
the file also wrote the checksum. Once the shop is serving downloads and
`install_bundle` runs pip from a requirement list found *inside* the archive,
that gap is the whole attack: a substituted bundle installs whatever it likes.

An Ed25519 signature over the archive's contents closes it. The signature
covers every entry's name and bytes, so renaming a file, editing a manifest, or
adding one is as detectable as replacing the payload.

Verification happens before extraction. Checking after writing attacker-chosen
paths to disk is checking after the interesting part has already happened.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("cryptography",
                    reason="Ed25519 verification needs the cryptography package")

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from core.module_bus.bundle import BundleError, ModuleManager  # noqa: E402
from core.module_bus.signing import (  # noqa: E402
    SIGNATURE_ENTRY, bundle_digest, public_key_bytes, sign_bundle,
    verify_bundle, write_private_key,
)

MANIFEST = """\
key         = "demo"
binary      = "bin/demo"
description = "signed"
output      = "json"

[[tools]]
name        = "ping"
description = "x"
argv        = ["ping"]
"""


def _unsigned(path: Path, manifest: str = MANIFEST) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.toml", manifest)
        z.writestr("island/template.html",
                   '<div class="island-capsule">x</div>'
                   '<div class="island-glance">x</div>'
                   '<div class="island-expanded">x</div>')
        z.writestr("demo_backend/__init__.py", "V = 1\n")
    return path


@pytest.fixture
def keypair(tmp_path):
    private = Ed25519PrivateKey.generate()
    key_path = tmp_path / "root.key"
    write_private_key(private, key_path)
    return key_path, public_key_bytes(private.public_key())


# ── what the signature covers ───────────────────────────────────────────────

def test_the_digest_ignores_the_order_entries_happen_to_be_in(tmp_path):
    """Zip ordering is an artefact of how the archive was written, not part of
    what the module is."""
    first = tmp_path / "a.aem"
    second = tmp_path / "b.aem"
    with zipfile.ZipFile(first, "w") as z:
        z.writestr("manifest.toml", MANIFEST)
        z.writestr("island/template.html", "x")
    with zipfile.ZipFile(second, "w") as z:
        z.writestr("island/template.html", "x")
        z.writestr("manifest.toml", MANIFEST)

    assert bundle_digest(first) == bundle_digest(second)


def test_changing_a_byte_changes_the_digest(tmp_path):
    before = bundle_digest(_unsigned(tmp_path / "a.aem"))
    after = bundle_digest(_unsigned(tmp_path / "b.aem",
                                    MANIFEST.replace('"signed"', '"altered"')))
    assert before != after


def test_renaming_a_file_changes_the_digest(tmp_path):
    """Names are covered too: swapping which file is the manifest is a
    different module, not a different filename."""
    plain = tmp_path / "a.aem"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("manifest.toml", MANIFEST)
        z.writestr("island/template.html", "x")
    renamed = tmp_path / "b.aem"
    with zipfile.ZipFile(renamed, "w") as z:
        z.writestr("manifest.toml", MANIFEST)
        z.writestr("island/other.html", "x")

    assert bundle_digest(plain) != bundle_digest(renamed)


def test_adding_a_file_changes_the_digest(tmp_path):
    before = bundle_digest(_unsigned(tmp_path / "a.aem"))
    extra = _unsigned(tmp_path / "b.aem")
    with zipfile.ZipFile(extra, "a") as z:
        z.writestr("extra/payload.py", "import os\n")

    assert bundle_digest(extra) != before


def test_the_signature_itself_is_outside_what_it_signs(tmp_path, keypair):
    """Otherwise signing would change the thing being signed."""
    key_path, _ = keypair
    bundle = _unsigned(tmp_path / "demo.aem")
    before = bundle_digest(bundle)
    sign_bundle(bundle, key_path)

    assert bundle_digest(bundle) == before
    with zipfile.ZipFile(bundle) as z:
        assert SIGNATURE_ENTRY in z.namelist()


# ── verifying ───────────────────────────────────────────────────────────────

def test_a_bundle_we_signed_verifies(tmp_path, keypair):
    key_path, public = keypair
    bundle = sign_bundle(_unsigned(tmp_path / "demo.aem"), key_path)

    assert verify_bundle(bundle, public) is True


def test_a_bundle_signed_by_someone_else_does_not(tmp_path, keypair):
    """The property that matters commercially: anyone can make a bundle, only
    one key can make one this harness installs."""
    _, ours = keypair
    theirs = tmp_path / "theirs.key"
    write_private_key(Ed25519PrivateKey.generate(), theirs)
    bundle = sign_bundle(_unsigned(tmp_path / "demo.aem"), theirs)

    assert verify_bundle(bundle, ours) is False


def test_an_edited_archive_stops_verifying(tmp_path, keypair):
    key_path, public = keypair
    bundle = sign_bundle(_unsigned(tmp_path / "demo.aem"), key_path)
    assert verify_bundle(bundle, public) is True

    with zipfile.ZipFile(bundle, "a") as z:
        z.writestr("demo_backend/evil.py", "import os; os.system('id')\n")

    assert verify_bundle(bundle, public) is False


def test_an_unsigned_bundle_does_not_verify(tmp_path, keypair):
    _, public = keypair
    assert verify_bundle(_unsigned(tmp_path / "demo.aem"), public) is False


# ── installing ──────────────────────────────────────────────────────────────

def test_a_signed_bundle_installs(tmp_path, keypair, monkeypatch):
    import core.module_bus.bundle as bundle_mod

    key_path, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)
    signed = sign_bundle(_unsigned(tmp_path / "demo.aem"), key_path)

    root = tmp_path / "modules"
    manifest = ModuleManager(root).install_bundle(signed)

    assert manifest.key == "demo"
    assert (root / "demo" / "manifest.toml").is_file()


def test_a_tampered_bundle_is_refused(tmp_path, keypair, monkeypatch):
    import core.module_bus.bundle as bundle_mod

    key_path, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)
    signed = sign_bundle(_unsigned(tmp_path / "demo.aem"), key_path)
    with zipfile.ZipFile(signed, "a") as z:
        z.writestr("demo_backend/evil.py", "import os; os.system('id')\n")

    root = tmp_path / "modules"
    with pytest.raises(BundleError, match="signature"):
        ModuleManager(root).install_bundle(signed)

    assert not root.exists() or not any(root.iterdir()), (
        "a bundle that failed verification still left files on disk")


def test_an_unsigned_bundle_is_refused_by_default(tmp_path, keypair, monkeypatch):
    import core.module_bus.bundle as bundle_mod

    _, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)

    with pytest.raises(BundleError, match="signed|signature"):
        ModuleManager(tmp_path / "modules").install_bundle(
            _unsigned(tmp_path / "demo.aem"))


def test_an_unsigned_bundle_installs_when_the_caller_says_so(tmp_path, keypair,
                                                             monkeypatch):
    """Developing a module means installing one nobody has signed yet."""
    import core.module_bus.bundle as bundle_mod

    _, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)

    manifest = ModuleManager(tmp_path / "modules").install_bundle(
        _unsigned(tmp_path / "demo.aem"), allow_unsigned=True)

    assert manifest.key == "demo"


def test_allow_unsigned_does_not_mean_allow_forged(tmp_path, keypair,
                                                   monkeypatch):
    """A bundle carrying a signature that does not check out is not the same
    as one carrying none. The developer escape hatch must not launder it."""
    import core.module_bus.bundle as bundle_mod

    _, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)
    theirs = tmp_path / "theirs.key"
    write_private_key(Ed25519PrivateKey.generate(), theirs)
    forged = sign_bundle(_unsigned(tmp_path / "demo.aem"), theirs)

    with pytest.raises(BundleError, match="signature"):
        ModuleManager(tmp_path / "modules").install_bundle(
            forged, allow_unsigned=True)


def test_verification_happens_before_anything_is_written(tmp_path, keypair,
                                                         monkeypatch):
    """Checking a signature after extracting attacker-chosen paths is checking
    after the interesting part already happened."""
    import core.module_bus.bundle as bundle_mod

    _, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)

    hostile = tmp_path / "hostile.aem"
    with zipfile.ZipFile(hostile, "w") as z:
        z.writestr("manifest.toml", MANIFEST)
        z.writestr("../../../../tmp/pwned-by-unsigned.txt", "owned")

    with pytest.raises(BundleError) as caught:
        ModuleManager(tmp_path / "modules").install_bundle(hostile)

    assert "sign" in str(caught.value).lower(), (
        "an unsigned bundle should be refused for being unsigned, before its "
        f"paths are even considered; got {caught.value}")


# ── the shipped root key ────────────────────────────────────────────────────

def test_the_harness_ships_a_root_key_and_not_a_private_one():
    from core.module_bus import bundle as bundle_mod
    from core.module_bus import signing

    assert len(bundle_mod.ROOT_PUBLIC_KEY) == 32, (
        "an Ed25519 public key is 32 bytes")

    source = Path(signing.__file__).read_text()
    assert "PRIVATE KEY" not in source, "a private key is in the shipped source"


# ── the crypto library is not a dependency of reading a manifest ────────────

def test_the_bus_imports_without_a_crypto_library(tmp_path):
    """`core.module_bus` is how a module author reads a manifest and how the
    free harness discovers what is installed. Requiring an Ed25519
    implementation for that couples the whole bus to a library it only needs
    when a bundle is being installed."""
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parent.parent
    script = (
        "import builtins, sys\n"
        "real = builtins.__import__\n"
        "def blocked(name, *a, **k):\n"
        "    if name.split('.')[0] == 'cryptography':\n"
        "        raise ImportError('no cryptography here')\n"
        "    return real(name, *a, **k)\n"
        "builtins.__import__ = blocked\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "import core.module_bus as mb\n"
        "print(bool(mb.ModuleBus))\n"
    )
    done = subprocess.run([_sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=60)

    assert done.returncode == 0, (
        f"importing the bus without cryptography failed:\n{done.stderr[-600:]}")


def test_verification_without_a_crypto_library_refuses_rather_than_allows(
        tmp_path, keypair, monkeypatch):
    """Fail closed. A missing library must never read as a valid signature —
    that would turn an absent dependency into an open door."""
    import core.module_bus.bundle as bundle_mod

    key_path, public = keypair
    monkeypatch.setattr(bundle_mod, "ROOT_PUBLIC_KEY", public)
    signed = sign_bundle(_unsigned(tmp_path / "demo.aem"), key_path)

    def _no_crypto(*_a, **_k):
        raise ImportError("no cryptography here")

    monkeypatch.setattr(bundle_mod, "_verify", _no_crypto)

    with pytest.raises(BundleError):
        ModuleManager(tmp_path / "modules").install_bundle(signed)
