from __future__ import annotations

import binascii
import json
import os
from base64 import standard_b64decode, standard_b64encode
from typing import Any

import base58check
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)

from cancelchain.exceptions import InvalidKeyError, NoPrivateKeyError
from cancelchain.milling import mill_hash_bin

RSAKey = RSAPrivateKey | RSAPublicKey

ADDRESS_TAG = 'CC'
KEY_SIZE = 3072


def b58decode(s: str) -> bytes:
    return base58check.b58decode(s.encode())  # type: ignore[no-any-return]


def b58encode(b: bytes) -> str:
    return base58check.b58encode(b).decode()  # type: ignore[no-any-return]


def b64decode(s: str) -> bytes:
    return standard_b64decode(s.encode())


def b64encode(b: bytes) -> str:
    return standard_b64encode(b).decode()


def export_binary_key(key: RSAKey, passphrase: str | None = None) -> bytes:
    if isinstance(key, RSAPublicKey):
        return key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    # RSAPrivateKey (narrowed by exclusion)
    encryption: serialization.KeySerializationEncryption
    if passphrase is None:
        encryption = serialization.NoEncryption()
    else:
        encryption = serialization.BestAvailableEncryption(passphrase.encode())
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def import_key(ks: bytes | str, passphrase: str | None = None) -> RSAKey | None:
    """Load an RSA key from PEM or DER bytes. Accepts both private and
    public keys (api.py / schema.py / models.py construct Wallet with
    a peer's public key alone for signature verification).
    """
    try:
        if isinstance(ks, str):
            ks = ks.encode()
        password = passphrase.encode() if passphrase is not None else None
        # lstrip handles leading whitespace/newlines from copy-pasted PEMs
        is_pem = ks.lstrip().startswith(b'-----BEGIN')
        # Private-key path first (the common case for wallet load flows)
        try:
            if is_pem:
                key = serialization.load_pem_private_key(ks, password)
            else:
                key = serialization.load_der_private_key(ks, password)
            if isinstance(key, RSAPrivateKey):
                return key
        except Exception:
            pass
        # Public-key fallback (peer-public-key wrap path)
        pub = (
            serialization.load_pem_public_key(ks)
            if is_pem
            else serialization.load_der_public_key(ks)
        )
        return pub if isinstance(pub, RSAPublicKey) else None
    except Exception:
        return None


def import_b58_key(ks: str, passphrase: str | None = None) -> RSAKey | None:
    try:
        return import_key(b58decode(ks), passphrase=passphrase)
    except Exception:
        return None


def import_b64_key(ks: str, passphrase: str | None = None) -> RSAKey | None:
    try:
        return import_key(b64decode(ks), passphrase=passphrase)
    except Exception:
        return None


class Wallet:
    def __init__(
        self,
        b64ks: str | None = None,
        b58ks: str | None = None,
        ks: bytes | str | None = None,
        passphrase: str | None = None,
    ) -> None:
        key: RSAKey | None
        if b64ks is not None:
            key = import_b64_key(b64ks, passphrase=passphrase)
        elif b58ks is not None:
            key = import_b58_key(b58ks, passphrase=passphrase)
        elif ks is not None:
            key = import_key(ks, passphrase=passphrase)
        else:
            key = rsa.generate_private_key(
                public_exponent=65537, key_size=KEY_SIZE
            )
        if not isinstance(key, (RSAPrivateKey, RSAPublicKey)):
            raise InvalidKeyError()
        if key.key_size != KEY_SIZE:
            raise InvalidKeyError()
        self.key: RSAKey = key

    @property
    def private_key(self) -> RSAPrivateKey | None:
        return self.key if isinstance(self.key, RSAPrivateKey) else None

    @property
    def public_key(self) -> RSAPublicKey:
        if isinstance(self.key, RSAPrivateKey):
            return self.key.public_key()
        return self.key

    @property
    def private_key_b58(self) -> str:
        return self.export_private_key_b58()

    @property
    def public_key_b64(self) -> str:
        return b64encode(export_binary_key(self.public_key))

    @property
    def address(self) -> str:
        aks = b58encode(mill_hash_bin(export_binary_key(self.public_key)))
        return f'{ADDRESS_TAG}{aks}{ADDRESS_TAG}'

    def export_private_key_pem(self, passphrase: str | None = None) -> bytes:
        if self.private_key is None:
            raise NoPrivateKeyError()
        encryption: serialization.KeySerializationEncryption
        if passphrase is None:
            encryption = serialization.NoEncryption()
        else:
            encryption = serialization.BestAvailableEncryption(
                passphrase.encode()
            )
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )

    def export_private_key_b58(self, passphrase: str | None = None) -> str:
        if self.private_key is None:
            raise NoPrivateKeyError()
        return b58encode(
            export_binary_key(self.private_key, passphrase=passphrase)
        )

    def sign(self, data: bytes) -> str:
        if self.private_key is None:
            raise NoPrivateKeyError()
        sig = self.private_key.sign(data, padding.PKCS1v15(), hashes.SHA384())
        return b64encode(sig)

    def validate_signature(self, data: bytes, signature: str | None) -> bool:
        if not (data and signature):
            return False
        try:
            self.public_key.verify(
                b64decode(signature),
                data,
                padding.PKCS1v15(),
                hashes.SHA384(),
            )
        except (InvalidSignature, binascii.Error, ValueError, TypeError):
            # InvalidSignature: pyca raises this on a bad signature.
            # binascii.Error: malformed base64 (bad padding, non-b64
            #   chars). It's a subclass of ValueError in Python 3 so
            #   the ValueError catch alone would suffice — explicit
            #   listing makes the b64 failure path obvious.
            # ValueError: bad-length signature bytes after b64decode.
            # TypeError: wrong types from caller.
            return False
        return True

    def to_dict(self) -> dict[str, str]:
        return {'private_key': self.private_key_b58}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_file(
        self, walletdir: str | None = None, passphrase: str | None = None
    ) -> str:
        filename = f'{self.address}.pem'
        if walletdir:
            filename = os.path.join(walletdir, filename)
        with open(filename, 'wb') as f:
            f.write(self.export_private_key_pem(passphrase=passphrase))
        return filename

    def __repr__(self) -> str:
        return f'Wallet({self.address})'

    __hash__: None = None  # type: ignore[assignment]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Wallet):
            return NotImplemented
        # cryptography RSAPrivateKey doesn't implement __eq__ by key
        # material; compare via unencrypted DER export instead.
        # RSAPublicKey does implement __eq__ correctly so we let pyca
        # handle the public-key path.
        if isinstance(self.key, RSAPrivateKey) and isinstance(
            other.key, RSAPrivateKey
        ):
            return export_binary_key(self.key) == export_binary_key(other.key)
        return bool(self.key == other.key)

    @classmethod
    def from_dict(cls, wallet_dict: dict[str, Any]) -> Wallet:
        return cls(b58ks=wallet_dict.get('private_key'))

    @classmethod
    def from_json(cls, wallet_json: str) -> Wallet:
        return cls.from_dict(json.loads(wallet_json))

    @classmethod
    def from_file(cls, filename: str, passphrase: str | None = None) -> Wallet:
        with open(filename, 'rb') as f:
            return cls(ks=f.read(), passphrase=passphrase)
