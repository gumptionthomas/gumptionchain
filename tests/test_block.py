import datetime
import json
from types import SimpleNamespace

import pytest

from gumptionchain.block import (
    MAX_TRANSACTIONS,
    TXN_TIMEOUT,
    Block,
    txn_is_expired,
)
from gumptionchain.chain import GENESIS_HASH
from gumptionchain.exceptions import (
    ExpiredTransactionError,
    InvalidBlockError,
    MissingCoinbaseError,
    OutOfOrderTransactionError,
    SealedBlockError,
    UnlinkedBlockError,
)
from gumptionchain.payload import Inflow, Outflow, encode_subject
from gumptionchain.transaction import CoinbaseMetrics, Transaction
from gumptionchain.util import dt_2_iso, iso_2_dt, now

TEST_TARGET = 'F' * 64


def new_txn(txid, subject, signing_key):
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
    txn.add_outflow(Outflow(amount=10, opposition=subject))
    txn.set_signing_key(signing_key)
    txn.seal()
    txn.sign()
    return txn


def test_from(reward, valid_block, signing_key):
    valid_block.link(0, GENESIS_HASH, TEST_TARGET)
    valid_block.seal(signing_key, reward, CoinbaseMetrics())
    valid_block.mill()
    new_block = Block.from_dict(valid_block.to_dict())
    assert new_block == valid_block
    new_block = Block.from_json(valid_block.to_json())
    assert new_block == valid_block


def test_coinbase(reward, valid_block, signing_key):
    valid_block.link(0, GENESIS_HASH, TEST_TARGET)
    valid_block.seal(signing_key, reward, CoinbaseMetrics())
    cb = valid_block.coinbase
    assert cb.outflows[0].amount == reward


def test_timestamp_dt(reward, single_block, signing_key):
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(signing_key, reward, CoinbaseMetrics())
    assert dt_2_iso(single_block.timestamp_dt) == single_block.timestamp


def test_valid(reward, valid_block, single_txn, signing_key):
    with pytest.raises(MissingCoinbaseError):
        valid_block.validate_coinbase()
    with pytest.raises(InvalidBlockError):
        valid_block.validate()
    valid_block.link(0, GENESIS_HASH, TEST_TARGET)
    valid_block.seal(signing_key, reward, CoinbaseMetrics())
    assert not valid_block.is_proved
    with pytest.raises(InvalidBlockError):
        valid_block.validate()
    valid_block.mill()
    valid_block.validate()
    with pytest.raises(SealedBlockError):
        valid_block.add_txn(single_txn)


def test_in_merkle_tree(reward, single_block, single_txn, signing_key):
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(signing_key, reward, CoinbaseMetrics())
    single_block.mill()
    single_block.validate()
    assert single_block.in_merkle_tree(single_txn.txid)


def _two_txn_block(
    single_block, signing_key, subject, txid, reward, time_machine
):
    """A sealed+milled block: two regular txns + coinbase (coinbase last)."""
    base = now()
    time_machine.move_to(base + datetime.timedelta(seconds=1))
    single_block.add_txn(new_txn(txid, subject, signing_key))
    time_machine.move_to(base + datetime.timedelta(seconds=2))
    single_block.add_txn(
        new_txn(txid[::-1], encode_subject('a second subject'), signing_key)
    )
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(signing_key, reward, CoinbaseMetrics())
    single_block.mill()
    return single_block


def test_merkle_root_order_independent(
    reward, single_block, signing_key, subject, txid, time_machine
):
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    block.validate()
    root = block.get_merkle_root()
    assert root == block.merkle_root
    # Simulate a non-canonical reload: coinbase no longer last.
    block.txns = [block.txns[-1], *block.txns[:-1]]
    assert block.get_merkle_root() == root
    block.validate_merkle_root()  # must not raise


