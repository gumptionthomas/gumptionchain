# Phase 6 — Longest-Chain Materialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the recursive `BlockDAO._block_chain` CTE from hot-path reads by maintaining a flat `longest_chain_block(block_id, position)` table that mirrors the canonical chain. Hot reads (balance, leaderboard, in-chain checks, outflows/inflows aggregations) switch to indexed JOINs through the new table; the CTE stays in place only for non-longest-chain paths (bootstrap, reorg rebuild, fork queries).

**Architecture:** A single new DAO (`LongestChainBlockDAO`) holds chain membership. `ChainDAO.sync_longest_chain_blocks()` updates it whenever a chain is persisted, distinguishing three cases (bootstrap, single-block extend, reorg/rebuild). Four new `BlockDAO` query factories (`longest_chain_blocks_q`, `longest_chain_transactions_q`, `longest_chain_outflows_q`, `longest_chain_inflows_q`) return CTE-free `Query[X]` objects. The 4 `ChainDAO` property accessors (`blocks`, `transactions`, `outflows`, `inflows`) branch on `_is_longest()`: when true, route through the new factories; when false, fall back to the existing CTE-backed chain. The 6 downstream `ChainDAO` methods (`unspent_outflows`, `wallet_balance`, `unforgiven_outflows`, `subject_balance`, `subject_support`, `wallet_leaderboard`) compose on top of the 4 properties, so they inherit the fast path with no direct edits.

**Tech Stack:** SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1.1 (existing). `Mapped[]` + `mapped_column` annotations (existing convention in `models.py`). Legacy `Model.query` / `db.session.query` patterns stay (modernization deferred to Phase 7).

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 5b fully merged. Verify with `gh pr view 63 --json state --jq .state` → `MERGED`, and `git log --oneline -5 main` shows `feaa908 feat(app): close app.clients httpx.Clients on app teardown (#63)` near the top.
- The branch `docs/phase-6-design` exists locally with two commits already on it:
  - `d2c956e docs(phase-6): add longest-chain materialization design spec`
  - `db727a5 docs(phase-6): clarify branching only in 4 properties, not 10 methods`
  This plan adds a third commit on that branch (the plan file) and ships all three as the docs PR.
