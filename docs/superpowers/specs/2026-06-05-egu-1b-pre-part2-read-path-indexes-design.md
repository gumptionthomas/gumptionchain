# EGU 1b-pre part 2 — read-path index pack — design

**Date:** 2026-06-05
**Status:** Approved design, pre-implementation
**Issue:** #161 (prerequisite to EGU 1b, part of #151); sibling to #157/#158
**Type:** Performance / indexing — schema index additions only, no model columns, no logic, no consensus rule change

## Summary

#157/#158 removed the recursive `_block_chain` CTE and made block-ancestry
**membership** O(1) via the `LongestChainBlockDAO` materialization. But the
queries that resolve *which row* satisfy a membership-scoped filter still filter
on **unindexed** columns, so the planner falls back to a full `block_transaction`
scan plus an on-the-fly **AUTOMATIC** index per call — height-proportional work.
This task adds the missing indexes so those queries seek instead of scan.

The headline is the **consensus hot path**: `inflows_in_chain_count` (the
per-inflow double-spend check, run on every block milled or received) filters on
`inflow.outflow_txid` / `inflow.outflow_idx`, which are not indexed. So #157/#158
made membership O(1) but left the spend-check itself O(chain inflows) to return a
≤1-row answer. EGU 1b (≈20× faster blocks) runs this check ≈20× more often, so
it is a genuine 1b prerequisite — the same "fix before you speed up" relationship
the CTE removal had.

No schema columns, no migrations stacked (greenfield: fold into the single
baseline), no logic change. Indexes only.

## Evidence (real `EXPLAIN QUERY PLAN`, current schema)

**#2 — `inflows_in_chain_count` (hot path, per inflow per block):**

```
BEFORE:  SCAN block_transaction
         SEARCH inflow USING AUTOMATIC PARTIAL COVERING INDEX (outflow_txid=? AND outflow_idx=? ...)
AFTER:   SEARCH inflow USING INDEX ix_inflow_outflow_txid_idx (outflow_txid=? AND outflow_idx=?)
         SEARCH transaction USING INTEGER PRIMARY KEY
         SEARCH longest_chain_block USING COVERING INDEX ... (position<?)
         SEARCH block_transaction USING COVERING INDEX ... (block_id=? AND transaction_id=?)
```

The `AUTOMATIC ... INDEX` line is SQLite building a throwaway index every call
because none exists. With the index, the plan flips from "scan the chain's
block_transaction table" to "seek the one inflow by index."

**#3 — `wallet_balance` (balance/stake reads):**

```
BEFORE:  SCAN block_transaction
         SEARCH outflow USING AUTOMATIC PARTIAL COVERING INDEX (address=? AND transaction_id=?)
         SEARCH inflow  USING AUTOMATIC COVERING INDEX (outflow_id=?) LEFT-JOIN
AFTER:   FK joins use ix_outflow_transaction_id / ix_inflow_outflow_id (covering); no AUTOMATIC index
```

Honest scope: a balance is inherently "all of this address's outflows in the
chain," so #3 still scans `block_transaction` in the membership-driven plan; the
win is eliminating the per-row AUTOMATIC indexes and FK-join scans, and letting a
selective `address` drive from `ix_outflow_address`. A whale address stays linear
in *its own holdings* — fundamental, not an index gap.

## Proposed index pack (each must earn its place via an EXPLAIN delta)

| Index | Columns | Serves |
|---|---|---|
| `ix_inflow_outflow_txid_idx` | `inflow(outflow_txid, outflow_idx)` | **#2 hot path** — `inflows_in_chain_count` double-spend check (validation/milling). The 1b prerequisite. |
| `ix_inflow_outflow_id` | `inflow(outflow_id)` | The unspent anti-join (`OutflowDAO.inflows` LEFT JOIN ... IS NULL) in every balance/stake query. |
| `ix_outflow_transaction_id` | `outflow(transaction_id)` | `outflow ⋈ transaction` FK join in the balance/read builders. |
| `ix_outflow_address` | `outflow(address)` | `wallet_balance`, `unspent_outflows`, `wallet_leaderboard` address filter. |
| `ix_outflow_opposition` | `outflow(opposition)` | `opposition_balance` / `unrescinded_outflows(kind='opposition')` subject filter. |
| `ix_outflow_support` | `outflow(support)` | `support_balance` / `unrescinded_outflows(kind='support')` subject filter. |

**Earn-its-place rule:** for each index, the implementation captures a before/
after `EXPLAIN QUERY PLAN` on at least one real production query and confirms the
index is actually used (`USING INDEX ix_...`). **Any index the planner ignores
across all real queries is dropped** — indexes carry write-amplification cost, so
a dead index is a net negative. The final pack is whatever survives this filter;
the table above is the candidate set.

