# EGU 1b-pre capstone — delete the recursive `_block_chain` CTE — design

**Date:** 2026-06-05
**Status:** Approved design, pre-implementation
**Issue:** #158 (capstone to #157; part of EGU 1b readiness, #151)
**Type:** Performance / read-path refactor + dead-CTE removal — no schema, no migration, no consensus rule change

## Summary

#157 removed the recursive `BlockDAO._block_chain` CTE from the
consensus-validation **hot path** (Tier 1). The CTE is still reachable from the
single-pass **read paths** (Tier 2): the non-longest branch of the `ChainDAO`
read accessors, and `address_transactions`. A recursive CTE on *any* reachable
path is a latent O(chain-height) failure at height. This task converts the
remaining consumers to the divergent-suffix + position-scoped primitive from
#157 and then **deletes the recursive CTE entirely**, so it can never run.

Goal: **zero recursive-CTE code in the tree.** No schema change, no
consensus-rule change — read results are bit-identical to the pre-deletion CTE.

## Tier-2 consumers converted

| Consumer | Location | Notes |
|---|---|---|
| `ChainDAO.{blocks,transactions,outflows,inflows}` non-longest branch | `models.py:629–647` | Only reached for a **fork** chain (the `_is_longest()` fast path already routes canonical chains to `longest_chain_*_q`). Consumers (`unspent_outflows`, `wallet_balance`, `unrescinded_outflows`, `_stake_balance`, `wallet_leaderboard`) all wrap these `Select`s in `.subquery()` for membership/aggregation, so their row **ordering is unobservable**. |
| `BlockDAO.address_transactions` + `ChainDAO.address_transactions` | `models.py:400`, `:897` | Wallet transaction-history query. Currently has **zero live callers** anywhere in the tree, but is being **kept and converted** (not deleted) per decision, so a future wallet-history feature lands on a CTE-free path. |

## Decisions log

- **`address_transactions`: convert, don't delete.** It is currently dead code
  (no callers in views/api/cli/templates/tests), but we keep the method and
  route it CTE-free so wallet-history can be wired up later without reintroducing
  the CTE.
- **Test oracle after deletion: a pure-Python `prev`-walk.** The #157 equivalence
  tests and the Phase-6 materialization tests currently use the recursive CTE
  (`block.block_chain` / `transactions_chain` / `inflows_chain`) as their
  ground-truth oracle. Deleting the CTE removes that oracle. Replace it with a
  test-only helper that walks `block.prev` in Python — fully independent of all
  production SQL (both the CTE and the materialization), so the oracle cannot
  share a bug with the code under test.
- **Touch only the non-longest branch.** The canonical `_is_longest()` →
  `longest_chain_*_q` fast path is left exactly as-is; only the fork fallback and
  `address_transactions` change.
- **No ordering on the non-longest read builders.** Every consumer wraps them in
  `.subquery()` for set membership/aggregation; tip→genesis order is unobservable
  there. The ordered canonical path is untouched.
- **One PR, ordered commits** (convert → delete → test-rework). Deletion must be
  atomic with the last consumer's conversion: the CTE can't be deleted while
  referenced, and leaving it converted-but-present would not satisfy the
  grep-clean acceptance goal.
- No schema / migration / consensus-rule change; read results bit-identical to
  the CTE.

## Core mechanism: ancestry as a composable `Select`

#157 added `BlockDAO._ancestry() -> (divergent_ids, cap_position)`, resolving a
block's ancestry against `LongestChainBlockDAO` without recursion. This task
exposes that ancestry as `Select`s for the read paths via a composable block
predicate:

```
block.id IN (:divergent_ids)
  OR EXISTS (SELECT 1 FROM longest_chain_block lcb
             WHERE lcb.block_id = block.id AND lcb.position <= :cap)
```

For a canonical anchor `divergent_ids` is empty and `:cap` is the tip position,
so the predicate degenerates to materialized membership (equivalent to today's
`longest_chain_*_q`). For a fork it is the short divergent suffix OR the
canonical prefix at/below the common ancestor. Genesis is always materialized,
so a common ancestor always exists post-bootstrap; in the transient empty
materialization (`cap is None`) the predicate is `block.id IN (:divergent_ids)`
over the whole walked chain.

### New `BlockDAO` query builders (sketch — illustrative, not final)