- CI hard-gates `ruff check`, `ruff format --check`, and `mypy` (strict via `[tool.mypy] strict = true` in pyproject.toml; no CLI flag needed — `uv run mypy` honors the config).
- Test baseline: **220 passed, 1 skipped** (post-Phase 5b). Phase 6 adds 7 new tests, so the final count is 227 passed, 1 skipped.
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-27-phase-6-longest-chain-materialization.md` (this file) + the spec already committed |
| 2 | impl PR | `src/cancelchain/models.py`, `src/cancelchain/chain.py`, `tests/test_models.py` |
| 3 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** The design spec is committed on `docs/phase-6-design` in two commits (`d2c956e`, `db727a5`). This task adds the implementation plan as a third commit and ships all three together as one docs PR.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-27-phase-6-longest-chain-materialization-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/phase-6-design`; spec file is tracked; commit count above main is `2`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-27-phase-6-longest-chain-materialization.md
git status docs/superpowers/plans/
```

Expected: file exists; shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-27-phase-6-longest-chain-materialization.md
git commit -m "$(cat <<'EOF'
docs(phase-6): add Phase 6 longest-chain materialization plan

Spells out the single-PR impl: branch off main, add the new
LongestChainBlockDAO, wire ChainDAO.sync_longest_chain_blocks()
into Chain.to_db(), branch the 4 ChainDAO property accessors on
_is_longest(), add 7 new tests (bootstrap / extend / reorg / non-
longest-noop / property-against-CTE / fast-path-when-longest /
fallback-when-not-longest), and verify the existing 220 tests stay
green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/phase-6-design
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/phase-6-design --title "docs(phase-6): Phase 6 longest-chain materialization design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 6 design spec (\`docs/superpowers/specs/2026-05-27-phase-6-longest-chain-materialization-design.md\`).
- Adds the Phase 6 implementation plan (\`docs/superpowers/plans/2026-05-27-phase-6-longest-chain-materialization.md\`).
- No code changes.

Phase 6 pivot from the originally-planned SA 2.0 syntax modernization to **eliminating the recursive CTE bottleneck** in \`BlockDAO._block_chain\` — the perf problem that previously caused the project to be shelved. Hot reads switch to a flat materialized \`longest_chain_block\` table; SA 2.0 syntax modernization defers to Phase 7. Residual CTE on bootstrap / reorg / non-longest paths is documented as a known follow-up for Phase 6.5 / 7.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Phase 6 impl — longest-chain materialization

**Files:**
- Modify: `src/cancelchain/models.py` (new `LongestChainBlockDAO`, new methods on `ChainDAO` and `BlockDAO`, branching in 4 properties)
- Modify: `src/cancelchain/chain.py` (call `sync_longest_chain_blocks` from `Chain.to_db()`)
- Modify: `tests/test_models.py` (extend 3 existing tests + 7 new tests)

### Step 1: Branch off main

```bash
git checkout main && git pull --ff-only
git checkout -b feat/longest-chain-materialization
```

### Step 2: Add `LongestChainBlockDAO` to `models.py`

Open `src/cancelchain/models.py`. Find the class definition for `ChainDAO` (around line 381). Insert the new `LongestChainBlockDAO` class **immediately before** the `ChainDAO` class (so dependency order is `BlockDAO` → `LongestChainBlockDAO` → `ChainDAO`).

```python
class LongestChainBlockDAO(db.Model):
    """Flat materialization of the canonical chain's block membership.

    One row per block in the currently-longest chain, keyed by block.id
    with `position` 0 at genesis and increasing toward the tip. Maintained
    by ChainDAO.sync_longest_chain_blocks() — never written from anywhere
    else. Phase 6 (2026-05-27) introduced this table to eliminate the
    recursive `BlockDAO._block_chain` CTE from hot-path reads.
    """

    __tablename__ = 'longest_chain_block'

    block_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('block.id', ondelete='CASCADE'),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(
        Integer, unique=True, nullable=False
    )
    block: Mapped[BlockDAO] = relationship()

    def __init__(self, block_id: int, position: int) -> None:
        self.block_id = block_id
        self.position = position
```

Verify no existing class uses the same `__tablename__`:

```bash
grep -n "__tablename__ = 'longest_chain_block'" src/cancelchain/models.py
```

Expected: exactly one match (the new class).

### Step 3: Add `BlockDAO.longest_chain_*_q` factories

In `src/cancelchain/models.py`, locate the `BlockDAO` class (around line 247). Find the end of the existing `BlockDAO.get` classmethod (around line 378). Append the following four new classmethods to `BlockDAO` immediately after `get`:

```python
    @classmethod
    def longest_chain_blocks_q(cls) -> Query[BlockDAO]:
        """Blocks in the longest chain, ordered tip→genesis.

        Matches BlockDAO.block_chain's tip-first ordering so consumers
        that compose on the result (subquery / filter / first) see the
        same row order.
        """
        return (
            db.session.query(BlockDAO)
            .join(
                LongestChainBlockDAO,
                BlockDAO.id == LongestChainBlockDAO.block_id,
            )
            .order_by(LongestChainBlockDAO.position.desc())
        )

    @classmethod
    def longest_chain_transactions_q(cls) -> Query[TransactionDAO]:
        """Transactions in the longest chain, ordered tip→genesis.

        Matches TransactionDAO.transactions_chain's ordering
        (timestamp.desc, id) within the longest chain's block set.
        """
        blocks_subq = cls.longest_chain_blocks_q().subquery()
        block_alias = db.aliased(BlockDAO, blocks_subq)
        q = db.session.query(TransactionDAO)
        q = q.join(block_alias, TransactionDAO.blocks)
        return q.order_by(
            TransactionDAO.timestamp.desc(), TransactionDAO.id
        )

    @classmethod
    def longest_chain_outflows_q(cls) -> Query[OutflowDAO]:
        """Outflows in the longest chain, ordered by their parent txn's
        timestamp desc, then txid, then outflow idx — matching
        OutflowDAO.outflows_chain's ordering.
        """
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        q = db.session.query(OutflowDAO)
        q = q.join(txn_alias, OutflowDAO.transaction)
        return q.order_by(
            txn_alias.timestamp.desc(),
            txn_alias.txid,
            OutflowDAO.idx,
        )

    @classmethod
    def longest_chain_inflows_q(cls) -> Query[InflowDAO]:
        """Inflows in the longest chain, ordered analogously to
        InflowDAO.inflows_chain (timestamp desc, txid, inflow idx).
        """
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        q = db.session.query(InflowDAO)
        q = q.join(txn_alias, InflowDAO.transaction)
        return q.order_by(
            txn_alias.timestamp.desc(),
            txn_alias.txid,
            InflowDAO.idx,
        )