Out of candidate set (verify, likely unnecessary): a `block_transaction`
reverse `(transaction_id, block_id)` index — the existing composite PK already
covers the `(block_id, transaction_id)` joins seen in the AFTER plans, and the
remaining `SCAN block_transaction` in #3 is the outer membership loop that a
reverse index would not remove. Include only if an EXPLAIN delta justifies it.

## File-by-file changes

| File | Change |
|---|---|
| `src/gumptionchain/models.py` | Add the surviving indexes to `OutflowDAO.__table_args__` and `InflowDAO.__table_args__` (`db.Index('ix_...', 'col'[, 'col2'])`), following the existing `ix_outflow_txid_idx` / `ix_inflow_txid_idx` style. |
| `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` | **Fold** the same indexes into the single baseline migration (greenfield — do **not** stack a new revision): add `batch_op.create_index('ix_...', [...], unique=False)` in `upgrade()` and the matching `batch_op.drop_index('ix_...')` in `downgrade()`, alongside the existing index ops. |
| `tests/test_models.py` (or a new `tests/test_query_plans.py`) | EXPLAIN-structural tests asserting the key production queries use the new indexes and build no AUTOMATIC index. |

`db check` stays green because the model metadata (`__table_args__`) and the
baseline migration are updated together; tests use `create_all()` from the models.

## Testing (EXPLAIN structural + db check)

1. **EXPLAIN structural tests (CI gate).** A small helper captures the **real**
   SQL emitted by a production call (via a SQLAlchemy `before_cursor_execute`
   listener recording `(statement, params)`), then runs `EXPLAIN QUERY PLAN` on
   each captured statement with the same params. Using the captured SQL (not a
   hand-reconstruction) keeps the test from drifting from production.
   - For **#2**: call `inflows_in_chain_count(outflow_txid, outflow_idx)` on a
     small built chain; assert the captured plan contains
     `USING INDEX ix_inflow_outflow_txid_idx` and contains **no** `AUTOMATIC`
     index and **no** `SCAN block_transaction`.
   - For **#3 / balance**: EXPLAIN the `Select` returned by
     `unspent_outflows(address)` (and `address_transactions(address)`); assert it
     uses the relevant `ix_outflow_*` / `ix_inflow_outflow_id` and builds no
     AUTOMATIC index.
   - This mirrors the project's established "structural SQL-shape" test pattern
     (the `'RECURSIVE' not in compiled_sql` guard from #157/#158).
2. **Correctness unaffected.** Indexes do not change results; the full existing
   suite must stay green (balance/validation/equivalence tests already cover
   correctness).
3. **`db check`** — model metadata matches the baseline migration after folding
   in the indexes; `gumptionchain db upgrade` + `db check` report no drift.
4. **No wall-clock assertion in CI** (flaky). The before/after `EXPLAIN QUERY
   PLAN` output is captured in the **PR description** for the visceral picture;
   the structural test is the durable gate.

## Out of scope

- **EGU 1b constant retune** (block time, retarget interval, difficulty floor,
  base reward, RSA→2048) — the task this unblocks.
- **#150** N+1 in `unrescinded_outflows` build path (result-size bounded, cold
  path) — separate follow-up.
- **Initial-sync `MAX_CHAIN_FILL_DEPTH` ceiling** (the fresh-node >50K sync
  blocker) — a sync-resumability design, tracked separately; not an index change.
- **`ChainDAO` fork-row pruning** (`longest()` sorts all tips) — separate
  follow-up; minor at expected fork counts.

## Decisions log

- **Full read-path pack**, not hot-path-only — all candidate indexes are cheap
  and close the read-path gaps in one migration; each is EXPLAIN-justified and
  dropped if the planner ignores it.
- **Proof: EXPLAIN structural tests + PR before/after**, not wall-clock
  benchmarks — deterministic and CI-stable, matching the existing structural-SQL
  test convention.
- **Fold into the baseline migration**, do not stack a new Alembic revision
  (greenfield, pre-launch).
- Indexes only — no model columns, no query logic change, no consensus change;
  results bit-identical.

## Definition of done

- The surviving indexes are defined in both `OutflowDAO`/`InflowDAO`
  `__table_args__` and the baseline migration `63d32cd7621a` (upgrade + downgrade).
- EXPLAIN structural tests prove `inflows_in_chain_count` and the balance reads
  use the new indexes and build no AUTOMATIC index; before/after `EXPLAIN` is in
  the PR description.
- Any candidate index unused by all real query plans is dropped (with a note).
- Full suite + ruff + ruff-format + mypy green; `gumptionchain db check` shows no
  drift.
