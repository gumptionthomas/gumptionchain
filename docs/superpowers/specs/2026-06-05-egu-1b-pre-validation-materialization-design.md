# EGU 1b-pre — materialize the consensus-validation hot path — design

**Date:** 2026-06-05
**Status:** Approved design, pre-implementation
**Issue:** #157 (prerequisite to EGU 1b, part of #151)
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

The fix routes those three lookups through the existing materialization using a
**position-scoped indexed query**, falling back to the recursive CTE only when
the anchor block is off the canonical chain (fork / off-tip validation). No
schema change, no consensus-rule change — results are bit-identical to the CTE.

## The three O(N) call sites

All three are reached from `Chain.validate_block` (`chain.py:195`) on **every
block milled or received**:

| Path | DAO method (CTE) | Frequency |
|---|---|---|
| `block_target` → `get_block_by_reverse_index` → `ChainDAO.get_block` → `block.get_block_in_chain` | `get_block_in_chain` (`models.py:353`) → `block_chain` CTE | once per block (target retarget check) |
| `validate_txn_inflow` → `get_transaction` → `block_dao.get_transaction_in_chain` | `get_transaction_in_chain` (`models.py:343`) → `transactions_chain` CTE | once per inflow |
| `validate_txn_inflow` → `get_inflows_count` → `block_dao.inflows_in_chain_count` | `inflows_in_chain_count` (`models.py:366`) → `inflows_chain` CTE | once per inflow |

Per block: ≈ `1 + 2·(num inflows)` recursive tip→genesis walks. Linear in chain
height, on the hottest path.

## Core mechanism: position-scoped ancestry

Every block on the canonical chain has a `LongestChainBlockDAO.position`
(0 = genesis, increasing toward tip). A canonical block's **ancestry is exactly
the canonical blocks with `position <= anchor.position`**. So "is X in this
block's ancestry?" becomes an indexed join against `longest_chain_block` —
no recursion:

1. Look up the anchor's position: one indexed PK query on
   `longest_chain_block.block_id`.
2. **If found (anchor is canonical):** answer via a join filtered by
   `position <= pos` — the materialized, indexed path.
3. **If `None` (anchor is a fork / not yet materialized):** fall back to the
   existing recursive CTE. Correct and safe for the off-tip case.

### Why the anchor is canonical on the hot path

When validating a new tip block N+1 (not yet persisted), `get_transaction`
(`chain.py:406`) and `get_inflows_count` (`chain.py:424`) walk the unpersisted
block(s) in memory, then anchor on the first **persisted** block — the current
tip, block N. Block N was materialized by `sync_longest_chain_blocks` when it was
saved (`Chain.to_db`), so its position lookup succeeds → materialized path.
`block_target`'s `get_block_in_chain` anchors on the tip likewise. Bootstrap
(empty materialization) and genuine fork validation hit the `None` branch and use
the CTE — exactly when recursion is actually required.

## File-by-file changes

| File | Change |
|---|---|
| `models.py` | `BlockDAO.get_transaction_in_chain`, `inflows_in_chain_count`, `get_block_in_chain` each gain a position lookup + materialized branch; CTE retained as the `pos is None` fallback. A small private helper resolves the anchor's canonical position. |
| `tests/test_models.py` (or the existing chain/validation test module) | equivalence tests (materialized result == CTE result) + a CTE-guard test (recursive `_block_chain` is **not** touched on the canonical validation path) + a fork-fallback test. |

No changes to `chain.py` — `Chain.get_transaction` / `get_inflows_count` /
`get_block_by_reverse_index` call the BlockDAO methods, which now route
internally. No schema, no migration, no consensus rule change.

### Sketch (illustrative, not final code)

```python
def _canonical_position(self) -> int | None:
    return db.session.scalar(
        db.select(LongestChainBlockDAO.position).where(
            LongestChainBlockDAO.block_id == self.id
        )
    )

def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
    pos = self._canonical_position()
    if pos is None:  # fork / un-materialized → recursive fallback
        return db.session.execute(
            self.transactions_chain.where(TransactionDAO.txid == txid)
        ).scalar_one_or_none()
    stmt = (
        db.select(TransactionDAO)
        .join(TransactionDAO.blocks)
        .join(LongestChainBlockDAO, LongestChainBlockDAO.block_id == BlockDAO.id)
        .where(LongestChainBlockDAO.position <= pos)
        .where(TransactionDAO.txid == txid)
    )
    return db.session.execute(stmt).scalar_one_or_none()
```

`inflows_in_chain_count` and `get_block_in_chain` follow the same shape
(`inflow → transaction → blocks → longest_chain_block`, and
`block → longest_chain_block` respectively), each preserving its existing
return semantics (`inflows_in_chain_count` returns `1`/`0` existence, not a true
count; `get_block_in_chain` filters by `block_hash` and/or `idx`). Canonical-chain
uniqueness guarantees ≤1 row for the single-result lookups, so `scalar_one_or_none`
parity holds.

## Testing (equivalence + CTE-guard)

1. **Equivalence** — on a mined canonical chain, for representative
   txids / (outflow_txid, idx) / block hashes & idxs, assert the materialized
   method returns the **same** result as the recursive CTE computed directly
   (`block.transactions_chain.where(...)`, etc.). Cover hit and miss cases.
2. **CTE-guard** — patch `BlockDAO._block_chain` to raise, then run a full
   `Chain.validate_block` over a canonical block (with inflows and a target
   check). It must succeed — proving none of the three hot-path lookups touched
   the recursive CTE.
3. **Fork fallback** — build a non-canonical (fork) block; assert its
   `get_transaction_in_chain` / `inflows_in_chain_count` / `get_block_in_chain`
   still return correct results via the CTE fallback (position lookup returns
   `None`).
4. **Bootstrap** — with an empty `longest_chain_block`, the methods fall back to
   the CTE and return correct results.

No wall-clock perf assertion (CI-flaky); the CTE-guard test is the structural
proof that validation no longer recurses on the canonical path, which is what
makes it flat in N.

## Out of scope

- **EGU 1b constant retune** (block time, retarget interval, difficulty floor,
  base-reward magnitude, RSA→2048) — the sibling task this unblocks.
- **#150** — the N+1 in `unrescinded_outflows` on the cold rescind-*build* path.
  Different path, different fix.
- Refactoring the recursive CTE itself or removing it (still needed for forks).

## Decisions log

- **Scope: all three call sites** (`get_transaction_in_chain`,
  `inflows_in_chain_count`, `get_block_in_chain`). Leaving the per-block target
  lookup on the CTE would keep validation linear in N; fixing all three makes the
  whole `validate_block` path flat on the canonical chain.
- **Mechanism: position-scoped ancestry** (`position <= anchor.position`) against
  `LongestChainBlockDAO`, mirroring the existing `_is_longest()` routing; CTE
  retained as the off-canonical fallback.
- **Proof: equivalence + CTE-guard** (no wall-clock perf test; deterministic,
  CI-stable).
- No schema / migration / consensus-rule change; results bit-identical to the CTE.
- Sequenced as a **prerequisite to EGU 1b** — fix and verify before reducing
  block time.