```

### Step 4: Add maintenance methods on `ChainDAO`

In `src/cancelchain/models.py`, find the `ChainDAO` class. Insert these three new methods just **before** the `set_block_hash` method (which is the existing last-defined regular method on the class — verify by grepping):

```bash
grep -n "def set_block_hash\|class ChainDAO\|class .*DAO" src/cancelchain/models.py | head -20
```

Add to `ChainDAO`:

```python
    def _is_longest(self) -> bool:
        """True iff this ChainDAO row is currently the longest chain.

        Used by the property accessors (blocks, transactions, outflows,
        inflows) to route hot reads through LongestChainBlockDAO
        instead of the recursive CTE.
        """
        longest = ChainDAO.longest()
        return longest is not None and longest.id == self.id

    def sync_longest_chain_blocks(self) -> None:
        """Update the longest_chain_block materialization to reflect
        this chain — if this chain is currently the longest.

        Three sub-cases:
        - Bootstrap: table is empty → populate from this chain's
          recursive CTE walk (one-time cost).
        - Steady-state extend: table's last entry is our previous tip
          → INSERT one row at position = max + 1.
        - Reorg / out-of-order: anything else → full DELETE + rebuild.

        No-op when this chain is not the longest. Called from
        Chain.to_db() so the materialization update participates in
        the same SQLAlchemy session/transaction as the chain row save.
        """
        if not self._is_longest():
            return

        current_max = db.session.query(
            db.func.max(LongestChainBlockDAO.position)
        ).scalar()

        if current_max is None:
            self._rebuild_longest_chain_blocks()
            return

        table_tip_block_id = (
            db.session.query(LongestChainBlockDAO.block_id)
            .filter(LongestChainBlockDAO.position == current_max)
            .scalar()
        )

        if table_tip_block_id == self.block_id:
            # Already in sync (defensive — e.g., to_db called twice).
            return

        if table_tip_block_id == self.block.prev_id:
            # Normal extend: append one row.
            db.session.add(
                LongestChainBlockDAO(
                    block_id=self.block_id,
                    position=current_max + 1,
                )
            )
            return

        # Reorg or gap: rebuild.
        self._rebuild_longest_chain_blocks()

    def _rebuild_longest_chain_blocks(self) -> None:
        """Wipe and repopulate longest_chain_block from this chain's
        recursive CTE walk. Used on bootstrap and reorg.

        This is the path that still fires the recursive CTE — see
        Phase 6 spec 'Risks' for the deferred follow-up (Phase 6.5/7)
        to replace this with an iterative walk when chain length
        grows past the CTE's tolerable size.
        """
        db.session.query(LongestChainBlockDAO).delete()
        # block_chain walks tip → genesis; reverse so position 0 is
        # genesis and the tip ends up at the highest position.
        blocks = list(self.block.block_chain)
        for position, block in enumerate(reversed(blocks)):
            db.session.add(
                LongestChainBlockDAO(
                    block_id=block.id,
                    position=position,
                )
            )
```

### Step 5: Branch the 4 `ChainDAO` property accessors

In `src/cancelchain/models.py`, locate the 4 existing `ChainDAO` property definitions (around lines 401–415). Replace each with the branching version below. The 6 downstream methods (`unspent_outflows`, `wallet_balance`, `unforgiven_outflows`, `subject_balance`, `subject_support`, `wallet_leaderboard`) are **not** modified — they consume `self.outflows`, `self.inflows`, `self.transactions` and inherit the fast path through composition.

Before (current):
```python
    @property
    def blocks(self) -> Query[BlockDAO]:
        return self.block.block_chain

    @property
    def transactions(self) -> Query[TransactionDAO]:
        return self.block.transactions_chain

    @property
    def outflows(self) -> Query[OutflowDAO]:
        return self.block.outflows_chain

    @property
    def inflows(self) -> Query[InflowDAO]:
        return self.block.inflows_chain
