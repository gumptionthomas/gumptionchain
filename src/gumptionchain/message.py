from __future__ import annotations

import hashlib
import time
from typing import Any

from gumptionchain.exceptions import InvalidKeyError
from gumptionchain.wallet import Wallet

MSG_SCHEME = 'gc-msg-v1'
MSG_VERSION = '1'


class MessageError(Exception):
    """Base class for message-signing errors."""


class BadProofError(MessageError):
    """Input is not a structurally valid gc-msg-v1 proof."""


def _message_canonical(*, address: str, timestamp: str, message: str) -> bytes:
    digest = hashlib.sha256(message.encode()).hexdigest()
    return '\n'.join(
        [MSG_SCHEME, MSG_VERSION, address, timestamp, digest]
    ).encode()


def sign_message(
    wallet: Wallet, message: str, timestamp: int | None = None
) -> dict[str, str]:
    ts = str(int(timestamp if timestamp is not None else time.time()))
    canonical = _message_canonical(
        address=wallet.address, timestamp=ts, message=message
    )
    return {
        'scheme': MSG_SCHEME,
        'version': MSG_VERSION,
        'address': wallet.address,
        'public_key': wallet.public_key_b64,
        'timestamp': ts,
        'message': message,
        'signature': wallet.sign(canonical),
    }


def verify_message(
    proof: Any, max_age: int | None = None, now: int | None = None
) -> dict[str, Any]:
    if not isinstance(proof, dict):
        msg = 'not a proof object'
        raise BadProofError(msg)
    scheme = proof.get('scheme')
    version = proof.get('version')
    address = proof.get('address')
    pubkey = proof.get('public_key')
    ts = proof.get('timestamp')
    message = proof.get('message')
    sig = proof.get('signature')
    if (
        scheme != MSG_SCHEME
        or version != MSG_VERSION
        or not all(
            isinstance(v, str) for v in (address, pubkey, ts, message, sig)
        )
    ):
        msg = 'malformed gc-msg-v1 proof'
        raise BadProofError(msg)
    assert isinstance(address, str)
    assert isinstance(pubkey, str)
    assert isinstance(ts, str)
    assert isinstance(message, str)
    assert isinstance(sig, str)
    try:
        wallet = Wallet(b64ks=pubkey)
    except InvalidKeyError as e:
        msg = 'invalid public key'
        raise BadProofError(msg) from e
    result: dict[str, Any] = {
        'address': address,
        'timestamp': ts,
        'message': message,
    }
    if wallet.address != address:
        return {**result, 'valid': False, 'reason': 'address-mismatch'}
    canonical = _message_canonical(
        address=address, timestamp=ts, message=message
    )
    if not wallet.validate_signature(canonical, sig):
        return {**result, 'valid': False, 'reason': 'bad-signature'}
    if max_age is not None:
        current = int(now if now is not None else time.time())
        if current - int(ts) > max_age:
            return {**result, 'valid': False, 'reason': 'expired'}
    return {**result, 'valid': True}
