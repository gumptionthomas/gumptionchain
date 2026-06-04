import datetime
from unittest.mock import patch

from _sa_helpers import _count, _count_select

from gumptionchain.block import Block
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import BlockDAO, ChainDAO, LongestChainBlockDAO
from gumptionchain.payload import Inflow, Outflow
from gumptionchain.transaction import CoinbaseMetrics, Transaction


def test_unspent_outflows(app, subject, time_stepper, wallet):
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, wallet, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        cb_1 = block_1.coinbase
        cb_1_amount = next(iter(cb_1.outflows)).amount
        chain_a.to_db()
        dao_a = chain_a.to_dao()

        assert _count(BlockDAO) == 1
        assert _count(LongestChainBlockDAO) == 1
        assert dao_a is not None
        assert _count_select(dao_a.unspent_outflows(wallet.address)) == 1
        balance = chain_a.block_reward()
        assert dao_a.wallet_balance(wallet.address) == balance

        _ = next(time_step)
        t_2a = Transaction()
        t_2a.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
        t_2a.add_outflow(Outflow(amount=cb_1_amount, opposition=subject))
        t_2a.set_wallet(wallet)
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
        chain_a.seal_block(block_2a, wallet, metrics_2a)
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, wallet, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        assert _count(BlockDAO) == 2
        assert _count(LongestChainBlockDAO) == 2
        assert _count_select(dao_a.unspent_outflows(wallet.address)) == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.wallet_balance(wallet.address) == balance
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
        assert _count_select(dao_b.unspent_outflows(wallet.address)) == 2
        balance = 2 * chain_b.block_reward()
        assert dao_b.wallet_balance(wallet.address) == balance
        assert dao_b.opposition_balance(subject) == 0

        assert _count_select(dao_a.unspent_outflows(wallet.address)) == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.wallet_balance(wallet.address) == balance
        assert dao_a.opposition_balance(subject) == cb_1_amount


def test_longest_chain_block_bootstrap(app, mill_block, wallet):
    """Building the first chain populates the materialization table
    with one row per block, ordered position 0 (genesis) → N-1 (tip).
    """
    with app.app_context():
        _m, _b1 = mill_block(wallet)
        _m, b2 = mill_block(wallet)
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


def test_longest_chain_block_single_extend(app, mill_block, wallet):
    """Each subsequent block inserts exactly one new row at the next
    position; prior rows are untouched.
    """
    with app.app_context():
        _m, _b1 = mill_block(wallet)
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

        _m, b2 = mill_block(wallet)

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


def test_longest_chain_block_non_longest_extend_noop(app, time_stepper, wallet):
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
        chain_a.seal_block(block_1, wallet, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, wallet, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, wallet, CoinbaseMetrics())
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
        cte_ids = [
            b.id
            for b in db.session.execute(longest.block.block_chain).scalars()
        ]
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
            longest.blocks.compile(compile_kwargs={'literal_binds': True})
        )
        assert 'RECURSIVE' not in compiled_sql.upper(), (
            f'Expected no recursive CTE in fast-path SQL, got:\n{compiled_sql}'
        )
        assert 'longest_chain_block' in compiled_sql.lower()


