from __future__ import annotations

import binascii
import hashlib
import json
import re
import time
from base64 import standard_b64decode, standard_b64encode
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
    if not re.fullmatch(r'[0-9]+', ts):
        # Guarantee a numeric timestamp before any freshness math, so JS and
        # Python agree (JS Number('x')->NaN would otherwise silently pass).
        msg = 'malformed gc-msg-v1 proof'
        raise BadProofError(msg)
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


ARMOR_HEADER = '-----BEGIN GUMPTION SIGNED MESSAGE-----'
ARMOR_SIG = '-----BEGIN GUMPTION SIGNATURE-----'
ARMOR_FOOTER = '-----END GUMPTION SIGNED MESSAGE-----'


def to_armored(proof: dict[str, str]) -> str:
    blob = standard_b64encode(json.dumps(proof).encode()).decode()
    return '\n'.join(
        [ARMOR_HEADER, proof['message'], ARMOR_SIG, blob, ARMOR_FOOTER]
    )


def from_armored(text: str) -> dict[str, Any]:
    lines = text.replace('\r\n', '\n').split('\n')
    try:
        h = lines.index(ARMOR_HEADER)
        s = lines.index(ARMOR_SIG)
        f = lines.index(ARMOR_FOOTER)
    except ValueError as e:
        msg = 'malformed armored message'
        raise BadProofError(msg) from e
    if not h < s < f:
        msg = 'malformed armored message'
        raise BadProofError(msg)
    cleartext = '\n'.join(lines[h + 1 : s])
    blob = ''.join(lines[s + 1 : f]).strip()
    try:
        proof = json.loads(standard_b64decode(blob.encode()).decode())
    except (ValueError, binascii.Error) as e:
        msg = 'malformed armored signature block'
        raise BadProofError(msg) from e
    if not isinstance(proof, dict):
        msg = 'malformed armored signature block'
        raise BadProofError(msg)
    proof_dict: dict[str, Any] = proof
    if proof_dict.get('message') != cleartext:
        msg = 'armored cleartext does not match signed message'
        raise BadProofError(msg)
    return proof_dict
