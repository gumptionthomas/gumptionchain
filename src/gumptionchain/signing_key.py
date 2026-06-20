from __future__ import annotations

import binascii
import contextlib
import json
import os
from base64 import standard_b64decode, standard_b64encode
from typing import Any

import base58check
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)

from gumptionchain import ed25519 as gc_ed25519
from gumptionchain.exceptions import InvalidKeyError, NoPrivateKeyError
from gumptionchain.milling import mill_hash_bin

RSAKey = RSAPrivateKey | RSAPublicKey
SignKey = RSAPrivateKey | RSAPublicKey | Ed25519PrivateKey | Ed25519PublicKey

ADDRESS_TAG = 'GC'
KEY_SIZE = 2048
PUBLIC_EXPONENT = 65537


def b58decode(s: str) -> bytes:
    return base58check.b58decode(s.encode())  # type: ignore[no-any-return]


def b58encode(b: bytes) -> str:
    return base58check.b58encode(b).decode()  # type: ignore[no-any-return]


def b64decode(s: str) -> bytes:
    return standard_b64decode(s.encode())


def b64encode(b: bytes) -> str:
    return standard_b64encode(b).decode()


def export_binary_key(key: SignKey, passphrase: str | None = None) -> bytes:
    if isinstance(key, (RSAPublicKey, Ed25519PublicKey)):
        return key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
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


def import_key(
    ks: bytes | str, passphrase: str | None = None
) -> SignKey | None:
    """Load an RSA or Ed25519 key from PEM or DER bytes. Accepts both
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
            if is_pem:
                key = serialization.load_pem_private_key(ks, password)
            else:
                key = serialization.load_der_private_key(ks, password)
            if isinstance(key, (RSAPrivateKey, Ed25519PrivateKey)):
                return key
        except Exception:
            pass
        # Public-key fallback (peer-public-key wrap path)
        pub = (
            serialization.load_pem_public_key(ks)
            if is_pem
            else serialization.load_der_public_key(ks)
        )
        if isinstance(pub, (RSAPublicKey, Ed25519PublicKey)):
            return pub
        return None
    except Exception:
        return None


def import_b58_key(ks: str, passphrase: str | None = None) -> SignKey | None:
    try:
        return import_key(b58decode(ks), passphrase=passphrase)
    except Exception:
        return None


def import_b64_key(ks: str, passphrase: str | None = None) -> SignKey | None:
    try:
        return import_key(b64decode(ks), passphrase=passphrase)
    except Exception:
        return None


class SigningKey:
    def __init__(
        self,
        b64ks: str | None = None,
        b58ks: str | None = None,
        ks: bytes | str | None = None,
        passphrase: str | None = None,
    ) -> None:
        key: SignKey | None
        if b64ks is not None:
            key = import_b64_key(b64ks, passphrase=passphrase)
        elif b58ks is not None:
            key = import_b58_key(b58ks, passphrase=passphrase)
        elif ks is not None:
            key = import_key(ks, passphrase=passphrase)
        else:
            key = rsa.generate_private_key(
                public_exponent=PUBLIC_EXPONENT, key_size=KEY_SIZE
            )
        if not isinstance(
            key,
            (RSAPrivateKey, RSAPublicKey, Ed25519PrivateKey, Ed25519PublicKey),
        ):
            raise InvalidKeyError()
        if isinstance(key, (RSAPrivateKey, RSAPublicKey)):
            if key.key_size != KEY_SIZE:
                raise InvalidKeyError()
            # Reject non-standard public exponents on import (audit WC2).
            # pyca accepts degenerate exponents (e.g. e=3); pin e to the
            # same value this node generates so keys share one profile.
            pub = key.public_key() if isinstance(key, RSAPrivateKey) else key
            if pub.public_numbers().e != PUBLIC_EXPONENT:
                raise InvalidKeyError()
        self.key: SignKey = key

    @property
    def private_key(self) -> RSAPrivateKey | Ed25519PrivateKey | None:
        if isinstance(self.key, (RSAPrivateKey, Ed25519PrivateKey)):
            return self.key
        return None

    @property
    def public_key(self) -> RSAPublicKey | Ed25519PublicKey:
        if isinstance(self.key, (RSAPrivateKey, Ed25519PrivateKey)):
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
        pk = self.private_key
        if pk is None:
            raise NoPrivateKeyError()
        if isinstance(pk, Ed25519PrivateKey):
            sig = pk.sign(data)
        else:
            sig = pk.sign(data, padding.PKCS1v15(), hashes.SHA384())
        return b64encode(sig)

    def validate_signature(self, data: bytes, signature: str | None) -> bool:
        if not (data and signature):
            return False
        pub = self.public_key
        if isinstance(pub, Ed25519PublicKey):
            try:
                raw = pub.public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
                return gc_ed25519.verify(raw, b64decode(signature), data)
            except (binascii.Error, ValueError, TypeError):
                return False
        try:
            pub.verify(
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
        # Neither RSAPrivateKey nor Ed25519PrivateKey implement __eq__ by key
        # material in pyca; compare via unencrypted DER export instead.
        # Public keys implement __eq__ correctly so we let pyca handle those.
        priv_types = (RSAPrivateKey, Ed25519PrivateKey)
        if isinstance(self.key, priv_types) and isinstance(
            other.key, priv_types
        ):
            return export_binary_key(self.key) == export_binary_key(other.key)
        return bool(self.key == other.key)

    @classmethod
    def generate_ed25519(cls) -> SigningKey:
        priv = Ed25519PrivateKey.generate()
        der = priv.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cls(ks=der)

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
        return cls(b58ks=signing_key_dict.get('private_key'))

    @classmethod
    def from_json(cls, signing_key_json: str) -> SigningKey:
        return cls.from_dict(json.loads(signing_key_json))

    @classmethod
    def from_file(
        cls, filename: str, passphrase: str | None = None
    ) -> SigningKey:
        with open(filename, 'rb') as f:
            return cls(ks=f.read(), passphrase=passphrase)
