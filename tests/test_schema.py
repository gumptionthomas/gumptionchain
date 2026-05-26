"""Regression tests for the schema validator try/except guards.

`validate_address`, `validate_public_key`, and `validate_signature` each
construct a `Wallet(b64ks=...)`. Wallet's `__init__` raises `InvalidKeyError`
on malformed key strings; without the try/except wrappers added in this PR,
that exception would propagate through marshmallow's validation pipeline
and surface as a 500 instead of a structured 400.
"""

from cancelchain.schema import (
    validate_address,
    validate_public_key,
    validate_signature,
)


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

from pydantic import BaseModel, Field, model_validator  # noqa: E402

from cancelchain.schema import pydantic_errors_to_messages  # noqa: E402


def test_pydantic_errors_to_messages_simple_field():
    """Single field error produces a flat dict with a list of messages."""

    class M(BaseModel):
        amount: int = Field(ge=1)

    try:
        M.model_validate({'amount': 0})
        raise AssertionError
    except AssertionError:
        raise
    except Exception as exc:
        result = pydantic_errors_to_messages(exc)  # type: ignore[arg-type]

    assert 'amount' in result
    assert isinstance(result['amount'], list)
    assert len(result['amount']) == 1


def test_pydantic_errors_to_messages_nested():
    """Nested model field produces a nested dict."""

    class Inner(BaseModel):
        amount: int = Field(ge=1)

    class Outer(BaseModel):
        outflows: list[Inner]

    try:
        Outer.model_validate({'outflows': [{'amount': 0}]})
        raise AssertionError
    except AssertionError:
        raise
    except Exception as exc:
        result = pydantic_errors_to_messages(exc)  # type: ignore[arg-type]

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

    try:
        M.model_validate({'a': 5, 'b': 1})
        raise AssertionError
    except AssertionError:
        raise
    except Exception as exc:
        result = pydantic_errors_to_messages(exc)  # type: ignore[arg-type]

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
    result = pydantic_errors_to_messages(fake)  # type: ignore[arg-type]

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
    result = pydantic_errors_to_messages(fake)  # type: ignore[arg-type]

    assert isinstance(result['a'], dict)
    assert result['a']['x'] == ['nested error']
    assert result['a']['_self'] == ['leaf error']