def test_canonical_txns_orders_coinbase_last(
    reward, single_block, signing_key, subject, txid, time_machine
):
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    shuffled = [block.txns[-1], *reversed(block.txns[:-1])]
    block.txns = shuffled
    canonical = block.canonical_txns()
    assert canonical[-1].is_coinbase
    assert not any(t.is_coinbase for t in canonical[:-1])
    regulars = canonical[:-1]
    assert regulars == sorted(
        regulars, key=lambda t: (t.timestamp, t.txid or '')
    )


def test_in_merkle_tree_multi_txn(
    reward, single_block, signing_key, subject, txid, time_machine
):
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    block.validate()
    for t in block.txns:
        assert block.in_merkle_tree(t.txid)
    assert not block.in_merkle_tree('nonexistent-txid')


def test_from_dao_canonicalizes_transaction_order(
    app, reward, single_block, signing_key, subject, txid, time_machine
):
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    block.validate()
    # DB presents transactions coinbase-FIRST (the (timestamp,txid)
    # tie-break flip).
    with app.app_context():
        cb_dao = block.coinbase.to_dao()
        reg_daos = [t.to_dao() for t in block.regular_txns]
    fake_dao = SimpleNamespace(
        idx=block.idx,
        timestamp=iso_2_dt(block.timestamp),
        block_hash=block.block_hash,
        prev_hash=block.prev_hash,
        target=block.target,
        proof_of_work=block.proof_of_work,
        merkle_root=block.merkle_root,
        version=block.version,
        transactions=[cb_dao, *reg_daos],
    )
    reloaded = Block.from_dao(fake_dao)
    assert reloaded.coinbase.is_coinbase
    assert reloaded.coinbase.txid == block.coinbase.txid
    assert not any(t.is_coinbase for t in reloaded.regular_txns)
    reloaded.validate()


def test_from_dict_canonicalizes_transaction_order(
    reward, single_block, signing_key, subject, txid, time_machine
):
    # A serialized block whose txns array is NON-canonical (coinbase not last)
    # must reconstruct in canonical order, so the position-based coinbase /
    # regular_txns properties stay correct. (Block.__eq__ ignores txns order,
    # so assert the order explicitly rather than via ==.)
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    block.validate()
    d = block.to_dict()
    d['txns'] = list(reversed(d['txns']))  # coinbase first — non-canonical
    reconstructed = Block.from_dict(d)
    assert reconstructed.txns[-1].is_coinbase
    assert reconstructed.coinbase.txid == block.coinbase.txid
    assert not any(t.is_coinbase for t in reconstructed.regular_txns)
    reconstructed.validate()


def test_from_json_canonicalizes_transaction_order(
    reward, single_block, signing_key, subject, txid, time_machine
):
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    block.validate()
    d = block.to_dict()
    d['txns'] = list(reversed(d['txns']))  # coinbase first — non-canonical
    reconstructed = Block.from_json(json.dumps(d))
    assert reconstructed.txns[-1].is_coinbase
    assert reconstructed.coinbase.txid == block.coinbase.txid
    assert not any(t.is_coinbase for t in reconstructed.regular_txns)
    reconstructed.validate()


def test_in_merkle_tree_skips_untxid_leaf(
    reward, single_block, signing_key, subject, txid, time_machine
):
    # in_merkle_tree's leaf index must be computed over the SAME leaves the
    # tree is built from — build_merkle_tree skips txns with no txid. Inject a
    # txid-less txn that sorts canonically first; pre-hardening its phantom
    # slot shifted every real leaf index by one, breaking the inclusion proof.
    block = _two_txn_block(
        single_block, signing_key, subject, txid, reward, time_machine
    )
    real = block.regular_txns[0]
    dummy = Transaction()  # unsealed → txid is None
    dummy.timestamp = '2000-01-01T00:00:00Z'  # sorts before every real txn
    block.txns.insert(0, dummy)
    assert block.in_merkle_tree(real.txid)
    assert block.in_merkle_tree(block.coinbase.txid)
    assert not block.in_merkle_tree('nonexistent-txid')


