import pytest

from cancelchain.chain import GENESIS_HASH
from cancelchain.exceptions import (
    InvalidSignatureError,
    InvalidTransactionError,
    MissingWalletError,
    UnsealedTransactionError,
)
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import PendingTxnSet, Transaction
from cancelchain.util import dt_2_iso
from cancelchain.wallet import Wallet


def test_txn_from(valid_txn):
    valid_txn.seal()
    valid_txn.sign()
    new_txn = Transaction.from_dict(valid_txn.to_dict())
    assert new_txn == valid_txn
    new_txn = Transaction.from_json(valid_txn.to_json())
    assert new_txn == valid_txn


def test_txn_timestamp_dt(single_txn):
    assert dt_2_iso(single_txn.timestamp_dt) == single_txn.timestamp


def test_txn_valid(single_txn):
    with pytest.raises(UnsealedTransactionError):
        single_txn.sign()
    single_txn.seal()
    assert hash(single_txn) is not None
    single_txn.wallet = None
    with pytest.raises(MissingWalletError):
        single_txn.sign()


def test_txn_invalid(invalid_txn, single_txn):
    with pytest.raises(InvalidTransactionError):
        invalid_txn.validate()
    invalid_txn.seal()
    with pytest.raises(InvalidTransactionError):
        invalid_txn.validate()
    invalid_txn.sign()
    with pytest.raises(InvalidTransactionError):
        invalid_txn.validate()
    with pytest.raises(InvalidTransactionError):
        single_txn.validate()
    single_txn.seal()
    with pytest.raises(InvalidSignatureError):
        single_txn.validate()


def test_txn_schadenfreude(subject, txid, wallet):
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
    txn.add_outflow(Outflow(amount=9, subject=subject))
    txn.add_outflow(Outflow(amount=10, subject=subject))
    txn.set_wallet(wallet)
    assert txn.schadenfreude == 9


def test_txn_grace(subject, txid, wallet):
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
    txn.add_outflow(Outflow(amount=9, forgive=subject))
    txn.add_outflow(Outflow(amount=10, forgive=subject))
    txn.set_wallet(wallet)
    assert txn.grace == 9


def test_txn_mudita(subject, txid, wallet):
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
    txn.add_outflow(Outflow(amount=9, support=subject))
    txn.add_outflow(Outflow(amount=10, support=subject))
    txn.set_wallet(wallet)
    assert txn.mudita == 19


def test_coinbase_txn_valid(valid_coinbase_txn):
    valid_coinbase_txn.seal()
    valid_coinbase_txn.sign()
    valid_coinbase_txn.validate_coinbase()


def test_coinbase_txn_invalid(valid_txn):
    with pytest.raises(InvalidTransactionError):
        valid_txn.validate_coinbase()


def test_txn_get_inflow(single_txn):
    assert single_txn.get_inflow() is not None
    assert single_txn.get_inflow(index=1) is None


def test_txn_get_outflow(single_txn):
    assert single_txn.get_outflow() is not None
    assert single_txn.get_outflow(index=1) is None


def test_txn_invalid_address(single_txn):
    single_txn.address = Wallet().address
    single_txn.seal()
    single_txn.sign()
    with pytest.raises(
        InvalidTransactionError, match='Address/public key mismatch'
    ):
        single_txn.validate()


def test_txn_invalid_signature(single_txn):
    single_txn.seal()
    single_txn.sign()
    w = Wallet()
    single_txn.public_key = w.public_key_b64
    single_txn.address = w.address
    with pytest.raises(InvalidSignatureError):
        single_txn.validate()


def test_db(app, wallet):
    with app.app_context():
        cb = Transaction.coinbase(wallet, 20, 10, 9, 8, prev_hash=GENESIS_HASH)
        cb.to_db()
        cb_copy = Transaction.from_db(cb.txid)
        assert cb_copy == cb
        # prev_hash is compare=False, so == ignores it; assert the
        # DAO round-trip restored it explicitly, and that the reloaded
        # coinbase's recomputed txid still matches (validate_txid).
        assert cb_copy.prev_hash == cb.prev_hash == GENESIS_HASH
        cb_copy.validate_coinbase()


def test_pending_txns(app, subject, wallet):
    cb = Transaction.coinbase(wallet, 10, 0, 0, 0, prev_hash=GENESIS_HASH)
    with app.app_context():
        cb.to_db()
        pending = PendingTxnSet()
        txn = Transaction()
        txn.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        txn.add_outflow(Outflow(amount=10, subject=subject))
        txn.set_wallet(wallet)
        txn.seal()
        txn.sign()
        pending.add(txn)
        assert len(pending) == 1
        assert txn in pending
        assert next(iter(pending)) == txn
        pending.discard(txn)
        assert len(pending) == 0


