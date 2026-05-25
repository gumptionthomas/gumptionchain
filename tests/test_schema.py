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
