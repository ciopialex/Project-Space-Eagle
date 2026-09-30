"""CBC with no MAC is a ciphertext anyone on the path can edit.

The old layer was AES-256-CBC with the key set to SHA256(6-char PIN + a fixed
salt) and no authentication at all. Three things follow from that, and each has
a test below.

Malleability: flipping a byte of ciphertext flips the corresponding plaintext
byte in the next block. Nothing detected it.

A padding oracle: _decrypt returned the plaintext or None, and the route turned
None into HTTP 400. That is a distinguishable answer to "was the padding
valid", repeated as often as an attacker likes, which is the whole oracle.

And the key was 29.7 bits of human-typed PIN, which /api/device-login then
mailed back to the client in cleartext.

What this does NOT fix, stated here so nobody reads more into it: over plain
HTTP an attacker on the wire sees the pairing exchange and gets the secret.
Client-side crypto cannot fix that; TLS can. This kills the oracle, makes
tampering detectable, and stops the long-lived key from being six characters.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import crypto  # noqa: E402


@pytest.fixture()
def keys():
    return crypto.derive(crypto.new_secret())


def test_a_round_trip_returns_exactly_what_went_in(keys):
    for text in ("hello", "", "unicode: ✅ 中文 🦅", "x" * 5000):
        assert crypto.decrypt(keys, crypto.encrypt(keys, text)) == text


def test_a_secret_is_256_bits_of_randomness():
    a, b = crypto.new_secret(), crypto.new_secret()
    assert a != b
    assert len(base64.urlsafe_b64decode(a + "==")) >= 32


def test_two_encryptions_of_the_same_text_differ():
    """A fresh IV per message, or the ciphertext leaks equality."""
    k = crypto.derive(crypto.new_secret())
    assert crypto.encrypt(k, "same") != crypto.encrypt(k, "same")


def test_a_flipped_ciphertext_byte_is_rejected(keys):
    """The malleability test. This must raise, not decrypt to garbage."""
    raw = bytearray(base64.b64decode(crypto.encrypt(keys, "transfer 10")))
    raw[-3] ^= 0x01
    with pytest.raises(crypto.BadMessage):
        crypto.decrypt(keys, base64.b64encode(bytes(raw)).decode())


def test_a_flipped_iv_byte_is_rejected(keys):
    raw = bytearray(base64.b64decode(crypto.encrypt(keys, "transfer 10")))
    raw[2] ^= 0x01
    with pytest.raises(crypto.BadMessage):
        crypto.decrypt(keys, base64.b64encode(bytes(raw)).decode())


def test_a_truncated_message_is_rejected(keys):
    raw = base64.b64decode(crypto.encrypt(keys, "hello"))
    with pytest.raises(crypto.BadMessage):
        crypto.decrypt(keys, base64.b64encode(raw[:20]).decode())


def test_garbage_is_rejected_as_one_kind_of_failure(keys):
    """No oracle: bad base64, bad MAC and bad padding are indistinguishable."""
    for junk in ("", "!!!!", "AAAA", base64.b64encode(b"\x00" * 48).decode()):
        with pytest.raises(crypto.BadMessage):
            crypto.decrypt(keys, junk)


def test_another_secret_cannot_read_the_message():
    a, b = crypto.derive(crypto.new_secret()), crypto.derive(crypto.new_secret())
    with pytest.raises(crypto.BadMessage):
        crypto.decrypt(b, crypto.encrypt(a, "private"))


def test_the_mac_key_and_the_cipher_key_are_different(keys):
    assert keys.cipher != keys.mac, "one key used for two jobs"
    assert len(keys.cipher) == 32 and len(keys.mac) == 32


def test_derivation_is_deterministic():
    s = crypto.new_secret()
    assert crypto.derive(s).cipher == crypto.derive(s).cipher


def test_the_mac_covers_the_iv_as_well_as_the_ciphertext(keys):
    """Encrypt-then-MAC over IV||ct. Moving the IV must be detected — the
    flipped-IV test proves detection; this proves the layout is what the
    JavaScript half will be written against."""
    raw = base64.b64decode(crypto.encrypt(keys, "x"))
    assert len(raw) >= 16 + 16 + 32
    assert (len(raw) - 16 - 32) % 16 == 0
