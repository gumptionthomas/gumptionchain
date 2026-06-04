import datetime
from unittest.mock import patch

import pytest
from _sa_helpers import _count_select

from gumptionchain.block import TXN_TIMEOUT, Block
from gumptionchain.chain import (
    GENESIS_HASH,
    GRAIN_PER_GRIT,
    REWARD,
    Chain,
)
from gumptionchain.database import db
from gumptionchain.exceptions import (
    FutureBlockError,
    ImbalancedTransactionError,
    InflowOutflowAddressMismatchError,
    InsufficientFundsError,
    InvalidBlockError,
    InvalidBlockIndexError,
    InvalidChainError,
    InvalidCoinbaseErrorRewardError,
    InvalidTargetError,
    MissingInflowOutflowError,
    OutOfOrderBlockError,
    SpentTransactionError,
)
from gumptionchain.milling import mill_hash_str
from gumptionchain.payload import Inflow, Outflow
from gumptionchain.transaction import Transaction
from gumptionchain.util import now, now_iso
from gumptionchain.wallet import Wallet

TEST_TARGET = 'F' * 64


def test_from(valid_chain):
    d = valid_chain.to_dict()
    new_chain = Chain.from_dict(d)
    assert new_chain == valid_chain
    j = valid_chain.to_json()
    new_chain = Chain.from_json(j)
    assert new_chain == valid_chain


def test_empty():
    chain = Chain()
    with pytest.raises(InvalidChainError):
        chain.validate()


def test_valid(add_chain_block, app, wallet):
    with app.app_context():
        chain, _ = add_chain_block()
        chain.validate()


def test_invalid_prev_hash(app, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        block.idx = 0
        chain.link_block(block)
        # A well-formed mill hash that is neither GENESIS_HASH nor a real
        # block: validate_block rejects it as an unknown parent
        # (InvalidPreviousHashError). A malformed value like 'foo' would
        # now be rejected earlier by the coinbase prev_hash binding
        # (CoinbaseTransactionModel) at seal() time, which is a different
        # check than the add_block prev_hash validation under test here.
        block.prev_hash = mill_hash_str('foo')
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidBlockError):
            chain.add_block(block)


def test_invalid_txn_timestamp(app, time_machine, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        chain.add_block(block)
        cb = block.coinbase
        now_dt = now()
        when_dt = now_dt + datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        time_machine.move_to(now_dt)
        block = Block()
        block.add_txn(t)
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidBlockError, match='FutureTransactionError'):
            chain.add_block(block)
        then_dt = now_dt - TXN_TIMEOUT - datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        time_machine.move_to(now_dt)
        block = Block()
        block.add_txn(t)
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidBlockError, match='ExpiredTransactionError'):
            chain.add_block(block)


@patch('gumptionchain.chain.TARGET_INTERVAL', 5)
def test_decrease_target(app, wallet):
    with app.app_context():
        chain = Chain()
        original_target = chain.target
        for _i in range(0, 5):
            block = Block()
            chain.link_block(block)
            block.seal(wallet, chain.block_reward(block))
            assert block.target == TEST_TARGET
            assert block.target == chain.target
            block.mill()
            chain.add_block(block)
        new_target = int(int(original_target, 16) * 0.25)
        assert int(chain.target, 16) == new_target
        block = Block()
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        assert block.target == chain.target
        block.mill()
        chain.add_block(block)


