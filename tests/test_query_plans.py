from sqlalchemy import event
from test_models import _build_canonical_chain_with_spend

from gumptionchain.database import db
from gumptionchain.models import BlockDAO, ChainDAO


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
    app, add_chain_block, time_stepper, wallet
):
    """The per-inflow double-spend check must seek via
    ix_inflow_outflow_txid_idx, not SCAN block_transaction / build an
    AUTOMATIC index.
    """
    with app.app_context():
        _chain, block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
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
    app, add_chain_block, time_stepper, wallet
):
    """unspent_outflows (basis of wallet/stake balances) must not fall back
    to an AUTOMATIC index, and must use an outflow/inflow index.
    """
    with app.app_context():
        _chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        chain_dao = ChainDAO.get(block2.block_hash)
        assert chain_dao is not None

        plans = _explain_plans(
            lambda: db.session.execute(
                chain_dao.unspent_outflows(wallet.address)
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
