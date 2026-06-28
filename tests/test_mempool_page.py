import datetime
import json

from gumptionchain.api_client import ApiClient
from gumptionchain.block import expiry_cutoff
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import PendingTxnDAO
from gumptionchain.signing_key import SigningKey
from gumptionchain.util import now


def _post_pending(host, chain, signing_key, amount, subject):
    txn = chain.create_opposition(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


# ---- data-layer: pending_q ---------------------------------------------


def test_pending_q_returns_all_ordered_received_desc(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        m, _b = mill_block(signing_key)
        lc = m.longest_chain
        txn1 = _post_pending(host, lc, signing_key, 300, subject)
        txn2 = _post_pending(host, lc, signing_key, 200, subject)

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
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        txn = _post_pending(host, m.longest_chain, signing_key, 300, subject)

        total_out = sum(o.amount or 0 for o in txn.outflows)

        resp = app.test_client().get('/mempool')
        assert resp.status_code == 200
        body = resp.data
        # the pending txid is shown
        assert txn.txid.encode() in body
        # the numeric total-out is shown
        assert str(total_out).encode() in body
        # the txid links to its transaction view (egu-364)
        assert f'/transaction/{txn.txid}'.encode() in body


def _reinsert_pending(txn):
    # Simulate re-gossip of an already-mined txn: its pending row exists
    # while its txid is already canonical.
    PendingTxnDAO(
        txid=txn.txid,
        timestamp=txn.timestamp_dt,
        json_data=txn.to_json(),
    ).commit()


def test_pending_q_exclude_confirmed(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        confirmed = _post_pending(
            host, m.longest_chain, signing_key, 300, subject
        )
        m, _b = mill_block(signing_key)  # confirms + prunes `confirmed`
        _reinsert_pending(confirmed)
        unconfirmed = _post_pending(
            host, m.longest_chain, signing_key, 200, subject
        )

        # default (opt-out): both rows return -> no behavior change for
        # the miller's pending_chain_txns / PendingTxnSet.__iter__
        txids = {
            row.txid for row in db.session.scalars(PendingTxnDAO.pending_q())
        }
        assert txids == {confirmed.txid, unconfirmed.txid}

        # opt-in: the canonical-confirmed row is excluded
        txids = {
            row.txid
            for row in db.session.scalars(
                PendingTxnDAO.pending_q(exclude_confirmed=True)
            )
        }
        assert txids == {unconfirmed.txid}


def test_json_datas_exclude_confirmed(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        confirmed = _post_pending(
            host, m.longest_chain, signing_key, 300, subject
        )
        m, _b = mill_block(signing_key)  # confirms + prunes `confirmed`
        _reinsert_pending(confirmed)
        unconfirmed = _post_pending(
            host, m.longest_chain, signing_key, 200, subject
        )

        # default: both
        datas = list(PendingTxnDAO.json_datas())
        assert len(datas) == 2

        # opt-in: only the unconfirmed txn
        datas = list(PendingTxnDAO.json_datas(exclude_confirmed=True))
        assert len(datas) == 1
        top_txid = json.loads(datas[0])['txid']
        assert top_txid == unconfirmed.txid
        assert top_txid != confirmed.txid


def test_mempool_view_hides_confirmed_txn(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        confirmed = _post_pending(
            host, m.longest_chain, signing_key, 300, subject
        )
        m, _b = mill_block(signing_key)  # confirms + prunes `confirmed`
        _reinsert_pending(confirmed)
        unconfirmed = _post_pending(
            host, m.longest_chain, signing_key, 200, subject
        )

        resp = app.test_client().get('/mempool')
        assert resp.status_code == 200
        assert unconfirmed.txid.encode() in resp.data
        assert confirmed.txid.encode() not in resp.data


def test_exclude_confirmed_is_reorg_safe(
    add_chain_block, app, host, mill_block, requests_proxy, subject, signing_key
):
    # An orphaned block leaves LongestChainBlockDAO, so its txns
    # re-qualify as pending in the filtered reads — i.e. the filter
    # excludes canonical membership, not any-block membership. Fork
    # construction mirrors tests/test_chain.py::
    # test_transaction_provenance_orphaned.
    with app.app_context():
        # signing_key2 needs no role: add_chain_block writes via
        # Chain.add_block directly, bypassing the HTTP/auth layer.
        signing_key2 = SigningKey()
        m, b1 = mill_block(signing_key)  # genesis
        txn = _post_pending(host, m.longest_chain, signing_key, 300, subject)
        m, _b2 = mill_block(signing_key)  # b2 confirms + prunes txn
        _reinsert_pending(txn)

        # while canonical-confirmed: excluded
        rows = db.session.scalars(
            PendingTxnDAO.pending_q(exclude_confirmed=True)
        ).all()
        assert rows == []

        # orphan b2 with a strictly-longer fork off b1
        alt = Chain(block_hash=b1.block_hash)
        add_chain_block(chain=alt, milling_signing_key=signing_key2)
        _, _ = add_chain_block(chain=alt, milling_signing_key=signing_key2)
        alt.to_db()  # sync_longest_chain_blocks -> alt is canonical

        # txn now sits only in a non-canonical fork block -> it
        # re-qualifies as pending
        txids = [
            row.txid
            for row in db.session.scalars(
                PendingTxnDAO.pending_q(exclude_confirmed=True)
            )
        ]
        assert txids == [txn.txid]
