"""Regression tests for the schema validator try/except guards.

`validate_address`, `validate_public_key`, and `validate_signature` each
construct a `Wallet(b64ks=...)`. Wallet's `__init__` raises `InvalidKeyError`
on malformed key strings; without the try/except wrappers added in this PR,
that exception would propagate through marshmallow's validation pipeline
and surface as a 500 instead of a structured 400.
"""

from base64 import b64encode

import pytest
from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from cancelchain.schema import (
    AddressType,
    Base64Type,
    MillHashType,
    PublicKeyType,
    TimestampType,
    pydantic_errors_to_messages,
    validate_address,
    validate_public_key,
    validate_signature,
)
from cancelchain.util import now_iso


def test_validate_address_returns_false_on_malformed_key():
    assert validate_address('not-a-valid-base64-key', 'CCxxxCC') is False


def test_validate_address_returns_false_on_empty_key():
    assert validate_address('', 'CCxxxCC') is False


def test_validate_public_key_returns_false_on_malformed_key():
    assert validate_public_key('not-a-valid-base64-key') is False


def test_validate_public_key_returns_false_on_empty_key():
    assert validate_public_key('') is False


def test_validate_signature_returns_false_on_malformed_key():
    assert validate_signature('not-a-valid-base64-key', b'data', 'sig') is False


def test_validate_signature_returns_false_on_empty_key():
    assert validate_signature('', b'data', 'sig') is False


# ---------------------------------------------------------------------------
# pydantic_errors_to_messages
# ---------------------------------------------------------------------------


def test_pydantic_errors_to_messages_simple_field():
    """Single field error produces a flat dict with a list of messages."""

    class M(BaseModel):
        amount: int = Field(ge=1)

    with pytest.raises(PydanticValidationError) as exc_info:
        M.model_validate({'amount': 0})
    result = pydantic_errors_to_messages(exc_info.value)

    assert 'amount' in result
    assert isinstance(result['amount'], list)
    assert len(result['amount']) == 1


def test_pydantic_errors_to_messages_nested():
    """Nested model field produces a nested dict."""

    class Inner(BaseModel):
        amount: int = Field(ge=1)

    class Outer(BaseModel):
        outflows: list[Inner]

    with pytest.raises(PydanticValidationError) as exc_info:
        Outer.model_validate({'outflows': [{'amount': 0}]})
    result = pydantic_errors_to_messages(exc_info.value)

    assert 'outflows' in result
    assert '0' in result['outflows']
    assert 'amount' in result['outflows']['0']
    assert isinstance(result['outflows']['0']['amount'], list)


def test_pydantic_errors_to_messages_whole_model_error():
    """@model_validator raising ValueError with empty loc → '_schema' bucket."""

    class M(BaseModel):
        a: int
        b: int

        @model_validator(mode='after')
        def check_a_lt_b(self) -> 'M':
            msg = 'a must be less than b'
            if self.a >= self.b:
                raise ValueError(msg)
            return self

    with pytest.raises(PydanticValidationError) as exc_info:
        M.model_validate({'a': 5, 'b': 1})
    result = pydantic_errors_to_messages(exc_info.value)

    assert '_schema' in result
    assert isinstance(result['_schema'], list)
    assert any('a must be less than b' in m for m in result['_schema'])


class _FakeValidationError:
    """Synthetic ValidationError-like object for overlap tests.

    Pydantic schemas don't reliably emit overlapping loc paths on their
    own; this helper lets us construct exactly the edge-case inputs that
    exercise the _self sentinel and the nested-then-leaf rewrite branch.
    """

    def __init__(self, errs: list[dict]) -> None:
        self._errs = errs

    def errors(self) -> list[dict]:
        return self._errs


def test_pydantic_errors_to_messages_leaf_then_nested_overlap():
    """loc=('a',) then loc=('a','x') — leaf preserved under _self."""
    fake = _FakeValidationError(
        [
            {'loc': ('a',), 'msg': 'leaf error'},
            {'loc': ('a', 'x'), 'msg': 'nested error'},
        ]
    )
    result = pydantic_errors_to_messages(fake)

    assert isinstance(result['a'], dict)
    assert result['a']['_self'] == ['leaf error']
    assert result['a']['x'] == ['nested error']


def test_pydantic_errors_to_messages_nested_then_leaf_overlap():
    """loc=('a','x') then loc=('a',) — both preserved; no AttributeError."""
    fake = _FakeValidationError(
        [
            {'loc': ('a', 'x'), 'msg': 'nested error'},
            {'loc': ('a',), 'msg': 'leaf error'},
        ]
    )
    result = pydantic_errors_to_messages(fake)

    assert isinstance(result['a'], dict)
    assert result['a']['x'] == ['nested error']
    assert result['a']['_self'] == ['leaf error']


# ---------------------------------------------------------------------------
# *Type alias AfterValidator tests
# ---------------------------------------------------------------------------


def test_address_type_accepts_valid(wallet):
    class M(BaseModel):
        address: AddressType

    m = M(address=wallet.address)
    assert m.address == wallet.address


def test_address_type_rejects_invalid():
    class M(BaseModel):
        address: AddressType

    with pytest.raises(PydanticValidationError):
        M(address='not-an-address')


def test_base64_type_accepts_valid():
    class M(BaseModel):
        value: Base64Type

    m = M(value='aGVsbG8=')  # 'hello' in base64
    assert m.value == 'aGVsbG8='


def test_base64_type_rejects_invalid():
    class M(BaseModel):
        value: Base64Type

    with pytest.raises(PydanticValidationError):
        M(value='not_valid_base64!@#')


def test_mill_hash_type_accepts_valid():
    # MillHashType requires valid base64 AND exactly 64 chars.
    # 48 raw bytes → 64 base64 chars.
    valid_hash = b64encode(b'A' * 48).decode()
    assert len(valid_hash) == 64

    class M(BaseModel):
        h: MillHashType

    m = M(h=valid_hash)
    assert m.h == valid_hash


def test_mill_hash_type_rejects_wrong_length():
    class M(BaseModel):
        h: MillHashType

    with pytest.raises(PydanticValidationError):
        M(h='aGVsbG8=')  # valid base64 but not 64 chars


def test_timestamp_type_accepts_valid():
    class M(BaseModel):
        t: TimestampType

    ts = now_iso()
    m = M(t=ts)
    assert m.t == ts


def test_timestamp_type_rejects_invalid():
    class M(BaseModel):
        t: TimestampType

    with pytest.raises(PydanticValidationError):
        M(t='not-a-timestamp')


def test_public_key_type_accepts_valid(wallet):
    class M(BaseModel):
        pk: PublicKeyType

    m = M(pk=wallet.public_key_b64)
    assert m.pk == wallet.public_key_b64


def test_public_key_type_rejects_invalid():
    class M(BaseModel):
        pk: PublicKeyType

    with pytest.raises(PydanticValidationError):
        M(pk='not-a-key')


def test_base64_type_truncates_long_invalid_input_in_message():
    """ValueError message caps long invalid input to keep responses bounded."""
    # 501 chars: not a multiple of 4, so base64 decode will fail/not round-trip.
    long_input = 'x' * 501  # invalid base64, definitely > 32 chars

    class M(BaseModel):
        value: Base64Type

    with pytest.raises(PydanticValidationError) as exc_info:
        M(value=long_input)
    # The full input must NOT appear verbatim in any error message;
    # the truncation marker should.
    messages = pydantic_errors_to_messages(exc_info.value)
    flat = str(messages)
    assert long_input not in flat
    assert '... (501 chars)' in flat