def test_non_longest_chain_blocks_uses_cte(app, time_stepper, wallet):
    """A non-longest ChainDAO's .blocks still emits the recursive CTE
    (we did not optimize that path). Verified by emitted SQL.

    Builds a real fork (chain_a + chain_b sharing block_1) so that a
    non-longest ChainDAO row genuinely exists.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, wallet, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, wallet, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, wallet, CoinbaseMetrics())
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

        # Table is back to 3 rows in tip→genesis order matching CTE.
        cte_ids = [
            b.id
            for b in db.session.execute(longest.block.block_chain).scalars()
        ]
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
        assert cte_ids == mat_ids
        assert len(mat_ids) == 3


def test_iterative_walk_matches_cte(app, mill_block, wallet):
    """_rebuild_longest_chain_blocks via current.prev produces the
    same block ordering as the prior recursive-CTE walk would have.
    Uses self.block.block_chain (still defined; used as fallback)
    as ground truth.
    """
    with app.app_context():
        for _ in range(10):
            mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None

        # Capture CTE ground truth before rebuild.
        cte_ids = [
            b.id
            for b in db.session.execute(longest.block.block_chain).scalars()
        ]

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
        assert cte_ids == mat_ids
        assert len(mat_ids) == 10


def test_iterative_walk_long_chain(app, mill_block, wallet):
    """Iterative walk handles a longer chain (50 blocks) and produces
    the right count with no exceptions. Primarily a smoke test that
    the walk terminates and the materialization stays consistent.
    """
    with app.app_context():
        for _ in range(50):
            mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        longest._rebuild_longest_chain_blocks()
        db.session.commit()
        count = _count(LongestChainBlockDAO)
        assert count == 50


def test_is_longest_cache_hit_avoids_query(app, mill_block, wallet):
    """Calling _is_longest twice on the same instance hits the cache
    on the second call and does NOT re-issue ChainDAO.longest().
    """
    with app.app_context():
        mill_block(wallet)
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


def test_is_longest_cache_invalidated_by_bump(app, mill_block, wallet):
    """Calling ChainDAO._bump_generation() after a cached _is_longest
    call forces a recomputation on the next access.
    """
    with app.app_context():
        mill_block(wallet)
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


def test_is_longest_cache_survives_across_method_calls(app, mill_block, wallet):
    """One ChainDAO.longest() call total across a wallet_balance read
    that internally accesses self.outflows AND self.inflows. Without
    caching this would be 2+ calls.
    """
    with app.app_context():
        _m, _b = mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(ChainDAO, 'longest', wraps=ChainDAO.longest) as spy:
            # wallet_balance reads self.outflows and self.inflows;
            # each property accessor calls _is_longest.
            longest.wallet_balance(wallet.address)
            assert spy.call_count == 1, (
                f'expected one ChainDAO.longest() call across the '
                f'wallet_balance method (cached after the first '
                f'property access), got {spy.call_count}'
            )


def test_smart_reorg_shallow(app, mill_block, wallet):
    """A steady-state +1 block via smart-reorg preserves earlier
    positions (common ancestor at position max-1, only the new tip
    is inserted)."""
    with app.app_context():
        _m, _a1 = mill_block(wallet)
        _m, _a2 = mill_block(wallet)

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
        _m, _a3 = mill_block(wallet)

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


def test_smart_reorg_walks_only_to_common_ancestor(app, mill_block, wallet):
    """The walk stops at the first block found in the materialization
    instead of falling through to the rebuild path. Verified by
    patching ChainDAO._rebuild_longest_chain_blocks and asserting it
    is NOT invoked during a steady-state extend (the smart-reorg
    common-ancestor branch must handle the case).
    """
    with app.app_context():
        for _ in range(5):
            mill_block(wallet)

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
            _m, _new_tip = mill_block(wallet)

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


def test_smart_reorg_already_in_sync_short_circuits(app, mill_block, wallet):
    """Calling sync_longest_chain_blocks twice on the same chain
    instance: the second call finds the tip already in the table on
    its first walk iteration and returns without mutation or
    generation bump.
    """
    with app.app_context():
        mill_block(wallet)
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
    app, mill_block, time_stepper, wallet
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
            mill_block(wallet)
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
        chain_b.seal_block(block_b1, wallet, CoinbaseMetrics())
        block_b1.mill()
        block_b1.to_db()
        chain_b.block_hash = block_b1.block_hash
        _ = next(time_step)
        block_b2 = Block()
        chain_b.link_block(block_b2)
        chain_b.seal_block(block_b2, wallet, CoinbaseMetrics())
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
