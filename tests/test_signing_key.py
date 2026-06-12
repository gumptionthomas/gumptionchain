import logging

import pytest

from gumptionchain.exceptions import InvalidKeyError, NoPrivateKeyError
from gumptionchain.schema import validate_address_format
from gumptionchain.signing_key import KEY_SIZE, SigningKey

PASSPHRASE = 'fourty-two'


def test_new():
    signing_key = SigningKey()
    assert signing_key is not None
    assert signing_key.private_key is not None
    assert signing_key.public_key is not None
    assert signing_key.address is not None


def test_signing_key_key_size_is_2048():
    assert KEY_SIZE == 2048
    signing_key = SigningKey()
    assert signing_key.private_key is not None
    assert signing_key.private_key.key_size == 2048
    # sign + verify round-trip at the new size
    message = b'browser-signing_key friendliness'
    signature = signing_key.sign(message)
    assert signing_key.validate_signature(message, signature) is True


def test_invalid_address():
    assert not validate_address_format('foo')
    assert not validate_address_format('f' * 47)
    assert not validate_address_format(1)


def test_create_invalid_key():
    with pytest.raises(InvalidKeyError):
        SigningKey(b64ks='foo')
    with pytest.raises(InvalidKeyError):
        SigningKey(b58ks='foo')
    with pytest.raises(InvalidKeyError):
        SigningKey(ks='foo')


def test_create_from_key(
    signing_key_private_key_b58,
    signing_key_public_key_b64,
    signing_key_address,
    signing_key_dict,
    signing_key_json,
):
    signing_key = SigningKey(b58ks=signing_key_private_key_b58)
    assert signing_key is not None
    assert signing_key.private_key_b58 == signing_key_private_key_b58
    assert signing_key.public_key_b64 == signing_key_public_key_b64
    assert signing_key.address == signing_key_address
    assert signing_key.to_dict() == signing_key_dict
    assert signing_key.to_json() == signing_key_json


def test_from(signing_key):
    d = signing_key.to_dict()
    new_signing_key = SigningKey.from_dict(d)
    assert signing_key == new_signing_key
    j = signing_key.to_json()
    new_signing_key = SigningKey.from_json(j)
    assert signing_key == new_signing_key


def test_file(tmp_path, signing_key):
    f = signing_key.to_file(signing_keydir=tmp_path)
    w = SigningKey.from_file(f)
    assert w == signing_key


def test_file_passphrase(tmp_path, signing_key):
    f = signing_key.to_file(signing_keydir=tmp_path, passphrase=PASSPHRASE)
    with pytest.raises(InvalidKeyError):
        SigningKey.from_file(f, passphrase=f'{PASSPHRASE}!')
    assert SigningKey.from_file(f, passphrase=PASSPHRASE) == signing_key


def test_export(signing_key):
    b58ks = signing_key.export_private_key_b58(passphrase=PASSPHRASE)
    assert SigningKey(b58ks=b58ks, passphrase=PASSPHRASE) == signing_key


def test_sign(signing_key, signing_key_signature_data, signing_key_signature):
    assert (
        signing_key.sign(signing_key_signature_data.encode())
        == signing_key_signature
    )
    assert signing_key.sign(b'foo') != signing_key_signature


def test_eq(signing_key):
    signing_key_copy = SigningKey(b58ks=signing_key.private_key_b58)
    assert signing_key == signing_key_copy
    new_signing_key = SigningKey()
    assert signing_key != new_signing_key


def test_repr(caplog, logger, signing_key, signing_key_address):
    with caplog.at_level(logging.INFO):
        logger.info(signing_key)
        assert f'SigningKey({signing_key_address})' in caplog.text


def test_signing_key_address_round_trips_through_pem(tmp_path):
    """Freshly generated signing_key → write PEM → read back → same address."""
    w1 = SigningKey()
    path = w1.to_file(signing_keydir=str(tmp_path))
    w2 = SigningKey.from_file(path)
    assert w1.address == w2.address


def test_signing_key_address_round_trips_through_b58():
    """Freshly generated signing_key → b58 → read back → same address."""
    w1 = SigningKey()
    w2 = SigningKey(b58ks=w1.private_key_b58)
    assert w1.address == w2.address


def test_signing_key_sign_verify_happy_path():
    w = SigningKey()
    sig = w.sign(b'hello world')
    assert w.validate_signature(b'hello world', sig) is True


def test_signing_key_verify_rejects_mutated_payload():
    w = SigningKey()
    sig = w.sign(b'hello world')
    assert w.validate_signature(b'hello WORLD', sig) is False


def test_signing_key_verify_rejects_garbage_signature():
    w = SigningKey()
    assert w.validate_signature(b'data', 'garbagebase64==') is False


def test_signing_key_encrypted_pem_round_trip(tmp_path):
    """Encrypted PEM with a passphrase round-trips."""
    w1 = SigningKey()
    path = w1.to_file(signing_keydir=str(tmp_path), passphrase=PASSPHRASE)
    w2 = SigningKey.from_file(path, passphrase=PASSPHRASE)
    assert w1.address == w2.address


def test_signing_key_public_key_only_constructs(signing_key):
    """SigningKey(b64ks=public_key_b64) accepts a peer's public key alone.

    Used by api.py / schema.py / models.py to wrap a remote party's
    public key for signature verification. Private operations
    (sign, decrypt, export_private_key_*) raise NoPrivateKeyError.
    """
    w = SigningKey(b64ks=signing_key.public_key_b64)
    assert w.private_key is None
    assert w.public_key is not None
    assert w.address == signing_key.address
    # Public verify should still work
    sig = signing_key.sign(b'data')
    assert w.validate_signature(b'data', sig) is True
    # Private operations raise
    with pytest.raises(NoPrivateKeyError):
        w.sign(b'data')
