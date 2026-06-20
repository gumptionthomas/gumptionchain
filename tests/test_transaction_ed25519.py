import pytest

from gumptionchain.exceptions import InvalidTransactionError
from gumptionchain.signing_key import SigningKey


def test_ed25519_transaction_validates_end_to_end(single_txn):
    single_txn.set_signing_key(SigningKey.generate_ed25519())
    single_txn.seal()
    single_txn.sign()
    single_txn.validate()  # signature + txid + address; must not raise


def test_ed25519_transaction_address_mismatch_fails(single_txn):
    single_txn.set_signing_key(SigningKey.generate_ed25519())
    # Overwrite the address so it no longer derives from the (Ed25519) pubkey.
    single_txn.address = SigningKey.generate_ed25519().address
    single_txn.seal()
    single_txn.sign()
    with pytest.raises(
        InvalidTransactionError, match='Address/public key mismatch'
    ):
        single_txn.validate()
