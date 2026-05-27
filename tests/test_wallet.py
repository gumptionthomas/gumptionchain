import logging

import pytest

from cancelchain.exceptions import InvalidKeyError, NoPrivateKeyError
from cancelchain.schema import validate_address_format
from cancelchain.wallet import Wallet

PASSPHRASE = 'fourty-two'


def test_new():
    wallet = Wallet()
    assert wallet is not None
    assert wallet.private_key is not None
    assert wallet.public_key is not None
    assert wallet.address is not None


def test_invalid_address():
    assert not validate_address_format('foo')
    assert not validate_address_format('f' * 47)
    assert not validate_address_format(1)


def test_create_invalid_key():
    with pytest.raises(InvalidKeyError):
        Wallet(b64ks='foo')
    with pytest.raises(InvalidKeyError):
        Wallet(b58ks='foo')
    with pytest.raises(InvalidKeyError):
        Wallet(ks='foo')


def test_crypto(wallet):
    s = 'This is a secret'
    msg = wallet.encrypt(s.encode())
    assert msg != s
    wallet2 = Wallet()
    with pytest.raises(ValueError):
        wallet2.decrypt(msg)
    assert wallet.decrypt(msg).decode() == s


def test_create_from_key(
    wallet_private_key_b58,
    wallet_public_key_b64,
    wallet_address,
    wallet_dict,
    wallet_json,
):
    wallet = Wallet(b58ks=wallet_private_key_b58)
    assert wallet is not None
    assert wallet.private_key_b58 == wallet_private_key_b58
    assert wallet.public_key_b64 == wallet_public_key_b64
    assert wallet.address == wallet_address
    assert wallet.to_dict() == wallet_dict
    assert wallet.to_json() == wallet_json


def test_from(wallet):
    d = wallet.to_dict()
    new_wallet = Wallet.from_dict(d)
    assert wallet == new_wallet
    j = wallet.to_json()
    new_wallet = Wallet.from_json(j)
    assert wallet == new_wallet


def test_file(tmp_path, wallet):
    f = wallet.to_file(walletdir=tmp_path)
    w = Wallet.from_file(f)
    assert w == wallet


def test_file_passphrase(tmp_path, wallet):
    f = wallet.to_file(walletdir=tmp_path, passphrase=PASSPHRASE)
    with pytest.raises(InvalidKeyError):
        Wallet.from_file(f, passphrase=f'{PASSPHRASE}!')
    assert Wallet.from_file(f, passphrase=PASSPHRASE) == wallet


def test_export(wallet):
    b58ks = wallet.export_private_key_b58(passphrase=PASSPHRASE)
    assert Wallet(b58ks=b58ks, passphrase=PASSPHRASE) == wallet


def test_sign(wallet, wallet_signature_data, wallet_signature):
    assert wallet.sign(wallet_signature_data.encode()) == wallet_signature
    assert wallet.sign(b'foo') != wallet_signature


def test_eq(wallet):
    wallet_copy = Wallet(b58ks=wallet.private_key_b58)
    assert wallet == wallet_copy
    new_wallet = Wallet()
    assert wallet != new_wallet


def test_repr(caplog, logger, wallet, wallet_address):
    with caplog.at_level(logging.INFO):
        logger.info(wallet)
        assert f'Wallet({wallet_address})' in caplog.text


def test_wallet_address_round_trips_through_pem(tmp_path):
    """Freshly generated wallet → write PEM → read back → same address."""
    w1 = Wallet()
    path = w1.to_file(walletdir=str(tmp_path))
    w2 = Wallet.from_file(path)
    assert w1.address == w2.address


def test_wallet_address_round_trips_through_b58():
    """Freshly generated wallet → b58 → read back → same address."""
    w1 = Wallet()
    w2 = Wallet(b58ks=w1.private_key_b58)
    assert w1.address == w2.address


def test_wallet_sign_verify_happy_path():
    w = Wallet()
    sig = w.sign(b'hello world')
    assert w.validate_signature(b'hello world', sig) is True


def test_wallet_verify_rejects_mutated_payload():
    w = Wallet()
    sig = w.sign(b'hello world')
    assert w.validate_signature(b'hello WORLD', sig) is False


def test_wallet_verify_rejects_garbage_signature():
    w = Wallet()
    assert w.validate_signature(b'data', 'garbagebase64==') is False


def test_wallet_encrypt_decrypt_round_trip():
    w = Wallet()
    plaintext = b'session-challenge-payload'
    ciphertext = w.encrypt(plaintext)
    assert w.decrypt(ciphertext) == plaintext


def test_wallet_encrypted_pem_round_trip(tmp_path):
    """Encrypted PEM with a passphrase round-trips."""
    w1 = Wallet()
    path = w1.to_file(walletdir=str(tmp_path), passphrase=PASSPHRASE)
    w2 = Wallet.from_file(path, passphrase=PASSPHRASE)
    assert w1.address == w2.address


def test_wallet_public_key_only_constructs(wallet):
    """Wallet(b64ks=public_key_b64) accepts a peer's public key alone.

    Used by api.py / schema.py / models.py to wrap a remote party's
    public key for signature verification. Private operations
    (sign, decrypt, export_private_key_*) raise NoPrivateKeyError.
    """
    w = Wallet(b64ks=wallet.public_key_b64)
    assert w.private_key is None
    assert w.public_key is not None
    assert w.address == wallet.address
    # Public verify should still work
    sig = wallet.sign(b'data')
    assert w.validate_signature(b'data', sig) is True
    # Private operations raise
    with pytest.raises(NoPrivateKeyError):
        w.sign(b'data')
