import json
import os
from base64 import standard_b64decode, standard_b64encode

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


def b58decode(s):
    return base58check.b58decode(s.encode())


def b58encode(b):
    return base58check.b58encode(b).decode()


def b64decode(s):
    return standard_b64decode(s.encode())


def b64encode(b):
    return standard_b64encode(b).decode()


def export_binary_key(key, passphrase=None):
    if passphrase is None:
        return key.export_key(format='DER')
    else:
        return key.export_key(format='DER', pkcs=8, passphrase=passphrase)


def import_key(ks, passphrase=None):
    try:
        return RSA.import_key(ks, passphrase=passphrase)
    except Exception:
        return None


def import_b58_key(ks, passphrase=None):
    try:
        return import_key(b58decode(ks), passphrase=passphrase)
    except Exception:
        return None


def import_b64_key(ks, passphrase=None):
    try:
        return import_key(b64decode(ks), passphrase=passphrase)
    except Exception:
        return None


class Wallet:
    def __init__(self, b64ks=None, b58ks=None, ks=None, passphrase=None):
        if b64ks is not None:
            self.key = import_b64_key(b64ks, passphrase=passphrase)
        elif b58ks is not None:
            self.key = import_b58_key(b58ks, passphrase=passphrase)
        elif ks is not None:
            self.key = import_key(ks, passphrase=passphrase)
        else:
            self.key = RSA.generate(KEY_SIZE)
        if not (self.key and self.key.size_in_bits() == KEY_SIZE):
            raise InvalidKeyError()

    @property
    def private_key(self):
        return self.key if self.key.has_private() else None

    @property
    def public_key(self):
        return self.private_key.public_key() if self.private_key else self.key

    @property
    def private_key_b58(self):
        return self.export_private_key_b58()

    @property
    def public_key_b64(self):
        return b64encode(export_binary_key(self.public_key))

    @property
    def address(self):
        aks = b58encode(mill_hash_bin(export_binary_key(self.public_key)))
        return f'{ADDRESS_TAG}{aks}{ADDRESS_TAG}'

    def export_private_key_pem(self, passphrase=None):
        if self.private_key is None:
            raise NoPrivateKeyError()
        return self.private_key.export_key(
            pkcs=1 if passphrase is None else 8,
            passphrase=passphrase,
            protection='scryptAndAES128-CBC',
        )

    def export_private_key_b58(self, passphrase=None):
        if self.private_key is None:
            raise NoPrivateKeyError()
        return b58encode(
            export_binary_key(self.private_key, passphrase=passphrase)
        )

    def sign(self, data):
        if self.private_key is None:
            raise NoPrivateKeyError()
        signer = PKCS1_v1_5.new(self.private_key)
        hasher = SHA384.new(data=data)
        return b64encode(signer.sign(hasher))

    def validate_signature(self, data, signature):
        if not (data and signature):
            return False
        verifier = PKCS1_v1_5.new(self.public_key)
        hasher = SHA384.new(data=data)
        return verifier.verify(hasher, b64decode(signature))

    def encrypt(self, data):
        session_key = Crypto.Random.get_random_bytes(16)
        cipher_rsa = PKCS1_OAEP.new(self.public_key)
        enc_session_key = cipher_rsa.encrypt(session_key)
        cipher_aes = AES.new(session_key, AES.MODE_EAX)
        ciphertext, tag = cipher_aes.encrypt_and_digest(data)
        return b64encode(
            b''.join(
                x for x in (enc_session_key, cipher_aes.nonce, tag, ciphertext)
            )
        )

    def decrypt(self, msg):
        def msg_parts(key_size, msg):
            for n in (key_size, 16, 16):
                yield msg[:n]
                msg = msg[n:]
            yield msg

        if self.private_key is None:
            raise NoPrivateKeyError()
        part = msg_parts(self.private_key.size_in_bytes(), b64decode(msg))
        cipher_rsa = PKCS1_OAEP.new(self.private_key)
        session_key = cipher_rsa.decrypt(next(part))
        cipher_aes = AES.new(session_key, AES.MODE_EAX, next(part))
        tag = next(part)
        data = cipher_aes.decrypt_and_verify(next(part), tag)
        return data

    def to_dict(self):
        return {'private_key': self.private_key_b58}

    def to_json(self):
        return json.dumps(self.to_dict())

    def to_file(self, walletdir=None, passphrase=None):
        filename = f'{self.address}.pem'
        if walletdir:
            filename = os.path.join(walletdir, filename)
        with open(filename, 'wb') as f:
            f.write(self.export_private_key_pem(passphrase=passphrase))
        return filename

    def __repr__(self):
        return f'Wallet({self.address})'

    __hash__ = None  # not used as dict key/set member

    def __eq__(self, other):
        return self.key == other.key

    @classmethod
    def from_dict(cls, wallet_dict):
        return cls(b58ks=wallet_dict.get('private_key'))

    @classmethod
    def from_json(cls, wallet_json):
        return cls.from_dict(json.loads(wallet_json))

    @classmethod
    def from_file(cls, filename, passphrase=None):
        with open(filename, 'rb') as f:
            return cls(ks=f.read(), passphrase=passphrase)
