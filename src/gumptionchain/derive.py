from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 600_000


def _hkdf(ikm: bytes, info: str, length: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=b'', info=info.encode()
    ).derive(ikm)


def _pbkdf2(passphrase: str, salt: bytes, length: int) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    ).derive(passphrase.encode())


def derive_seed(prf_output: bytes, passphrase: str | None = None) -> bytes:
    """Derive a 32-byte Ed25519 seed from a WebAuthn PRF output.

    PRF-only by default; mixing in a passphrase (PBKDF2-stretched with a
    PRF-bound salt) yields a distinct, reproducible 2FA seed. Byte-identical
    to the JS gc-derive.deriveSeed.
    """
    if not prf_output:
        msg = 'derive_seed requires a non-empty PRF output'
        raise ValueError(msg)
    if passphrase:
        pass_salt = _hkdf(prf_output, 'gc-pass-salt-v1', 16)
        pk = _pbkdf2(passphrase, pass_salt, 32)
        return _hkdf(prf_output + pk, 'gc-seed-v1', 32)
    return _hkdf(prf_output, 'gc-seed-v1', 32)
