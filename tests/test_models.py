import datetime
from unittest.mock import patch

from _sa_helpers import _count, _count_select

from gumptionchain.block import Block
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import (
    BlockDAO,
    ChainDAO,
    InflowDAO,
    LongestChainBlockDAO,
    OutflowDAO,
    TransactionDAO,
)
from gumptionchain.payload import Inflow, Outflow
from gumptionchain.transaction import CoinbaseMetrics, Transaction


def _pythonic_ancestry_ids(block_dao):
    """Ground-truth ancestry: block ids from this block back to genesis via
    `prev`, computed in pure Python — independent of the CTE and the
    materialization, so it cannot share a bug with the code under test.
    """
    ids = []
    current = block_dao
    while current is not None:
        ids.append(current.id)
        current = current.prev
    return ids


def _oracle_block_ids(select_stmt):
    return sorted(b.id for b in db.session.execute(select_stmt).scalars().all())


def test_unspent_outflows(app, subject, time_stepper, signing_key):
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        cb_1 = block_1.coinbase
        cb_1_amount = next(iter(cb_1.outflows)).amount
        chain_a.to_db()
        dao_a = chain_a.to_dao()

        assert _count(BlockDAO) == 1
        assert _count(LongestChainBlockDAO) == 1
        assert dao_a is not None
        assert _count_select(dao_a.unspent_outflows(signing_key.address)) == 1
        balance = chain_a.block_reward()
        assert dao_a.signing_key_balance(signing_key.address) == balance

        _ = next(time_step)
        t_2a = Transaction()
        t_2a.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
        t_2a.add_outflow(Outflow(amount=cb_1_amount, opposition=subject))
        t_2a.set_signing_key(signing_key)
        t_2a.seal()
        t_2a.sign()

        _ = next(time_step)
        block_2a = Block()
        block_2a.add_txn(t_2a)
        chain_a.link_block(block_2a)
        metrics_2a = sum(
            (
                chain_a.validate_block_txn(block_2a, txn)
                for txn in block_2a.txns
            ),
            CoinbaseMetrics(),
        )
        chain_a.seal_block(block_2a, signing_key, metrics_2a)
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        assert _count(BlockDAO) == 2
        assert _count(LongestChainBlockDAO) == 2
        assert _count_select(dao_a.unspent_outflows(signing_key.address)) == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.signing_key_balance(signing_key.address) == balance
        assert dao_a.opposition_balance(subject) == cb_1_amount

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()
        dao_b = chain_b.to_dao()
        assert dao_b is not None

        assert _count(BlockDAO) == 3
        # Materialization mirrors the longest chain only. chain_a and
        # chain_b both have length 2 but chain_a wins the (idx DESC,
        # timestamp ASC) tiebreaker in ChainDAO.chains(), so the
        # materialization holds chain_a's 2 blocks (not the 3 distinct
        # BlockDAOs in the database).
        assert _count(LongestChainBlockDAO) == 2
        assert _count_select(dao_b.unspent_outflows(signing_key.address)) == 2
        balance = 2 * chain_b.block_reward()
        assert dao_b.signing_key_balance(signing_key.address) == balance
        assert dao_b.opposition_balance(subject) == 0

        assert _count_select(dao_a.unspent_outflows(signing_key.address)) == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.signing_key_balance(signing_key.address) == balance
        assert dao_a.opposition_balance(subject) == cb_1_amount


def test_longest_chain_block_bootstrap(app, mill_block, signing_key):
    """Building the first chain populates the materialization table
    with one row per block, ordered position 0 (genesis) → N-1 (tip).
    """
    with app.app_context():
        _m, _b1 = mill_block(signing_key)
        _m, b2 = mill_block(signing_key)
        rows = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert rows[0].position == 0
        assert rows[1].position == 1
        # Position 1 is the tip (newest block).
        assert rows[1].block_id == BlockDAO.get(b2.block_hash).id


def test_longest_chain_block_single_extend(app, mill_block, signing_key):
    """Each subsequent block inserts exactly one new row at the next
    position; prior rows are untouched.
    """
    with app.app_context():
        _m, _b1 = mill_block(signing_key)
        rows_before = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        before_count = len(rows_before)
        before_ids = [r.block_id for r in rows_before]

        _m, b2 = mill_block(signing_key)

        rows_after = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        assert len(rows_after) == before_count + 1
        # First N positions unchanged.
        assert [r.block_id for r in rows_after[:before_count]] == before_ids
        # New row at the tail with the new block's id.
        assert rows_after[-1].position == before_count
        assert rows_after[-1].block_id == BlockDAO.get(b2.block_hash).id


