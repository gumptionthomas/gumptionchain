import hashlib

import pytest

from gumptionchain.bip39 import mnemonic_to_seed, seed_to_mnemonic
from gumptionchain.bip39_wordlist import WORDLIST


def test_wordlist_is_official_bip39_english():
    assert len(WORDLIST) == 2048
    assert WORDLIST[0] == 'abandon'
    assert WORDLIST[-1] == 'zoo'
    joined = ('\n'.join(WORDLIST) + '\n').encode()
    assert (
        hashlib.sha256(joined).hexdigest()
        == '2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda'
    )


def test_round_trip():
    seed = bytes(range(32))
    m = seed_to_mnemonic(seed)
    assert len(m.split()) == 24
    assert mnemonic_to_seed(m) == seed


def test_all_zero_vector():
    m = seed_to_mnemonic(bytes(32))
    assert m.split()[-1] == 'art'
    assert m.split()[0] == 'abandon'
    assert mnemonic_to_seed(m) == bytes(32)


def test_all_ff_official_vector():
    # Official BIP-39 256-bit all-0xFF vector ends in 'vote' (24-word).
    m = seed_to_mnemonic(bytes([0xFF]) * 32)
    assert m.split()[-1] == 'vote'
    assert mnemonic_to_seed(m) == bytes([0xFF]) * 32


def test_bad_checksum_rejected():
    seed = bytes(range(1, 33))
    words = seed_to_mnemonic(seed).split()
    words[-1] = 'zone' if words[-1] == 'zoo' else 'zoo'
    with pytest.raises(ValueError, match='checksum'):
        mnemonic_to_seed(' '.join(words))


def test_bad_word_and_length_rejected():
    with pytest.raises(ValueError, match='24'):
        mnemonic_to_seed('too short')
    words = seed_to_mnemonic(bytes(32)).split()
    words[0] = 'notaword'
    with pytest.raises(ValueError, match='invalid'):
        mnemonic_to_seed(' '.join(words))


def test_seed_must_be_32_bytes():
    with pytest.raises(ValueError, match='32'):
        seed_to_mnemonic(bytes(31))
