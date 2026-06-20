import os

from gumptionchain.bech32 import (
    Encoding,
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