def test_longest_chain_block_non_longest_extend_noop(
    app, time_stepper, signing_key
):
    """Calling sync on a non-longest chain leaves the materialization
    aligned with whichever chain IS longest.

    Builds a real fork (chain_a + chain_b sharing block_1) so that a
    non-longest ChainDAO row genuinely exists, mirroring the pattern
    in test_unspent_outflows.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()

        longest = ChainDAO.longest()
        assert longest is not None
        non_longest = next(
            (
                d
                for d in db.session.execute(ChainDAO.chains()).scalars()
                if d.id != longest.id
            ),
            None,
        )
        assert non_longest is not None, (
            'fixture did not produce a non-longest ChainDAO row'
        )
        assert non_longest._is_longest() is False

        longest_rows_before = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        non_longest.sync_longest_chain_blocks()
        longest_rows_after = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        assert [(r.block_id, r.position) for r in longest_rows_after] == [
            (r.block_id, r.position) for r in longest_rows_before
        ]


def test_prune_stale_forks_on_canonical_add(app, time_stepper, signing_key):
    """Advancing the canonical chain past FORK_PRUNE_DEPTH prunes the
    stale fork's ChainDAO row — chain rows only, no cascade to blocks.

    Builds a deep fork at height 2 (a losing block_2b sharing block_1)
    plus a shallow within-depth fork near the canonical tip, then drives
    the canonical chain forward until tip_idx > fork_tip_idx + depth.
    Asserts the deep fork's ChainDAO row is deleted, the canonical and
    within-depth rows remain, and the deep fork's BlockDAO survives
    (ancestry still resolves it).
    """
    app.config['FORK_PRUNE_DEPTH'] = 2
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))

        def _extend(chain):
            _ = next(time_step)
            block = Block()
            chain.link_block(block)
            chain.seal_block(block, signing_key, CoinbaseMetrics())
            block.mill()
            chain.add_block(block)
            chain.to_db()
            return block

        # block_1 (idx 0), shared root.
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        # block_2a (idx 1, canonical) + block_2b (idx 1, losing fork).
        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()
        fork_tip_hash = block_2b.block_hash

        # Deep fork row exists and is at tip_idx 1.
        deep_fork_row = ChainDAO.get(block_hash=fork_tip_hash)
        assert deep_fork_row is not None
        assert deep_fork_row.tip_idx == 1

        # Advance canonical chain to idx 3 (block_3a, block_4a), then
        # branch a shallow within-depth fork off block_4a at idx 4.
        _block_3a = _extend(chain_a)
        _block_4a = _extend(chain_a)

        # Shallow fork: block_5b off block_4a (idx 4), a losing branch.
        _ = next(time_step)
        block_5b = Block()
        chain_a.link_block(block_5b)
        chain_a.seal_block(block_5b, signing_key, CoinbaseMetrics())
        block_5b.mill()
        # block_5a (idx 4) is the canonical winner at this height.
        _ = next(time_step)
        block_5a = Block()
        chain_a.link_block(block_5a)
        chain_a.seal_block(block_5a, signing_key, CoinbaseMetrics())
        block_5a.mill()

        _ = next(time_step)
        chain_a.add_block(block_5a)
        chain_a.to_db()
        _ = next(time_step)
        chain_b2 = Chain()
        chain_b2.add_block(block_5b)
        chain_b2.to_db()
        shallow_fork_hash = block_5b.block_hash

        # Drive canonical to idx 6 (two extends off block_5a at idx 4), so the
        # prune threshold is 6 - depth(2) = 4: the deep fork (tip_idx 1) is
        # pruned (1 < 4) while the shallow fork (tip_idx 4) survives (4 is NOT
        # < 4 — within depth).
        _extend(chain_a)
        canonical_tip = _extend(chain_a)
        canonical_idx = canonical_tip.idx

        canonical_row = ChainDAO.longest()
        assert canonical_row is not None
        assert canonical_row.tip_idx == canonical_idx

        # Deep fork (tip_idx 1 < 6 - 2 = 4): pruned.
        assert ChainDAO.get(block_hash=fork_tip_hash) is None
        # Shallow fork (tip_idx 4, NOT < 4): NOT pruned (within depth).
        assert ChainDAO.get(block_hash=shallow_fork_hash) is not None
        # Canonical row survives.
        assert ChainDAO.get(block_hash=canonical_tip.block_hash) is not None

        # No cascade: the pruned fork's BlockDAO still exists and its
        # ancestry resolves back to genesis.
        fork_block = BlockDAO.get(block_hash=fork_tip_hash)
        assert fork_block is not None
        ancestry = _pythonic_ancestry_ids(fork_block)
        assert len(ancestry) == 2  # block_2b + block_1


def test_longest_chain_block_property_matches_prev_walk(
    app, mill_block, signing_key
):
    """After any chain build, the materialization table contents
    (ordered position DESC, i.e. tip→genesis) must match the pure-Python
    prev-walk. Uses the prev-walk oracle as ground truth.
    """
    with app.app_context():
        for _ in range(5):
            mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None
        oracle_ids = _pythonic_ancestry_ids(longest.block)
        mat_ids = [
            r.block_id
            for r in db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position.desc()
                )
            )
            .scalars()
            .all()
        ]
        assert oracle_ids == mat_ids


def test_longest_chain_blocks_q_fast_path_skips_cte(
    app, mill_block, signing_key
):
    """ChainDAO.longest().blocks uses the materialization JOIN, not
    the recursive CTE. Verified by emitted SQL: the fast-path query
    should NOT contain a 'WITH RECURSIVE' clause.
    """
    with app.app_context():
        _m, _b1 = mill_block(signing_key)
        _m, _b2 = mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None
        compiled_sql = str(
            longest.blocks.compile(compile_kwargs={'literal_binds': True})
        )
        assert 'RECURSIVE' not in compiled_sql.upper(), (
            f'Expected no recursive CTE in fast-path SQL, got:\n{compiled_sql}'
        )
        assert 'longest_chain_block' in compiled_sql.lower()


def test_non_longest_chain_blocks_is_cte_free(app, time_stepper, signing_key):
    """A non-longest ChainDAO's .blocks must NOT emit a recursive CTE after
    #158 — it resolves ancestry via the divergent-suffix + materialization
    predicate. Verified by emitted SQL.

    Builds a real fork (chain_a + chain_b sharing block_1) so that a
    non-longest ChainDAO row genuinely exists.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()

        longest = ChainDAO.longest()
        assert longest is not None
        non_longest = next(
            (
                d
                for d in db.session.execute(ChainDAO.chains()).scalars()
                if d.id != longest.id
            ),
            None,
        )
        assert non_longest is not None, (
            'fixture did not produce a non-longest ChainDAO row'
        )
        assert non_longest._is_longest() is False

        compiled_sql = str(
            non_longest.blocks.compile(compile_kwargs={'literal_binds': True})
        )
        assert 'RECURSIVE' not in compiled_sql.upper(), (
            f'non-longest .blocks should be CTE-free, got:\n{compiled_sql}'
        )
        assert 'longest_chain_block' in compiled_sql.lower()


def test_longest_chain_block_rebuild_on_reorg(app, mill_block, signing_key):
    """Forcing a rebuild (via _rebuild_longest_chain_blocks) wipes
    the table and repopulates it from the longest chain's prev-walk
    so the contents match exactly.
    """
    with app.app_context():
        _m, b1 = mill_block(signing_key)
        _m, _b2 = mill_block(signing_key)
        _m, _b3 = mill_block(signing_key)
        # Sanity: table has 3 rows.
        assert _count(LongestChainBlockDAO) == 3

        # Insert junk to simulate a corrupted / out-of-date table.
        db.session.execute(db.delete(LongestChainBlockDAO))
        db.session.add(
            LongestChainBlockDAO(
                block_id=BlockDAO.get(b1.block_hash).id, position=99
            )
        )
        db.session.commit()
        assert _count(LongestChainBlockDAO) == 1

        # Rebuild from the current longest chain.
        longest = ChainDAO.longest()
        assert longest is not None
        longest._rebuild_longest_chain_blocks()
        db.session.commit()

        # Table is back to 3 rows in tip→genesis order matching the
        # prev-walk oracle.
        oracle_ids = _pythonic_ancestry_ids(longest.block)
        mat_ids = [
            r.block_id
            for r in db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position.desc()
                )
            )
            .scalars()
            .all()
        ]
        assert oracle_ids == mat_ids
        assert len(mat_ids) == 3