def test_to_dao_unsealed_raises(subject):
    """to_dao() raises UnsealedTransactionError when txid is None."""
    txn = Transaction()
    txn.add_outflow(Outflow(amount=1, subject=subject))
    assert txn.txid is None
    with pytest.raises(UnsealedTransactionError):
        txn.to_dao()


def test_to_dao_inflow_missing_outflow_ref_raises(subject):
    """Transaction.to_dao() raises if an inflow has None outflow_txid/idx."""
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=None, outflow_idx=0))
    txn.add_outflow(Outflow(amount=1, subject=subject))
    txn.seal()
    with pytest.raises(
        InvalidTransactionError, match='Inflow 0 missing outflow reference'
    ):
        txn.to_dao()


def test_to_dao_outflow_missing_amount_raises(subject):
    """Transaction.to_dao() raises if an outflow has None amount."""
    txn = Transaction()
    txn.add_outflow(Outflow(amount=None, subject=subject))
    txn.seal()
    with pytest.raises(
        InvalidTransactionError, match='Outflow 0 missing amount'
    ):
        txn.to_dao()


def test_pending_add_unsealed_raises(app, subject, wallet):
    """PendingTxnSet.add() raises UnsealedTransactionError on unsealed txn."""
    with app.app_context():
        pending = PendingTxnSet()
        txn = Transaction()
        txn.add_outflow(Outflow(amount=1, subject=subject))
        assert txn.txid is None
        with pytest.raises(UnsealedTransactionError):
            pending.add(txn)


def test_pending_add_inflow_missing_ref_raises(app, subject, wallet):
    """PendingTxnSet.add() raises on inflow with None outflow_txid/idx."""
    with app.app_context():
        pending = PendingTxnSet()
        txn = Transaction()
        txn.add_inflow(Inflow(outflow_txid=None, outflow_idx=0))
        txn.add_outflow(Outflow(amount=1, subject=subject))
        txn.seal()
        with pytest.raises(
            InvalidTransactionError,
            match='Inflow 0 missing outflow reference',
        ):
            pending.add(txn)


def test_pending_add_outflow_missing_amount_raises(app, subject, wallet):
    """PendingTxnSet.add() raises on outflow with None amount."""
    with app.app_context():
        pending = PendingTxnSet()
        txn = Transaction()
        txn.add_outflow(Outflow(amount=None, subject=subject))
        txn.seal()
        with pytest.raises(
            InvalidTransactionError, match='Outflow 0 missing amount'
        ):
            pending.add(txn)


def test_regular_txn_data_csv_excludes_prev_hash(wallet):
    """A4.c v2 guard: a regular txn's data_csv (and therefore its txid)
    is unchanged by the coinbase prev_hash binding.

    The prev_hash field is conditionally appended to data_csv only when
    set; regular txns leave it None, so their data_csv must be the exact
    6-field join (timestamp, address, public_key, inflows, outflows,
    version) with no trailing prev_hash field.
    """
    t = Transaction()
    t.add_inflow(Inflow(outflow_txid='a' * 64, outflow_idx=0))
    t.add_outflow(Outflow(amount=5, address=wallet.address))
    t.set_wallet(wallet)
    t.seal()
    assert t.prev_hash is None
    expected = ','.join(
        [
            str(t.timestamp),
            str(t.address),
            str(t.public_key),
            ','.join(i.data_csv for i in t.inflows),
            ','.join(o.data_csv for o in t.outflows),
            str(t.version),
        ]
    )
    assert t.data_csv == expected
    # to_dict (asdict_sans_none) must not surface a prev_hash key.
    assert 'prev_hash' not in t.to_dict()


def test_coinbase_txn_requires_prev_hash(wallet):
    """A4.c v2: a coinbase with no prev_hash binding is rejected by
    validate_coinbase().

    CoinbaseTransactionModel declares `prev_hash: MillHashType` (required,
    no default), so a coinbase built without a binding fails coinbase
    validation. This pins the structural invariant the whole binding
    scheme depends on — if the field were ever relaxed back to
    `MillHashType | None = None`, this negative assertion fails.
    """
    cb = Transaction(outflows=[Outflow(amount=100, address=wallet.address)])
    cb.set_wallet(wallet)
    cb.seal()
    cb.sign()
    assert cb.prev_hash is None
    with pytest.raises(InvalidTransactionError):
        cb.validate_coinbase()
