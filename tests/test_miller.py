import datetime
from unittest.mock import patch

import pytest
from _sa_helpers import _count

from gumptionchain.block import TXN_TIMEOUT
from gumptionchain.chain import GRAIN_PER_GRIT as GPG
from gumptionchain.chain import REWARD
from gumptionchain.exceptions import (
    DuplicateMinedTransactionError,
    InsufficientFundsError,
)
from gumptionchain.miller import Miller
from gumptionchain.models import PendingIOflowDAO, PendingTxnDAO
from gumptionchain.payload import Inflow, Outflow
from gumptionchain.transaction import Transaction
from gumptionchain.util import now
from gumptionchain.wallet import Wallet


def test_miller_create_block(app, time_machine, time_stepper, wallet):
    with app.app_context():
        now_dt = now()
        time_step = time_stepper(start=now_dt - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)
        _ = next(time_step)
        b0 = m.create_block()
        m.mill_block(b0)
        _ = next(time_step)
        assert m.longest_chain.length == 1
        assert m.longest_chain.balance(wallet.address) == REWARD
        b1 = m.create_block()
        m.mill_block(b1)
        _ = next(time_step)
        assert m.longest_chain.length == 2
        assert m.longest_chain.balance(wallet.address) == 2 * REWARD
        cb0 = b0.coinbase
        cb0_amount = next(iter(cb0.outflows)).amount
        w2 = Wallet()
        remit = 2 * GPG
        t0 = Transaction()
        t0.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t0.add_outflow(Outflow(amount=remit, address=w2.address))
        t0.add_outflow(
            Outflow(amount=cb0_amount - remit, address=wallet.address)
        )
        t0.set_wallet(wallet)
        t0.seal()
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        b2 = m.create_block()
        m.mill_block(b2)
        _ = next(time_step)
        assert m.longest_chain.length == 3
        assert m.longest_chain.balance(wallet.address) == (3 * REWARD) - (
            2 * GPG
        )
        assert m.longest_chain.balance(w2.address) == 2 * GPG
        time_machine.move_to(now_dt)
        assert m.longest_chain.get_block(b2.block_hash)


def test_expired_transaction(app, time_machine, wallet):
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        when_dt = now_dt - TXN_TIMEOUT - datetime.timedelta(seconds=1)
        time_machine.move_to(when_dt)
        t0 = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t0.sign()
        time_machine.move_to(now_dt)
        m.receive_transaction(t0.txid, t0.to_json())
        assert len(m.pending_txns) == 1
        b1 = m.create_block()
        assert len(m.pending_txns) == 0
        m.mill_block(b1)
        assert len(b1.txns) == 1


def test_discard_expired_removes_ioflow_children(app, time_machine, wallet):
    """discard_expired_pending_txns evicts expired pending txns AND their
    companion PendingIOflowDAO rows. The bulk SQL-filtered delete uses
    ORM session.delete() per row precisely so the `ioflows` cascade fires
    — a Core bulk DELETE would orphan the children (the FK has no ON
    DELETE CASCADE)."""
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        # Build a transfer spending the mined coinbase; the inflow
        # references an existing (mined) outflow, so add() creates a
        # PendingIOflowDAO companion row.
        when_dt = now_dt - TXN_TIMEOUT - datetime.timedelta(seconds=1)
        time_machine.move_to(when_dt)
        t0 = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t0.sign()
        time_machine.move_to(now_dt)
        m.receive_transaction(t0.txid, t0.to_json())
        assert PendingTxnDAO.count() == 1
        assert _count(PendingIOflowDAO) == 1
        m.discard_expired_pending_txns()
        assert PendingTxnDAO.count() == 0
        assert _count(PendingIOflowDAO) == 0


def test_duplicate_transaction(app, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        cb0_amount = next(iter(cb0.outflows)).amount
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t0 = Transaction()
        t0.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t0.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t0.set_wallet(wallet)
        t0.seal()
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        m.receive_transaction(t0.txid, t0.to_json())
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 1
        b1 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b1)
        assert len(b1.txns) == 2
        assert len(m.pending_txns) == 1
        b2 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b2)
        assert len(b2.txns) == 1
        assert len(m.pending_txns) == 1
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 1
        b3 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b3)
        assert len(b3.txns) == 1
        assert len(m.pending_txns) == 1
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t1 = Transaction()
        t1.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t1.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t1.set_wallet(wallet)
        t1.seal()
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 2
        b4 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b4)
        assert len(b4.txns) == 1
        assert len(m.pending_txns) == 1


