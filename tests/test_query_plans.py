import datetime

from sqlalchemy import event
from test_models import _build_canonical_chain_with_spend

from gumptionchain.database import db
from gumptionchain.models import BlockDAO, ChainDAO, PendingTxnDAO


def _explain_plans(fn):
    """Run fn(), capturing EXPLAIN QUERY PLAN for every SELECT it emits.

    Records the real production SQL + params via a before_cursor_execute
    listener, then EXPLAINs each on the raw DBAPI cursor (qmark params bind
    positionally; the raw cursor bypasses the listener).
    """
    bind = db.session.get_bind()
    captured = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        if not executemany:
            captured.append((statement, parameters))

    event.listen(bind, 'before_cursor_execute', _rec)
    try:
        fn()
    finally:
        event.remove(bind, 'before_cursor_execute', _rec)

    raw = db.session.connection().connection.dbapi_connection
    plans = []
    for stmt, params in captured:
        if not stmt.lstrip().upper().startswith('SELECT'):
            continue
        cur = raw.cursor()
        try:
            cur.execute('EXPLAIN QUERY PLAN ' + stmt, params or ())
            detail = '\n'.join(row[3] for row in cur.fetchall())
        finally:
            cur.close()
        plans.append((stmt, detail))
    return plans


def test_inflows_in_chain_count_uses_index(
    app, add_chain_block, time_stepper, signing_key
):
    """The per-inflow double-spend check must seek via
    ix_inflow_outflow_txid_idx, not SCAN block_transaction / build an
    AUTOMATIC index.
    """
    with app.app_context():
        _chain, block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, signing_key
        )
        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        cb1 = block1.coinbase.txid

        plans = _explain_plans(lambda: tip.inflows_in_chain_count(cb1, 0))
        membership = [p for s, p in plans if 'outflow_txid' in s]
        assert membership, 'expected a query filtering on inflow.outflow_txid'
        joined = '\n'.join(membership)
        assert 'ix_inflow_outflow_txid_idx' in joined, joined
        assert 'AUTOMATIC' not in joined, joined
        assert 'SCAN block_transaction' not in joined, joined


def test_balance_read_builds_no_automatic_index(
    app, add_chain_block, time_stepper, signing_key
):
    """unspent_outflows (basis of signing_key/stake balances) must not fall back
    to an AUTOMATIC index, and must use an outflow/inflow index.
    """
    with app.app_context():
        _chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, signing_key
        )
        chain_dao = ChainDAO.get(block2.block_hash)
        assert chain_dao is not None

        plans = _explain_plans(
            lambda: db.session.execute(
                chain_dao.unspent_outflows(signing_key.address)
            ).all()
        )
        joined = '\n'.join(p for _s, p in plans)
        # NOTE: unspent_outflows still MATERIALIZEs the full self.inflows
        # subquery (the anon_ derived table). That materialization is a known,
        # out-of-scope cost — the index pack covers base-table access, not
        # subquery materialization (issue #161 scope) — so we deliberately do
        # NOT assert against it here. Its absence below is intentional.
        # SQLite always builds an AUTOMATIC covering index to seek into a
        # MATERIALIZEd derived table (the inflows subquery, aliased anon_N) —
        # a base-table index can't cover a transient result set, so those
        # lines are expected and out of scope for indexing. The regression we
        # guard is an AUTOMATIC index over a *base table* (e.g. SCAN /
        # AUTOMATIC on block_transaction or inflow), which means a missing
        # persistent index.
        base_table_automatic = [
            line
            for line in joined.splitlines()
            if 'AUTOMATIC' in line and ' anon_' not in line
        ]
        assert not base_table_automatic, '\n'.join(base_table_automatic)
        assert ('ix_outflow_' in joined) or (
            'ix_inflow_outflow_id' in joined
        ), joined


def test_pending_q_exclude_confirmed_uses_index(
    app, add_chain_block, time_stepper, signing_key
):
    """pending_q(exclude_confirmed=True) correlated NOT EXISTS must seek via
    ix_transaction_txid, not SCAN the transaction table or build an AUTOMATIC
    covering index.
    """
    with app.app_context():
        _chain, _block1, _block2, spend_txid = (
            _build_canonical_chain_with_spend(
                add_chain_block, time_stepper, signing_key
            )
        )
        # Insert a pending row for the canonical txn so the filter is exercised.
        PendingTxnDAO(
            txid=spend_txid,
            timestamp=datetime.datetime.now(datetime.UTC),
            json_data='{}',
        ).commit()

        plans = _explain_plans(
            lambda: db.session.scalars(
                PendingTxnDAO.pending_q(exclude_confirmed=True)
            ).all()
        )
        # Keep the statements that reference the 'transaction' table — i.e.
        # the one carrying the NOT-EXISTS subquery. Anti-vacuity: if
        # exclude_confirmed were dropped, no statement would qualify.
        subquery_plans = [p for s, p in plans if 'transaction' in s.lower()]
        assert subquery_plans, (
            'expected a query involving the transaction table'
        )
        joined = '\n'.join(subquery_plans)
        # The correlated lookup must use the ix_transaction_txid covering index.
        assert 'ix_transaction_txid' in joined, joined
        # This plan materializes nothing, so no AUTOMATIC covering index may
        # appear anywhere; nor a full scan of transaction.
        assert 'AUTOMATIC' not in joined, joined
        assert 'SCAN transaction' not in joined, joined
