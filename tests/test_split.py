import pytest

from gumptionchain.api_client import ApiClient
from gumptionchain.exceptions import (
    InsufficientFundsError,
    PendingFundsError,
)


def test_create_split_mints_chips_plus_change(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        txn = lc.create_split(signing_key, denomination=100, count=3)
        chips = [o for o in txn.outflows if o.amount == 100]
        change = [o for o in txn.outflows if o.amount != 100]
        assert len(chips) == 3
        assert all(o.address == signing_key.address for o in txn.outflows)
        assert len(change) == 1  # leftover reward as one change UTXO
        assert len(txn.outflows) <= 50


def test_create_split_exact_has_no_change(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        bal = lc.balance(signing_key.address)
        txn = lc.create_split(signing_key, denomination=bal, count=1)
        assert len(txn.outflows) == 1  # whole balance, no remainder
        assert txn.outflows[0].amount == bal


def test_create_split_49_chips_within_max_flows(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        txn = lc.create_split(signing_key, denomination=1, count=49)
        assert sum(1 for o in txn.outflows if o.amount == 1) == 49
        assert len(txn.outflows) <= 50  # 49 chips + 1 change


def test_create_split_insufficient_funds(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        bal = lc.balance(signing_key.address)
        with pytest.raises(InsufficientFundsError):
            # count=2 means 2x bal -- exceeds funds
            lc.create_split(signing_key, denomination=bal, count=2)


def test_create_split_pending_funds(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        bal = lc.balance(signing_key.address)
        # Lock the only UTXO in a pending transfer, then split must see the
        # confirmed balance but no spendable (non-pending) funds.
        xfer = lc.create_transfer(signing_key, 1, signing_key.address)
        xfer.sign()
        ApiClient(host, signing_key).post_transaction(xfer)
        with pytest.raises(PendingFundsError):
            lc.create_split(signing_key, denomination=bal, count=1)