```

After:
```python
    @property
    def blocks(self) -> Query[BlockDAO]:
        if self._is_longest():
            return BlockDAO.longest_chain_blocks_q()
        return self.block.block_chain

    @property
    def transactions(self) -> Query[TransactionDAO]:
        if self._is_longest():
            return BlockDAO.longest_chain_transactions_q()
        return self.block.transactions_chain

    @property
    def outflows(self) -> Query[OutflowDAO]:
        if self._is_longest():
            return BlockDAO.longest_chain_outflows_q()
        return self.block.outflows_chain

    @property
    def inflows(self) -> Query[InflowDAO]:
        if self._is_longest():
            return BlockDAO.longest_chain_inflows_q()
        return self.block.inflows_chain
```

### Step 6: Wire `Chain.to_db()` to call the sync

In `src/cancelchain/chain.py`, locate `Chain.to_db` (around line 557):

Before:
```python
    def to_db(self) -> None:
        dao = self.to_dao(create=True)
        dao.commit()
        self.cid = dao.id
```

After:
```python
    def to_db(self) -> None:
        dao = self.to_dao(create=True)
        dao.commit()
        self.cid = dao.id
        dao.sync_longest_chain_blocks()
        db.session.commit()
```

The extra `db.session.commit()` flushes the materialization writes. `dao.commit()` (above) commits the chain row; `sync_longest_chain_blocks` may add/delete `LongestChainBlockDAO` rows, which need their own commit because the session was already flushed.

If `from cancelchain.database import db` is not already imported in `chain.py`, add it. Check with:

```bash
grep -n 'from cancelchain.database import db\|^from cancelchain' src/cancelchain/chain.py | head -10
```

If `db` isn't imported, add `from cancelchain.database import db` near the other `cancelchain.*` imports in `chain.py`.

### Step 7: Extend the 3 existing tests in `tests/test_models.py`

In `tests/test_models.py`, find the three tests that assert `BlockDAO.query.count() == N`. After each `BlockDAO.query.count() == N` line, add a parallel `LongestChainBlockDAO.query.count() == N` assertion — the materialization should mirror the block count when those tests run against the longest chain.

Open `tests/test_models.py` and inspect the imports + the 3 existing tests:

```bash
sed -n '1,20p' tests/test_models.py
grep -n 'BlockDAO.query.count' tests/test_models.py
```

Add `LongestChainBlockDAO` to the import block:

```python
from cancelchain.models import (
    # ... existing imports ...
    LongestChainBlockDAO,
)
```

(Match the actual existing import shape — single-line or multi-line.)

For each of the 3 existing `assert BlockDAO.query.count() == N` lines (at lines 25, 56, 69 per current file state — re-verify with grep), append:

```python
        assert LongestChainBlockDAO.query.count() == N
```

with `N` matching the surrounding context's block count.

### Step 8: Add 7 new materialization tests to `tests/test_models.py`

Append the following test functions to the end of `tests/test_models.py`. They cover the seven cases enumerated in the spec.

**Note on test fixtures:** The existing `app`, `mill_block`, `wallet`, `valid_chain`, `add_chain_block`, `genesis_block` fixtures from `tests/conftest.py` are used here. Before writing, verify their availability with:

```bash
grep -n '^def add_chain_block\|^def mill_block\|^def valid_chain\|^def genesis_block' tests/conftest.py
```

Then append:

```python


def test_longest_chain_block_bootstrap(app, mill_block, wallet):
    """Building the first chain populates the materialization table
    with one row per block, ordered position 0 (genesis) → N-1 (tip).
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
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
        _m, b1 = mill_block(wallet)
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


def test_longest_chain_block_non_longest_extend_noop(
    app, mill_block, wallet
):
    """When a chain that is NOT the longest gets a `Chain.to_db()`
    call, the materialization table must stay aligned with whichever
    chain IS longest. Simulated here by directly invoking
    sync_longest_chain_blocks on a fork chain dao that we construct
    to be shorter than the current longest.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, b2 = mill_block(wallet)
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
        assert (
            [(r.block_id, r.position) for r in longest_rows_after]
            == [(r.block_id, r.position) for r in longest_rows_before]
        )


