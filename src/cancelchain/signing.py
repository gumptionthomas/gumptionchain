from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

from cancelchain.exceptions import InvalidKeyError
from cancelchain.wallet import Wallet

SIG_VERSION = '1'  # CC-Sig-Version header value (dispatch key)
SIG_SCHEME = 'cc-sig-v1'  # scheme id bound into the signed canonical
FRESHNESS_SECONDS = 300

H_VERSION = 'CC-Sig-Version'
H_ADDRESS = 'CC-Address'
H_PUBKEY = 'CC-Public-Key'
H_TIMESTAMP = 'CC-Timestamp'
H_SIGNATURE = 'CC-Signature'


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
    wallet: Wallet,
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
        address=wallet.address,
    )
    return {
        H_VERSION: SIG_VERSION,
        H_ADDRESS: wallet.address,
        H_PUBKEY: wallet.public_key_b64,
        H_TIMESTAMP: ts,
        H_SIGNATURE: wallet.sign(canonical),
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
    """Verify a `cc-sig-v1` signed request; return the authenticated
    address or raise SignatureError.
    """
    if headers.get(H_VERSION) != SIG_VERSION:
        msg = 'unsupported signature version'
        raise SignatureError(msg)
    address: str | None = headers.get(H_ADDRESS)
    pubkey: str | None = headers.get(H_PUBKEY)
    ts: str | None = headers.get(H_TIMESTAMP)
    sig: str | None = headers.get(H_SIGNATURE)
    if not (address and pubkey and ts and sig):
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
        wallet = Wallet(b64ks=pubkey)
    except InvalidKeyError as e:
        msg = 'invalid public key'
        raise SignatureError(msg) from e
    if wallet.address != address:
        msg = 'public key does not match address'
        raise SignatureError(msg)
    canonical = _canonical(
        method=method,
        path=path,
        query=query,
        body=body,
        node_host=node_host,
        timestamp=ts,
        address=address,
    )
    if not wallet.validate_signature(canonical, sig):
        msg = 'signature verification failed'
        raise SignatureError(msg)
    return address
