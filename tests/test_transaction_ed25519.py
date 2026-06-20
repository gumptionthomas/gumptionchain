import pytest

from gumptionchain.exceptions import InvalidSignatureError
from gumptionchain.signing_key import SigningKey


def test_ed25519_transaction_validates_end_to_end(single_txn):
    single_txn.set_signing_key(SigningKey.generate_ed25519())
    single_txn.seal()
    single_txn.sign()
    single_txn.validate()  # signature + txid + address; must not raise


def test_ed25519_transaction_address_mismatch_fails(single_txn):
    single_txn.set_signing_key(SigningKey.generate_ed25519())
    # The address IS the verifying key now (no separate public_key on the
    # wire), so overwriting it to a different key makes signature
    # verification reconstruct the wrong key and reject the signature.
    single_txn.seal()
    single_txn.sign()
    single_txn.address = SigningKey.generate_ed25519().address
    with pytest.raises(InvalidSignatureError):
        single_txn.validate_signature()


def test_txn_has_no_public_key_and_verifies_via_address(single_txn):
    single_txn.set_signing_key(SigningKey())
    single_txn.seal()
    single_txn.sign()
    single_txn.validate()  # reconstructs key from address; must not raise
    assert (
        not hasattr(single_txn, 'public_key') or single_txn.public_key is None
    )
    assert 'public_key' not in single_txn.data_csv  # not in the txid preimage


def test_txid_excludes_the_signature(single_txn):
    # Defense-in-depth against Ed25519 malleability: the txid is committed by
    # seal() (over data_csv: timestamp/address/public_key/flows/version) BEFORE
    # signing, so signature bytes never enter the id. Thus a malleated-but-valid
    # signature cannot produce a second txid for one logical transaction.
    single_txn.set_signing_key(SigningKey.generate_ed25519())
    single_txn.seal()
    txid_before_signing = single_txn.txid
    single_txn.sign()
    assert single_txn.txid == txid_before_signing
    assert single_txn.calculate_txid() == txid_before_signing
