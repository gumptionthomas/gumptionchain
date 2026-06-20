import os

import pytest

from gumptionchain.bech32 import (
    CHARSET,
    Encoding,
    bech32_decode,
    bech32_encode,
    convertbits,
    decode_address,
    encode_address,
)


def test_round_trip_zero_pubkey():
    pubkey = b'\x00' * 32
    assert decode_address(encode_address(pubkey)) == pubkey


def test_round_trip_random_pubkey():
    pubkey = os.urandom(32)
    assert decode_address(encode_address(pubkey)) == pubkey


def test_address_starts_with_gc1():
    assert encode_address(b'\x00' * 32).startswith('gc1')


def test_single_char_flip_rejected():
    addr = encode_address(os.urandom(32))
    # Flip a character in the data part (after the 'gc1' separator).
    idx = len(addr) - 5
    flipped = 'q' if addr[idx] != 'q' else 'p'
    bad = addr[:idx] + flipped + addr[idx + 1 :]
    assert decode_address(bad) is None


def test_wrong_hrp_rejected():
    pubkey = os.urandom(32)
    data = convertbits(list(pubkey), 8, 5)
    assert data is not None
    other = bech32_encode('bc', data, Encoding.BECH32M)
    assert decode_address(other) is None


def test_non_32_byte_payload_rejected():
    data = convertbits(list(b'short'), 8, 5)
    assert data is not None
    short = bech32_encode('gc', data, Encoding.BECH32M)
    assert decode_address(short) is None


def test_bech32_non_m_rejected():
    pubkey = os.urandom(32)
    data = convertbits(list(pubkey), 8, 5)
    assert data is not None
    non_m = bech32_encode('gc', data, Encoding.BECH32)
    assert decode_address(non_m) is None


def test_uppercase_address_decodes():
    # bech32 is case-insensitive (uppercase is used in QR codes); per BIP-350
    # the decoder normalizes case. An all-uppercase address must round-trip.
    pubkey = os.urandom(32)
    addr = encode_address(pubkey)
    assert decode_address(addr.upper()) == pubkey


def test_mixed_case_address_rejected():
    # ...but a MIXED-case string is invalid per the spec and must be rejected.
    # Uppercase only the HRP's first letter ('g') so the string is guaranteed
    # mixed-case regardless of which (caseless) digits the checksum produced.
    addr = encode_address(os.urandom(32))
    mixed = 'G' + addr[1:]
    assert decode_address(mixed) is None


def test_encode_address_rejects_non_32_byte_pubkey():
    with pytest.raises(ValueError, match='32-byte'):
        encode_address(b'\x00' * 31)
    with pytest.raises(ValueError, match='32-byte'):
        encode_address(b'\x00' * 33)


def test_exhaustive_single_char_mutation_never_yields_same_key():
    # The headline guarantee: NO single-character transcription error on an
    # address can silently decode back to the original key. Sweep every
    # position in the data+checksum part against every other charset symbol.
    pubkey = bytes(range(32))
    addr = encode_address(pubkey)
    sep = addr.index('1')  # mutate only after the 'gc1' separator
    mutations = 0
    charset = CHARSET.decode() if isinstance(CHARSET, bytes) else CHARSET
    for i in range(sep + 1, len(addr)):
        for c in charset:
            if c == addr[i]:
                continue
            bad = addr[:i] + c + addr[i + 1 :]
            # decode_address returns None (bad checksum) or a DIFFERENT key —
            # never the original. A single-char error is always detectable.
            assert decode_address(bad) != pubkey
            mutations += 1
    assert mutations >= 1700  # the full sweep actually ran


# Official BIP-350 test vectors (https://github.com/bitcoin/bips, BIP-0350).
_BIP350_VALID = [
    'A1LQFN3A',
    'a1lqfn3a',
    'an83characterlonghumanreadablepartthatcontainsthetheexcludedcharact'
    'ersbioandnumber11sg7hg6',
    'abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx',
    'split1checkupstagehandshakeupstreamerranterredcaperredlc445v',
    '?1v759aa',
]

_BIP350_INVALID = [
    '\x201xj0phk',  # HRP char out of range (0x20)
    '\x7f1g6xzxy',  # HRP char out of range (0x7f)
    '\x801vctc34',  # HRP char out of range (0x80)
    'an84characterslonghumanreadablepartthatcontainsthetheexcludedcharact'
    'ersbioandnumber11d6pts4',  # overall max length exceeded
    'qyrz8wqd2c9m',  # no separator
    '1qyrz8wqd2c9m',  # empty HRP
    'y1b0jsk6g',  # invalid data char
    'lt1igcx5c0',  # invalid data char
    'in1muywd',  # too short checksum
    'mm1crxm3i',  # invalid char in checksum
    'au1s5cgom',  # invalid char in checksum
    'M1VUXWEZ',  # checksum computed with uppercase HRP (needs case-normalize)
    '16plkw9',  # empty HRP, too short checksum
    '1p2gdwpf',  # empty HRP
]


@pytest.mark.parametrize('vec', _BIP350_VALID)
def test_bip350_valid_bech32m_vectors_decode(vec):
    hrp, data, spec = bech32_decode(vec)
    assert hrp is not None and data is not None
    assert spec == Encoding.BECH32M


@pytest.mark.parametrize('vec', _BIP350_INVALID)
def test_bip350_invalid_bech32m_vectors_rejected(vec):
    assert bech32_decode(vec) == (None, None, None)
