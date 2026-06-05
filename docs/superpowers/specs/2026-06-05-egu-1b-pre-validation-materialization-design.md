# EGU 1b-pre — materialize the consensus-validation hot path — design

**Date:** 2026-06-05
**Status:** Approved design, pre-implementation
**Issue:** #157 (prerequisite to EGU 1b, part of #151); capstone follow-up #158
**Type:** Performance / read-path refactor — no schema, no migration, no consensus rule change

## Summary

Eliminate the recursive `BlockDAO._block_chain` CTE from the
**consensus-validation hot path**. Phase 6's `LongestChainBlockDAO`
materialization already removed the CTE from the read/balance paths, but three
lookups invoked by `Chain.validate_block` — once per block plus twice per inflow
— still run the recursive CTE, each O(chain-height). This makes block validation
O(inflows × height): invisible at small N, lethal at large N. It is the scaling
cliff that previously shelved the project, and it **gates EGU 1b** (cutting block
time ~20× grows the chain ~20× faster and reaches the wall ~20× sooner).

This task converts those three lookups so they **never run the recursive CTE**:

- **Canonical anchor** (the steady-state hot path) → position-scoped indexed
  query against `LongestChainBlockDAO`.
- **Fork / off-tip anchor** → a **bounded divergent-suffix walk** (the same
  primitive `sync_longest_chain_blocks` already uses), **not** the recursive CTE.

So after this task, the three hot-path methods are O(reorg-depth + result), never
O(height). The recursive CTE property itself still exists (it is reached only by
the single-pass *read* paths — `address_transactions`, non-longest accessors)
and is **deleted in the capstone follow-up #158**. No schema change, no
consensus-rule change — results are bit-identical to the CTE.

## The three O(N) call sites

All three are reached from `Chain.validate_block` (`chain.py:195`) on **every
block milled or received**:

| Path | DAO method (CTE) | Frequency |
|---|---|---|
| `block_target` → `get_block_by_reverse_index` → `ChainDAO.get_block` → `block.get_block_in_chain` | `get_block_in_chain` (`models.py:353`) → `block_chain` CTE | once per block (target retarget check) |
| `validate_txn_inflow` → `get_transaction` → `block_dao.get_transaction_in_chain` | `get_transaction_in_chain` (`models.py:343`) → `transactions_chain` CTE | once per inflow |
| `validate_txn_inflow` → `get_inflows_count` → `block_dao.inflows_in_chain_count` | `inflows_in_chain_count` (`models.py:366`) → `inflows_chain` CTE | once per inflow |

Per block: ≈ `1 + 2·(num inflows)` recursive tip→genesis walks. Linear in chain
height, on the hottest path. **A fork fallback that kept the recursive CTE would
not fix this** — a CTE walks fork-tip→genesis regardless of how shallowly the
fork diverges, so a 1-block fork at height N still triggers an N-row recursion.
The fork path must be CTE-free too.

## Core mechanism: ancestry without recursion

Every block on the canonical chain has a `LongestChainBlockDAO.position`
(0 = genesis, increasing toward tip). A canonical block's **ancestry is exactly
the canonical blocks with `position <= anchor.position`** — answerable by an
indexed join, no recursion.

A *fork* block's ancestry is its short **divergent suffix** (the blocks above the
common ancestor, none of which are in the materialization) plus the canonical
prefix at/below the common ancestor. Genesis is always materialized, so a common
ancestor always exists (post-bootstrap).

A single helper resolves both cases by walking `prev` links only across the
divergent suffix:

```python
def _ancestry(self) -> tuple[list[int], int | None]:
    """Resolve this block's ancestry against the materialization without
    recursion.

    Returns (divergent_ids, cap_position):
    - divergent_ids: ids of blocks on the divergent suffix (not in
      LongestChainBlockDAO), nearest-first. Empty when this block is
      canonical.
    - cap_position: position of the common ancestor in
      LongestChainBlockDAO; the canonical prefix is `position <= cap`.
      None only when the materialization is empty (bootstrap), in which
      case divergent_ids covers the whole walked chain.

    Cost is O(divergent-suffix length) indexed `prev` lookups: 0 extra for
    a canonical anchor (first lookup hits), reorg-depth for a fork. Never
    O(chain-height) except transient bootstrap (empty materialization).
    """
    divergent: list[int] = []
    current: BlockDAO | None = self
    while current is not None:
        pos = db.session.scalar(
            db.select(LongestChainBlockDAO.position).where(
                LongestChainBlockDAO.block_id == current.id
            )
        )
        if pos is not None:
            return divergent, pos
        divergent.append(current.id)
        current = current.prev
    return divergent, None
```

### Why the anchor is canonical on the hot path

When validating a new tip block N+1 (not yet persisted), `get_transaction`
(`chain.py:406`) and `get_inflows_count` (`chain.py:424`) walk the unpersisted
block(s) in memory, then anchor on the first **persisted** block — the current
tip, block N. Block N was materialized by `sync_longest_chain_blocks` when it was
saved (`Chain.to_db`), so `_ancestry` returns `([], tip_position)` on its first
lookup — zero divergent walk, one indexed query. `block_target`'s
`get_block_in_chain` anchors on the tip likewise. Fork validation (sync/reorg)
hits the divergent-suffix branch, bounded by reorg depth.

