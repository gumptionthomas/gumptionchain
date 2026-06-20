import time

import pytest

from gumptionchain import signing
from gumptionchain.signing_key import SigningKey

REQ = {
    'method': 'POST',
    'path': '/api/block/abc',
    'query': 'earliest=1',
    'body': b'{"x":1}',
    'node_host': 'http://localhost:8080',
}


@pytest.fixture
def make_key():
    def _make() -> SigningKey:
        return SigningKey()

    return _make


def test_sign_then_verify_roundtrip(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    assert headers[signing.H_VERSION] == signing.SIG_VERSION
    assert headers[signing.H_ADDRESS] == w.address
    addr = signing.verify(headers, **REQ)
    assert addr == w.address


def test_verify_rejects_tampered_path(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'path': '/api/block/DIFFERENT'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_query(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'query': 'earliest=999'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_method(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'method': 'GET'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_tampered_body(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'body': b'{"x":2}'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_wrong_node(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    bad = {**REQ, 'node_host': 'http://peer.node:8888'}
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **bad)


def test_verify_rejects_stale_timestamp(make_key):
    # Pin a single `now` for both the signed timestamp and verify(): using
    # int(time.time()) independently on each side lets a 1-second tick land
    # between them, collapsing the boundary check (the just-past-boundary
    # offset + truncation can net to exactly FRESHNESS_SECONDS, which the
    # strict `>` does not reject) — a real flake. verify() exposes `now`
    # precisely for this.
    w = make_key()
    now = int(time.time())
    old = now - (signing.FRESHNESS_SECONDS + 1)
    headers = signing.sign_headers(w, timestamp=old, **REQ)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, now=now, **REQ)


def test_verify_rejects_future_timestamp(make_key):
    # See test_verify_rejects_stale_timestamp: pin one `now` for both sides
    # so the int(time.time()) truncation race cannot collapse the boundary.
    w = make_key()
    now = int(time.time())
    future = now + (signing.FRESHNESS_SECONDS + 1)
    headers = signing.sign_headers(w, timestamp=future, **REQ)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, now=now, **REQ)


def test_verify_rejects_missing_header(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    del headers[signing.H_SIGNATURE]
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_unknown_version(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_VERSION] = '999'
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_verify_rejects_signature_from_other_key(make_key):
    # A signature made by a DIFFERENT key over the SAME canonical (claiming w's
    # identity) must be rejected — the core auth guarantee, for both key types.
    # _canonical is the same internal sign_headers/verify use; building it here
    # lets `other` sign exactly the bytes verify will check under w's pubkey.
    w = make_key()
    other = make_key()
    headers = signing.sign_headers(w, **REQ)
    canonical = signing._canonical(
        method=REQ['method'],
        path=REQ['path'],
        query=REQ['query'],
        body=REQ['body'],
        node_host=REQ['node_host'],
        timestamp=headers[signing.H_TIMESTAMP],
        address=w.address,
    )
    headers[signing.H_SIGNATURE] = other.sign(canonical)
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


def test_sign_headers_v2_has_no_pubkey_header(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    assert headers[signing.H_VERSION] == '2'
    assert signing.H_PUBKEY not in headers  # no GC-Public-Key
    assert signing.verify(headers, **REQ) == w.address


def test_verify_rejects_v1_pubkey_scheme(make_key):
    w = make_key()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_VERSION] = '1'  # old scheme
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)
