"""Demonstration tests for the 2026-06-02 wallet/crypto threat-model audit.

Each test below demonstrates one audit finding and asserts the DESIRED
post-fix behavior. While a finding is still open it carries
``@pytest.mark.xfail(strict=True)`` — strict mode means the test MUST fail
today (the gap is real) and forces the marker's removal at remediation (the
xfail would otherwise "unexpectedly pass" and error the suite). Once a finding
is remediated the marker is dropped and the test becomes a passing
regression; tests below may therefore be a mix of strict-xfail (open) and
plain regression (closed). See
docs/superpowers/audits/2026-06-02-wallet-crypto-audit.md.

The audit found 0 exploitable findings (0 Critical / 0 High / 0 Medium); the
two Low items below are non-exploitable defense-in-depth / hygiene residuals,
recorded for tracked remediation. No test performs real key cracking, brute
force, or RSA/AES cryptanalysis.
"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cancelchain.exceptions import InvalidKeyError
from cancelchain.wallet import KEY_SIZE, Wallet, b64encode


def test_wc1_bespoke_encrypt_decrypt_removed():
    """WC1 (Low) — REMEDIATED. The RSA-OAEP + AES-GCM hybrid `Wallet.encrypt`
    / `Wallet.decrypt` had no production caller after PR #111 replaced the
    challenge/response handshake (only tests referenced it). Unreachable
    bespoke crypto is a re-introduction hazard and standing surface; it was
    removed. This regression asserts it stays gone.
    """
    assert not hasattr(Wallet, 'encrypt')
    assert not hasattr(Wallet, 'decrypt')


@pytest.mark.xfail(
    strict=True,
    reason='WC2: Wallet.__init__ validates key_size but not the public '
    'exponent, so a degenerate e=3 key is accepted; flips to passing once an '
    'exponent check (e == 65537) rejects it.',
)
def test_wc2_import_rejects_degenerate_exponent():
    """WC2 (Low) — `Wallet.__init__` checks `isinstance(RSA*)` + `key_size ==
    3072` but not the public exponent (pyca does not enforce a minimum). A
    3072-bit `e=3` key loads and is accepted. Not exploitable today (pyca's
    strict PKCS#1 v1.5 verifier forecloses cube-root forgery, and a weak
    self-chosen key derives its own distinct address), but accepting
    non-standard exponents is unnecessary and inconsistent with the node's own
    e=65537 generation. The desired behavior is rejection on import.
    """
    weak_key = rsa.generate_private_key(public_exponent=3, key_size=KEY_SIZE)
    pub_b64 = b64encode(
        weak_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    with pytest.raises(InvalidKeyError):
        Wallet(b64ks=pub_b64)