## File-by-file changes

| File | Change |
|---|---|
| `models.py` | Add `BlockDAO._ancestry` helper. Rewrite `get_transaction_in_chain`, `inflows_in_chain_count`, `get_block_in_chain` to use canonical (position-scoped) + divergent-suffix queries — **no `_block_chain` reference in any of the three**. The recursive `_block_chain` / `*_chain` properties remain in the file (still used by Tier-2 read paths) until #158 deletes them. |
| `tests/test_models.py` (or the chain/validation test module) | equivalence tests (new path == old CTE result, canonical & fork) + a CTE-guard test (recursive `_block_chain` is **not** touched on the validation path, **canonical or fork**) + a bootstrap test. |

No changes to `chain.py` — `Chain.get_transaction` / `get_inflows_count` /
`get_block_by_reverse_index` call the BlockDAO methods, which now route
internally. No schema, no migration, no consensus rule change.

### Sketch (illustrative, not final code)

```python
def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
    divergent, cap = self._ancestry()
    if divergent:  # short fork suffix: direct id-set lookup
        hit = db.session.execute(
            db.select(TransactionDAO)
            .join(TransactionDAO.blocks)
            .where(BlockDAO.id.in_(divergent))
            .where(TransactionDAO.txid == txid)
        ).scalars().first()
        if hit is not None:
            return hit
    if cap is not None:  # canonical prefix: indexed, position-bounded
        return db.session.execute(
            db.select(TransactionDAO)
            .join(TransactionDAO.blocks)
            .join(LongestChainBlockDAO, LongestChainBlockDAO.block_id == BlockDAO.id)
            .where(LongestChainBlockDAO.position <= cap)
            .where(TransactionDAO.txid == txid)
        ).scalar_one_or_none()
    return None
```

`inflows_in_chain_count` and `get_block_in_chain` follow the same two-part shape
(`inflow → transaction → blocks`, and `block` directly), preserving existing
return semantics: `inflows_in_chain_count` returns `1`/`0` existence (not a true
count); `get_block_in_chain` filters by `block_hash` and/or `idx`.
Canonical-chain uniqueness guarantees ≤1 row for the single-result lookups, so
`scalar_one_or_none` parity holds on the canonical branch.

## Testing (equivalence + CTE-guard)

1. **Equivalence (canonical)** — on a mined canonical chain, for representative
   txids / (outflow_txid, idx) / block hashes & idxs, assert the new method
   returns the **same** result as the recursive CTE computed directly
   (`block.transactions_chain.where(...)`, etc.). Cover hit and miss cases.
2. **Equivalence (fork)** — build a fork block whose `_ancestry` returns a
   non-empty divergent suffix; assert the new methods return the same results as
   the CTE for txns/inflows/blocks that live in the divergent suffix *and* below
   the common ancestor.
3. **CTE-guard** — patch `BlockDAO._block_chain` to raise, then run a full
   `Chain.validate_block` over (a) a canonical block and (b) a fork block, each
   with inflows and a target check. Both must succeed — proving none of the three
   hot-path lookups touched the recursive CTE on either path.
4. **Bootstrap** — with an empty `longest_chain_block`, the methods resolve via
   the all-divergent walk and return correct results.

No wall-clock perf assertion (CI-flaky); the CTE-guard test is the structural
proof that validation no longer recurses on any path, which is what makes it flat
in N.

## Out of scope

- **Tier 2 + CTE deletion (#158)** — routing `address_transactions` and the
  non-longest read accessors through materialized/divergent-suffix queries, then
  deleting `_block_chain` and the four `*_chain` properties. This task leaves
  those properties in place (Tier-2 read paths still reference them) but ensures
  the hot path no longer does.
- **EGU 1b constant retune** (block time, retarget interval, difficulty floor,
  base-reward magnitude, RSA→2048) — the sibling task this unblocks.
- **#150** — the N+1 in `unrescinded_outflows` on the cold rescind-*build* path.

## Decisions log

- **Scope: all three call sites** (`get_transaction_in_chain`,
  `inflows_in_chain_count`, `get_block_in_chain`). Leaving the per-block target
  lookup on the CTE would keep validation linear in N.
- **Fork fallback is CTE-free** — a bounded divergent-suffix walk
  (`_ancestry`), not the recursive CTE. A CTE fork fallback would re-arm the
  O(height)-per-fork-block bomb. PR1 therefore ships with **zero** per-block CTE
  calls; the quadratic dies the moment it merges.
- **Mechanism: ancestry without recursion** — position-scoped query for the
  canonical prefix (`position <= cap`) + id-set query for the short divergent
  suffix.
- **Proof: equivalence + CTE-guard over canonical *and* fork** (no wall-clock
  perf test; deterministic, CI-stable).
- **CTE deletion deferred to #158** (capstone), since Tier-2 read paths still
  reference `_block_chain` until they are converted. Two coupled PRs, smaller
  blast radius each.
- No schema / migration / consensus-rule change; results bit-identical to the CTE.
