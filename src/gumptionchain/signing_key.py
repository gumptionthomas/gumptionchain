from __future__ import annotations

import binascii
import contextlib
import json
import os
from base64 import standard_b64decode, standard_b64encode
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from gumptionchain import ed25519 as gc_ed25519
from gumptionchain.bech32 import (
    decode_address,
    decode_secret,
    encode_address,
    encode_secret,
)
from gumptionchain.exceptions import InvalidKeyError, NoPrivateKeyError

SignKey = Ed25519PrivateKey | Ed25519PublicKey


def b64decode(s: str) -> bytes:
    return standard_b64decode(s.encode())


def b64encode(b: bytes) -> str:
    return standard_b64encode(b).decode()


def export_binary_key(key: SignKey, passphrase: str | None = None) -> bytes:
    if isinstance(key, Ed25519PublicKey):
        return key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    encryption: serialization.KeySerializationEncryption = (
        serialization.NoEncryption()
        if passphrase is None
        else serialization.BestAvailableEncryption(passphrase.encode())
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def import_key(
    ks: bytes | str, passphrase: str | None = None
) -> SignKey | None:
    """Load an Ed25519 key from PEM or DER bytes. Accepts both
    private and public keys (api.py / schema.py / models.py construct
    SigningKey with a peer's public key alone for signature verification).
    """
    try:
        if isinstance(ks, str):
            ks = ks.encode()
        password = passphrase.encode() if passphrase is not None else None
        # lstrip handles leading whitespace/newlines from copy-pasted PEMs
        is_pem = ks.lstrip().startswith(b'-----BEGIN')
        # Private-key path first (the common case for signing_key load flows)
        try:
            key = (
                serialization.load_pem_private_key(ks, password)
                if is_pem
                else serialization.load_der_private_key(ks, password)
            )
            if isinstance(key, Ed25519PrivateKey):
                return key
        except Exception:
            pass
        # Public-key fallback (peer-public-key wrap path)
        pub = (
            serialization.load_pem_public_key(ks)
            if is_pem
            else serialization.load_der_public_key(ks)
        )
        return pub if isinstance(pub, Ed25519PublicKey) else None
    except Exception:
        return None


def import_b64_key(ks: str, passphrase: str | None = None) -> SignKey | None:
    try:
        return import_key(b64decode(ks), passphrase=passphrase)
    except Exception:
        return None


def import_secret_key(secret: str) -> SignKey | None:
    raw = decode_secret(secret)
    if raw is None:
        return None
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError):
        return None


def public_key_from_address(address: str) -> Ed25519PublicKey:
    raw = decode_address(address)
    if raw is None:
        raise InvalidKeyError()
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as e:
        raise InvalidKeyError() from e


