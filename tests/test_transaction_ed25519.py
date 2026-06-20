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
    # The verifying key (DER SPKI b64 — what the old wire carried) must NOT
    # appear in the txid preimage; it's reconstructed from the address. A bare
    # `'public_key' not in data_csv` substring check would be a no-op (the
    # preimage held the key VALUE, never the literal field name), so assert the
    # actual key bytes are absent.
    assert single_txn.signing_key.public_key_b64 not in single_txn.data_csv
    # And structurally: the field after the address is the first inflow, with
    # no extra segment wedged between address (field 1) and inflows (field 2).
    segments = single_txn.data_csv.split(',')
    assert segments[1] == single_txn.address
    assert segments[2] == single_txn.inflows[0].data_csv.split(',')[0]


def test_txid_excludes_the_signature(single_txn):
    # Defense-in-depth against Ed25519 malleability: the txid is committed by
    # seal() (over data_csv: timestamp/address/flows/version) BEFORE
    # signing, so signature bytes never enter the id. Thus a malleated-but-valid
    # signature cannot produce a second txid for one logical transaction.
    single_txn.set_signing_key(SigningKey.generate_ed25519())
    single_txn.seal()
    txid_before_signing = single_txn.txid
    single_txn.sign()
    assert single_txn.txid == txid_before_signing
    assert single_txn.calculate_txid() == txid_before_signing
