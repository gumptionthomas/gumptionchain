from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

from gumptionchain.exceptions import InvalidKeyError
from gumptionchain.signing_key import SigningKey

SIG_VERSION = '1'  # GC-Sig-Version header value (dispatch key)
SIG_SCHEME = 'gc-sig-v1'  # scheme id bound into the signed canonical
FRESHNESS_SECONDS = 300

H_VERSION = 'GC-Sig-Version'
H_ADDRESS = 'GC-Address'
H_TIMESTAMP = 'GC-Timestamp'
H_SIGNATURE = 'GC-Signature'
# gc-sig-v1 carries no GC-Public-Key header — the verifier reconstructs the key
# from GC-Address. A stray public-key header (if any) is simply ignored; the
# scheme never reads or emits one. Mismatched versions fail the version gate.


class SignatureError(Exception):
    """A signed request failed verification (treated as 401 by the API)."""


def _canonical(
    *,
    method: str,
    path: str,
    query: str,
    body: bytes | None,
    node_host: str,
    timestamp: str,
    address: str,
) -> bytes:
    body_digest = hashlib.sha256(body or b'').hexdigest()
    return '\n'.join(
        [
            SIG_SCHEME,
            method.upper(),
            path,
            query,
            body_digest,
            node_host,
            timestamp,
            address,
        ]
    ).encode()


def sign_headers(
    signing_key: SigningKey,
    *,
    method: str,
    path: str,
    query: str,
    body: bytes | None,
    node_host: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = str(int(timestamp if timestamp is not None else time.time()))
    canonical = _canonical(
        method=method,
        path=path,
        query=query,
        body=body,
        node_host=node_host,
        timestamp=ts,
        address=signing_key.address,
    )
    return {
        H_VERSION: SIG_VERSION,
        H_ADDRESS: signing_key.address,
        H_TIMESTAMP: ts,
        H_SIGNATURE: signing_key.sign(canonical),
    }


def verify(
    headers: Mapping[str, Any],
    *,
    method: str,
    path: str,
    query: str,
    body: bytes | None,
    node_host: str,
    now: int | None = None,
) -> str:
    """Verify a `gc-sig-v1` signed request; return the authenticated
    address or raise SignatureError.
    """
    if headers.get(H_VERSION) != SIG_VERSION:
        msg = 'unsupported signature version'
        raise SignatureError(msg)
    address: str | None = headers.get(H_ADDRESS)
    ts: str | None = headers.get(H_TIMESTAMP)
    sig: str | None = headers.get(H_SIGNATURE)
    if not (address and ts and sig):
        msg = 'missing signature headers'
        raise SignatureError(msg)
    try:
        ts_val = int(ts)
    except (TypeError, ValueError) as e:
        msg = 'malformed timestamp'
        raise SignatureError(msg) from e
    current = int(now if now is not None else time.time())
    if abs(current - ts_val) > FRESHNESS_SECONDS:
        msg = 'stale or future timestamp'
        raise SignatureError(msg)
    try:
        signing_key = SigningKey.from_address(address)
    except InvalidKeyError as e:
        msg = 'invalid address'
        raise SignatureError(msg) from e
    canonical = _canonical(
        method=method,
        path=path,
        query=query,
        body=body,
        node_host=node_host,
        timestamp=ts,
        address=address,
    )
    if not signing_key.validate_signature(canonical, sig):
        msg = 'signature verification failed'
        raise SignatureError(msg)
    return address
