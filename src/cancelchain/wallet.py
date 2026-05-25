from __future__ import annotations

import json
import os
from base64 import standard_b64decode, standard_b64encode
from collections.abc import Generator
from typing import Any

import base58check
import Crypto.Random
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA384
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5

from cancelchain.exceptions import InvalidKeyError, NoPrivateKeyError
from cancelchain.milling import mill_hash_bin

ADDRESS_TAG = 'CC'
KEY_SIZE = 2048


def b58decode(s: str) -> bytes:
    return base58check.b58decode(s.encode())  # type: ignore[no-any-return]


def b58encode(b: bytes) -> str:
    return base58check.b58encode(b).decode()  # type: ignore[no-any-return]


def b64decode(s: str) -> bytes:
    return standard_b64decode(s.encode())


def b64encode(b: bytes) -> str:
    return standard_b64encode(b).decode()


def export_binary_key(key: Any, passphrase: str | None = None) -> bytes:
    if passphrase is None:
        return key.export_key(format='DER')  # type: ignore[no-any-return]
    else:
        return key.export_key(  # type: ignore[no-any-return]
            format='DER', pkcs=8, passphrase=passphrase
        )


def import_key(ks: bytes | str, passphrase: str | None = None) -> Any | None:
    try:
        return RSA.import_key(ks, passphrase=passphrase)
    except Exception:
        return None


def import_b58_key(ks: str, passphrase: str | None = None) -> Any | None:
    try:
        return import_key(b58decode(ks), passphrase=passphrase)
    except Exception:
        return None


def import_b64_key(ks: str, passphrase: str | None = None) -> Any | None:
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
        if b64ks is not None:
            self.key: Any = import_b64_key(b64ks, passphrase=passphrase)
        elif b58ks is not None:
            self.key = import_b58_key(b58ks, passphrase=passphrase)
        elif ks is not None:
            self.key = import_key(ks, passphrase=passphrase)
        else:
            self.key = RSA.generate(KEY_SIZE)
        if not (self.key and self.key.size_in_bits() == KEY_SIZE):
            raise InvalidKeyError()

    @property
    def private_key(self) -> Any | None:
        return self.key if self.key.has_private() else None

    @property
    def public_key(self) -> Any:
        return self.private_key.public_key() if self.private_key else self.key

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
        return self.private_key.export_key(  # type: ignore[no-any-return]
            pkcs=1 if passphrase is None else 8,
            passphrase=passphrase,
            protection='scryptAndAES128-CBC',
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
        signer = PKCS1_v1_5.new(self.private_key)
        hasher = SHA384.new(data=data)
        return b64encode(signer.sign(hasher))

    def validate_signature(self, data: bytes, signature: str | None) -> bool:
        if not (data and signature):
            return False
        verifier = PKCS1_v1_5.new(self.public_key)
        hasher = SHA384.new(data=data)
        return bool(verifier.verify(hasher, b64decode(signature)))

    def encrypt(self, data: bytes) -> str:
        session_key: bytes = Crypto.Random.get_random_bytes(16)
        cipher_rsa = PKCS1_OAEP.new(self.public_key)
        enc_session_key: bytes = cipher_rsa.encrypt(session_key)
        cipher_aes = AES.new(session_key, AES.MODE_EAX)
        ciphertext, tag = cipher_aes.encrypt_and_digest(data)
        return b64encode(
            b''.join(
                x for x in (enc_session_key, cipher_aes.nonce, tag, ciphertext)
            )
        )

    def decrypt(self, msg: str) -> bytes:
        def msg_parts(
            key_size: int, raw: bytes
        ) -> Generator[bytes, None, None]:
            for n in (key_size, 16, 16):
                yield raw[:n]
                raw = raw[n:]
            yield raw

        if self.private_key is None:
            raise NoPrivateKeyError()
        part = msg_parts(self.private_key.size_in_bytes(), b64decode(msg))
        cipher_rsa = PKCS1_OAEP.new(self.private_key)
        session_key: bytes = cipher_rsa.decrypt(next(part))
        cipher_aes = AES.new(session_key, AES.MODE_EAX, next(part))
        tag = next(part)
        data: bytes = cipher_aes.decrypt_and_verify(next(part), tag)
        return data

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

    __hash__: None = None  # type: ignore[assignment]  # not used as dict key/set member

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Wallet):
            return NotImplemented
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
