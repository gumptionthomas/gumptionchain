import datetime

from gumptionchain.api_client import ApiClient
from gumptionchain.block import expiry_cutoff
from gumptionchain.database import db
from gumptionchain.models import PendingTxnDAO
from gumptionchain.util import now


def _post_pending(host, chain, wallet, amount, subject):
    txn = chain.create_opposition(wallet, amount, subject)
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


# ---- data-layer: pending_q ---------------------------------------------


def test_pending_q_returns_all_ordered_received_desc(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        txn1 = _post_pending(host, lc, wallet, 300, subject)
        txn2 = _post_pending(host, lc, wallet, 200, subject)

        rows = db.session.scalars(PendingTxnDAO.pending_q()).all()
        txids = [row.txid for row in rows]
        assert set(txids) == {txn1.txid, txn2.txid}
        # ordered by received desc, tie-break txid
        receiveds = [row.received for row in rows]
        assert receiveds == sorted(receiveds, reverse=True)


def test_pending_q_expired_filter_is_read_only(app):
    with app.app_context():
        old_ts = now() - datetime.timedelta(hours=8)
        dao = PendingTxnDAO(
            txid='a' * 64,
            timestamp=old_ts,
            json_data='{}',
        )
        dao.commit()
        assert PendingTxnDAO.count() == 1

        cutoff = expiry_cutoff(now())
        rows = db.session.scalars(PendingTxnDAO.pending_q(expired=cutoff)).all()
        # the old txn is excluded by the expiry filter
        assert rows == []
        # ...but NOT deleted: the query is read-only
        assert PendingTxnDAO.count() == 1


# ---- mempool view ------------------------------------------------------


def test_mempool_empty(test_client):
    resp = test_client.get('/mempool')
    assert resp.status_code == 200
    assert b'Mempool is empty' in resp.data


def test_mempool_shows_pending_txn(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        txn = _post_pending(host, m.longest_chain, wallet, 300, subject)

        total_out = sum(o.amount or 0 for o in txn.outflows)

        resp = app.test_client().get('/mempool')
        assert resp.status_code == 200
        body = resp.data
        # the pending txid is shown
        assert txn.txid.encode() in body
        # the numeric total-out is shown
        assert str(total_out).encode() in body
        # link-free: no /transaction/<txid> link rendered for it
        assert f'/transaction/{txn.txid}'.encode() not in body
