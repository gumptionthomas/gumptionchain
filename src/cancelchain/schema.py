from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Annotated, Any, Protocol

from pydantic import AfterValidator
from pydantic_core import ErrorDetails

from cancelchain.exceptions import InvalidKeyError
from cancelchain.util import iso_2_dt
from cancelchain.wallet import (
    ADDRESS_TAG,
    Wallet,
    b58decode,
    b64decode,
    b64encode,
)


def asdict_sans_none(dc: Any) -> dict[str, Any]:
    return asdict(
        dc, dict_factory=lambda x: {k: v for (k, v) in x if v is not None}
    )


def validate_address(public_key_b64: str | None, address: str | None) -> bool:
    try:
        wallet = Wallet(b64ks=public_key_b64)
    except InvalidKeyError:
        return False
    return bool((wallet is not None) and address == wallet.address)


def validate_address_format(address: str) -> bool:
    try:
        if (
            address.startswith(ADDRESS_TAG)
            and address.endswith(ADDRESS_TAG)
            and len(
                b58decode(
                    address.removeprefix(ADDRESS_TAG).removesuffix(ADDRESS_TAG)
                )
            )
            == 32
        ):
            return True
    except Exception:
        pass
    return False


def validate_base64(s: str) -> bool:
    try:
        return bool(b64encode(b64decode(s)) == s)
    except Exception:
        pass
    return False


def validate_public_key(public_key_b64: str) -> bool:
    try:
        wallet = Wallet(b64ks=public_key_b64)
    except InvalidKeyError:
        return False
    return wallet is not None and wallet.private_key is None


def validate_signature(
    public_key_b64: str | None,
    signing_data: bytes,
    signature: str | None,
) -> bool:
    try:
        wallet = Wallet(b64ks=public_key_b64)
    except InvalidKeyError:
        return False
    if wallet is not None:
        return bool(wallet.validate_signature(signing_data, signature))
    return False


def validate_timestamp(s: str) -> bool:
    try:
        _ = iso_2_dt(s)
        return True
    except Exception:
        pass
    return False


# --- Pydantic v2 custom type aliases (introduced in Phase 4 / PR-1).
# AfterValidator runs after Pydantic's built-in coercion; the callback
# either returns the value (possibly transformed) or raises ValueError,
# which Pydantic wraps into a ValidationError for the caller.


def truncate(s: str, max_len: int = 32) -> str:
    """Cap a user-provided value for echo in validation messages.

    Pydantic surfaces these messages in HTTP 400 responses and logs.
    Echoing unbounded input would let clients bloat responses or leak
    arbitrary content; cap to a short prefix plus a length indicator.
    """
    if len(s) <= max_len:
        return s
    return f'{s[:max_len]}... ({len(s)} chars)'


def _check_address_format(s: str) -> str:
    if not validate_address_format(s):
        msg = f'Invalid address format: {truncate(s)!r}'
        raise ValueError(msg)
    return s


def _check_base64(s: str) -> str:
    if not validate_base64(s):
        msg = f'Invalid base64 value: {truncate(s)!r}'
        raise ValueError(msg)
    return s


def _check_mill_hash(s: str) -> str:
    if not validate_base64(s) or len(s) != 64:
        msg = f'Invalid mill hash: {truncate(s)!r}'
        raise ValueError(msg)
    return s


def _check_timestamp(s: str) -> str:
    if not validate_timestamp(s):
        msg = f'Invalid timestamp: {truncate(s)!r}'
        raise ValueError(msg)
    return s


def _check_public_key(s: str) -> str:
    if not validate_public_key(s):
        msg = f'Invalid public key: {truncate(s)!r}'
        raise ValueError(msg)
    return s


AddressType = Annotated[str, AfterValidator(_check_address_format)]
Base64Type = Annotated[str, AfterValidator(_check_base64)]
MillHashType = Annotated[str, AfterValidator(_check_mill_hash)]
TimestampType = Annotated[str, AfterValidator(_check_timestamp)]
PublicKeyType = Annotated[str, AfterValidator(_check_public_key)]


class _ErrorsAware(Protocol):
    """Anything with an .errors() method returning Pydantic-shaped error dicts.

    Pydantic's ValidationError implements this (returns list[ErrorDetails]);
    synthetic test fakes can satisfy it without subclassing.
    """

    def errors(self) -> Sequence[ErrorDetails]: ...


def pydantic_errors_to_messages(e: _ErrorsAware) -> dict[str, Any]:
    """Convert Pydantic ValidationError to Marshmallow-shaped messages.

    Accepts any object that satisfies the _ErrorsAware Protocol (duck-typed),
    so synthetic test fakes work without subclassing PydanticValidationError.

    Rebuilds a nested dict from Pydantic's flat err['loc'] tuples so
    api.py's make_error_response and the InvalidBlockError({...: e.messages})
    re-raise wrappers see the same nested layout downstream consumers
    already render. List indices in `loc` are stringified, since the
    resulting dict will be JSON-serialized to clients anyway (Marshmallow
    keeps integer keys in-Python; we don't — they're indistinguishable
    on the wire).

    When two errors share a prefix such that one path terminates at a
    node that the other treats as internal (rare but possible with
    discriminated unions or before-validators), the leaf messages are
    kept under a '_self' sentinel key so neither error is lost.

    Example output for outflows[0].amount failing Field(ge=1):
        {'outflows': {'0': {'amount': ['Input should be >= 1']}}}
    """
    result: dict[str, Any] = {}
    for err in e.errors():
        loc = err.get('loc', ())
        msg = err.get('msg', 'invalid')
        if not loc:
            result.setdefault('_schema', []).append(msg)
            continue
        current = result
        for part in loc[:-1]:
            key = str(part)
            existing = current.get(key)
            if isinstance(existing, dict):
                pass  # walk into it
            elif isinstance(existing, list):
                # Prior leaf at this position — preserve it under _self.
                current[key] = {'_self': existing}
            else:
                current[key] = {}
            current = current[key]
        last_key = str(loc[-1])
        existing_leaf = current.get(last_key)
        if isinstance(existing_leaf, dict):
            # Prior nesting under this key — append msg to _self list.
            existing_leaf.setdefault('_self', []).append(msg)
        else:
            current.setdefault(last_key, []).append(msg)
    return result