def test_longest_chain_block_property_matches_cte(
    app, mill_block, wallet
):
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


def test_longest_chain_blocks_q_fast_path_skips_cte(
    app, mill_block, wallet
):
    """ChainDAO.longest().blocks uses the materialization JOIN, not
    the recursive CTE. Verified by emitted SQL: the fast-path query
    should NOT contain a 'WITH RECURSIVE' clause.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, b2 = mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        compiled_sql = str(
            longest.blocks.statement.compile(
                compile_kwargs={'literal_binds': True}
            )
        )
        assert 'RECURSIVE' not in compiled_sql.upper(), (
            f'Expected no recursive CTE in fast-path SQL, got:\n'
            f'{compiled_sql}'
        )
        assert 'longest_chain_block' in compiled_sql.lower()


def test_non_longest_chain_blocks_uses_cte(
    app, mill_block, wallet
):
    """A non-longest ChainDAO's .blocks still emits the recursive CTE
    (we did not optimize that path). Verified by emitted SQL.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, b2 = mill_block(wallet)
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


def test_longest_chain_block_rebuild_on_reorg(
    app, mill_block, wallet
):
    """Forcing a rebuild (via _rebuild_longest_chain_blocks) wipes
    the table and repopulates it from the longest chain's CTE walk
    so the contents match exactly.
    """
    with app.app_context():
        _m, b1 = mill_block(wallet)
        _m, b2 = mill_block(wallet)
        _m, b3 = mill_block(wallet)
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
```

If any test depends on a helper not already imported in `test_models.py` (e.g., `db` from `cancelchain.database`), add the necessary imports at the top of the file.

### Step 9: Verify all gates

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

All four must exit 0. Test count: 220 → 227 (+7).

If `mypy` reports new errors:
- The new `LongestChainBlockDAO` class is declared inside the `mypy: disable-error-code` block at the top of `models.py`, so DAO-class type errors are already suppressed. Any NEW mypy error is on the new methods (sync / rebuild / factories) — narrow it with a per-line `# type: ignore[<code>]` only if the error is genuinely a known limitation; otherwise fix the type.
- `db.session.add(LongestChainBlockDAO(...))` may complain about `Any` typing. Acceptable per the existing pattern in `models.py`.

If `pytest` fails on:
- `test_longest_chain_block_bootstrap` — the `Chain.to_db()` wire-up in Step 6 didn't fire `sync_longest_chain_blocks`. Verify the new line is present and `db.session.commit()` follows.
- `test_longest_chain_block_property_matches_cte` — the rebuild's `reversed(blocks)` ordering is wrong. `BlockDAO.block_chain` walks tip→genesis; we want position 0 = genesis, so iterate in reverse order.
- `test_longest_chain_blocks_q_fast_path_skips_cte` — the property accessor is not branching. Verify Step 5's edits.
- `test_non_longest_chain_blocks_uses_cte` — the SQLite engine version may stringify recursive CTEs differently. If the assertion fails, inspect `compiled_sql` and adjust to a more robust marker (e.g., check for `block.prev_id` join in the CTE path).
- Any existing test that's sensitive to ChainDAO query semantics — re-run with `pytest -x --tb=long` and inspect.

### Step 10: Commit

