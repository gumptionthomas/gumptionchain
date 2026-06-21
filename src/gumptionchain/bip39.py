from __future__ import annotations

import hashlib

from gumptionchain.bip39_wordlist import WORDLIST

_INDEX = {w: i for i, w in enumerate(WORDLIST)}


def seed_to_mnemonic(seed: bytes) -> str:
    """Encode a raw 32-byte Ed25519 seed as a 24-word BIP-39 phrase."""
    if len(seed) != 32:
        msg = f'seed_to_mnemonic requires a 32-byte seed, got {len(seed)}'
        raise ValueError(msg)
    checksum = hashlib.sha256(seed).digest()
    bits: list[int] = []
    for b in seed:
        bits.extend((b >> i) & 1 for i in range(7, -1, -1))
    bits.extend((checksum[0] >> i) & 1 for i in range(7, -1, -1))
    words = []
    for i in range(0, len(bits), 11):
        idx = 0
        for j in range(11):
            idx = (idx << 1) | bits[i + j]
        words.append(WORDLIST[idx])
    return ' '.join(words)


def mnemonic_to_seed(mnemonic: str) -> bytes:
    """Decode a 24-word BIP-39 phrase back to the raw 32-byte seed."""
    words = mnemonic.strip().split()
    if len(words) != 24:
        msg = 'expected a 24-word recovery phrase'
        raise ValueError(msg)
    bits: list[int] = []
    for w in words:
        if w not in _INDEX:
            msg = f'invalid recovery word: {w}'
            raise ValueError(msg)
        idx = _INDEX[w]
        bits.extend((idx >> j) & 1 for j in range(10, -1, -1))
    seed = bytearray(32)
    for i in range(256):
        seed[i >> 3] |= bits[i] << (7 - (i % 8))
    checksum = hashlib.sha256(bytes(seed)).digest()
    for i in range(8):
        if bits[256 + i] != ((checksum[0] >> (7 - i)) & 1):
            msg = 'recovery phrase checksum failed'
            raise ValueError(msg)
    return bytes(seed)