def test_iterative_walk_matches_prev_walk(app, mill_block, signing_key):
    """_rebuild_longest_chain_blocks via current.prev produces the
    same block ordering as the pure-Python prev-walk oracle.
    """
    with app.app_context():
        for _ in range(10):
            mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None

        # Capture prev-walk ground truth before rebuild.
        oracle_ids = _pythonic_ancestry_ids(longest.block)

        # Force a rebuild via the iterative walk (also runs on bootstrap
        # by sync_longest_chain_blocks; here we exercise it directly).
        longest._rebuild_longest_chain_blocks()
        db.session.commit()

        mat_ids = [
            r.block_id
            for r in db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position.desc()
                )
            )
            .scalars()
            .all()
        ]
        assert oracle_ids == mat_ids
        assert len(mat_ids) == 10


def test_iterative_walk_long_chain(app, mill_block, signing_key):
    """Iterative walk handles a longer chain (50 blocks) and produces
    the right count with no exceptions. Primarily a smoke test that
    the walk terminates and the materialization stays consistent.
    """
    with app.app_context():
        for _ in range(50):
            mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None
        longest._rebuild_longest_chain_blocks()
        db.session.commit()
        count = _count(LongestChainBlockDAO)
        assert count == 50


def test_is_longest_cache_hit_avoids_query(app, mill_block, signing_key):
    """Calling _is_longest twice on the same instance hits the cache
    on the second call and does NOT re-issue ChainDAO.longest().
    """
    with app.app_context():
        mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None
        # Reset cache state and bump generation so the next call is a miss.
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(ChainDAO, 'longest', wraps=ChainDAO.longest) as spy:
            assert longest._is_longest() is True
            assert longest._is_longest() is True
            assert spy.call_count == 1, (
                f'expected one ChainDAO.longest() call (cache hit on '
                f'2nd), got {spy.call_count}'
            )


def test_is_longest_cache_invalidated_by_bump(app, mill_block, signing_key):
    """Calling ChainDAO._bump_generation() after a cached _is_longest
    call forces a recomputation on the next access.
    """
    with app.app_context():
        mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(ChainDAO, 'longest', wraps=ChainDAO.longest) as spy:
            assert longest._is_longest() is True
            ChainDAO._bump_generation()
            assert longest._is_longest() is True
            assert spy.call_count == 2, (
                f'expected two ChainDAO.longest() calls (miss, then '
                f'miss after bump), got {spy.call_count}'
            )


def test_is_longest_cache_survives_across_method_calls(
    app, mill_block, signing_key
):
    """One ChainDAO.longest() call total across a signing_key_balance read
    that internally accesses self.outflows AND self.inflows. Without
    caching this would be 2+ calls.
    """
    with app.app_context():
        _m, _b = mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(ChainDAO, 'longest', wraps=ChainDAO.longest) as spy:
            # signing_key_balance reads self.outflows and self.inflows;
            # each property accessor calls _is_longest.
            longest.signing_key_balance(signing_key.address)
            assert spy.call_count == 1, (
                f'expected one ChainDAO.longest() call across the '
                f'signing_key_balance method (cached after the first '
                f'property access), got {spy.call_count}'
            )


def test_smart_reorg_shallow(app, mill_block, signing_key):
    """A steady-state +1 block via smart-reorg preserves earlier
    positions (common ancestor at position max-1, only the new tip
    is inserted)."""
    with app.app_context():
        _m, _a1 = mill_block(signing_key)
        _m, _a2 = mill_block(signing_key)

        before = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        assert [r.position for r in before] == [0, 1]
        before_snapshot = [(r.block_id, r.position) for r in before]

        # Mining one more block goes through smart-reorg's "walk back
        # one step to find common ancestor at position 1, insert one
        # row at position 2" path — equivalent to the old extend path
        # in observable behavior.
        _m, _a3 = mill_block(signing_key)

        after = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        # Positions 0 and 1 unchanged.
        assert [(r.block_id, r.position) for r in after[:2]] == before_snapshot
        # Position 2 is new.
        assert after[2].position == 2
        assert len(after) == 3


def test_smart_reorg_walks_only_to_common_ancestor(
    app, mill_block, signing_key
):
    """The walk stops at the first block found in the materialization
    instead of falling through to the rebuild path. Verified by
    patching ChainDAO._rebuild_longest_chain_blocks and asserting it
    is NOT invoked during a steady-state extend (the smart-reorg
    common-ancestor branch must handle the case).
    """
    with app.app_context():
        for _ in range(5):
            mill_block(signing_key)

        rows_before = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        snapshot_before = [(r.block_id, r.position) for r in rows_before]

        with patch.object(
            ChainDAO,
            '_rebuild_longest_chain_blocks',
            autospec=True,
        ) as rebuild_spy:
            _m, _new_tip = mill_block(signing_key)

        # The smart-reorg path must NOT have invoked the rebuild
        # method for a steady-state extend. If the implementation
        # regresses to a full rebuild, this fails loudly.
        rebuild_spy.assert_not_called()

        rows_after = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        # First 5 rows' (block_id, position) pairs unchanged.
        snapshot_after = [(r.block_id, r.position) for r in rows_after[:5]]
        assert snapshot_after == snapshot_before
        # And exactly one new row at the tail.
        assert len(rows_after) == len(rows_before) + 1
        assert rows_after[-1].position == 5


def test_smart_reorg_already_in_sync_short_circuits(
    app, mill_block, signing_key
):
    """Calling sync_longest_chain_blocks twice on the same chain
    instance: the second call finds the tip already in the table on
    its first walk iteration and returns without mutation or
    generation bump.
    """
    with app.app_context():
        mill_block(signing_key)
        longest = ChainDAO.longest()
        assert longest is not None

        gen_before = ChainDAO._chain_generation
        rows_before = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        snapshot_before = [(r.block_id, r.position) for r in rows_before]

        # Re-invoke sync; nothing should change.
        longest.sync_longest_chain_blocks()

        rows_after = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        snapshot_after = [(r.block_id, r.position) for r in rows_after]

        assert snapshot_before == snapshot_after
        assert ChainDAO._chain_generation == gen_before, (
            f'expected generation to be unchanged after no-op sync, '
            f'got {ChainDAO._chain_generation} (was {gen_before})'
        )


