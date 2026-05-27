# Phase 6.5 — Eliminate residual CTE + cache `_is_longest()`

**Status:** Draft for review
**Date:** 2026-05-27
**Scope:** Close the two Phase 6 deferrals against `src/cancelchain/models.py`:
1. Replace the recursive CTE in `ChainDAO._rebuild_longest_chain_blocks` with an iterative walk via `BlockDAO.prev` so long-chain bootstrap / reorg rebuilds don't depend on the CTE planner.
2. Add a class-level generation-counter cache to `ChainDAO._is_longest()` so the 6 downstream methods (`wallet_balance`, `wallet_leaderboard`, etc.) don't fire redundant `ChainDAO.longest()` queries per property access.

## Goal

The Phase 6 spec acknowledged two residual concerns:
- `_rebuild_longest_chain_blocks` still fires the recursive CTE (`list(self.block.block_chain)`) on bootstrap and reorg events. For long chains, that's the same planner overhead that previously caused the project to be shelved.
- `_is_longest()` runs `ChainDAO.longest()` per property access. The 6 downstream `ChainDAO` methods read multiple properties, so a single `wallet_leaderboard` call fires the lookup 3 times. Copilot flagged this on PR #65; deferred per the spec.

Phase 6.5 closes both. The recursive CTE is removed from every code path in `models.py` (Phase 7 will then remove the now-dead CTE infrastructure from `BlockDAO._block_chain` / `block_chain` / `transactions_chain` / `outflows_chain` / `inflows_chain` — but those stay for Phase 6.5 since they're still used as the CTE fallback in the property branches for non-longest chains).

## Non-goals

- **No SA 2.0 syntax modernization.** Phase 7 (`Model.query` / `db.session.query(...)` → `db.session.execute(db.select(...))`).
- **No DeclarativeBase migration.** Phase 7.
- **No removal of the `mypy: disable-error-code` block at the top of `models.py`.** Phase 7.
- **No removal of the CTE-backed `block_chain` / `transactions_chain` / `outflows_chain` / `inflows_chain` on `BlockDAO`.** Those are still the CTE fallback inside `ChainDAO`'s 4 property branches for non-longest-chain queries. Removing them requires generalizing the materialization to all chains, which is out of scope.
- **No generalization of caching to other DAO classes.** Only `_is_longest()` is cached.
- **No cross-worker cache invalidation.** Multi-process invalidation needs coordination (Redis pubsub / DB notify channels) — out of scope. The cross-worker stale-cache risk is documented in Risks.
- **No batched CTE / chunked walk.** Single-row iterative walk via `BlockDAO.prev` is sufficient for current scale; if profiling later shows it's still slow on very long chains, batched fetch (`WHERE id IN (...)`) is the next step in a Phase 6.6 / 7.
- **No change to consensus rules, block validation, or hashing.**

## Decisions taken during brainstorming

- **Iterative-walk implementation: `current = current.prev` in a loop.** Each step is one indexed PK lookup. `BlockDAO.prev` is already declared as a SQLAlchemy relationship (`models.py:260-264`) — no new schema or relationship needed.
- **Cache invalidation: class-level generation counter.** `ChainDAO._chain_generation: ClassVar[int]` is bumped inside `sync_longest_chain_blocks` after any actual mutation (steady-state INSERT) and inside `_rebuild_longest_chain_blocks` after any rebuild. Each instance caches `(generation_at_cache_time, is_longest)` and re-checks the class-level counter on each `_is_longest()` call. Correctly invalidates across all in-process instances; documented stale-cache risk across workers.
- **Cache stored as instance attribute (`_is_longest_cache`).** Set lazily on first call via `setattr`. Not a `Mapped[]` column — SQLAlchemy's ORM ignores non-mapped instance attributes.
- **No cache on `ChainDAO.longest()` itself.** Only `_is_longest()` is cached. Other callers of `longest()` (CLI, tests, etc.) keep their direct query.

## Architecture

### Change 1: Iterative walk in `_rebuild_longest_chain_blocks`

Current (line 660-679 of `models.py`):
```python
def _rebuild_longest_chain_blocks(self) -> None:
    db.session.query(LongestChainBlockDAO).delete()
    blocks = list(self.block.block_chain)  # ← recursive CTE walk
    for position, block in enumerate(reversed(blocks)):
        db.session.add(LongestChainBlockDAO(
            block_id=block.id, position=position,
        ))
```

New:
```python
def _rebuild_longest_chain_blocks(self) -> None:
    """Wipe and repopulate longest_chain_block by walking the chain
    iteratively from tip → genesis via BlockDAO.prev links.

    Each step is one indexed PK lookup (block.id). Avoids the
    recursive CTE's planner overhead on long chains — the cost
    that caused the project to be shelved in the past.
    """
    db.session.query(LongestChainBlockDAO).delete()
    blocks: list[BlockDAO] = []
    current: BlockDAO | None = self.block
    while current is not None:
        blocks.append(current)
        current = current.prev
    for position, block in enumerate(reversed(blocks)):
        db.session.add(LongestChainBlockDAO(
            block_id=block.id, position=position,
        ))
    ChainDAO._bump_generation()
```

### Change 2: Class-level generation counter + per-instance cache

Additions to `ChainDAO`:
```python
class ChainDAO(db.Model):
    # Bumped on any longest_chain_block mutation; invalidates all
    # ChainDAO instances' cached _is_longest values within this
    # process. Cross-worker invalidation is out of scope (see Risks).
    _chain_generation: ClassVar[int] = 0

    @classmethod
    def _bump_generation(cls) -> None:
        cls._chain_generation += 1

    def _is_longest(self) -> bool:
        """True iff this ChainDAO row is currently the longest chain.

        Cached per instance and invalidated by class-level generation
        bumps inside sync_longest_chain_blocks / rebuild paths.
        """
        cached: tuple[int, bool] | None = getattr(
            self, '_is_longest_cache', None
        )
        if cached is not None and cached[0] == ChainDAO._chain_generation:
            return cached[1]
        longest = ChainDAO.longest()
        result = longest is not None and longest.id == self.id
        self._is_longest_cache = (ChainDAO._chain_generation, result)
        return result
```

### Change 3: Two bump sites in `sync_longest_chain_blocks`

The existing decision tree has three mutating paths (bootstrap, single-row extend, reorg/rebuild). Two of them call `_rebuild_longest_chain_blocks` (which bumps internally per Change 1); the single-row extend bumps in-line:

```python
def sync_longest_chain_blocks(self) -> None:
    if not self._is_longest():
        return
    current_max = db.session.query(
        db.func.max(LongestChainBlockDAO.position)
    ).scalar()
    if current_max is None:
        self._rebuild_longest_chain_blocks()  # bumps internally
        return
    table_tip_block_id = (
        db.session.query(LongestChainBlockDAO.block_id)
        .filter(LongestChainBlockDAO.position == current_max)
        .scalar()
    )
    if table_tip_block_id == self.block_id:
        return  # no-op, no bump
    if table_tip_block_id == self.block.prev_id:
        db.session.add(LongestChainBlockDAO(
            block_id=self.block_id, position=current_max + 1,
        ))
        ChainDAO._bump_generation()  # ← new: bump on extend
        return
    self._rebuild_longest_chain_blocks()  # bumps internally
```

## Changes

### Files

- Modify: `src/cancelchain/models.py`
  - Add `from typing import ClassVar` if not already imported.
  - Add `_chain_generation: ClassVar[int] = 0` at class scope in `ChainDAO`.
  - Add `_bump_generation()` classmethod on `ChainDAO`.
  - Rewrite `_is_longest()` body with the (generation, result) tuple cache.
  - Add `ChainDAO._bump_generation()` call after the single-row INSERT in `sync_longest_chain_blocks`.
  - Rewrite `_rebuild_longest_chain_blocks` body: iterative walk via `current.prev`, then `ChainDAO._bump_generation()` at the end.
- Modify: `tests/test_models.py` — add tests (see Test plan).

No other source files change. No schema migration; no `database.py` change.

## Test plan

### Iterative walk
- **Iterative walk produces same positions as CTE walk** (new test). Build a chain of 10 blocks, call `_rebuild_longest_chain_blocks` directly, then compare the materialized rows to a reference `list(chain.block.block_chain)` (CTE). They must match.
- **Iterative walk on a longer chain** (new test, ~50 blocks). Verifies the iteration terminates correctly and produces the right count; primarily a smoke test that scales.

### Cache hit / miss
- **Cache hit returns cached value without re-querying** (new test). Mock `ChainDAO.longest` to count invocations; call `_is_longest()` twice on the same instance; assert the mock was invoked exactly once.
- **Generation bump invalidates cache** (new test). Mock `ChainDAO.longest`; call `_is_longest()` once; then call `ChainDAO._bump_generation()`; call `_is_longest()` again; assert the mock was invoked exactly twice.
- **Cache survives across method calls within the same instance** (new test). Call `chain.wallet_balance(addr)` (which internally hits `self.outflows` and `self.inflows`, each calling `_is_longest()`). Mock `ChainDAO.longest`; assert the mock was invoked exactly once across both property reads.
- **`sync_longest_chain_blocks` bumps generation on extend** (new test). Add a block; observe that the cached `_is_longest` value on the chain instance gets recomputed (mock `ChainDAO.longest` and verify it's called a second time after the extend).

### Regression
- All existing 227 tests stay green.

Test count: 227 → 232 (+5 new tests).

## Acceptance

- `grep -n 'block_chain\b' src/cancelchain/models.py` shows the CTE-backed property is still referenced (by the 4 property branches for the non-longest fallback path) — but `_rebuild_longest_chain_blocks` no longer references it.
- `grep -n '_chain_generation\|_bump_generation' src/cancelchain/models.py` shows the new class-level counter and the two bump sites.
- `uv run mypy` exits 0.
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run pytest` exits 0; test count grows by 5 (227 → 232).
- `uv run pytest --runmulti` exits 0.
- A manual stopwatch on a 100-block chain shows `_rebuild_longest_chain_blocks` completes in O(N) indexed lookups, not a single CTE plan.

## Risks

- **Cross-worker stale cache.** The generation counter is process-local. If Worker B reorgs the chain (bumping its own generation, not Worker A's), Worker A's held ChainDAO instances retain their cached `_is_longest = True`. A subsequent `chain_a.blocks` read in Worker A routes through the fast path against the new (chain_b-aligned) materialized table, returning chain_b's blocks where the caller expected chain_a's. The window is bounded to a held instance's lifetime within one worker, typically a single Flask request. Mitigations (out of scope): (a) session-scoped cache cleared at request boundaries; (b) cross-process notification via Redis / DB NOTIFY. Document the limitation in `_is_longest()`'s docstring.

- **Iterative walk slow on very long chains.** Each `current.prev` access is one indexed PK lookup. For a 50k-block chain, that's 50k queries serialized over the SQLAlchemy session. Faster than a CTE plan on the same chain (no exponential blowup) but still O(N) round-trips. If this surfaces as a bottleneck in future profiling, the follow-up is batched fetch: load M blocks at a time via `BlockDAO.query.filter(BlockDAO.id.in_(ids))` after collecting prev_ids in chunks. Tracked as a future Phase 6.6 / 7 item.

- **`ClassVar[int]` mutation across SQLAlchemy mapped subclass.** SQLAlchemy's declarative metaclass doesn't interfere with plain `ClassVar` annotations — they're treated as Python class attributes, not columns. Verified by inspection of `db.Model` from Flask-SQLAlchemy 3.1.1. Mutation via `cls._chain_generation += 1` is atomic under the GIL; safe for single-process multi-threaded workers.

- **`getattr(self, '_is_longest_cache', None)` + dynamic instance attribute.** SQLAlchemy instances allow non-mapped attributes via the instance dict. The pattern is safe; mypy strict accepts it under the existing `models.py` ignore block. The attribute won't appear in `__dict__` until first call, which is intentional.

- **Test isolation.** The class-level `_chain_generation` persists across tests within the same pytest process. If a test mutates the counter, subsequent tests see the bumped value. This is harmless (each test that cares about cache state resets via a fresh `_bump_generation()` call or fresh instances), but worth confirming: each new test's `_is_longest()` call would recompute (cache miss on first call) regardless of prior generation state. No test fixture needs to reset the counter.

## Open decisions

None at design time. Brainstorming resolved:
- Walk implementation (`current = current.prev`).
- Cache invalidation strategy (class-level generation counter).
- Cache storage (per-instance attribute set lazily).
- Bump sites (2 total: rebuild and extend).
- Cross-worker invalidation explicitly deferred.

## What comes next

- **Phase 7 — SQLAlchemy 2.0 syntax modernization.** Translate `Model.query` / `db.session.query` → `db.session.execute(db.select(...))`. Switch to typed `DeclarativeBase`. Remove the `mypy: disable-error-code` block at the top of `models.py`. With `block_chain` etc. still in use for non-longest-chain fallback, this is the next big mechanical pass.
- **Phase 6.6 / 7+ — Batched iterative walk.** If profiling shows the single-row iterative walk is the new bottleneck on very long chains, add batched-fetch mode (`WHERE id IN (...)`) for `_rebuild_longest_chain_blocks`.
- **Phase 7+ — Cross-worker cache invalidation.** If the cross-worker stale-cache window becomes a real concern (multi-worker deployment with concurrent writes), add a coordination mechanism (Redis pubsub or Postgres NOTIFY).
