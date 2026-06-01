import time

import pytest

from cancelchain import signing
from cancelchain.wallet import Wallet

REQ = {
    'method': 'POST',
    'path': '/api/block/abc',
    'query': 'earliest=1',
    'body': b'{"x":1}',
    'node_host': 'http://localhost:8080',
}


def test_sign_then_verify_roundtrip():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    assert headers[signing.H_VERSION] == signing.SIG_VERSION
    assert headers[signing.H_ADDRESS] == w.address
    addr = signing.verify(headers, **REQ)
    assert addr == w.address


def test_verify_rejects_tampered_path():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'path': '/api/block/DIFFERENT'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_query():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'query': 'earliest=999'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_method():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'method': 'GET'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_body():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'body': b'{"x":2}'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_wrong_node():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'node_host': 'http://peer.node:8888'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_stale_timestamp():
    w = Wallet()
    old = int(time.time()) - (signing.FRESHNESS_SECONDS + 1)
    headers = signing.sign_headers(w, timestamp=old, **REQ)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_future_timestamp():
    w = Wallet()
    future = int(time.time()) + (signing.FRESHNESS_SECONDS + 1)
    headers = signing.sign_headers(w, timestamp=future, **REQ)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_pubkey_address_mismatch():
    w = Wallet()
    other = Wallet()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_PUBKEY] = other.public_key_b64
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_missing_header():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    del headers[signing.H_SIGNATURE]
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_unknown_version():
    w = Wallet()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_VERSION] = '999'
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)