def test_smart_reorg_deep_reorg_with_no_common_ancestor_falls_back(
    app, mill_block, time_stepper, signing_key
):
    """If the materialization holds block_ids that aren't reachable
    from the current chain's tip via prev pointers, the walk reaches
    genesis without finding a common ancestor. The fallback uses the
    collected list to fully replace the materialization.

    Uses block_ids from a divergent fork chain so the rows satisfy
    the LongestChainBlockDAO.block_id → block.id FK constraint
    (which would fire on Postgres or SQLite with foreign_keys=ON).
    time_stepper ensures the fork's blocks have different timestamps
    (and thus different block_hashes) than chain_a's, even under the
    test's easy-target milling.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)

        # Build chain_a of length 3 (the canonical chain).
        for _ in range(3):
            mill_block(signing_key)
            _ = next(time_step)
        chain_a_longest = ChainDAO.longest()
        assert chain_a_longest is not None
        chain_a_block_ids = {
            r.block_id
            for r in db.session.execute(db.select(LongestChainBlockDAO))
            .scalars()
            .all()
        }

        # Build a divergent fork chain_b at genesis. Different timestamps
        # (via time_stepper) make chain_b's blocks have different hashes
        # than chain_a's blocks under the easy-target deterministic-
        # nonce milling regime, so they get distinct BlockDAO rows.
        #
        # Use block.to_db() directly (bypassing Chain.validate_block) so
        # that the new DuplicateGenesisError check — which correctly
        # rejects an alternate genesis at the protocol level — does not
        # interfere with this off-protocol DB-corruption setup.
        # chain_b.block_hash is advanced manually after each to_db() call
        # because chain_b.link_block reads chain_b.last_block from the DB
        # via the tip block_hash; without advancing the tip, block_b2 would
        # link at idx 0 (a second genesis) instead of chaining onto block_b1.
        chain_b = Chain()
        block_b1 = Block()
        chain_b.link_block(block_b1)
        chain_b.seal_block(block_b1, signing_key, CoinbaseMetrics())
        block_b1.mill()
        block_b1.to_db()
        chain_b.block_hash = block_b1.block_hash
        _ = next(time_step)
        block_b2 = Block()
        chain_b.link_block(block_b2)
        chain_b.seal_block(block_b2, signing_key, CoinbaseMetrics())
        block_b2.mill()
        block_b2.to_db()
        chain_b.block_hash = block_b2.block_hash
        # NOTE: deliberately do NOT call chain_b.to_db() — that would
        # involve the sync code under test. We want to corrupt the
        # table by hand to exercise the fallback.
        fork_block_ids = [
            BlockDAO.get(block_b1.block_hash).id,
            BlockDAO.get(block_b2.block_hash).id,
        ]
        # Sanity: fork blocks are real BlockDAO rows distinct from
        # chain_a's.
        assert all(bid is not None for bid in fork_block_ids)
        assert not set(fork_block_ids) & chain_a_block_ids

        # Corrupt the materialization with fork block_ids — real FKs
        # to BlockDAO rows, but not reachable from chain_a's tip via
        # prev pointers.
        db.session.execute(db.delete(LongestChainBlockDAO))
        db.session.add(
            LongestChainBlockDAO(
                block_id=fork_block_ids[0],
                position=0,
            )
        )
        db.session.add(
            LongestChainBlockDAO(
                block_id=fork_block_ids[1],
                position=1,
            )
        )
        db.session.commit()

        # Sync chain_a. The walk from chain_a's tip won't find any
        # fork block in the materialization (chain_a's prev chain
        # doesn't reach into chain_b's blocks), so it walks to genesis
        # and falls back to full DELETE + bulk insert.
        chain_a_longest.sync_longest_chain_blocks()
        db.session.commit()

        rows = (
            db.session.execute(
                db.select(LongestChainBlockDAO).order_by(
                    LongestChainBlockDAO.position
                )
            )
            .scalars()
            .all()
        )
        # 3 real chain_a blocks now in the materialization; no fork
        # block_ids.
        assert len(rows) == 3
        assert all(r.block_id not in fork_block_ids for r in rows)
        # Positions 0, 1, 2 (genesis-first).
        assert [r.position for r in rows] == [0, 1, 2]


def _build_canonical_chain_with_spend(
    add_chain_block, time_stepper, signing_key
):
    """Build a 2-block canonical chain where block 2 contains a txn that
    spends block 1's coinbase. Returns (chain, block1, block2, spend_txid)."""
    time_step = time_stepper(
        start=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    )
    _ = next(time_step)
    chain, block1 = add_chain_block(milling_signing_key=signing_key)
    cb = block1.coinbase
    cb_amount = next(iter(cb.outflows)).amount
    _ = next(time_step)
    t = Transaction()
    t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
    t.add_outflow(Outflow(amount=cb_amount, address=signing_key.address))
    t.set_signing_key(signing_key)
    t.seal()
    t.sign()
    t.to_db()
    _ = next(time_step)
    block2 = Block()
    block2.add_txn(t)
    _, block2 = add_chain_block(
        chain=chain, block=block2, milling_signing_key=signing_key
    )
    chain.to_db()
    return chain, block1, block2, t.txid


def _oracle_get_block_in_chain(block_dao, block_hash=None, idx=None):
    """Ground-truth get_block_in_chain via the Python prev-walk ancestry."""
    ids = _pythonic_ancestry_ids(block_dao)
    stmt = db.select(BlockDAO).where(BlockDAO.id.in_(ids))
    if block_hash is not None:
        stmt = stmt.where(BlockDAO.block_hash == block_hash)
    if idx is not None:
        stmt = stmt.where(BlockDAO.idx == idx)
    return db.session.execute(stmt).scalar_one_or_none()


def _oracle_txn_in_chain(block_dao, txid):
    ids = _pythonic_ancestry_ids(block_dao)
    return db.session.execute(
        db.select(TransactionDAO)
        .join(TransactionDAO.blocks)
        .where(BlockDAO.id.in_(ids))
        .where(TransactionDAO.txid == txid)
    ).scalar_one_or_none()


def _oracle_inflow_exists(block_dao, outflow_txid, outflow_idx):
    ids = _pythonic_ancestry_ids(block_dao)
    hit = (
        db.session.execute(
            db.select(InflowDAO)
            .join(InflowDAO.transaction)
            .join(TransactionDAO.blocks)
            .where(BlockDAO.id.in_(ids))
            .where(InflowDAO.outflow_txid == outflow_txid)
            .where(InflowDAO.outflow_idx == outflow_idx)
        )
        .scalars()
        .first()
    )
    return 1 if hit is not None else 0


