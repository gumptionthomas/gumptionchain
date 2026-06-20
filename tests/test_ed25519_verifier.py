from binascii import unhexlify

from gumptionchain.ed25519 import L as ED_L
from gumptionchain.ed25519 import P as ED_P
from gumptionchain.ed25519 import verify


def test_rfc8032_test1_accepts():
    public = unhexlify(
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
    )
    signature = unhexlify(
        'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f'
        'b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'
    )
    assert verify(public, signature, b'') is True


def test_rfc8032_test2_accepts():
    public = unhexlify(
        '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c'
    )
    signature = unhexlify(
        '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08'
        '5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'
    )
    assert verify(public, signature, unhexlify('72')) is True


def test_flipped_message_byte_rejects():
    public = unhexlify(
        '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c'
    )
    signature = unhexlify(
        '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08'
        '5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'
    )
    assert verify(public, signature, unhexlify('73')) is False


def test_wrong_lengths_reject():
    assert verify(b'\x00' * 31, b'\x00' * 64, b'm') is False
    assert verify(b'\x00' * 32, b'\x00' * 63, b'm') is False


# The 8 canonical small-order point encodings on Ed25519 (order divides 8).
# Source: RFC 8032 / "Taming the many EdDSAs" small-order set.
SMALL_ORDER = [
    unhexlify(
        '0100000000000000000000000000000000000000000000000000000000000000'
    ),
    unhexlify(
        'ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f'
    ),
    unhexlify(
        '0000000000000000000000000000000000000000000000000000000000000000'
    ),
    unhexlify(
        '0000000000000000000000000000000000000000000000000000000000000080'
    ),
    unhexlify(
        '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05'
    ),
    unhexlify(
        '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85'
    ),
    unhexlify(
        'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a'
    ),
    unhexlify(
        'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa'
    ),
]


def test_small_order_public_key_rejected():
    # Any signature under a small-order A must be rejected (Option B), whatever
    # the signature bytes — these public keys are never legitimate signers.
    for a in SMALL_ORDER:
        assert verify(a, b'\x00' * 64, b'message') is False


def test_non_canonical_public_key_rejected():
    # y == P is a non-canonical encoding; recover_x rejects y >= P.
    non_canonical = ED_P.to_bytes(32, 'little')
    assert verify(non_canonical, b'\x00' * 64, b'm') is False


def test_scalar_s_not_below_l_rejected():
    # A signature whose S == L (and S == L + 1) must be rejected (canonical
    # scalar rule), even though A and R decode fine. Use RFC 8032 TEST 1's
    # public key and the R half of its signature so decoding succeeds.
    public = unhexlify(
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
    )
    r = unhexlify(
        'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155'
    )
    for s_val in (ED_L, ED_L + 1):
        sig = r + s_val.to_bytes(32, 'little')
        assert verify(public, sig, b'') is False
