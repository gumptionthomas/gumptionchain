import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

import gumptionchain.signing_key as signing_key_module
from gumptionchain.exceptions import InvalidKeyError
from gumptionchain.signing_key import SigningKey


def test_generate_ed25519_round_trips_and_signs():
    sk = SigningKey.generate_ed25519()
    assert sk.private_key is not None
    data = b'hello consensus'
    sig = sk.sign(data)
    pub = SigningKey(b64ks=sk.public_key_b64)
    assert pub.private_key is None
    assert pub.validate_signature(data, sig) is True
    assert pub.validate_signature(b'tampered', sig) is False


def test_ed25519_address_is_stable_and_tagged():
    sk = SigningKey.generate_ed25519()
    a1 = sk.address
    a2 = SigningKey(b64ks=sk.public_key_b64).address
    assert a1 == a2
    assert a1.startswith('GC') and a1.endswith('GC')


def test_ed25519_verify_does_not_use_pyca(monkeypatch):
    # The Ed25519 verify DECISION must go through the vendored module, never
    # pyca. Patch the CONCRETE runtime public-key class (not the ABC, whose
    # method the Rust class shadows) to explode; a correct signature must still
    # verify, proving the consensus path never calls pyca's verify.
    msg = 'pyca Ed25519 verify must not be on the path'

    def boom(self, *a, **k):
        raise AssertionError(msg)

    sk = SigningKey.generate_ed25519()
    monkeypatch.setattr(type(sk.public_key), 'verify', boom)
    sig = sk.sign(b'x')
    assert SigningKey(b64ks=sk.public_key_b64).validate_signature(b'x', sig)


def test_from_ed25519_seed_is_deterministic():
    seed = bytes(range(32))
    assert (
        SigningKey.from_ed25519_seed(seed).address
        == SigningKey.from_ed25519_seed(seed).address
    )


def test_from_ed25519_seed_bad_length_raises_invalidkey():
    with pytest.raises(InvalidKeyError):
        SigningKey.from_ed25519_seed(b'\x00' * 31)


def test_signingkey_default_is_ed25519():
    sk = SigningKey()  # no-arg default must now be Ed25519
    assert isinstance(sk.key, Ed25519PrivateKey)


def test_no_rsa_in_signing_key_module():
    src = __import__('inspect').getsource(signing_key_module)
    assert 'rsa' not in src.lower()
    assert 'RSAPrivateKey' not in src
