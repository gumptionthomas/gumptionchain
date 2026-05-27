# Phase 6 — Longest-Chain Materialization

**Status:** Draft for review
**Date:** 2026-05-27
**Scope:** Eliminate the recursive `BlockDAO._block_chain` CTE from hot-path reads by maintaining a flat `longest_chain_block(block_id, position)` table that mirrors the canonical chain. Reads that today re-run the CTE on every call (balance, leaderboard, in-chain checks, outflows/inflows aggregations) switch to indexed JOINs through the new table. The CTE stays in place for the rare non-longest-chain paths (bootstrap, reorg rebuild, queries against forks).

## Goal

The recursive CTE in `BlockDAO._block_chain` is the project's known performance bottleneck — it once made the app unworkable as the chain grew. Every "what's in the active chain?" query — `block_chain`, `transactions_chain`, `outflows_chain`, `inflows_chain`, plus all balance / leaderboard / unspent-outflow / unforgiven-outflow / subject-support queries built on top — re-walks the chain from tip back to genesis. Phase 6 makes these hot reads O(1) per query via a materialized membership table updated only on chain mutations.

**Greenfield posture.** No deployed instance to migrate. Schema additions are safe (`db.create_all()` picks up the new model on next `cancelchain init`). No on-wire compat constraint.

## Non-goals

