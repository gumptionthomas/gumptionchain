"""EGU #208: confirmed txns are pruned from the pending pool on live
block acceptance (Node.process_block), and a txn pruned on a
later-orphaned block stays out of the pool (accept + document)."""

from sqlalchemy.exc import OperationalError

from gumptionchain.api_client import ApiClient
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import PendingIOflowDAO, PendingTxnDAO
from gumptionchain.signing_key import SigningKey
from gumptionchain.transaction import PendingTxnSet


def _post_pending(host, chain, signing_key, amount, subject):
    txn = chain.create_opposition(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


def _count_ioflows():
    return (
        db.session.scalar(
            db.select(db.func.count()).select_from(PendingIOflowDAO)
        )
        or 0
    )


def test_process_block_prunes_confirmed_pending(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b1 = mill_block(signing_key)  # genesis funds the signing_key
        txn = _post_pending(host, m.longest_chain, signing_key, 300, subject)
        assert PendingTxnDAO.count() == 1
        assert _count_ioflows() == 1  # spends the mined coinbase

        m2, b2 = mill_block(signing_key)  # b2 confirms txn

        # the confirmed txn is pruned from the pool (ioflow children
        # cascade with it)...
        assert PendingTxnDAO.get(txn.txid) is None
        assert _count_ioflows() == 0
        # ...and only it: the coinbase discard is a no-op, so the pool
        # count drops by exactly the number of regular txns
        assert len(b2.regular_txns) == 1
        assert PendingTxnDAO.count() == 0
        # the txn is canonical
        assert m2.longest_chain.get_transaction(txn.txid) is not None


def test_prune_failure_does_not_block_acceptance(
    app, host, mill_block, monkeypatch, requests_proxy, subject, signing_key
):
    # Best-effort prune: a DB error while discarding must not abort
    # acceptance of the already-committed block (or its gossip).
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        txn = _post_pending(host, m.longest_chain, signing_key, 300, subject)
        assert PendingTxnDAO.count() == 1

        def boom(self, txn):
            stmt = 'stmt'
            raise OperationalError(stmt, {}, Exception('db down'))

        monkeypatch.setattr(PendingTxnSet, 'discard', boom)
        m2, b2 = mill_block(signing_key)  # must not raise

        # the block was accepted despite the failed prune...
        assert b2 is not None
        assert m2.longest_chain.get_transaction(txn.txid) is not None
        # ...and the pool row simply lingers (read filter handles display)
        assert PendingTxnDAO.count() == 1


def test_orphaned_block_txn_stays_pruned(
    add_chain_block, app, host, mill_block, requests_proxy, subject, signing_key
):
    # Accept + document (#208): a txn pruned on a later-orphaned block
    # is NOT auto-re-added to this node's pool; recovery relies on peer
    # re-gossip or sender re-submit. Fork construction mirrors
    # tests/test_chain.py::test_transaction_provenance_orphaned.
    with app.app_context():
        # signing_key2 needs no role: add_chain_block writes via Chain.add_block
        # directly, bypassing the HTTP/auth layer entirely.
        signing_key2 = SigningKey()
        m, b1 = mill_block(signing_key)  # genesis
        txn = _post_pending(host, m.longest_chain, signing_key, 300, subject)
        m, _b2 = mill_block(signing_key)  # b2 confirms + prunes txn
        assert PendingTxnDAO.count() == 0

        # build a strictly-longer fork off b1 that excludes b2
        alt = Chain(block_hash=b1.block_hash)
        add_chain_block(chain=alt, milling_signing_key=signing_key2)
        _, _ = add_chain_block(chain=alt, milling_signing_key=signing_key2)
        alt.to_db()  # sync_longest_chain_blocks -> alt is canonical

        # the txn is orphaned (not in the canonical chain)...
        assert m.longest_chain.get_transaction(txn.txid) is None
        # ...and stays out of the pool (the documented trade-off)
        assert PendingTxnDAO.count() == 0


def test_prune_handles_multiple_regular_txns(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        m, _b2 = mill_block(signing_key)  # two coinbases to spend from
        txn1 = _post_pending(host, m.longest_chain, signing_key, 300, subject)
        txn2 = _post_pending(host, m.longest_chain, signing_key, 200, subject)
        assert PendingTxnDAO.count() == 2

        m3, b3 = mill_block(signing_key)  # confirms both

        assert len(b3.regular_txns) == 2
        assert PendingTxnDAO.count() == 0
        assert _count_ioflows() == 0
        assert m3.longest_chain.get_transaction(txn1.txid) is not None
        assert m3.longest_chain.get_transaction(txn2.txid) is not None