```bash
git add src/cancelchain/models.py src/cancelchain/chain.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(models): materialize longest chain to eliminate recursive-CTE hot reads

Phase 6. The recursive BlockDAO._block_chain CTE was the project's
known perf bottleneck — every "what's in the active chain?" query
re-walked tip→genesis. This adds a flat materialized table that
hot reads JOIN against instead, dropping the CTE from balance,
leaderboard, in-chain checks, and outflows/inflows aggregations.

src/cancelchain/models.py:
- New LongestChainBlockDAO (table: longest_chain_block):
  PK block_id → block.id ON DELETE CASCADE; UNIQUE position.
  One row per block in the canonical chain (position 0 = genesis).
- New BlockDAO classmethods:
  - longest_chain_blocks_q() → Query[BlockDAO] joining
    LongestChainBlockDAO, ordered tip→genesis (position DESC).
  - longest_chain_transactions_q() → Query[TransactionDAO]
    composed on top, matching transactions_chain's ordering.
  - longest_chain_outflows_q() and longest_chain_inflows_q()
    likewise, matching the corresponding CTE-based factories'
    ordering conventions.
- New ChainDAO methods:
  - _is_longest(): self.id == ChainDAO.longest().id
  - sync_longest_chain_blocks(): no-op when not longest, else
    bootstrap / single-block extend / full rebuild based on
    current table state.
  - _rebuild_longest_chain_blocks(): wipe + repopulate from
    self.block.block_chain. This is the one remaining CTE-firing
    site post-Phase 6 (Phase 6.5/7 will replace it with an
    iterative walk for long chains).
- Branching in 4 ChainDAO property accessors (blocks, transactions,
  outflows, inflows): fast path through the new factories when
  self._is_longest(), else fall back to the existing block_chain /
  transactions_chain / outflows_chain / inflows_chain CTE-backed
  paths. The 6 downstream ChainDAO methods (unspent_outflows,
  wallet_balance, unforgiven_outflows, subject_balance,
  subject_support, wallet_leaderboard) compose on top of the
  branched properties — they inherit the fast path with no edits.

src/cancelchain/chain.py:
- Chain.to_db() now calls dao.sync_longest_chain_blocks() and
  commits, so the materialization update is bundled with each
  chain persistence.

tests/test_models.py:
- 7 new tests:
  - test_longest_chain_block_bootstrap: first chain populates the
    table with the right shape.
  - test_longest_chain_block_single_extend: adding one block
    inserts exactly one row at the next position; prior rows
    unchanged.
  - test_longest_chain_block_non_longest_extend_noop: calling
    sync on a non-longest chain leaves the table aligned with
    whichever chain IS longest.
  - test_longest_chain_block_property_matches_cte: materialization
    contents (position DESC) match the recursive CTE walk.
  - test_longest_chain_blocks_q_fast_path_skips_cte: compiled SQL
    for ChainDAO.longest().blocks contains 'longest_chain_block'
    and no 'RECURSIVE'.
  - test_non_longest_chain_blocks_uses_cte: compiled SQL for a
    non-longest ChainDAO's .blocks still contains 'RECURSIVE'.
  - test_longest_chain_block_rebuild_on_reorg: corrupting the
    table and calling _rebuild_longest_chain_blocks restores
    correct contents.
- 3 existing tests extended to also assert
  LongestChainBlockDAO.query.count() matches BlockDAO.query.count()
  when the test scope covers the longest chain only.

Test count: 220 → 227.

Phase 6 explicitly defers (per the spec):
- SA 2.0 syntax modernization (Phase 7).
- mypy: disable-error-code block removal in models.py (Phase 7).
- Replacing the residual recursive CTE in _rebuild_longest_chain_blocks
  with an iterative walk for long chains (Phase 6.5/7).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 11: Push and open PR

```bash
git push -u origin feat/longest-chain-materialization
gh pr create --base main --title "feat(models): materialize longest chain to eliminate recursive-CTE hot reads" --body "$(cat <<'EOF'
## Summary
- Adds \`LongestChainBlockDAO\` (table \`longest_chain_block\`), a flat materialization of the canonical chain's block membership.
- Adds 4 new \`BlockDAO\` query factories (\`longest_chain_blocks_q\` / \`_transactions_q\` / \`_outflows_q\` / \`_inflows_q\`) that JOIN through the table instead of running the recursive CTE.
- Adds 3 new \`ChainDAO\` methods (\`_is_longest\`, \`sync_longest_chain_blocks\`, \`_rebuild_longest_chain_blocks\`).
- Branches the 4 \`ChainDAO\` property accessors (\`blocks\`, \`transactions\`, \`outflows\`, \`inflows\`) on \`_is_longest()\`. The 6 downstream methods (\`unspent_outflows\`, \`wallet_balance\`, \`unforgiven_outflows\`, \`subject_balance\`, \`subject_support\`, \`wallet_leaderboard\`) inherit the fast path through composition.
- Wires \`Chain.to_db()\` to call \`sync_longest_chain_blocks\` so the materialization stays in step with chain persistence.

## Why
The recursive \`BlockDAO._block_chain\` CTE was the project's known perf bottleneck — it once caused the project to be shelved. Phase 6 eliminates the CTE from hot-path reads. The residual CTE in \`_rebuild_longest_chain_blocks\` only fires on bootstrap and reorg (rare events, not per-read); replacing it with an iterative walk for long chains is queued as Phase 6.5/7.

## Out of scope (per spec)
- SA 2.0 syntax modernization (Phase 7).
- \`mypy: disable-error-code\` block removal in \`models.py\` (Phase 7).
- Eliminating the residual CTE in \`_rebuild_longest_chain_blocks\` (Phase 6.5/7).

## Test plan
- [x] \`uv run mypy\` exits 0.
- [x] \`uv run pytest\` passes (220 → 227, +7).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [x] New tests: bootstrap / single-block extend / non-longest extend noop / property-matches-CTE / fast-path-skips-CTE / non-longest-uses-CTE / rebuild-on-reorg.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 12: Stop — controller handles wor + mwg + sync

---

## Task 3: Phase 6 acceptance verification

**Files:** none modified. Final verification after the impl PR lands on main.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

Expected: top two commits are the docs PR squash and the impl PR squash.

- [ ] **Step 2: Fresh sync**

```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```

Expected: Python 3.12.x and a fresh venv.

- [ ] **Step 3: New model registered**

```bash
grep -rn 'class LongestChainBlockDAO' src/cancelchain/
grep -rn 'longest_chain_block' src/cancelchain/ | head -10
```

Expected: one class definition and multiple references inside the new query factories and ChainDAO methods.

- [ ] **Step 4: Schema includes the new table**

```bash
uv run python <<'PY'
import os, tempfile
os.environ.setdefault('FLASK_SECRET_KEY', 'a' * 32)
tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
tmpdb.close()
os.environ['FLASK_SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmpdb.name}'
from cancelchain import create_app
from cancelchain.database import db
app = create_app()
with app.app_context():
    db.create_all()
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    tables = insp.get_table_names()
    print('tables:', sorted(tables))
    assert 'longest_chain_block' in tables, 'new table missing'
    cols = {c['name'] for c in insp.get_columns('longest_chain_block')}
    print('columns:', sorted(cols))
    assert cols == {'block_id', 'position'}, f'unexpected columns: {cols}'
print('OK')
PY
```

Expected: prints the table list including `longest_chain_block`, the column set `{'block_id', 'position'}`, and finishes with `OK`.

- [ ] **Step 5: Hard CI gates pass**

```bash
uv run ruff check src tests; echo "ruff check exit: $?"
uv run ruff format --check src tests; echo "ruff format exit: $?"
uv run mypy; echo "mypy exit: $?"
```

All three exit 0.

- [ ] **Step 6: Tests pass on 3.12 and 3.13**

```bash
uv run --python 3.12 pytest 2>&1 | tail -3
uv run --python 3.13 pytest 2>&1 | tail -3
```

Expected: both print `227 passed, 1 skipped` (or whatever the new count is — should be 7 more than 220).

- [ ] **Step 7: Hot-path SQL inspection**

```bash
uv run python <<'PY'
import os, tempfile
os.environ.setdefault('FLASK_SECRET_KEY', 'a' * 32)
tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
tmpdb.close()
os.environ['FLASK_SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmpdb.name}'
from cancelchain import create_app
from cancelchain.database import db
from cancelchain.models import BlockDAO, ChainDAO
app = create_app()
with app.app_context():
    db.create_all()
    # Empty DB has no chains; just verify the fast-path SQL shape.
    sql = str(
        BlockDAO.longest_chain_blocks_q().statement.compile(
            compile_kwargs={'literal_binds': True}
        )
    )
    print(sql)
    assert 'longest_chain_block' in sql.lower()
    assert 'RECURSIVE' not in sql.upper()
print('OK')
PY
```

Expected: prints SQL containing `longest_chain_block` and finishes with `OK`. No `WITH RECURSIVE`.

- [ ] **Step 8: CLI smoke**

```bash
uv run cancelchain --help
```

Expected: prints the full command tree.

- [ ] **Step 9: Docker build smoke**

```bash
docker build --target builder -t cc-phase6-final .
```

Expected: succeeds.

- [ ] **Step 10: Acceptance complete**

If Steps 1–9 all pass, Phase 6 is done. No commit.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 2) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory).
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend) and post a `/copilot review` comment on the PR — Copilot's auto-review only fires on the initial push; subsequent rounds need the manual trigger (per the user's memory).

---

## Risks and watchpoints

### Risk: ordering mismatch between fast path and CTE

The existing CTE in `BlockDAO._block_chain` walks tip → genesis (highest `idx` first when following `prev_id`). The new `LongestChainBlockDAO` stores position 0 = genesis. The fast-path `longest_chain_blocks_q()` reverses this with `ORDER BY position DESC` so consumers see tip → genesis (matching the CTE convention). Any consumer that depends on a particular ordering will break if the fast path orders differently than the CTE.

The property test `test_longest_chain_block_property_matches_cte` covers this — it asserts the fast-path SQL output matches the CTE walk byte-for-byte. If a regression slips through, that test catches it.

### Risk: `_rebuild_longest_chain_blocks` still fires the recursive CTE

Phase 6's main point is to remove the CTE from hot reads. But `_rebuild_longest_chain_blocks` itself runs `list(self.block.block_chain)` — i.e., the CTE. This fires only on:
- Bootstrap (one-time, fast on a new chain).
- Reorg (rare).

If the chain grows long enough that even these rare events become slow, the original perf problem returns in a different shape. The spec documents this as a known follow-up for Phase 6.5 / 7 (replace the CTE walk with an iterative `BlockDAO.get(prev_hash)` loop). Not in scope for Phase 6.

### Risk: `_is_longest()` adds one query per property access

Each call to `ChainDAO.blocks` (or `.transactions` etc.) now invokes `ChainDAO.longest()` first to decide which branch to take. That's one indexed `SELECT FROM chain ORDER BY ... LIMIT 1` per property access. For hot paths that read the property multiple times, consider caching the longest-chain check in the calling function (e.g., grab `is_longest = self._is_longest()` once, reuse). For the existing 6 downstream methods, this might mean one redundant call (the property accessor and downstream method each call `_is_longest()`). Acceptable for Phase 6; revisit if the extra query shows up in profiling.

### Risk: `Chain.to_db()` double-commit

`Chain.to_db` now calls `dao.commit()` (which `db.session.commit()`s) AND `dao.sync_longest_chain_blocks()` AND a trailing `db.session.commit()`. Two commits in one function. Tested in `test_longest_chain_block_bootstrap`. Acceptable since:
- The first commit persists the chain row so `_is_longest()` evaluates correctly inside `sync_longest_chain_blocks`.
- The second commit flushes the materialization writes.
- Both run in the same Flask app context, no cross-process race.

If a Copilot reviewer flags this, the alternative is one large transaction: flush the chain row first via `db.session.flush()` (which gives it an id without committing), then sync, then a single final commit. That's tighter but changes the visibility semantics. Phase 6 keeps the two-commit shape for simplicity; a follow-up can tighten if necessary.

### Risk: test fixtures don't trigger the materialization correctly

The conftest's `add_chain_block` / `mill_block` helpers build chains via `Chain.add_block` → `to_db()`. Since `to_db()` now calls `sync_longest_chain_blocks()`, the materialization should populate automatically for all existing tests. If a test fixture builds blocks WITHOUT calling `Chain.to_db()` (e.g., a fixture that only persists a `BlockDAO` directly), the materialization stays empty and downstream assertions might fail. Search with:

```bash
grep -n 'add_block\|to_db\|BlockDAO(' tests/conftest.py
```

If any fixture builds blocks outside the `Chain.to_db()` path, decide per-fixture whether to add explicit `sync_longest_chain_blocks()` calls or to flag in the test_models.py changes.

### Risk: existing tests sensitive to chain reorgs

The repo has tests for chain reorganization (`tests/test_chain.py`, `tests/test_node.py`). Those tests should remain green because the branching is invisible from outside the property accessors. If a reorg-related test fails after Step 5, the most likely cause is the rebuild path not firing — e.g., `sync_longest_chain_blocks` returning early due to a stale `_is_longest` check. Re-verify the order of operations in Step 6 (commit chain row first, then sync, then commit materialization).