@patch('gumptionchain.chain.TARGET_INTERVAL', 5)
def test_increase_target(app, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        then_dt = now_dt - datetime.timedelta(days=100)
        time_machine.move_to(then_dt)
        chain = Chain()
        block = Block()
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        chain.add_block(block)
        time_machine.move_to(now_dt)
        for _i in range(0, 4):
            block = Block()
            chain.link_block(block)
            block.seal(wallet, chain.block_reward(block))
            assert block.target == chain.target == TEST_TARGET
            block.mill()
            chain.add_block(block)
        assert chain.target == TEST_TARGET


@patch('gumptionchain.chain.TARGET_INTERVAL', 5)
def test_invalid_target(app, time_machine, time_stepper, wallet):
    with app.app_context():
        now_dt = now()
        time_step = time_stepper(start=now_dt - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain = Chain()
        original_target = chain.target
        for _i in range(0, 5):
            _ = next(time_step)
            block = Block()
            chain.link_block(block)
            block.seal(wallet, chain.block_reward(block))
            assert block.target == chain.target == TEST_TARGET
            block.mill()
            chain.add_block(block)
        time_machine.move_to(now_dt)
        new_target = int(int(original_target, 16) * 0.25)
        assert int(chain.target, 16) == new_target
        block = Block()
        chain.link_block(block)
        block.target = TEST_TARGET
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidTargetError):
            chain.add_block(block)


def test_block_reward(add_chain_block, app, wallet):
    with app.app_context():
        chain = Chain()
        assert chain.block_reward() == REWARD


def test_generators(add_chain_block, app, time_stepper, wallet):
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        wallet2 = Wallet()
        chain, block = add_chain_block()
        for _i in range(0, 5):
            _ = next(time_step)
            prev_block = chain.last_block
            prev_coinbase = prev_block.coinbase
            block = Block()
            txn = Transaction()
            amount = 0
            for o in prev_coinbase.outflows:
                if o.address == wallet.address:
                    txn.add_inflow(
                        Inflow(outflow_txid=prev_coinbase.txid, outflow_idx=0)
                    )
                    amount += o.amount
            txn.add_outflow(Outflow(amount=amount, address=wallet2.address))
            txn.set_wallet(wallet)
            txn.seal()
            txn.sign()
            _ = next(time_step)
            block.add_txn(txn)
            add_chain_block(chain=chain, block=block)


def test_mill(wallet):
    chain = Chain()
    block = Block()
    chain.link_block(block)
    chain.seal_block(block, wallet)
    block.mill()


@pytest.mark.multi
def test_mill_mp(wallet):
    chain = Chain()
    block = Block()
    chain.link_block(block)
    chain.seal_block(block, wallet)
    block.mill(mp=True)


def test_db(add_chain_block, app, time_machine, wallet):
    with app.app_context():
        wallet2 = Wallet()
        now_dt = now()
        then_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        chain, block = add_chain_block()
        block_copy = Block.from_db(block.block_hash)
        assert block == block_copy
        cb = block.coinbase
        cb_amount = next(iter(block.coinbase.outflows)).amount
        remit = 2 * GRAIN_PER_GRIT
        then_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=remit, address=wallet2.address))
        t.add_outflow(Outflow(amount=cb_amount - remit, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        t.to_db()
        time_machine.move_to(now_dt)
        block2 = Block()
        block2.add_txn(t)
        add_chain_block(chain=chain, block=block2)
        block2_copy = Block.from_db(block2.block_hash)
        assert block2 == block2_copy
        chain.to_db()
        chain_copy = Chain.from_db(chain.cid)
        assert chain == chain_copy


def test_dao(add_chain_block, app, time_machine, time_stepper, wallet):
    with app.app_context():
        now_dt = now()
        time_step = time_stepper(now_dt - datetime.timedelta(hours=1))
        wallet2 = Wallet()
        wallet3 = Wallet()
        _ = next(time_step)
        chain, _ = add_chain_block()
        _ = next(time_step)
        _, block2 = add_chain_block(chain=chain)
        _ = next(time_step)
        alt_chain = Chain(block_hash=block2.block_hash)
        add_chain_block(chain=alt_chain)
        _ = next(time_step)
        add_chain_block(chain=chain, milling_wallet=wallet2)
        _ = next(time_step)
        add_chain_block(chain=alt_chain, milling_wallet=wallet3)
        _ = next(time_step)
        add_chain_block(chain=chain, milling_wallet=wallet3)
        _ = next(time_step)
        add_chain_block(chain=chain, milling_wallet=wallet3)
        _ = next(time_step)
        add_chain_block(chain=chain)
        _ = next(time_step)
        add_chain_block(chain=chain)
        _ = next(time_step)
        _, last_block = add_chain_block(chain=chain)
        time_machine.move_to(now_dt)

        blocks = chain.to_dao(create=True).blocks
        assert _count_select(blocks) == 8
        assert [b.id for b in db.session.execute(blocks).scalars().all()] == [
            10,
            9,
            8,
            7,
            6,
            4,
            2,
            1,
        ]
        alt_blocks = alt_chain.to_dao(create=True).blocks
        assert _count_select(alt_blocks) == 4
        assert [
            b.id for b in db.session.execute(alt_blocks).scalars().all()
        ] == [5, 3, 2, 1]

        transactions = chain.to_dao(create=True).transactions
        assert _count_select(transactions) == 8
        assert [
            t.id for t in db.session.execute(transactions).scalars().all()
        ] == [10, 9, 8, 7, 6, 4, 2, 1]
        alt_transactions = alt_chain.to_dao(create=True).transactions
        assert _count_select(alt_transactions) == 4
        assert [
            b.id for b in db.session.execute(alt_transactions).scalars().all()
        ] == [5, 3, 2, 1]

        outflows = chain.to_dao(create=True).outflows
        assert _count_select(outflows) == 8
        assert [t.id for t in db.session.execute(outflows).scalars().all()] == [
            10,
            9,
            8,
            7,
            6,
            4,
            2,
            1,
        ]
        alt_outflows = alt_chain.to_dao(create=True).outflows
        assert _count_select(alt_outflows) == 4
        assert [
            b.id for b in db.session.execute(alt_outflows).scalars().all()
        ] == [5, 3, 2, 1]

        inflows = chain.to_dao(create=True).inflows
        assert _count_select(inflows) == 0
        assert [t.id for t in db.session.execute(inflows).scalars().all()] == []
        alt_inflows = alt_chain.to_dao(create=True).inflows
        assert _count_select(alt_inflows) == 0
        assert [
            b.id for b in db.session.execute(alt_inflows).scalars().all()
        ] == []

        wallet_leaders = list(
            db.session.execute(chain.to_dao(create=True).wallet_leaderboard())
        )
        assert wallet_leaders[0][0] == wallet.address
        assert wallet_leaders[0][1] == 5 * REWARD
        assert wallet_leaders[1][0] == wallet3.address
        assert wallet_leaders[1][1] == 2 * REWARD
        assert wallet_leaders[2][0] == wallet2.address
        assert wallet_leaders[2][1] == REWARD
        assert len(wallet_leaders) == 3
        wallet_leaders = list(
            db.session.execute(
                chain.to_dao(create=True).wallet_leaderboard(
                    earliest=block2.timestamp_dt,
                    latest=last_block.timestamp_dt,
                    limit=2,
                )
            )
        )
        assert len(wallet_leaders) == 2
        assert wallet_leaders[0][0] == wallet.address
        assert wallet_leaders[0][1] == 3 * REWARD
        assert wallet_leaders[1][0] == wallet3.address
        assert wallet_leaders[1][1] == 2 * REWARD


def test_validate_block(add_chain_block, app, time_machine, wallet):
    with app.app_context():
        chain, _block = add_chain_block()
        now_dt = now()
        then_dt = now_dt + datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        block2 = Block()
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.mill()
        time_machine.move_to(now_dt)
        with pytest.raises(FutureBlockError):
            chain.add_block(block2)

        then_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        block2 = Block()
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.mill()
        time_machine.move_to(now_dt)
        with pytest.raises(OutOfOrderBlockError):
            chain.add_block(block2)

        block2 = Block()
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.idx += 1
        block2.mill()
        with pytest.raises(InvalidBlockIndexError):
            chain.add_block(block2)


def test_validate_block_txn(add_chain_block, app, time_machine, wallet):
    with app.app_context():
        chain = Chain()
        assert chain.get_block_by_reverse_index(0) is None

        _, block = add_chain_block(chain=chain)
        cb = block.coinbase
        cb_amount = next(iter(cb.outflows)).amount

        block2 = Block()
        chain.link_block(block2)
        now_dt = now()
        then_dt = now_dt + datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        time_machine.move_to(now_dt)
        block2.add_txn(t)
        chain.seal_block(block2, wallet)
        block2.mill()
        with pytest.raises(InvalidBlockError, match='FutureTransactionError'):
            chain.add_block(block2)

        block2 = Block()
        chain.link_block(block2)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount - 1, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2.add_txn(t)
        chain.seal_block(block2, wallet)
        block2.mill()
        with pytest.raises(ImbalancedTransactionError):
            chain.add_block(block2)


def test_validate_txn_inflow(add_chain_block, app, time_machine, txid, wallet):
    with app.app_context():
        chain = Chain()
        # txn inflow's outflow exists and amount > 0
        block = Block()
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=100, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block.add_txn(t)
        chain.link_block(block)
        chain.seal_block(block, wallet)
        block.mill()
        with pytest.raises(MissingInflowOutflowError):
            chain.add_block(block)
        wallet2 = Wallet()
        now_dt = now()
        then_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        chain, block = add_chain_block()
        cb = block.coinbase
        cb_amount = next(iter(block.coinbase.outflows)).amount
        then_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=1000))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        block2.add_txn(t)
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.mill()
        with pytest.raises(MissingInflowOutflowError):
            chain.add_block(block2)
        # txn inflow's outflow not already used in other inflow
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        block2.add_txn(t)
        add_chain_block(chain=chain, block=block2)
        time_machine.move_to(now_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block3 = Block()
        block3.add_txn(t)
        chain.link_block(block3)
        chain.seal_block(block3, wallet)
        block3.mill()
        with pytest.raises(SpentTransactionError):
            chain.add_block(block3)


def test_validate_block_coinbase(add_chain_block, app, subject, txid, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        chain.link_block(block)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, opposition=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block.add_txn(t, is_coinbase=False)
        block.merkle_root = block.get_merkle_root()
        block.timestamp = now_iso()
        block.mill()
        with pytest.raises(InvalidBlockError, match='inflows'):
            chain.add_block(block)

        block = Block()
        chain.link_block(block)
        block.link(0, GENESIS_HASH, TEST_TARGET)
        block.seal(wallet, REWARD + 1)
        block.mill()
        with pytest.raises(InvalidCoinbaseErrorRewardError):
            chain.add_block(block)

        _, block = add_chain_block(chain=chain)
        cb = block.coinbase
        cb_amount = next(iter(cb.outflows)).amount

        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, opposition=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        chain.link_block(block2)
        block2.add_txn(t)
        cb2 = Transaction(prev_hash=block2.prev_hash)
        cb2.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb2.set_wallet(wallet)
        cb2.seal()
        cb2.sign()
        block2.add_txn(cb2, is_coinbase=True)
        block2.merkle_root = block2.get_merkle_root()
        block2.timestamp = now_iso()
        block2.mill()
        with pytest.raises(InvalidBlockError, match='InvalidCoinbaseError'):
            chain.add_block(block2)

        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, opposition=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        block2.add_txn(t)
        add_chain_block(chain=chain, block=block2)

        t2 = Transaction()
        t2.add_inflow(Inflow(outflow_txid=t.txid, outflow_idx=0))
        t2.add_outflow(
            Outflow(
                amount=cb_amount, rescind=subject, rescind_kind='opposition'
            )
        )
        t2.set_wallet(wallet)
        t2.seal()
        t2.sign()
        block3 = Block()
        chain.link_block(block3)
        block3.add_txn(t2)
        cb3 = Transaction(prev_hash=block3.prev_hash)
        cb3.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb3.set_wallet(wallet)
        cb3.seal()
        cb3.sign()
        block3.add_txn(cb3, is_coinbase=True)
        block3.merkle_root = block3.get_merkle_root()
        block3.timestamp = now_iso()
        block3.mill()
        with pytest.raises(InvalidBlockError, match='InvalidCoinbaseError'):
            chain.add_block(block3)


def test_validate_io_address_mismatch(app, wallet):
    with app.app_context():
        wallet2 = Wallet()
        chain = Chain()
        block = Block()
        chain.link_block(block)
        chain.seal_block(block, wallet)
        block.mill()
        chain.add_block(block)
        cb = block.coinbase
        cb_amount = next(iter(cb.outflows)).amount

        block2 = Block()
        chain.link_block(block2)
        t2 = Transaction()
        t2.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t2.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t2.set_wallet(wallet2)
        t2.seal()
        t2.sign()
        block2.add_txn(t2)
        chain.seal_block(block2, wallet2)
        block2.mill()
        with pytest.raises(InflowOutflowAddressMismatchError):
            chain.add_block(block2)


def test_validate_opposition_ioflows(app, subject, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        chain.link_block(block)
        chain.seal_block(block, wallet)
        block.mill()
        chain.add_block(block)
        cb = block.coinbase
        cb_amount = next(iter(cb.outflows)).amount

        block2 = Block()
        chain.link_block(block2)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, opposition=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2.add_txn(t)
        chain.seal_block(block2, wallet)
        block2.mill()
        chain.add_block(block2)

        block3 = Block()
        chain.link_block(block3)
        t2 = Transaction()
        t2.add_inflow(Inflow(outflow_txid=t.txid, outflow_idx=0))
        t2.add_outflow(
            Outflow(
                amount=cb_amount, rescind=subject, rescind_kind='opposition'
            )
        )
        t2.set_wallet(wallet)
        t2.seal()
        t2.sign()
        block3.add_txn(t2)
        chain.seal_block(block3, wallet)
        block3.mill()
        chain.add_block(block3)


def test_rescind_support_drops_support_balance(
    add_chain_block, app, subject, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain, block = add_chain_block()
        cb = block.coinbase
        amt = next(iter(cb.outflows)).amount

        # stake support
        _ = next(time_step)
        t_support = Transaction()
        t_support.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t_support.add_outflow(Outflow(amount=amt, support=subject))
        t_support.set_wallet(wallet)
        t_support.seal()
        t_support.sign()
        block2 = Block()
        block2.add_txn(t_support)
        add_chain_block(chain=chain, block=block2)
        chain.to_db()
        assert chain.support_balance(subject) == amt

        # rescind support
        _ = next(time_step)
        rescind_txn = chain.create_rescind(wallet, amt, subject, 'support')
        rescind_txn.sign()
        block3 = Block()
        block3.add_txn(rescind_txn)
        add_chain_block(chain=chain, block=block3)
        chain.to_db()
        assert chain.support_balance(subject) == 0


def test_support_rescind_mints_regret(
    add_chain_block, app, subject, time_stepper, wallet
):
    """Block with a support-rescind txn mints regret == rescind_amount // 2."""
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain, block = add_chain_block()
        cb = block.coinbase
        amt = next(iter(cb.outflows)).amount

        # stake support
        _ = next(time_step)
        t_support = Transaction()
        t_support.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t_support.add_outflow(Outflow(amount=amt, support=subject))
        t_support.set_wallet(wallet)
        t_support.seal()
        t_support.sign()
        block2 = Block()
        block2.add_txn(t_support)
        add_chain_block(chain=chain, block=block2)
        chain.to_db()

        # rescind support — amt is even (genesis reward), so // 2 is exact
        _ = next(time_step)
        rescind_txn = chain.create_rescind(wallet, amt, subject, 'support')
        rescind_txn.sign()
        block3 = Block()
        block3.add_txn(rescind_txn)
        _, milled_block = add_chain_block(chain=chain, block=block3)

        expected_regret = amt // 2
        assert milled_block.regret == expected_regret
        coinbase_outflows = list(milled_block.coinbase.outflows)
        regret_outflows = [
            o for o in coinbase_outflows if o.address == wallet.address
        ]
        assert any(o.amount == expected_regret for o in regret_outflows)


def test_rescind_support_insufficient_when_only_opposition(
    add_chain_block, app, subject, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain, block = add_chain_block()
        cb = block.coinbase
        amt = next(iter(cb.outflows)).amount

        # stake opposition only
        _ = next(time_step)
        t_opp = Transaction()
        t_opp.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t_opp.add_outflow(Outflow(amount=amt, opposition=subject))
        t_opp.set_wallet(wallet)
        t_opp.seal()
        t_opp.sign()
        block2 = Block()
        block2.add_txn(t_opp)
        add_chain_block(chain=chain, block=block2)
        chain.to_db()

        # trying to rescind 'support' when only opposition was staked → error
        with pytest.raises(InsufficientFundsError):
            chain.create_rescind(wallet, amt, subject, 'support')


def test_rescind_kind_mismatch_rejected(
    add_chain_block, app, subject, time_stepper, wallet
):
    """Cross-kind rescind (support outflow, opposition kind) is rejected."""
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain, block = add_chain_block()
        cb = block.coinbase
        amt = next(iter(cb.outflows)).amount

        # stake support
        _ = next(time_step)
        t_support = Transaction()
        t_support.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t_support.add_outflow(Outflow(amount=amt, support=subject))
        t_support.set_wallet(wallet)
        t_support.seal()
        t_support.sign()
        block2 = Block()
        block2.add_txn(t_support)
        add_chain_block(chain=chain, block=block2)
        chain.to_db()

        # hand-build a cross-kind rescind: consumes support outflow but
        # claims kind='opposition', misrouting to the opposition pool
        _ = next(time_step)
        bad_rescind = Transaction()
        bad_rescind.add_inflow(
            Inflow(outflow_txid=t_support.txid, outflow_idx=0)
        )
        bad_rescind.add_outflow(
            Outflow(amount=amt, rescind=subject, rescind_kind='opposition')
        )
        bad_rescind.set_wallet(wallet)
        bad_rescind.seal()
        bad_rescind.sign()
        block3 = Block()
        block3.add_txn(bad_rescind)
        chain.link_block(block3)
        chain.seal_block(block3, wallet)
        block3.mill()
        with pytest.raises(ImbalancedTransactionError):
            chain.add_block(block3)


def test_support_outflow_cannot_reach_address(
    add_chain_block, app, subject, time_stepper, wallet
):
    """Staked support grains cannot be claimed to a wallet address."""
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain, block = add_chain_block()
        cb = block.coinbase
        amt = next(iter(cb.outflows)).amount

        # stake support
        _ = next(time_step)
        t_support = Transaction()
        t_support.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t_support.add_outflow(Outflow(amount=amt, support=subject))
        t_support.set_wallet(wallet)
        t_support.seal()
        t_support.sign()
        block2 = Block()
        block2.add_txn(t_support)
        add_chain_block(chain=chain, block=block2)
        chain.to_db()

        # try to reclaim support grains to a wallet address
        _ = next(time_step)
        bad_txn = Transaction()
        bad_txn.add_inflow(Inflow(outflow_txid=t_support.txid, outflow_idx=0))
        bad_txn.add_outflow(Outflow(amount=amt, address=wallet.address))
        bad_txn.set_wallet(wallet)
        bad_txn.seal()
        bad_txn.sign()
        block3 = Block()
        block3.add_txn(bad_txn)
        chain.link_block(block3)
        chain.seal_block(block3, wallet)
        block3.mill()
        with pytest.raises(ImbalancedTransactionError):
            chain.add_block(block3)


def test_partial_support_rescind_change_back(
    add_chain_block, app, subject, time_stepper, wallet
):
    """Partial rescind of support keeps remainder in the support pool."""
    with app.app_context():
        time_step = time_stepper(start=now() - datetime.timedelta(hours=1))
        _ = next(time_step)
        chain, block = add_chain_block()
        cb = block.coinbase
        amt = next(iter(cb.outflows)).amount
        # use an even amount so the halving is exact
        assert amt % 2 == 0, 'REWARD must be even for this test'
        half = amt // 2

        # stake support
        _ = next(time_step)
        t_support = Transaction()
        t_support.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t_support.add_outflow(Outflow(amount=amt, support=subject))
        t_support.set_wallet(wallet)
        t_support.seal()
        t_support.sign()
        block2 = Block()
        block2.add_txn(t_support)
        add_chain_block(chain=chain, block=block2)
        chain.to_db()
        assert chain.support_balance(subject) == amt

        # rescind half
        _ = next(time_step)
        rescind_txn = chain.create_rescind(wallet, half, subject, 'support')
        rescind_txn.sign()
        block3 = Block()
        block3.add_txn(rescind_txn)
        add_chain_block(chain=chain, block=block3)
        chain.to_db()
        assert chain.support_balance(subject) == amt - half


def test_to_dao_create_without_block_hash_raises():
    """to_dao(create=True) raises InvalidChainError on None block_hash."""
    chain = Chain()
    assert chain.block_hash is None
    with pytest.raises(InvalidChainError, match='Cannot create ChainDAO'):
        chain.to_dao(create=True)


def test_to_dao_without_block_hash_returns_none():
    """Chain.to_dao() (no create) returns None when block_hash is None."""
    chain = Chain()
    assert chain.block_hash is None
    assert chain.to_dao() is None
