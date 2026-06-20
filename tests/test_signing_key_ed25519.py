import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gumptionchain.exceptions import InvalidKeyError
from gumptionchain.signing_key import KEY_SIZE, SigningKey, b64encode


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


def test_rsa_still_signs_and_verifies():
    sk = SigningKey()  # default is RSA
    sig = sk.sign(b'data')
    assert SigningKey(b64ks=sk.public_key_b64).validate_signature(b'data', sig)


def test_rsa_degenerate_exponent_still_rejected():
    # audit WC2 regression: an e=3 RSA key must still be rejected on import.
    weak = rsa.generate_private_key(public_exponent=3, key_size=KEY_SIZE)
    der = weak.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(InvalidKeyError):
        SigningKey(b64ks=b64encode(der))


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