class SigningKey:
    def __init__(
        self,
        b64ks: str | None = None,
        secret: str | None = None,
        ks: bytes | str | None = None,
        passphrase: str | None = None,
    ) -> None:
        key: SignKey | None
        if b64ks is not None:
            key = import_b64_key(b64ks, passphrase=passphrase)
        elif secret is not None:
            key = import_secret_key(secret)
        elif ks is not None:
            key = import_key(ks, passphrase=passphrase)
        else:
            key = Ed25519PrivateKey.generate()
        if not isinstance(key, (Ed25519PrivateKey, Ed25519PublicKey)):
            raise InvalidKeyError()
        self.key: SignKey = key

    @property
    def private_key(self) -> Ed25519PrivateKey | None:
        return self.key if isinstance(self.key, Ed25519PrivateKey) else None

    @property
    def public_key(self) -> Ed25519PublicKey:
        if isinstance(self.key, Ed25519PrivateKey):
            return self.key.public_key()
        return self.key

    @property
    def secret(self) -> str:
        return self.export_secret()

    @property
    def public_key_b64(self) -> str:
        return b64encode(export_binary_key(self.public_key))

    @property
    def address(self) -> str:
        raw = self.public_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return encode_address(raw)

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

    def export_secret(self) -> str:
        if self.private_key is None:
            raise NoPrivateKeyError()
        return encode_secret(self.private_key.private_bytes_raw())

    def sign(self, data: bytes) -> str:
        pk = self.private_key
        if pk is None:
            raise NoPrivateKeyError()
        return b64encode(pk.sign(data))

    def validate_signature(self, data: bytes, signature: str | None) -> bool:
        if not (data and signature):
            return False
        try:
            raw = self.public_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            return gc_ed25519.verify(raw, b64decode(signature), data)
        except (binascii.Error, ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, str]:
        return {'private_key': self.secret}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_file(
        self, signing_keydir: str | None = None, passphrase: str | None = None
    ) -> str:
        filename = f'{self.address}.pem'
        if signing_keydir:
            filename = os.path.join(signing_keydir, filename)
        pem = self.export_private_key_pem(passphrase=passphrase)
        # Write the private key owner-only and exclusively (audit CLI1).
        # O_EXCL refuses to follow a pre-planted symlink or clobber an
        # existing key; the 0o600 mode means the key is never momentarily
        # group/world-readable (a plain open('wb') inherits the umask,
        # commonly 0o644). umask can only clear bits, never set them, so it
        # can never widen 0o600 to group/world access — a pathological umask
        # could at most further restrict the owner bits, never expand them.
        fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(pem)
        except BaseException:
            # The file was newly created (O_EXCL): don't leave a partial or
            # empty key behind (read_signing_keys tries to load it). os.fdopen
            # closes the fd on its own failure; the with-block closes it on a
            # write failure — so here we only remove the orphaned file.
            with contextlib.suppress(OSError):
                os.unlink(filename)
            raise
        return filename

    def __repr__(self) -> str:
        return f'SigningKey({self.address})'

    __hash__: None = None  # type: ignore[assignment]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SigningKey):
            return NotImplemented
        # Ed25519PrivateKey does not implement __eq__ by key material in pyca;
        # compare via unencrypted DER export instead. Public keys implement
        # __eq__ correctly so we let pyca handle those.
        if isinstance(self.key, Ed25519PrivateKey) and isinstance(
            other.key, Ed25519PrivateKey
        ):
            return export_binary_key(self.key) == export_binary_key(other.key)
        return bool(self.key == other.key)

    @classmethod
    def from_address(cls, address: str) -> SigningKey:
        """Public-only SigningKey reconstructed from a gc1… address.

        Raises InvalidKeyError on a malformed/bad-checksum address.
        """
        pub = public_key_from_address(address)
        sk = cls.__new__(cls)
        sk.key = pub
        return sk

    @classmethod
    def from_secret(cls, secret: str) -> SigningKey:
        return cls(secret=secret)

    @classmethod
    def generate_ed25519(cls) -> SigningKey:
        return cls()

    @classmethod
    def from_ed25519_seed(cls, seed: bytes) -> SigningKey:
        # Surface a bad seed (wrong length / type) as InvalidKeyError, matching
        # how the rest of SigningKey construction reports invalid key material
        # (pyca raises a raw ValueError on a non-32-byte seed).
        try:
            priv = Ed25519PrivateKey.from_private_bytes(seed)
        except (ValueError, TypeError) as e:
            raise InvalidKeyError() from e
        der = priv.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cls(ks=der)

    @classmethod
    def from_dict(cls, signing_key_dict: dict[str, Any]) -> SigningKey:
        # Fail closed on a missing/None 'private_key': passing secret=None to
        # the constructor would fall through to keygen and silently mint a NEW
        # identity (a malformed/truncated restore file would look like success
        # while producing the wrong key). A restore must error, not generate.
        secret = signing_key_dict.get('private_key')
        if secret is None:
            raise InvalidKeyError()
        return cls(secret=secret)

    @classmethod
    def from_json(cls, signing_key_json: str) -> SigningKey:
        return cls.from_dict(json.loads(signing_key_json))

    @classmethod
    def from_file(
        cls, filename: str, passphrase: str | None = None
    ) -> SigningKey:
        with open(filename, 'rb') as f:
            return cls(ks=f.read(), passphrase=passphrase)
