import pytest

from gumptionchain.api_client import ApiClient
from gumptionchain.exceptions import InsufficientFundsError, PendingFundsError
from gumptionchain.signing_key import SigningKey


def test_pending_change_raises_pending_funds_error(
    app, host, mill_block, requests_proxy, signing_key
):
    # Spend most of a single coinbase UTXO in an UNCONFIRMED txn, then try to
    # spend again. The change is pending (not yet a confirmed UTXO) and the
    # coinbase is spoken-for, so the builder must report PendingFundsError
    # ("still confirming"), not a generic shortfall — the funds DO exist.
    with app.app_context():
        m, _b = mill_block(signing_key)  # signing_key earns one coinbase reward
        dest = SigningKey().address
        first = m.longest_chain.create_transfer(signing_key, 400, dest)
        first.sign()
        ApiClient(host, signing_key).post_transaction(
            first
        )  # pending, NOT mined

        with pytest.raises(PendingFundsError):
            m.longest_chain.create_transfer(signing_key, 100, dest)
        # the confirmed balance still shows the funds — they're just locked.
        assert m.longest_chain.balance(signing_key.address) >= 100


def test_pending_funds_error_for_stakes_too(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        first = m.longest_chain.create_opposition(signing_key, 400, subject)
        first.sign()
        ApiClient(host, signing_key).post_transaction(first)

        with pytest.raises(PendingFundsError):
            m.longest_chain.create_support(signing_key, 100, subject)


def test_empty_signing_key_raises_plain_insufficient_funds(
    app, host, mill_block, requests_proxy, signing_key
):
    # A key with no funds and nothing pending gets the genuine error — NOT
    # PendingFundsError (there's no in-flight txn locking anything).
    with app.app_context():
        m, _b = mill_block(signing_key)  # funds `signing_key`, not `empty`
        empty = SigningKey()
        with pytest.raises(InsufficientFundsError) as exc:
            m.longest_chain.create_transfer(empty, 100, SigningKey().address)
        assert not isinstance(exc.value, PendingFundsError)