def _build_fork(time_stepper, signing_key, subject):
    """Build a real fork: chain_a (canonical, longest) and chain_b (fork)
    both share block_1. chain_b's tip block_2b spends block_1's coinbase, so
    the divergent suffix carries a genuine inflow. Returns a dict with the
    fork tip BlockDAO and the txids/hashes the tests assert against.
    """
    time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
    _ = next(time_step)
    chain_a = Chain()
    block_1 = Block()
    chain_a.link_block(block_1)
    chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
    block_1.mill()
    chain_a.add_block(block_1)
    chain_a.to_db()
    cb_1 = block_1.coinbase
    cb_1_amount = next(iter(cb_1.outflows)).amount

    _ = next(time_step)
    block_2a = Block()
    chain_a.link_block(block_2a)
    chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
    block_2a.mill()

    # Fork tip spends block_1's coinbase, so the divergent suffix has a
    # real inflow to count.
    _ = next(time_step)
    spend = Transaction()
    spend.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
    spend.add_outflow(Outflow(amount=cb_1_amount, opposition=subject))
    spend.set_signing_key(signing_key)
    spend.seal()
    spend.sign()

    # chain_a's tip is still block_1 here (block_2a is milled but not yet
    # added), so linking block_2b against chain_a chains it onto block_1 as
    # an alternate child — the fork point.
    block_2b = Block()
    block_2b.add_txn(spend)
    chain_a.link_block(block_2b)
    metrics_2b = sum(
        (chain_a.validate_block_txn(block_2b, txn) for txn in block_2b.txns),
        CoinbaseMetrics(),
    )
    chain_a.seal_block(block_2b, signing_key, metrics_2b)
    block_2b.mill()

    # block_2a has the earlier seal timestamp, so chain_a wins the
    # (idx DESC, timestamp ASC) tiebreaker in ChainDAO.chains() and is
    # canonical regardless of add order (timestamp is fixed at seal()).
    _ = next(time_step)
    chain_a.add_block(block_2a)
    chain_a.to_db()

    _ = next(time_step)
    chain_b = Chain()
    chain_b.add_block(block_2b)
    chain_b.to_db()

    fork = BlockDAO.get(block_2b.block_hash)
    return {
        'fork': fork,
        'block_1': block_1,
        'block_2a': block_2a,
        'block_2b': block_2b,
        'fork_cb_txid': block_2b.coinbase.txid,
        'ancestor_cb_txid': block_1.coinbase.txid,
        'spend_txid': spend.txid,
        'spend_outflow_txid': cb_1.txid,
    }


def test_hot_path_methods_match_oracle_canonical(
    app, add_chain_block, time_stepper, signing_key
):
    with app.app_context():
        _chain, block1, block2, spend_txid = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, signing_key
        )
        cb1_txid = block1.coinbase.txid
        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None

        for txid in (spend_txid, cb1_txid, 'missing'):
            oracle = _oracle_txn_in_chain(tip, txid)
            new = tip.get_transaction_in_chain(txid)
            assert (new.id if new else None) == (
                oracle.id if oracle else None
            ), f'txn mismatch for txid={txid!r}'

        for otxid, oidx in ((cb1_txid, 0), ('missing', 0)):
            oracle_exists = _oracle_inflow_exists(tip, otxid, oidx)
            assert tip.inflows_in_chain_count(otxid, oidx) == oracle_exists, (
                f'inflow-existence mismatch for outflow=({otxid!r}, {oidx})'
            )

        for kwargs in (
            {'block_hash': block1.block_hash},
            {'idx': 0},
            {'idx': 1},
            {'block_hash': 'missing'},
        ):
            oracle_block = _oracle_get_block_in_chain(tip, **kwargs)
            new_block = tip.get_block_in_chain(**kwargs)
            assert (new_block.id if new_block else None) == (
                oracle_block.id if oracle_block else None
            ), f'block mismatch for {kwargs!r}'


def test_hot_path_methods_match_oracle_fork(
    app, time_stepper, signing_key, subject
):
    """A fork (non-longest) block resolves its divergent-suffix ancestry the
    same as the prev-walk oracle.
    """
    with app.app_context():
        f = _build_fork(time_stepper, signing_key, subject)
        fork = f['fork']
        assert fork is not None
        assert fork._ancestry()[0]  # non-empty divergent suffix

        for txid in (f['fork_cb_txid'], f['ancestor_cb_txid'], 'missing'):
            oracle = _oracle_txn_in_chain(fork, txid)
            new = fork.get_transaction_in_chain(txid)
            assert (new.id if new else None) == (
                oracle.id if oracle else None
            ), f'txn mismatch for txid={txid!r}'

        for kwargs in (
            {'block_hash': f['block_2b'].block_hash},
            {'block_hash': f['block_1'].block_hash},
            {'idx': 0},
        ):
            oracle_block = _oracle_get_block_in_chain(fork, **kwargs)
            new_block = fork.get_block_in_chain(**kwargs)
            assert (new_block.id if new_block else None) == (
                oracle_block.id if oracle_block else None
            ), f'block mismatch for {kwargs!r}'

        # The fork's divergent suffix consumes block_1's coinbase; check it
        # against the prev-walk oracle (hit) plus a miss.
        # inflows_in_chain_count is 0/1 existence, not a true count.
        for otxid, oidx, expected in (
            (f['spend_outflow_txid'], 0, 1),
            ('missing', 0, 0),
        ):
            oracle_exists = _oracle_inflow_exists(fork, otxid, oidx)
            assert oracle_exists == expected, (
                f'oracle ground-truth mismatch for outflow=({otxid!r}, {oidx})'
            )
            assert fork.inflows_in_chain_count(otxid, oidx) == oracle_exists, (
                f'inflow-existence mismatch for outflow=({otxid!r}, {oidx})'
            )


def test_hot_path_methods_match_oracle_empty_materialization(
    app, add_chain_block, time_stepper, signing_key
):
    """With an empty LongestChainBlockDAO (bootstrap), _ancestry walks the
    whole chain into divergent_ids and the methods still match the oracle.
    """
    with app.app_context():
        _chain, block1, block2, spend_txid = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, signing_key
        )
        db.session.execute(db.delete(LongestChainBlockDAO))
        db.session.commit()
        assert _count(LongestChainBlockDAO) == 0

        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        divergent, cap = tip._ancestry()
        assert cap is None
        assert len(divergent) == 2  # whole chain is "divergent"

        for txid in (spend_txid, block1.coinbase.txid, 'missing'):
            oracle = _oracle_txn_in_chain(tip, txid)
            new = tip.get_transaction_in_chain(txid)
            assert (new.id if new else None) == (
                oracle.id if oracle else None
            ), f'txn mismatch for txid={txid!r}'
        assert tip.get_block_in_chain(idx=0) is not None
        assert tip.inflows_in_chain_count(block1.coinbase.txid, 0) == 1


