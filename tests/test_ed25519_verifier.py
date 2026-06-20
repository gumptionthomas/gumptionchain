from binascii import unhexlify

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
