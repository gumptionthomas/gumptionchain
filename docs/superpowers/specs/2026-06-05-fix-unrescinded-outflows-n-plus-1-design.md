# Fix the N+1 in the unspent/unrescinded outflow generators — design

**Date:** 2026-06-05
**Status:** Approved design, pre-implementation
**Issue:** #150
**Type:** Performance / correctness-neutral refactor — no schema, no consensus change

## Summary

Three domain-layer generators in `chain.py` — `unspent_outflows`,
`unrescinded_outflows`, `unrescinded_address_outflows` — rebuild the **entire
parent `Transaction`** for every matched outflow just to extract the one outflow
they already hold. Each iteration lazy-loads `outflow_dao.transaction`, then
`Transaction.from_dao` lazy-loads that transaction's `.inflows` and `.outflows`
— ≈3 extra queries per matched outflow (the #150 N+1), plus wasted object
construction.

The fix is a direct `OutflowDAO → Outflow` converter so the loop converts the
outflow it already has, with **zero extra queries**. Root-cause, not a batching
patch.

## Current code (the waste)

All three generators share this loop body (`chain.py`):

```python
for outflow_dao in outflow_daos:
    txn = Transaction.from_dao(outflow_dao.transaction)  # lazy-load txn
    index = outflow_dao.idx
    outflow = txn.get_outflow(index=index)               # rebuilds .inflows + .outflows
    if outflow is None:
        continue
    ...
    yield (outflow_dao.txid, outflow_dao.idx, outflow)
```

`Transaction.from_dao` (`transaction.py:333`) already converts an `OutflowDAO`
into a domain `Outflow` via a flat 6-field copy. `get_outflow(index)` is just
`self.outflows[index]`, and the outflows list is built from `dao.outflows`
ordered by `idx`, so `get_outflow(outflow_dao.idx)` returns exactly the
`outflow_dao` the loop already holds. The whole round-trip is equivalent to a
direct conversion of `outflow_dao` — but pays ≈3 queries to get there.

## Change

1. **Add `Outflow.from_dao(cls, dao)`** to `payload.py` — the same flat 6-field
   copy `Transaction.from_dao` does inline (`amount`, `address`, `opposition`,
   `rescind`, `support`, `rescind_kind` with the existing `StakeKind` cast).
   Add `Inflow.from_dao(cls, dao)` for symmetry (`outflow_txid`, `outflow_idx`).
2. **Refactor `Transaction.from_dao`** (`transaction.py`) to build its `inflows`
   / `outflows` via `Inflow.from_dao` / `Outflow.from_dao` — one source of truth
   for the conversion (DRY), no behavior change.
3. **Replace the round-trip** in all three generators with the direct convert:
   ```python
   for outflow_dao in outflow_daos:
       outflow = Outflow.from_dao(outflow_dao)
       ...
       yield (outflow_dao.txid, outflow_dao.idx, outflow)
   ```
   The `if outflow is None: continue` guard becomes dead (the dao is always
   present) and is removed. Loop-local `txn` / `index` variables go away. The
   `amount`/`limit` accumulation and the yield tuple are unchanged.

No change to the underlying DAO queries (`to_dao().unspent_outflows(...)` /
`unrescinded_outflows(...)`), no schema, no consensus rule, no public return
shape (`Iterator[tuple[str, int, Outflow]]` unchanged).

## Testing

1. **N+1 regression guard (the point of the fix).** Reusing the
   `before_cursor_execute` query-capture pattern from `tests/test_query_plans.py`,
   build a chain with several (e.g. 3+) unrescinded stake outflows for one
   subject/address, then assert that fully draining
   `unrescinded_address_outflows` (and `unrescinded_outflows`, `unspent_outflows`)
   issues a **constant** number of SQL statements independent of the match count
   — i.e. it does **not** scale with M. (Assert an exact small bound, or compare
   the count for M=1 vs M=3 and require equality.) This is the structural "no
   N+1" analog of the EXPLAIN guards.
2. **Behavior-equivalence.** The existing rescind/transfer/stake tests
   (`create_rescind`, balance/stake-balance, transfer build) must pass unchanged
   — the yielded `(txid, idx, Outflow)` tuples are identical. Add/confirm a test
   that the converted `Outflow` for a representative outflow equals what the old
   `Transaction.from_dao(...).get_outflow(...)` path produced (same fields).
3. Full suite + ruff + ruff-format + mypy green; `db check` unaffected (no schema
   change).

## Out of scope

- The chain-wide *scan* cost of the underlying balance/unspent SQL (the
  materialized-subquery `anon_` cost noted in #161) — that's a query-restructure,
  not this N+1.
- EGU 1b constant retune (#151) and the sync/pruning items (#163/#164).

## Decisions log

- **Root-cause converter, not eager-load.** A direct `Outflow.from_dao` removes
  both the N+1 *and* the wasted full-transaction reconstruction; eager-loading
  would only batch the queries while still rebuilding transactions to extract one
  outflow. Simpler and faster.
- **Add `Inflow.from_dao` too** so `Transaction.from_dao` is fully DRY and the two
  converters are symmetric.
- **Behavior-identical:** `get_outflow(idx)` == `self.outflows[idx]` over an
  idx-ordered list == the held `outflow_dao`, so the direct convert yields the
  same domain `Outflow`; the `None` guard was unreachable for a present dao and is
  removed.

## Definition of done

- `Outflow.from_dao` / `Inflow.from_dao` added; `Transaction.from_dao` reuses
  them; the three generators convert `outflow_dao` directly with no per-row
  transaction reconstruction and no dead `None` guard.
- A query-count regression test proves the generators issue O(1) statements, not
  O(matched-outflows).
- Existing rescind/transfer/stake/balance tests pass unchanged; full suite + ruff
  + mypy green.
