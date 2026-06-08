import pytest

from gumptionchain.api_client import ApiClient
from gumptionchain.exceptions import InsufficientFundsError, PendingFundsError
from gumptionchain.wallet import Wallet


def test_pending_change_raises_pending_funds_error(
    app, host, mill_block, requests_proxy, wallet
):
    # Spend most of a single coinbase UTXO in an UNCONFIRMED txn, then try to
    # spend again. The change is pending (not yet a confirmed UTXO) and the
    # coinbase is spoken-for, so the builder must report PendingFundsError
    # ("still confirming"), not a generic shortfall — the funds DO exist.
    with app.app_context():
        m, _b = mill_block(wallet)  # wallet earns one coinbase reward
        dest = Wallet().address
        first = m.longest_chain.create_transfer(wallet, 400, dest)
        first.sign()
        ApiClient(host, wallet).post_transaction(first)  # pending, NOT mined

        with pytest.raises(PendingFundsError):
            m.longest_chain.create_transfer(wallet, 100, dest)
        # the confirmed balance still shows the funds — they're just locked.
        assert m.longest_chain.balance(wallet.address) >= 100


def test_pending_funds_error_for_stakes_too(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        first = m.longest_chain.create_opposition(wallet, 400, subject)
        first.sign()
        ApiClient(host, wallet).post_transaction(first)

        with pytest.raises(PendingFundsError):
            m.longest_chain.create_support(wallet, 100, subject)


def test_empty_wallet_raises_plain_insufficient_funds(
    app, host, mill_block, requests_proxy, wallet
):
    # A wallet with no funds and nothing pending gets the genuine error — NOT
    # PendingFundsError (there's no in-flight txn locking anything).
    with app.app_context():
        m, _b = mill_block(wallet)  # funds `wallet`, not `empty`
        empty = Wallet()
        with pytest.raises(InsufficientFundsError) as exc:
            m.longest_chain.create_transfer(empty, 100, Wallet().address)
        assert not isinstance(exc.value, PendingFundsError)