@patch('gumptionchain.miller.MAX_TRANSACTIONS', 10)
def test_max_txns(app, time_machine, wallet):
    with app.app_context():
        max_txns = 10
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=4)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        prev_t = cb0
        for _i in range(max_txns + 3):
            amount = next(iter(prev_t.outflows)).amount
            when_dt += datetime.timedelta(seconds=1)
            time_machine.move_to(when_dt)
            t = Transaction()
            t.add_inflow(Inflow(outflow_txid=prev_t.txid, outflow_idx=0))
            t.add_outflow(Outflow(amount=amount, address=wallet.address))
            t.set_wallet(wallet)
            t.seal()
            t.sign()
            prev_t = t
            m.receive_transaction(t.txid, t.to_json())
        assert len(m.pending_txns) == max_txns + 3
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        b1 = m.create_block()
        assert len(b1.txns) == max_txns


def test_opposition_rescind_txns(app, subject, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        amount = m.longest_chain.balance(wallet.address)
        t0 = m.longest_chain.create_opposition(wallet, amount, subject)
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        b1 = m.create_block()
        m.mill_block(b1)
        assert m.longest_chain.opposition_balance(subject) == amount
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t1 = m.longest_chain.create_rescind(wallet, amount, subject)
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        b2 = m.create_block()
        m.mill_block(b2)
        assert m.longest_chain.opposition_balance(subject) == 0


def test_invalid_opposition_rescind_txns(app, subject, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        amount = m.longest_chain.balance(wallet.address) + 1
        with pytest.raises(InsufficientFundsError):
            t0 = m.longest_chain.create_opposition(wallet, amount, subject)
        amount = m.longest_chain.balance(wallet.address) - 2
        t0 = m.longest_chain.create_opposition(wallet, amount, subject)
        assert len(t0.outflows) == 2
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        b1 = m.create_block()
        assert len(b1.txns) == 2
        m.mill_block(b1)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        with pytest.raises(InsufficientFundsError):
            t1 = m.longest_chain.create_rescind(wallet, amount + 1, subject)
        t1 = m.longest_chain.create_rescind(wallet, amount - 1, subject)
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        b2 = m.create_block()
        assert len(b2.txns) == 2
        m.mill_block(b2)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        with pytest.raises(InsufficientFundsError):
            t2 = m.longest_chain.create_rescind(wallet, 2, subject)
        t2 = m.longest_chain.create_rescind(wallet, 1, subject)
        t2.sign()
        m.receive_transaction(t2.txid, t2.to_json())
        b3 = m.create_block()
        m.mill_block(b3)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        with pytest.raises(InsufficientFundsError):
            m.longest_chain.create_rescind(wallet, 1, subject)


def test_pending_chain_txns_boundary_alive(app, time_machine, wallet):
    """A7.e: a pending txn timestamped exactly now - TXN_TIMEOUT is
    yielded by pending_chain_txns (alive at the open boundary)."""
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        time_machine.move_to(now_dt - TXN_TIMEOUT)
        t = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t.sign()
        time_machine.move_to(now_dt)
        m.pending_txns.add(t)
        yielded = list(m.pending_chain_txns(m.longest_chain))
        assert t in yielded


def test_pending_chain_txns_expired_excluded(app, time_machine, wallet):
    """A7.e: a pending txn one second older than the boundary (strictly
    older than TXN_TIMEOUT) is NOT yielded by pending_chain_txns."""
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        time_machine.move_to(
            now_dt - TXN_TIMEOUT - datetime.timedelta(seconds=1)
        )
        t = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t.sign()
        time_machine.move_to(now_dt)
        m.pending_txns.add(t)
        yielded = list(m.pending_chain_txns(m.longest_chain))
        assert t not in yielded


def test_mined_txn_replay_rejected(app, time_machine, wallet):
    """A1.f: a fresh txn is admitted to pending, but replaying it after
    it is mined raises DuplicateMinedTransactionError (and is not re-added)."""
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        assert cb0 is not None
        cb0_amount = next(iter(cb0.outflows)).amount
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        # A fresh (never-mined) txn is admitted to pending.
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        m.receive_transaction(t.txid, t.to_json())
        assert t in m.pending_txns
        # Mine it, then drain pending (cross-node replay scenario).
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        b1 = m.create_block()
        m.mill_block(b1)
        for ptxn in list(m.pending_txns):
            m.pending_txns.discard(ptxn)
        assert len(m.pending_txns) == 0
        # Replaying the now-mined txn is rejected and not re-added.
        with pytest.raises(DuplicateMinedTransactionError):
            m.receive_transaction(t.txid, t.to_json())
        assert len(m.pending_txns) == 0
