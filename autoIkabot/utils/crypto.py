"""Cryptographic primitives for account storage (Phase 1.4).

Key derivation: Argon2id via argon2-cffi
Cipher: AES-256-GCM via cryptography

Binary file format:
    salt (16 bytes) || nonce (12 bytes) || ciphertext || GCM tag (16 bytes)

The GCM tag is appended to ciphertext automatically by AESGCM.
The salt is not secret — it prevents precomputed attacks.

Argon2id is lazy-imported so the module can be imported even when
argon2-cffi is not installed (manual mode doesn't need encryption).
"""

import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from autoIkabot.config import (
    ARGON2_TIME_COST,
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_HASH_LEN,
    ARGON2_SALT_LEN,
    AES_NONCE_LEN,
    DOCKER_SECRET_PATH,
    MASTER_KEY_ENV_VAR,
)


def _import_argon2():
    """Lazy import of argon2-cffi's low-level module.

    Returns the argon2.low_level module.

    Raises
    ------
    ImportError
        If argon2-cffi is not installed, with a helpful message.
    """
    try:
        import argon2.low_level
        return argon2.low_level
    except ImportError:
        raise ImportError(
            "The 'argon2-cffi' package is required for master-password mode. "
            "Install it with: pip install argon2-cffi"
        )


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 256-bit encryption key from a password using Argon2id.

    Argon2id is memory-hard, making it resistant to GPU/ASIC brute-force
    attacks. The salt must be random and unique per file.

    Parameters
    ----------
    password : str
        The master password.
    salt : bytes
        A 16-byte random salt (stored alongside the ciphertext).

    Returns
    -------
    bytes
        A 32-byte (256-bit) derived key suitable for AES-256.
    """
    low_level = _import_argon2()
    return low_level.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=ARGON2_HASH_LEN,
        type=low_level.Type.ID,  # Argon2id
    )


def encrypt(plaintext: bytes, password: str) -> bytes:
    """Encrypt plaintext with a password.

    Generates a random salt and nonce, derives a key with Argon2id,
    and encrypts with AES-256-GCM. The result is a self-contained
    binary blob that can be written directly to disk.

    Parameters
    ----------
    plaintext : bytes
        The data to encrypt.
    password : str
        The master password.

    Returns
    -------
    bytes
        Binary blob: salt (16) || nonce (12) || ciphertext + GCM tag.
    """
    salt = os.urandom(ARGON2_SALT_LEN)
    nonce = os.urandom(AES_NONCE_LEN)
    key = derive_key(password, salt)

    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext, None)

    return salt + nonce + ciphertext_and_tag


def decrypt(blob: bytes, password: str) -> bytes:
    """Decrypt a blob produced by encrypt().

    Parameters
    ----------
    blob : bytes
        Binary blob: salt (16) || nonce (12) || ciphertext + GCM tag.
    password : str
        The master password.

    Returns
    -------
    bytes
        The decrypted plaintext.

    Raises
    ------
    cryptography.exceptions.InvalidTag
        If the password is wrong or the data has been tampered with.
    ValueError
        If the blob is too short to contain the required header fields.
    """
    # Minimum size: salt + nonce + GCM tag (no actual ciphertext)
    min_len = ARGON2_SALT_LEN + AES_NONCE_LEN + 16
    if len(blob) < min_len:
        raise ValueError(
            f"Encrypted blob too short ({len(blob)} bytes, minimum {min_len})"
        )

    salt = blob[:ARGON2_SALT_LEN]
    nonce = blob[ARGON2_SALT_LEN:ARGON2_SALT_LEN + AES_NONCE_LEN]
    ciphertext_and_tag = blob[ARGON2_SALT_LEN + AES_NONCE_LEN:]

    key = derive_key(password, salt)
    aesgcm = AESGCM(key)

    return aesgcm.decrypt(nonce, ciphertext_and_tag, None)


def get_master_password_from_environment() -> Optional[str]:
    """Try to read the master password from Docker secret or env var.

    Checks in priority order:
    1. Docker secret file at /run/secrets/autoikabot_key
    2. AUTOIKABOT_MASTER_KEY environment variable
    3. Returns None (caller should prompt interactively)

    Returns
    -------
    Optional[str]
        The master password string, or None if not found.
    """
    # Try Docker secret first (most secure — not visible in docker inspect)
    if DOCKER_SECRET_PATH.exists():
        try:
            password = DOCKER_SECRET_PATH.read_text(encoding="utf-8").strip()
            if password:
                return password
        except OSError:
            pass

    # Try environment variable (less secure but simpler for basic setups)
    env_val = os.environ.get(MASTER_KEY_ENV_VAR)
    if env_val:
        return env_val.strip()

    return None
