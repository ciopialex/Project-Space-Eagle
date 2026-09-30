"""Authenticated Encrypt-then-MAC (AES-256-CBC + HMAC-SHA256) for Dashboard sessions.

Wire layout:
    base64( IV[16] || Ciphertext[16n] || HMAC-SHA256(mac_key, IV || Ciphertext)[32] )

Keys are derived from a 256-bit random root secret using HKDF-SHA256.
Authentication is verified in constant time before any unpadding occurs,
eliminating CBC malleability and padding oracle vulnerabilities.
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import padding as sym_pad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


class BadMessage(Exception):
    """Raised on any decryption, authentication, or formatting error."""
    pass


@dataclass(frozen=True)
class Keys:
    cipher: bytes
    mac: bytes


def new_secret() -> str:
    """Generates 256 bits of cryptographic entropy encoded as URL-safe base64."""
    return secrets.token_urlsafe(32)


def derive(secret: str) -> Keys:
    """Derives independent 32-byte encryption and MAC keys from root secret."""
    secret_bytes = secret.encode("utf-8")
    
    hkdf_cipher = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"aethelark-cipher-v2",
    )
    cipher_key = hkdf_cipher.derive(secret_bytes)

    hkdf_mac = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"aethelark-mac-v2",
    )
    mac_key = hkdf_mac.derive(secret_bytes)

    return Keys(cipher=cipher_key, mac=mac_key)


def encrypt(keys: Keys, plaintext: str) -> str:
    """Encrypts plaintext with AES-256-CBC and appends HMAC-SHA256 tag."""
    iv = os.urandom(16)
    padder = sym_pad.PKCS7(128).padder()
    padded_data = padder.update(plaintext.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(keys.cipher), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    # Encrypt-then-MAC over IV || Ciphertext
    payload = iv + ciphertext
    tag = hmac.new(keys.mac, payload, hashlib.sha256).digest()

    return base64.b64encode(payload + tag).decode("utf-8")


def decrypt(keys: Keys, blob: str) -> str:
    """Verifies HMAC tag and decrypts AES-256-CBC ciphertext."""
    if not blob:
        raise BadMessage("Empty message blob")

    try:
        raw = base64.b64decode(blob)
    except Exception as e:
        raise BadMessage(f"Invalid base64 encoding: {e}") from e

    # Minimum length: 16 (IV) + 16 (minimum PKCS7 block) + 32 (HMAC tag) = 64 bytes
    if len(raw) < 64:
        raise BadMessage("Message shorter than minimum framing")

    payload = raw[:-32]
    expected_tag = raw[-32:]

    # Constant-time MAC verification
    computed_tag = hmac.new(keys.mac, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(computed_tag, expected_tag):
        raise BadMessage("Authentication tag mismatch")

    iv = payload[:16]
    ciphertext = payload[16:]

    if len(ciphertext) % 16 != 0:
        raise BadMessage("Ciphertext length is not a multiple of 16")

    try:
        cipher = Cipher(algorithms.AES(keys.cipher), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(ciphertext) + decryptor.finalize()

        unpadder = sym_pad.PKCS7(128).unpadder()
        plaintext_bytes = unpadder.update(padded_data) + unpadder.finalize()
        return plaintext_bytes.decode("utf-8")
    except Exception as e:
        raise BadMessage(f"Decryption failed: {e}") from e