def test_recursive_cte_is_deleted():
    """#158 capstone: the recursive CTE and its *_chain builders must be
    gone from every DAO — no reachable recursive-CTE code remains.
    """
    for attr in (
        '_block_chain',
        'block_chain',
        'transactions_chain',
        'outflows_chain',
        'inflows_chain',
    ):
        assert not hasattr(BlockDAO, attr), (
            f'BlockDAO.{attr} should be deleted in #158'
        )
    assert not hasattr(TransactionDAO, 'transactions_chain')
    assert not hasattr(OutflowDAO, 'outflows_chain')
    assert not hasattr(InflowDAO, 'inflows_chain')


def test_ancestry_read_paths_match_oracle_canonical(
    app, add_chain_block, time_stepper, signing_key
):
    """ChainDAO read accessors + address_transactions on a canonical tip
    return exactly the ancestry computed by the Python prev-walk oracle.
    """
    with app.app_context():
        _chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, signing_key
        )
        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        oracle_ids = sorted(_pythonic_ancestry_ids(tip))

        chain_dao = ChainDAO.get(block2.block_hash)
        assert chain_dao is not None
        assert _oracle_block_ids(chain_dao.blocks) == oracle_ids

        txn_ids = {
            t.id
            for t in db.session.execute(chain_dao.transactions).scalars().all()
        }
        assert txn_ids
        for t in db.session.execute(chain_dao.transactions).scalars().all():
            assert {b.id for b in t.blocks} & set(oracle_ids)

        addr_txns = list(
            db.session.execute(
                tip.address_transactions(signing_key.address)
            ).scalars()
        )
        assert addr_txns
        assert all(t.address == signing_key.address for t in addr_txns)
        for t in addr_txns:
            assert {b.id for b in t.blocks} & set(oracle_ids)


def test_ancestry_read_paths_match_oracle_fork(
    app, time_stepper, signing_key, subject
):
    """The non-longest (fork) read accessors resolve the fork tip's ancestry
    (divergent suffix + shared prefix) identically to the Python oracle, and
    fork balances/outflows are correct.
    """
    with app.app_context():
        f = _build_fork(time_stepper, signing_key, subject)
        fork = f['fork']
        assert fork is not None
        assert fork._ancestry()[0]  # genuine non-empty divergent suffix
        oracle_ids = sorted(_pythonic_ancestry_ids(fork))

        chain_dao = ChainDAO.get(f['block_2b'].block_hash)
        assert chain_dao is not None
        assert chain_dao._is_longest() is False
        assert _oracle_block_ids(chain_dao.blocks) == oracle_ids

        opp = chain_dao.opposition_balance(subject)
        assert opp > 0

        unspent = list(
            db.session.execute(
                chain_dao.unspent_outflows(signing_key.address)
            ).scalars()
        )
        assert unspent
        for o in unspent:
            assert {b.id for b in o.transaction.blocks} & set(oracle_ids)


def test_ancestry_read_paths_match_oracle_bootstrap(
    app, add_chain_block, time_stepper, signing_key
):
    """With an empty materialization, ancestry_*_q resolve via the
    all-divergent predicate and still match the oracle.
    """
    with app.app_context():
        _chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, signing_key
        )
        db.session.execute(db.delete(LongestChainBlockDAO))
        db.session.commit()
        assert _count(LongestChainBlockDAO) == 0

        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        oracle_ids = sorted(_pythonic_ancestry_ids(tip))
        assert _oracle_block_ids(tip.ancestry_blocks_q()) == oracle_ids


def test_longest_chain_blocks_range_ascending(app, mill_block, signing_key):
    """longest_chain_blocks_range returns the canonical blocks at the
    requested positions, ascending (genesis→tip)."""
    with app.app_context():
        _m, b0 = mill_block(signing_key)  # idx 0 (genesis)
        _m, b1 = mill_block(signing_key)  # idx 1
        _m, b2 = mill_block(signing_key)  # idx 2

        rows = db.session.scalars(
            BlockDAO.longest_chain_blocks_range(1, 2)
        ).all()
        assert [r.idx for r in rows] == [1, 2]
        assert rows[0].block_hash == b1.block_hash
        assert rows[1].block_hash == b2.block_hash
        # sanity: genesis is excluded by the from_idx bound.
        assert b0.idx == 0


def test_longest_chain_blocks_range_past_tip_empty(
    app, mill_block, signing_key
):
    """A range entirely past the tip returns no rows."""
    with app.app_context():
        mill_block(signing_key)  # idx 0
        mill_block(signing_key)  # idx 1
        rows = db.session.scalars(
            BlockDAO.longest_chain_blocks_range(5, 3)
        ).all()
        assert list(rows) == []


def test_longest_chain_blocks_range_excludes_fork(
    app, time_stepper, signing_key
):
    """A fork block at a shared height is not returned — only the
    longest-chain (materialized) block per position."""
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()

        # block_2a and block_2b both live at height 1, but only the
        # longest chain's block is materialized at that position.
        rows = db.session.scalars(
            BlockDAO.longest_chain_blocks_range(1, 1)
        ).all()
        assert len(rows) == 1
        canonical_hash = rows[0].block_hash
        assert canonical_hash in {block_2a.block_hash, block_2b.block_hash}
        # The fork block at the same height is absent.
        other = (
            block_2b.block_hash
            if canonical_hash == block_2a.block_hash
            else block_2a.block_hash
        )
        assert all(r.block_hash != other for r in rows)
        # Pin WHICH block is canonical: the range query must agree with the
        # established longest-chain query at this height. A wrong join/filter
        # that returned the fork block would disagree here.
        lc_blocks = db.session.scalars(BlockDAO.longest_chain_blocks_q()).all()
        lc_at_1 = next(b for b in lc_blocks if b.idx == 1)
        assert canonical_hash == lc_at_1.block_hash


def test_longest_returns_none_on_empty_db(app):
    """longest() on an empty chain table returns None (no rows at the MAX
    tip_idx because there is no MAX)."""
    with app.app_context():
        assert ChainDAO.count() == 0
        assert ChainDAO.longest() is None


def test_longest_picks_highest_tip_idx(app, time_stepper, signing_key):
    """With forks at different tip heights, longest() returns the chain
    whose tip has the highest idx — independent of insertion order."""
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        # Two siblings on top of block_1: block_2a (the eventual loser, a
        # short fork at idx 1) and block_2b (which chain_a extends further).
        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        # Persist the short fork tip (block_2a) first as its own chain row.
        _ = next(time_step)
        fork = Chain()
        fork.add_block(block_2a)
        fork.to_db()

        # Then extend the main chain along block_2b to a strictly greater
        # height (idx 2), so it has the unique highest tip_idx.
        _ = next(time_step)
        chain_a.add_block(block_2b)
        chain_a.to_db()

        _ = next(time_step)
        block_3 = Block()
        chain_a.link_block(block_3)
        chain_a.seal_block(block_3, signing_key, CoinbaseMetrics())
        block_3.mill()
        chain_a.add_block(block_3)
        chain_a.to_db()

        longest = ChainDAO.longest()
        assert longest is not None
        assert longest.tip_idx == 2
        assert longest.block_hash == block_3.block_hash
        # The short fork row is still present but loses on tip height.
        assert ChainDAO.get(block_hash=block_2a.block_hash) is not None


