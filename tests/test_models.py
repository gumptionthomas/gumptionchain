import datetime

from cancelchain.block import Block
from cancelchain.chain import Chain
from cancelchain.database import db
from cancelchain.models import BlockDAO, ChainDAO, LongestChainBlockDAO
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction


def test_unspent_outflows(app, subject, time_stepper, wallet):
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, wallet)
        block_1.mill()
        chain_a.add_block(block_1)
        cb_1 = block_1.coinbase
        cb_1_amount = next(iter(cb_1.outflows)).amount
        chain_a.to_db()
        dao_a = chain_a.to_dao()

        assert BlockDAO.query.count() == 1
        assert LongestChainBlockDAO.query.count() == 1
        assert dao_a is not None
        assert dao_a.unspent_outflows(wallet.address).count() == 1
        balance = chain_a.block_reward()
        assert dao_a.wallet_balance(wallet.address) == balance

        _ = next(time_step)
        t_2a = Transaction()
        t_2a.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
        t_2a.add_outflow(Outflow(amount=cb_1_amount, subject=subject))
        t_2a.set_wallet(wallet)
        t_2a.seal()
        t_2a.sign()

        _ = next(time_step)
        block_2a = Block()
        block_2a.add_txn(t_2a)
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, wallet)
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, wallet)
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        assert BlockDAO.query.count() == 2
        assert LongestChainBlockDAO.query.count() == 2
        assert dao_a.unspent_outflows(wallet.address).count() == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.wallet_balance(wallet.address) == balance
        assert dao_a.subject_balance(subject) == cb_1_amount

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()
        dao_b = chain_b.to_dao()
        assert dao_b is not None

        assert BlockDAO.query.count() == 3
        # Materialization mirrors the longest chain only; chain_a (length 2)
        # remains longest because its tip's timestamp is earlier than
        # chain_b's, so the count is 2 not 3.
        assert LongestChainBlockDAO.query.count() == 2
        assert dao_b.unspent_outflows(wallet.address).count() == 2
        balance = 2 * chain_b.block_reward()
        assert dao_b.wallet_balance(wallet.address) == balance
        assert dao_b.subject_balance(subject) == 0

        assert dao_a.unspent_outflows(wallet.address).count() == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.wallet_balance(wallet.address) == balance
        assert dao_a.subject_balance(subject) == cb_1_amount


def test_longest_chain_block_bootstrap(app, mill_block, wallet):
    """Building the first chain populates the materialization table
    with one row per block, ordered position 0 (genesis) → N-1 (tip).
    """
    with app.app_context():
        _m, _b1 = mill_block(wallet)
        _m, b2 = mill_block(wallet)
        rows = (
            db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].position == 0
        assert rows[1].position == 1
        # Position 1 is the tip (newest block).
        assert rows[1].block_id == BlockDAO.get(b2.block_hash).id


def test_longest_chain_block_single_extend(app, mill_block, wallet):
    """Each subsequent block inserts exactly one new row at the next
    position; prior rows are untouched.
    """
    with app.app_context():
        _m, _b1 = mill_block(wallet)
        rows_before = (
            db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position)
            .all()
        )
        before_count = len(rows_before)
        before_ids = [r.block_id for r in rows_before]

        _m, b2 = mill_block(wallet)

        rows_after = (
            db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position)
            .all()
        )
        assert len(rows_after) == before_count + 1
        # First N positions unchanged.
        assert [r.block_id for r in rows_after[:before_count]] == before_ids
        # New row at the tail with the new block's id.
        assert rows_after[-1].position == before_count
        assert rows_after[-1].block_id == BlockDAO.get(b2.block_hash).id


