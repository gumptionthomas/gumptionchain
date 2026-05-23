from dataclasses import asdict

from marshmallow import Schema, fields, post_dump, validate

from cancelchain.util import iso_2_dt
from cancelchain.wallet import (
    ADDRESS_TAG,
    Wallet,
    b58decode,
    b64decode,
    b64encode,
)


def asdict_sans_none(dc):
    return asdict(
        dc, dict_factory=lambda x: {k: v for (k, v) in x if v is not None}
    )


def validate_address(public_key_b64, address):
    wallet = Wallet(b64ks=public_key_b64)
    return (wallet is not None) and address == wallet.address


def validate_address_format(address):
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


def validate_base64(s):
    try:
        return b64encode(b64decode(s)) == s
    except Exception:
        pass
    return False


def validate_public_key(public_key_b64):
    wallet = Wallet(b64ks=public_key_b64)
    return wallet is not None and wallet.private_key is None


def validate_signature(public_key_b64, signing_data, signature):
    wallet = Wallet(b64ks=public_key_b64)
    if wallet is not None:
        return wallet.validate_signature(signing_data, signature)
    return False


def validate_timestamp(s):
    try:
        _ = iso_2_dt(s)
        return True
    except Exception:
        pass
    return False


class Address(fields.String):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate_address_format)


class Base64(fields.String):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate_base64)


class MillHash(Base64):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate.Length(equal=64))


class Timestamp(fields.String):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate_timestamp)


class PublicKey(Base64):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate_public_key)


class SansNoneSchema(Schema):
    @post_dump
    def remove_none_values(self, data, **kwargs):
        return {k: v for k, v in data.items() if v is not None}
