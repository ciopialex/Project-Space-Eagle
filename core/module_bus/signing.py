"""Ed25519 signatures over `.aem` bundles.

The SHA-256 written beside a bundle is an integrity check — it catches a
truncated download. It says nothing about who wrote the file, because whoever
wrote the file also wrote the checksum. That distinction stops being academic
the moment the shop serves downloads and `install_bundle` runs pip from a
requirement list found *inside* the archive: a substituted bundle installs
whatever it names.

What gets signed is a digest over the archive's contents rather than the file
on disk. Zip files are not byte-stable — timestamps, compression settings and
entry order all vary without the module changing — so signing the container
would produce a signature that breaks when nothing meaningful has. The digest
covers each entry's name and bytes, sorted, so renaming a file, editing a
manifest, or adding one is as detectable as replacing the payload.

The signature lives inside the archive and is excluded from its own digest, or
signing would change the thing being signed.
"""
from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

#: Where the detached signature sits inside the bundle.
SIGNATURE_ENTRY = "signature.bin"

#: Domain separation. A signature over a bundle should never be mistakable for
#: a signature over anything else this key ever signs.
_CONTEXT = b"aethelark.aem.v1"


def bundle_digest(bundle: Path | str) -> bytes:
    """A stable digest of what a bundle contains.

    Entry names are hashed alongside their bytes, and lengths are hashed before
    the values they describe: without that, a file called `ab` holding `c` and
    one called `a` holding `bc` would feed identical bytes into the hash.
    """
    digest = hashlib.sha256()
    digest.update(_CONTEXT)
    with zipfile.ZipFile(bundle) as archive:
        entries = sorted(i for i in archive.namelist() if i != SIGNATURE_ENTRY)
        for name in entries:
            raw_name = name.encode("utf-8")
            payload = archive.read(name)
            digest.update(len(raw_name).to_bytes(8, "big"))
            digest.update(raw_name)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.digest()


# ── keys ────────────────────────────────────────────────────────────────────

def write_private_key(key: Ed25519PrivateKey, path: Path | str) -> Path:
    """Write a signing key, readable only by its owner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(path, 0o600)
    return path


def load_private_key(path: Path | str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(),
                                             password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return key


def public_key_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw,
                            serialization.PublicFormat.Raw)


# ── signing and verifying ───────────────────────────────────────────────────

def sign_bundle(bundle: Path | str, private_key: Path | str) -> Path:
    """Sign `bundle` in place, adding `signature.bin` to the archive."""
    bundle = Path(bundle)
    key = load_private_key(private_key)
    signature = key.sign(bundle_digest(bundle))

    with zipfile.ZipFile(bundle, "a", zipfile.ZIP_DEFLATED) as archive:
        if SIGNATURE_ENTRY in archive.namelist():
            raise ValueError(f"{bundle.name} is already signed")
        archive.writestr(SIGNATURE_ENTRY, signature)
    return bundle


def bundle_signature(bundle: Path | str) -> bytes | None:
    """The signature carried by a bundle, or None if it carries none."""
    try:
        with zipfile.ZipFile(bundle) as archive:
            if SIGNATURE_ENTRY not in archive.namelist():
                return None
            return archive.read(SIGNATURE_ENTRY)
    except (zipfile.BadZipFile, OSError, KeyError):
        return None


def verify_bundle(bundle: Path | str, public_key: bytes) -> bool:
    """Whether this bundle was signed by the holder of `public_key`."""
    signature = bundle_signature(bundle)
    if not signature:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, bundle_digest(bundle))
    except (InvalidSignature, ValueError, OSError, zipfile.BadZipFile):
        return False
    return True
