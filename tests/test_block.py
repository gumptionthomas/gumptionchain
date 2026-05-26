import datetime

import pytest

from cancelchain.block import MAX_TRANSACTIONS, TXN_TIMEOUT, Block
from cancelchain.chain import GENESIS_HASH
from cancelchain.exceptions import (
    ExpiredTransactionError,
    InvalidBlockError,
    MissingCoinbaseError,
    OutOfOrderTransactionError,
    SealedBlockError,
    UnlinkedBlockError,
)
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction
from cancelchain.util import dt_2_iso, now

TEST_TARGET = 'F' * 64


def new_txn(txid, subject, wallet):
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
    txn.add_outflow(Outflow(amount=10, subject=subject))
    txn.set_wallet(wallet)
    txn.seal()
    txn.sign()
    return txn


def test_from(reward, valid_block, wallet):
    valid_block.link(0, GENESIS_HASH, TEST_TARGET)
    valid_block.seal(wallet, reward)
    valid_block.mill()
    new_block = Block.from_dict(valid_block.to_dict())
    assert new_block == valid_block
    new_block = Block.from_json(valid_block.to_json())
    assert new_block == valid_block


def test_coinbase(reward, valid_block, wallet):
    valid_block.link(0, GENESIS_HASH, TEST_TARGET)
    valid_block.seal(wallet, reward)
    cb = valid_block.coinbase
    assert cb.outflows[0].amount == reward


def test_timestamp_dt(reward, single_block, wallet):
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(wallet, reward)
    assert dt_2_iso(single_block.timestamp_dt) == single_block.timestamp


def test_valid(reward, valid_block, single_txn, wallet):
    with pytest.raises(MissingCoinbaseError):
        valid_block.validate_coinbase()
    with pytest.raises(InvalidBlockError):
        valid_block.validate()
    valid_block.link(0, GENESIS_HASH, TEST_TARGET)
    valid_block.seal(wallet, reward)
    assert not valid_block.is_proved
    with pytest.raises(InvalidBlockError):
        valid_block.validate()
    valid_block.mill()
    valid_block.validate()
    with pytest.raises(SealedBlockError):
        valid_block.add_txn(single_txn)


def test_in_merkle_tree(reward, single_block, single_txn, wallet):
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(wallet, reward)
    single_block.mill()
    single_block.validate()
    assert single_block.in_merkle_tree(single_txn.txid)


def test_add_txn(single_block, subject, time_machine, txid, wallet):
    now_dt = now()
    then_dt = now_dt + datetime.timedelta(minutes=1)
    time_machine.move_to(then_dt)
    single_block.add_txn(new_txn(txid, subject, wallet))


def test_unlinked(reward, single_block, wallet):
    with pytest.raises(UnlinkedBlockError):
        single_block.seal(wallet, reward)


def test_future_txn(reward, single_block, subject, time_machine, txid, wallet):
    now_dt = now()
    later_dt = now_dt + datetime.timedelta(minutes=1)
    time_machine.move_to(later_dt)
    txn = new_txn(txid, subject, wallet)
    time_machine.move_to(now_dt)
    single_block.add_txn(txn)
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(wallet, reward)
    single_block.mill()
    with pytest.raises(InvalidBlockError, match='FutureTransactionError'):
        single_block.validate()


def test_invalid_transaction(
    reward, single_block, subject, time_machine, txid, wallet
):
    now_dt = now()
    time_machine.move_to(now_dt)
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(wallet, reward)
    then_dt = now_dt - datetime.timedelta(minutes=1)
    time_machine.move_to(then_dt)
    txn = new_txn(txid, subject, wallet)
    with pytest.raises(OutOfOrderTransactionError):
        single_block.validate_transaction(txn, prev_txn=single_block.txns[0])
    then_dt = now_dt - TXN_TIMEOUT - datetime.timedelta(minutes=1)
    time_machine.move_to(then_dt)
    txn = new_txn(txid, subject, wallet)
    with pytest.raises(ExpiredTransactionError):
        single_block.validate_transaction(txn)
    with pytest.raises(InvalidBlockError, match='block_hash'):
        single_block.validate()
    target = single_block.target
    single_block.target = '0' * 64
    single_block.block_hash = single_block.get_header_hash()
    with pytest.raises(InvalidBlockError, match='proof_of_work'):
        single_block.validate()
    single_block.target = target
    version = single_block.version
    single_block.version = 'foo'
    single_block.block_hash = single_block.get_header_hash()
    with pytest.raises(InvalidBlockError, match='version'):
        single_block.validate()
    single_block.version = version
    merkle_root = single_block.merkle_root
    single_block.merkle_root = None
    single_block.block_hash = single_block.get_header_hash()
    with pytest.raises(InvalidBlockError, match='merkle_root'):
        single_block.validate()
    single_block.merkle_root = merkle_root


def test_too_many_txns(reward, subject, txid, wallet):
    block = Block()
    for _i in range(MAX_TRANSACTIONS + 1):
        txn = Transaction()
        txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
        txn.add_outflow(Outflow(amount=10, subject=subject))
        txn.set_wallet(wallet)
        txn.seal()
        txn.sign()
        block.add_txn(txn)
    block.link(0, GENESIS_HASH, TEST_TARGET)
    block.seal(wallet, reward)
    block.mill()
    with pytest.raises(
        InvalidBlockError, match='List should have at most 100 items'
    ):
        block.validate()


def test_db(app, reward, wallet):
    with app.app_context():
        block = Block()
        block.link(0, GENESIS_HASH, TEST_TARGET)
        block.seal(wallet, reward)
        block.mill()
        block.validate()
        block.to_db()
        block_copy = Block.from_db(block.block_hash)
        assert block_copy == block


def test_to_dao_partial_block_raises():
    """Block.to_dao() raises InvalidBlockError on missing identity fields."""
    block = Block()
    assert block.block_hash is None
    with pytest.raises(InvalidBlockError, match='missing identity fields'):
        block.to_dao()