def test_longest_tiebreak_matches_old_chains_first(
    app, time_stepper, signing_key
):
    """On a SAME-tip-idx tie, longest() returns the same row the old
    chains().first() would: (earliest timestamp, then lowest block_hash).

    The fixture is two sibling tips at idx 1 (block_2a, block_2b) — a
    genuine tie. We compute the expected winner directly from the fork
    blocks and assert longest() agrees with both that and chains().first().
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()

        # Both tips live at idx 1 — a real tie on tip_idx.
        tip_a = ChainDAO.get(block_hash=block_2a.block_hash)
        tip_b = ChainDAO.get(block_hash=block_2b.block_hash)
        assert tip_a is not None and tip_b is not None
        assert tip_a.tip_idx == tip_b.tip_idx == 1

        # Expected winner, computed directly from the fork blocks via the
        # consensus tiebreak: earliest timestamp, then lowest block_hash.
        expected = min(
            (tip_a, tip_b),
            key=lambda d: (d.block.timestamp, d.block.block_hash),
        )

        old_first = db.session.execute(ChainDAO.chains()).scalars().first()
        longest = ChainDAO.longest()
        assert longest is not None
        assert longest.id == expected.id
        # And it agrees with the pre-rewrite chains().first() behavior.
        assert old_first is not None
        assert longest.id == old_first.id


def test_tip_idx_maintained_on_extend_and_fork(app, time_stepper, signing_key):
    """Extending the canonical chain advances the in-place row's tip_idx;
    a fork creates a NEW row whose tip_idx is the fork tip's height."""
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        row = ChainDAO.get(block_hash=block_1.block_hash)
        assert row is not None
        assert row.tip_idx == 0
        canonical_id = row.id

        # Build a sibling fork off block_1 before extending the canonical
        # chain, so the extend mutates in place and the fork is a new row.
        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        # Extend the canonical chain in place along block_2a.
        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        extended = ChainDAO.get(block_hash=block_2a.block_hash)
        assert extended is not None
        # Same row, advanced in place to the new tip height.
        assert extended.id == canonical_id
        assert extended.tip_idx == 1
        # The old tip hash no longer resolves to a chain row.
        assert ChainDAO.get(block_hash=block_1.block_hash) is None

        # The sibling fork is born as a NEW row at the fork tip's height.
        _ = next(time_step)
        fork = Chain()
        fork.add_block(block_2b)
        fork.to_db()

        fork_row = ChainDAO.get(block_hash=block_2b.block_hash)
        assert fork_row is not None
        assert fork_row.id != canonical_id
        assert fork_row.tip_idx == 1