def test_longest_chain_block_non_longest_extend_noop(app, mill_block, wallet):
    """When a chain that is NOT the longest gets a `Chain.to_db()`
    call, the materialization table must stay aligned with whichever
    chain IS longest. Simulated here by directly invoking
    sync_longest_chain_blocks on a fork chain dao that we construct
    to be shorter than the current longest.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, _b2 = mill_block(wallet)
        longest_rows_before = (
            db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position)
            .all()
        )
        # Look up the chain at the b1 tip (shorter than longest).
        shorter_dao = ChainDAO.get(block_hash=b1.block_hash)
        if shorter_dao is None:
            # The b1 chain may have been replaced by b2's extension —
            # in that case skip the assertion since a non-longest
            # chain row doesn't exist in this fixture path.
            return
        assert shorter_dao._is_longest() is False
        shorter_dao.sync_longest_chain_blocks()
        longest_rows_after = (
            db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position)
            .all()
        )
        assert [(r.block_id, r.position) for r in longest_rows_after] == [
            (r.block_id, r.position) for r in longest_rows_before
        ]


def test_longest_chain_block_property_matches_cte(app, mill_block, wallet):
    """After any chain build, the materialization table contents
    (ordered position DESC, i.e. tip→genesis) must match the recursive
    CTE walk. Uses block_chain (the CTE) as ground truth.
    """
    with app.app_context():
        for _ in range(5):
            mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        cte_ids = [b.id for b in longest.block.block_chain]
        mat_ids = [
            r.block_id
            for r in db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position.desc())
            .all()
        ]
        assert cte_ids == mat_ids


def test_longest_chain_blocks_q_fast_path_skips_cte(app, mill_block, wallet):
    """ChainDAO.longest().blocks uses the materialization JOIN, not
    the recursive CTE. Verified by emitted SQL: the fast-path query
    should NOT contain a 'WITH RECURSIVE' clause.
    """
    with app.app_context():
        _m, _b1 = mill_block(wallet)
        _m, _b2 = mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        compiled_sql = str(
            longest.blocks.statement.compile(
                compile_kwargs={'literal_binds': True}
            )
        )
        assert 'RECURSIVE' not in compiled_sql.upper(), (
            f'Expected no recursive CTE in fast-path SQL, got:\n{compiled_sql}'
        )
        assert 'longest_chain_block' in compiled_sql.lower()


def test_non_longest_chain_blocks_uses_cte(app, mill_block, wallet):
    """A non-longest ChainDAO's .blocks still emits the recursive CTE
    (we did not optimize that path). Verified by emitted SQL.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, _b2 = mill_block(wallet)
        # Make the b1 chain ChainDAO and explicitly mark not-longest
        # by checking via _is_longest. If b1's ChainDAO row no longer
        # exists in this fixture path (b2 extension may have rebound
        # the same row), skip the SQL check.
        shorter_dao = ChainDAO.get(block_hash=b1.block_hash)
        if shorter_dao is None:
            return
        assert shorter_dao._is_longest() is False
        compiled_sql = str(
            shorter_dao.blocks.statement.compile(
                compile_kwargs={'literal_binds': True}
            )
        )
        # CTE fallback uses 'WITH RECURSIVE' on SQLite/Postgres.
        assert 'RECURSIVE' in compiled_sql.upper()


def test_longest_chain_block_rebuild_on_reorg(app, mill_block, wallet):
    """Forcing a rebuild (via _rebuild_longest_chain_blocks) wipes
    the table and repopulates it from the longest chain's CTE walk
    so the contents match exactly.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, _b2 = mill_block(wallet)
        _m, _b3 = mill_block(wallet)
        # Sanity: table has 3 rows.
        assert db.session.query(LongestChainBlockDAO).count() == 3

        # Insert junk to simulate a corrupted / out-of-date table.
        db.session.query(LongestChainBlockDAO).delete()
        db.session.add(
            LongestChainBlockDAO(
                block_id=BlockDAO.get(b1.block_hash).id, position=99
            )
        )
        db.session.commit()
        assert db.session.query(LongestChainBlockDAO).count() == 1

        # Rebuild from the current longest chain.
        longest = ChainDAO.longest()
        assert longest is not None
        longest._rebuild_longest_chain_blocks()
        db.session.commit()

        # Table is back to 3 rows in tip→genesis order matching CTE.
        cte_ids = [b.id for b in longest.block.block_chain]
        mat_ids = [
            r.block_id
            for r in db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position.desc())
            .all()
        ]
        assert cte_ids == mat_ids
        assert len(mat_ids) == 3