- **No SA 2.0 syntax modernization.** Legacy `Model.query` / `db.session.query` patterns stay. Phase 7 will translate them.
- **No removal of the `mypy: disable-error-code` block** at the top of `models.py`. Phase 7.
- **No DeclarativeBase migration.** Phase 7.
- **No elimination of the recursive CTE from non-longest paths.** Bootstrap (one-time, expected small under greenfield), reorg rebuild (long-chain deep-reorg edge case), and non-longest chain API queries all keep using the existing CTE. Long-chain reorg is a known residual perf concern — Phase 6.5 / 7+ may address it via iterative Python-side walks or batched-CTE if/when the chain grows past the size where the CTE was previously problematic. See [Risks](#risks).
- **No generalization to all chains.** The materialized table tracks only the currently-longest chain. Other `ChainDAO` rows (forks, historical longest-at-the-time) keep using the CTE on the rare occasions they're queried.
- **No async chain maintenance.** All materialization updates happen synchronously inside the existing SQLAlchemy session/transaction.
- **No change to consensus rules.** Same block validation, same hashing, same fork-detection semantics.

## Decisions taken during brainstorming

- **Cache scope: longest chain only.** Optimizing the hot path is the highest-leverage move. Non-longest chains are touched mainly during reorg detection and peer-fill flows, which use other code paths (iterative walks via `BlockDAO.get(prev_hash)`) that don't depend on the CTE.
- **Storage shape: separate `longest_chain_block` table.** Flat `(block_id PK, position UNIQUE)`. Joins are indexed, "rebuild on reorg" is an atomic `DELETE` + bulk `INSERT`, and chain-membership state stays out of `BlockDAO` (no mixing of block data with chain state).
- **Branching shape: each `ChainDAO` method checks "am I the longest?"** No subclassing, no pass-by-construction tagging. The predicate is `self.block_hash == ChainDAO.longest().block_hash` — one extra cheap lookup per method invocation, dwarfed by the CTE elimination.
- **Maintenance entry point:** `ChainDAO.sync_longest_chain_blocks()` (new method), called from `Chain.to_db()` after the DAO is persisted. Handles three cases (steady-state extend, reorg rebuild, no-op when this chain isn't the longest) via a single decision tree.
- **Residual CTE is acceptable for Phase 6.** Bootstrap and reorg rebuilds run the CTE once per event, not per read. Long-chain reorg perf is documented as a known concern; Phase 6.5/7 may add iterative-walk fallbacks.

## Architecture

### New model: `LongestChainBlockDAO`

```python
class LongestChainBlockDAO(db.Model):
    __tablename__ = 'longest_chain_block'

    block_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('block.id', ondelete='CASCADE'),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(
        Integer, unique=True, nullable=False,
    )
    block: Mapped[BlockDAO] = relationship()
```

Empty when no chain exists. One row per block in the current canonical chain. `position = 0` is genesis; `position = N` is the tip.

### Maintenance: `ChainDAO.sync_longest_chain_blocks()`

```python
def sync_longest_chain_blocks(self) -> None:
    longest = ChainDAO.longest()
    if longest is None or self.id != longest.id:
        # I'm not the longest. Nothing to do — the table belongs to
        # whichever ChainDAO is longest.
        return

    # I am the longest. Three sub-cases:
    current_max = db.session.query(
        db.func.max(LongestChainBlockDAO.position)
    ).scalar()
    if current_max is None:
        # Bootstrap: table empty, populate from CTE walk.
        self._rebuild_longest_chain_blocks()
        return

    # Check if the current tip in the table matches our previous tip
    # (one-block extension) or is something else (reorg / out-of-order).
    table_tip_block_id = db.session.query(
        LongestChainBlockDAO.block_id
    ).filter(LongestChainBlockDAO.position == current_max).scalar()

    if table_tip_block_id == self.block.prev_id:
        # Normal extend: one new row at current_max + 1.
        db.session.add(LongestChainBlockDAO(
            block_id=self.block_id, position=current_max + 1,
        ))
        return

    if table_tip_block_id == self.block_id:
        # No-op: someone already synced us. Defensive.
        return

    # Reorg or larger gap: full rebuild.
    self._rebuild_longest_chain_blocks()


def _rebuild_longest_chain_blocks(self) -> None:
    """Wipe the table and repopulate from this chain's recursive CTE."""
    db.session.query(LongestChainBlockDAO).delete()
    # block_chain walks from tip back to genesis; enumerate in reverse
    # to assign genesis = position 0.
    blocks = list(self.block.block_chain)
    for position, block in enumerate(reversed(blocks)):
        db.session.add(LongestChainBlockDAO(
            block_id=block.id, position=position,
        ))
```

Called from `Chain.to_db()` after `dao.commit()` so the materialization is part of the same logical transaction as the chain persistence.

### Routing: new `BlockDAO` query factories for the fast path

```python
@classmethod
def longest_chain_blocks_q(cls) -> Query[BlockDAO]:
    """Blocks in the longest chain, ordered tip→genesis (matching
    the existing block_chain CTE convention).
    """
    return (
        db.session.query(BlockDAO)
        .join(LongestChainBlockDAO,
              BlockDAO.id == LongestChainBlockDAO.block_id)
        .order_by(LongestChainBlockDAO.position.desc())
    )


@classmethod
def longest_chain_transactions_q(cls) -> Query[TransactionDAO]:
    """Transactions in the longest chain, ordered tip→genesis,
    matching transactions_chain's ordering."""
    ...  # analogous JOIN through TransactionDAO.blocks
```

Similar factories for outflows and inflows. Each is a one-liner-equivalent: JOIN against `longest_chain_block`, no CTE.

### Routing: `ChainDAO` method branching

Branching happens in **only the 4 property accessors** (`blocks`, `transactions`, `outflows`, `inflows`). The 6 downstream methods (`unspent_outflows`, `wallet_balance`, `unforgiven_outflows`, `subject_balance`, `subject_support`, `wallet_leaderboard`) all compose on top of those properties — they read `self.outflows`, `self.inflows`, `self.transactions` — so they inherit the fast path automatically with no direct edits.

```python
@property
def blocks(self) -> Query[BlockDAO]:
    if self._is_longest():
        return BlockDAO.longest_chain_blocks_q()
    return self.block.block_chain  # CTE fallback

@property
def transactions(self) -> Query[TransactionDAO]:
    if self._is_longest():
        return BlockDAO.longest_chain_transactions_q()
    return self.block.transactions_chain  # CTE fallback

# outflows / inflows: analogous

def _is_longest(self) -> bool:
    longest = ChainDAO.longest()
    return longest is not None and longest.id == self.id
```

The fast-path `Query[X]` return shape matches the CTE-path `Query[X]` shape, so callers that further compose `.filter(...)`, `.subquery()`, etc. (the 6 downstream methods, plus any future consumers) work unchanged.

## Changes

### Files

- Modify: `src/cancelchain/models.py`
  - Add `LongestChainBlockDAO` class.
  - Add `ChainDAO.sync_longest_chain_blocks()` + `ChainDAO._rebuild_longest_chain_blocks()` + `ChainDAO._is_longest()`.
  - Add `BlockDAO.longest_chain_blocks_q()` + `.longest_chain_transactions_q()` + `.longest_chain_outflows_q()` + `.longest_chain_inflows_q()`.
  - Add branching in 4 `ChainDAO` property accessors (`blocks`, `transactions`, `outflows`, `inflows`); the 6 downstream methods get the fast path automatically through composition.
- Modify: `src/cancelchain/chain.py`
  - `Chain.to_db()`: call `dao.sync_longest_chain_blocks()` after `dao.commit()`.
- Modify: `tests/test_models.py`
  - Extend the 3 existing `BlockDAO.query.count()` tests to also assert `longest_chain_block` row counts match.
  - Add new focused tests for the materialization (see [Test plan](#test-plan)).
- No changes to `database.py` — `db.create_all()` picks up the new model on next schema init.

### Schema

```sql
CREATE TABLE longest_chain_block (
    block_id INTEGER NOT NULL PRIMARY KEY,
    position INTEGER NOT NULL UNIQUE,
    FOREIGN KEY (block_id) REFERENCES block(id) ON DELETE CASCADE
);
```

(SQLAlchemy renders the equivalent DDL from the model declaration; no hand-written migration. The `UNIQUE` constraint on `position` carries its own index — no separate `CREATE INDEX` needed.)

## Test plan

- **Bootstrap.** Build a chain of N blocks; assert `longest_chain_block` has N rows with positions 0..N-1 and block_ids in the expected order.
- **Single-block extend.** Build a chain; add one block; assert one new row was inserted at the next position; assert no other rows changed.
- **Reorg.** Build chain A of length M; build chain B of length M+K (fork); make B the longest; assert the table was rebuilt to contain B's blocks at positions 0..M+K-1 and contains zero of A's distinct blocks.
- **Non-longest extension is a no-op.** Extend a non-longest chain; assert the table is unchanged.
- **Property: table matches CTE.** After any sequence of `add_block` / mock-reorg operations, assert that `SELECT block_id FROM longest_chain_block ORDER BY position DESC` matches the block IDs returned by `ChainDAO.longest().block.block_chain` (the existing CTE — used as ground truth in tests only).
- **Branching: fast path used when longest.** Mock or assert (via SQL logging / explain) that querying `ChainDAO.longest().blocks` does NOT execute a recursive CTE — only a single JOIN. Querying a non-longest `ChainDAO`'s `.blocks` still uses the CTE.
- **Existing chain/balance/leaderboard tests stay green.** The branching should be invisible from the outside; outputs match the prior CTE-based path.

Test count: 220 → 227 (+7 new materialization tests: bootstrap, single-block extend, reorg-rebuild, non-longest-noop, property-against-CTE, branching-uses-fast-path, branching-falls-back-to-CTE).

## Acceptance

- `grep -rn 'longest_chain_block' src/cancelchain/` finds the new model and the four `BlockDAO.longest_chain_*` factories.
- `uv run cancelchain init` creates the new table without error.
- `uv run mypy` exits 0.
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run pytest` exits 0; test count grows by ~7.
- `uv run pytest --runmulti` exits 0.
- `docker build --target builder -t cc-phase6 .` succeeds.
- A manual stopwatch / EXPLAIN check on a 100-block chain shows hot-path `ChainDAO.longest().wallet_balance(...)` no longer uses the recursive CTE.

## Risks

- **Reorg failure mid-rebuild.** The rebuild is `DELETE` + bulk `INSERT` inside a single SQLAlchemy session. A crash between the two leaves the table empty until the next chain mutation triggers a re-sync. SQLite/Postgres transaction semantics roll the whole thing back, so we either have the old contents or the new ones, not a partial mix. The risk is "what if a reader fires between the DELETE and the INSERT in the same transaction?" — both engines isolate readers from in-progress transactions, so readers see the pre-rebuild snapshot until commit.

- **Branching consistency under concurrent writes.** Two concurrent `add_block` calls could race on `ChainDAO.longest()`. The existing chain code already serializes via the SQLAlchemy session (a single writer per session), so this isn't introduced by Phase 6 — but worth confirming the rebuild path doesn't widen the race window. Action: rebuild runs inside the same transaction as `Chain.to_db()`; commit is atomic.

- **`block_chain` ordering convention.** The existing CTE walks tip→genesis. The new `BlockDAO.longest_chain_blocks_q()` must match this ordering (`position DESC`) so existing callers that consume the query in tip-first order keep working. The property test (above) is the safety net.

- **Residual CTE on bootstrap and reorg rebuild.** The CTE still fires once per rebuild event. For long chains, this can become slow (the original perf problem). Greenfield posture means we don't have a long chain yet, so this is tolerable for Phase 6. If/when chain length approaches the danger zone, Phase 6.5 / Phase 7 should add iterative Python-side walks (or batched CTE with `LIMIT`) for the bootstrap and reorg rebuild paths. Document as known follow-up.

- **Residual CTE on non-longest chain API queries.** If `ChainDAO.longest()` is NOT what an API caller wants (e.g., explicit `Chain.from_db(block_hash=specific_hash)`), the query still runs the CTE. Audit reveals this is rare in current code, but a thorough sweep before Phase 6.5 should confirm no hot caller is missed.

- **Test fixtures.** The conftest's `add_chain_block` helper builds chains via `Chain.add_block` → `to_db()`. The new materialization hook fires inside `to_db()`, so existing fixtures should keep working without changes. Verify in the test plan.

## Open decisions

None at design time. Brainstorming resolved:
- Sequencing (chain perf first; SA 2.0 syntax deferred to Phase 7).
- Cache scope (longest chain only).
- Storage shape (separate table).
- Branching shape (per-method `_is_longest()` check).
- Residual CTE risk (documented + deferred to Phase 6.5/7).

## What comes next

- **Phase 6.5 / 7 — Eliminate the residual CTE.** Replace bootstrap and reorg rebuild CTE calls with iterative Python-side walks (or batched-CTE with `LIMIT`) so long-chain reorgs don't block the event loop. Audit non-longest chain API consumers and route them through an iterative path if they're hot enough to matter.
- **Phase 7 — SQLAlchemy 2.0 syntax modernization.** Translate `Model.query` / `db.session.query` → `db.session.execute(db.select(...))`. Switch to typed `DeclarativeBase`. Remove the `mypy: disable-error-code` block at the top of `models.py`.
- **Phase 7+ — Wallet.key typing tightening.** Deferred from Phase 5a (`Any` → `RSAPrivateKey | RSAPublicKey`).
