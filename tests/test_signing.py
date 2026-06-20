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


@pytest.fixture(params=['rsa', 'ed25519'])
def make_key(request):
    """Factory for a fresh SigningKey of the parametrized type, so every test
    runs for both RSA-2048 and Ed25519. Tests needing >1 key call it again."""

    def _make() -> SigningKey:
        if request.param == 'ed25519':
            return SigningKey.generate_ed25519()
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


def test_verify_rejects_pubkey_address_mismatch(make_key):
    w = make_key()
    other = make_key()
    headers = signing.sign_headers(w, **REQ)
    headers[signing.H_PUBKEY] = other.public_key_b64
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)


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


def _canonical_for(signing_key, timestamp):
    return signing._canonical(
        method=REQ['method'],
        path=REQ['path'],
        query=REQ['query'],
        body=REQ['body'],
        node_host=REQ['node_host'],
        timestamp=timestamp,
        address=signing_key.address,
    )


def test_verify_rejects_cross_scheme_signature():
    # Document that the OID-self-describing wire is safe: a signature of the
    # WRONG scheme (over the exact canonical verify checks) is rejected by the
    # scheme picked from the pubkey type. Not parametrized — inherently
    # cross-type. Address self-cert passes; only the signature scheme is wrong.
    rsa = SigningKey()
    ed = SigningKey.generate_ed25519()

    # RSA identity, but an Ed25519 (64-byte) signature -> RSA verify rejects.
    headers = signing.sign_headers(rsa, **REQ)
    headers[signing.H_SIGNATURE] = ed.sign(
        _canonical_for(rsa, headers[signing.H_TIMESTAMP])
    )
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)

    # Ed25519 identity, but an RSA (256-byte) signature -> the vendored
    # verifier's len(signature) != 64 check rejects.
    headers = signing.sign_headers(ed, **REQ)
    headers[signing.H_SIGNATURE] = rsa.sign(
        _canonical_for(ed, headers[signing.H_TIMESTAMP])
    )
    with pytest.raises(signing.SignatureError):
        signing.verify(headers, **REQ)