```python
def ancestry_blocks_q(self) -> Select[tuple[BlockDAO]]:
    divergent, cap = self._ancestry()
    clauses = []
    if divergent:
        clauses.append(BlockDAO.id.in_(divergent))
    if cap is not None:
        clauses.append(
            db.select(LongestChainBlockDAO.id)
            .where(
                LongestChainBlockDAO.block_id == BlockDAO.id,
                LongestChainBlockDAO.position <= cap,
            )
            .exists()
        )
    predicate = db.or_(*clauses) if clauses else db.false()
    return db.select(BlockDAO).where(predicate)

def ancestry_transactions_q(self) -> Select[tuple[TransactionDAO]]:
    blocks_subq = self.ancestry_blocks_q().subquery()
    block_alias = db.aliased(BlockDAO, blocks_subq)
    return db.select(TransactionDAO).join(block_alias, TransactionDAO.blocks)

def ancestry_outflows_q(self) -> Select[tuple[OutflowDAO]]:
    txn_subq = self.ancestry_transactions_q().subquery()
    txn_alias = db.aliased(TransactionDAO, txn_subq)
    return db.select(OutflowDAO).join(txn_alias, OutflowDAO.transaction)

def ancestry_inflows_q(self) -> Select[tuple[InflowDAO]]:
    txn_subq = self.ancestry_transactions_q().subquery()
    txn_alias = db.aliased(TransactionDAO, txn_subq)
    return db.select(InflowDAO).join(txn_alias, InflowDAO.transaction)
```

These mirror the `longest_chain_*_q` join structure (blocks → transactions →
outflows/inflows) and reuse the FSA-facade `# type: ignore[no-any-return]`
convention already established in the module.

## File-by-file changes

| File | Change |
|---|---|
| `src/gumptionchain/models.py` | Add `ancestry_blocks_q` / `ancestry_transactions_q` / `ancestry_outflows_q` / `ancestry_inflows_q` to `BlockDAO`. Rewrite `address_transactions` to use `ancestry_transactions_q`. Repoint the `ChainDAO.{blocks,transactions,outflows,inflows}` **non-longest** branches from `self.block.<x>_chain` to `self.block.ancestry_<x>_q()`. Then **delete** `BlockDAO._block_chain`, `.block_chain`, `.transactions_chain`, `.outflows_chain`, `.inflows_chain`; the classmethod builders `TransactionDAO.transactions_chain`, `OutflowDAO.outflows_chain`, `InflowDAO.inflows_chain`; the now-unused `CTE` import; and trim the `*_chain` mention in the module docstring (lines ~22–27). |
| `tests/test_models.py` | Add a `_pythonic_ancestry_ids(block_dao)` oracle. Rewrite the #157 equivalence tests and the Phase-6 materialization tests that use `block.block_chain`/`transactions_chain`/`inflows_chain` as oracle to use it instead. Replace the `_block_chain` booby-trap guard tests with structural absence assertions. Add fork read-path equivalence tests for the `ChainDAO` accessors + `wallet_balance`/`unspent_outflows`. |

No `chain.py` change — `Chain.block_chain` there is a Python `prev`-walk
generator, unrelated to the SQL CTE. No schema/migration; `db check` unaffected.

## Testing (equivalence + structural absence)

1. **Read-path equivalence (canonical + fork)** — for a mined canonical chain
   and a genuine fork, assert `ChainDAO.{blocks,transactions,outflows,inflows}`,
   `address_transactions`, `wallet_balance`, and `unspent_outflows` return the
   **same** rows/values as computed from the `_pythonic_ancestry_ids` oracle
   (the set of blocks reachable via `prev` from the chain tip). Cover the fork
   case specifically: a balance/outflow that exists only on the divergent suffix,
   and one below the common ancestor.
2. **Bootstrap** — with an empty `longest_chain_block`, the `ancestry_*_q`
   builders resolve via the all-divergent predicate and still match the oracle.
3. **Structural absence (the "CTE is gone" gate)** — assert the recursive-CTE
   attributes no longer exist: `not hasattr(BlockDAO, '_block_chain')`, and the
   four `*_chain` properties, and the three classmethod builders. This replaces
   the #157 booby-trap guard tests.
4. **Full suite green** — the existing chain/validation/balance/miller tests must
   pass unchanged; behavior is preserved, only the CTE implementation is removed.
5. **`db check`** — no schema drift (this change adds no columns/tables).

No wall-clock perf assertion (CI-flaky); the structural-absence test plus the
equivalence tests are the proof that no path recurses.

## Out of scope

- **EGU 1b constant retune** (block time, retarget interval, difficulty floor,
  base-reward magnitude, RSA→2048) — the sibling task this unblocks.
- **#150** — the N+1 in `unrescinded_outflows` on the cold rescind-*build* path.
  (`unrescinded_outflows` is touched here only to repoint its `self.inflows` /
  `self.transactions` / `self.outflows` sources through the converted accessors;
  the N+1 itself is a separate concern.)

## Definition of done

- `ancestry_*_q` builders added; the non-longest `ChainDAO` accessors and
  `address_transactions` resolve ancestry via materialization (canonical) +
  divergent-suffix (fork), with **no** `_block_chain` reference.
- `BlockDAO._block_chain`, the four `*_chain` properties, and the three
  classmethod builders are **deleted**; the `CTE` import is removed.
- grep for `_block_chain` / `*_chain` CTE props is clean across `src`.
- Structural-absence test confirms the CTE attributes are gone; read-path
  equivalence (canonical + fork + bootstrap) passes against the Python oracle.
- Full suite + ruff + ruff-format + mypy green; `db check` shows no drift.