def test_antijoin_equivalence_all_methods(
    app, subject, time_stepper, signing_key
):
    """Pin exact results for every unspent/balance method over a real fork
    so both the longest-chain and ancestry routings of self.inflows run.

    This is the equivalence guard for the NOT EXISTS rewrite (#165): the
    values below are computed from the known spent/unspent partition and must
    not change when the anti-join SQL is restructured.

    Fixture shape (matches the sibling-fork pattern in test_unspent_outflows /
    test_longest_chain_block_non_longest_extend_noop): block_1 funds signing_key
    with coinbase cb_1; block_2a and block_2b are real SIBLINGS off block_1
    (both linked while block_1 is still the tip, before either is committed).
    block_2a spends cb_1 entirely into an opposition stake on `subject`;
    block_2b is an empty extension. chain_a (tip block_2a) wins the longest
    race, so dao_a routes through longest_chain_inflows_q and dao_b (tip
    block_2b) routes through ancestry_inflows_q.

    Coinbase note: sealing block_2a over the opposition stake mints a second
    coinbase outflow to the signing_key (half the net new stake on the subject),
    so on chain_a the key holds TWO unspent outflows — cb_2a's base reward
    plus that mint — not one. The pinned values below reflect that.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)

        # block_1: coinbase cb_1 to signing_key.
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        cb_1 = block_1.coinbase
        cb_1_amount = next(iter(cb_1.outflows)).amount
        chain_a.to_db()
        reward = chain_a.block_reward()
        # The opposition stake mints a second coinbase outflow worth half the
        # net new stake; cb_1_amount is even, so this divides exactly.
        mint = cb_1_amount // 2

        # block_2a: spend cb_1 entirely into an opposition stake on `subject`.
        # Linked onto block_1 (still the tip).
        _ = next(time_step)
        t_2a = Transaction()
        t_2a.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
        t_2a.add_outflow(Outflow(amount=cb_1_amount, opposition=subject))
        t_2a.set_signing_key(signing_key)
        t_2a.seal()
        t_2a.sign()
        _ = next(time_step)
        block_2a = Block()
        block_2a.add_txn(t_2a)
        chain_a.link_block(block_2a)
        metrics_2a = sum(
            (
                chain_a.validate_block_txn(block_2a, txn)
                for txn in block_2a.txns
            ),
            CoinbaseMetrics(),
        )
        chain_a.seal_block(block_2a, signing_key, metrics_2a)
        block_2a.mill()

        # block_2b: a REAL sibling of block_2a — linked while block_1 is still
        # the tip (before block_2a is committed), so it also links onto
        # block_1. An empty extension (no stake).
        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        # Commit block_2a as chain_a's tip (chain_a is the longest chain).
        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()
        dao_a = chain_a.to_dao()
        assert dao_a is not None
        assert dao_a._is_longest()  # longest-chain routing

        # chain_b shares block_1; its tip is block_2b (the losing sibling).
        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()
        dao_b = chain_b.to_dao()
        assert dao_b is not None
        assert not dao_b._is_longest()  # ancestry routing

        # On chain_a: cb_1 is SPENT (consumed by t_2a). block_2a's coinbase
        # yields TWO unspent outflows to key — base reward + the stake mint.
        # The opposition stake (an outflow on `subject`, no address) is unspent.
        assert _count_select(dao_a.unspent_outflows(signing_key.address)) == 2
        assert dao_a.signing_key_balance(signing_key.address) == reward + mint
        assert dao_a.opposition_balance(subject) == cb_1_amount
        assert dao_a.support_balance(subject) == 0
        assert (
            _count_select(dao_a.unrescinded_outflows(subject, 'opposition'))
            == 1
        )
        assert (
            _count_select(dao_a.unrescinded_outflows(subject, 'support')) == 0
        )
        # Leaderboards (longest chain = chain_a).
        wl = db.session.execute(dao_a.signing_key_leaderboard()).all()
        assert wl == [(signing_key.address, reward + mint)]
        sl = db.session.execute(dao_a.subject_leaderboard()).all()
        # (subject, opposition, support, total)
        assert sl == [(subject, cb_1_amount, 0, cb_1_amount)]

        # On chain_b (ancestry routing): t_2a is NOT in chain_b, so cb_1 is
        # UNSPENT; block_2b's coinbase is a single unspent reward (no stake →
        # no mint). Two unspent transfers, no stake on subject.
        assert _count_select(dao_b.unspent_outflows(signing_key.address)) == 2
        assert dao_b.signing_key_balance(signing_key.address) == 2 * reward
        assert dao_b.opposition_balance(subject) == 0
        assert (
            _count_select(dao_b.unrescinded_outflows(subject, 'opposition'))
            == 0
        )
        assert db.session.execute(dao_b.subject_leaderboard()).all() == []
        assert db.session.execute(dao_b.signing_key_leaderboard()).all() == [
            (signing_key.address, 2 * reward)
        ]

        # chain_a values are unchanged by chain_b's existence.
        assert dao_a.signing_key_balance(signing_key.address) == reward + mint
        assert dao_a.opposition_balance(subject) == cb_1_amount


def _query_plan_rows(stmt):
    """EXPLAIN QUERY PLAN for a Select, as a list of uppercased detail rows.

    Compiles with literal binds so the SQL can be wrapped verbatim; SQLite's
    EXPLAIN QUERY PLAN returns rows whose last column is the human-readable
    `detail` (e.g. 'MATERIALIZE anon_3', 'SEARCH ... USING AUTOMATIC ...').
    Returning one string per plan node lets a guard reason about a single
    node (e.g. 'does any AUTOMATIC-index node touch inflow?').
    """
    compiled = stmt.compile(db.engine, compile_kwargs={'literal_binds': True})
    rows = db.session.execute(db.text(f'EXPLAIN QUERY PLAN {compiled}')).all()
    # Only the last column (`detail`) is the human-readable plan node; the
    # leading id/parent/notused columns are integers that would just add
    # noise to the substring guards below.
    return [str(row[-1]).upper() for row in rows]


def test_antijoin_no_materialization(app, subject, time_stepper, signing_key):
    """The unspent/balance reads must not MATERIALIZE the whole-chain inflow
    set nor build a per-call AUTOMATIC index over it (#165).

    Before the rewrite, EXPLAIN QUERY PLAN showed the inflow anti-join as
    ``MATERIALIZE anon_N`` (the whole-chain inflow set) followed by
    ``SEARCH anon_N USING AUTOMATIC COVERING INDEX (outflow_id=?) LEFT-JOIN``
    (a throwaway per-call index over that materialization). Both scale with
    chain height regardless of result size — that is the cost #165 removes.

    After the rewrite the inflow set is reached, in every one of these plans,
    via a ``CORRELATED SCALAR SUBQUERY`` whose only inflow access is
    ``SEARCH inflow USING INDEX ix_inflow_outflow_id (outflow_id=?)`` — an
    index seek, no materialization, no AUTOMATIC index over inflows.

    Step-9 contingency, settled against the actual SQLite plan: a residual
    ``AUTOMATIC COVERING INDEX`` survives — but ONLY in signing_key_leaderboard,
    and ONLY over ``anon_1``, which is the chain-**transactions** membership
    sub-select (a ``CO-ROUTINE`` over block_transaction/transaction backing
    the txn_alias join), NOT the inflow set. That subquery is shared,
    pre-existing, and out of scope for #165; the inflow anti-join is the
    target. So the guards are asserted precisely:

      * no ``MATERIALIZE`` anywhere (the inflow set is never materialized, and
        the real plans confirm nothing else is either);
      * the inflow table is always reached by ``ix_inflow_outflow_id`` (the
        positive witness that the correlated index seek is in effect); and
      * no AUTOMATIC index is ever built over the inflow set. The regression
        node renders as ``SEARCH anon_N USING AUTOMATIC COVERING INDEX
        (outflow_id=?) LEFT-JOIN`` — an AUTOMATIC index keyed on ``outflow_id``
        over the materialized inflow set (the materialized set is an anonymous
        ``anon_N`` alias, so the table name ``inflow`` does NOT appear in that
        row — keying the guard off ``outflow_id`` is what makes it catch the
        regression). The only legitimate AUTOMATIC post-rewrite is the
        transaction-membership ``anon_1`` co-routine, keyed ``(id=?)``; and the
        correlated inflow seek uses ``USING INDEX ix_inflow_outflow_id``, not
        AUTOMATIC. So ``AUTOMATIC`` paired with ``outflow_id`` in one node is
        the precise regression signature.

    The int-returning methods (signing_key_balance / _stake_balance) share the
    exact same _unspent_clause(), so the Select-returners are a sufficient
    witness.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()
        dao_a = chain_a.to_dao()
        assert dao_a is not None

        plans = [
            _query_plan_rows(dao_a.unspent_outflows(signing_key.address)),
            _query_plan_rows(dao_a.unrescinded_outflows(subject, 'opposition')),
            _query_plan_rows(dao_a.signing_key_leaderboard()),
            _query_plan_rows(dao_a.subject_leaderboard()),
        ]
        for plan in plans:
            text = '\n'.join(plan)
            # The inflow set is never materialized.
            assert 'MATERIALIZE' not in text, text
            # The inflow anti-join is a correlated index seek on outflow_id.
            assert 'IX_INFLOW_OUTFLOW_ID' in text, text
            # No AUTOMATIC index is ever built over the inflow set. The
            # materialize-then-auto-index regression renders as a single node
            # `SEARCH anon_N USING AUTOMATIC COVERING INDEX (outflow_id=?)`
            # — the materialized inflow set is an anonymous alias, so its row
            # carries `AUTOMATIC` + `OUTFLOW_ID` but NOT the table name
            # `inflow`. Keying off `outflow_id` (not `inflow`) is what makes
            # this catch the regression: the only legitimate AUTOMATIC node
            # post-rewrite is the transaction-membership anon_1 co-routine,
            # keyed (id=?); the correlated inflow seek uses
            # `USING INDEX ix_inflow_outflow_id`, not AUTOMATIC.
            automatic_over_inflow = [
                row
                for row in plan
                if 'AUTOMATIC' in row and 'OUTFLOW_ID' in row
            ]
            assert not automatic_over_inflow, text