def test_add_txn(single_block, subject, time_machine, txid, signing_key):
    now_dt = now()
    then_dt = now_dt + datetime.timedelta(minutes=1)
    time_machine.move_to(then_dt)
    single_block.add_txn(new_txn(txid, subject, signing_key))


def test_unlinked(reward, single_block, signing_key):
    with pytest.raises(UnlinkedBlockError):
        single_block.seal(signing_key, reward, CoinbaseMetrics())


def test_future_txn(
    reward, single_block, subject, time_machine, txid, signing_key
):
    now_dt = now()
    later_dt = now_dt + datetime.timedelta(minutes=1)
    time_machine.move_to(later_dt)
    txn = new_txn(txid, subject, signing_key)
    time_machine.move_to(now_dt)
    single_block.add_txn(txn)
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(signing_key, reward, CoinbaseMetrics())
    single_block.mill()
    with pytest.raises(InvalidBlockError, match='FutureTransactionError'):
        single_block.validate()


def test_invalid_transaction(
    reward, single_block, subject, time_machine, txid, signing_key
):
    now_dt = now()
    time_machine.move_to(now_dt)
    single_block.link(0, GENESIS_HASH, TEST_TARGET)
    single_block.seal(signing_key, reward, CoinbaseMetrics())
    then_dt = now_dt - datetime.timedelta(minutes=1)
    time_machine.move_to(then_dt)
    txn = new_txn(txid, subject, signing_key)
    with pytest.raises(OutOfOrderTransactionError):
        single_block.validate_transaction(txn, prev_txn=single_block.txns[0])
    then_dt = now_dt - TXN_TIMEOUT - datetime.timedelta(minutes=1)
    time_machine.move_to(then_dt)
    txn = new_txn(txid, subject, signing_key)
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


def test_too_many_txns(reward, subject, txid, signing_key):
    block = Block()
    for _i in range(MAX_TRANSACTIONS + 1):
        txn = Transaction()
        txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
        txn.add_outflow(Outflow(amount=10, opposition=subject))
        txn.set_signing_key(signing_key)
        txn.seal()
        txn.sign()
        block.add_txn(txn)
    block.link(0, GENESIS_HASH, TEST_TARGET)
    block.seal(signing_key, reward, CoinbaseMetrics())
    block.mill()
    with pytest.raises(
        InvalidBlockError, match='List should have at most 100 items'
    ):
        block.validate()


def test_db(app, reward, signing_key):
    with app.app_context():
        block = Block()
        block.link(0, GENESIS_HASH, TEST_TARGET)
        block.seal(signing_key, reward, CoinbaseMetrics())
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


def test_genesis_from_db(app, reward, signing_key):
    """Block.genesis_from_db() returns None until a genesis is persisted,
    then returns the persisted canonical genesis."""
    with app.app_context():
        assert Block.genesis_from_db() is None
        block = Block()
        block.link(0, GENESIS_HASH, TEST_TARGET)
        block.seal(signing_key, reward, CoinbaseMetrics())
        block.mill()
        block.validate()
        block.to_db()
        genesis = Block.genesis_from_db()
        assert genesis is not None
        assert genesis == block
        assert genesis.idx == 0


def test_txn_is_expired_boundary():
    ref = now()
    one_sec = datetime.timedelta(seconds=1)
    # Strictly older than TXN_TIMEOUT -> expired.
    assert txn_is_expired(ref - TXN_TIMEOUT - one_sec, ref) is True
    # Exactly TXN_TIMEOUT old -> alive (open boundary).
    assert txn_is_expired(ref - TXN_TIMEOUT, ref) is False
    # Younger than TXN_TIMEOUT -> alive.
    assert txn_is_expired(ref - TXN_TIMEOUT + one_sec, ref) is False
