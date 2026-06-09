# EGU #165 — unspent/balance anti-join → correlated `NOT EXISTS`

**Date:** 2026-06-09
**Issue:** #165 (the unspent/balance anti-join MATERIALIZEs the whole-chain inflow set per call). EGU readiness umbrella (#151), launch checklist (#190). Last untracked item from the EGU 1b-pre scalability audit.
**Status:** design approved

## Goal

Make the "unspent" / balance / stake reads cost proportional to the **outflows
they actually touch**, not to the **whole chain's inflow count**. Today each call
turns `self.inflows` into a `.subquery()`, which SQLite **MATERIALIZE**s into a
derived table with a per-call **AUTOMATIC COVERING INDEX**, then LEFT-JOINs and
filters `IS NULL`. Both the materialization and the throwaway index scale with
chain height regardless of result size — a residual chain-wide cost the #161
index pack could not remove (a base-table index can't cover a transient
materialized subquery).

This is a **correctness-preserving** restructuring: the winner is identical
result sets, produced by a query plan that index-seeks instead of materializing.

## Background (from the issue + code audit)

The anti-join shape appears in **six** `ChainDAO` methods
(`src/gumptionchain/models.py`), all identical in structure:

```python
inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())  # whole-chain inflow set
stmt = self.outflows.where(<filter>)
stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
stmt = stmt.where(inflows_alias.id.is_(None))                   # "unspent" = no consuming inflow
```

| Method | Returns | Notes |
|---|---|---|
| `unspent_outflows` | `Select[OutflowDAO]` | + optional `filter_pending` |
| `unrescinded_outflows` | `Select[OutflowDAO]` | + optional `address` join, `filter_pending` |
| `wallet_balance` | `int` | wraps the unspent select in `sum()` |
| `_stake_balance` | `int` | wraps the unspent select in `sum()` |
| `wallet_leaderboard` | `Select[Any]` | grouped by address, summed |
| `subject_leaderboard` | `Select[Any]` | UNION of opposition/support legs, grouped |

`subject_leaderboard` is **not** named in #165's enumerated list but carries the
identical anti-join and feeds `/subjects`; it is **in scope** here (decided
during brainstorming) so no straggler keeps materializing the inflow set. The
addition is noted back on #165.

Observed `EXPLAIN QUERY PLAN` today (from #162):

```
MATERIALIZE anon_3
  SCAN block_transaction_2
  ...
  SEARCH inflow USING INDEX ix_inflow_transaction_id (transaction_id=?)
...
SEARCH anon_3 USING AUTOMATIC COVERING INDEX (outflow_id=?) LEFT-JOIN
```

`self.inflows` already encapsulates the **longest-chain-vs-ancestry routing**
(`_is_longest()` → `longest_chain_inflows_q()` else `ancestry_inflows_q()`). That
routing is consensus-relevant and must not be duplicated or altered.

The enabling index — `ix_inflow_outflow_id` on `inflow.outflow_id` — already
exists (added in #161). **No schema change** in this work.

## Approach

A single private helper builds the correlated anti-join clause from the existing
chain-scoped inflow query; the six methods filter with it.

```python
def _unspent_clause(self) -> ColumnElement[bool]:
    # An outflow is unspent iff no inflow in this chain consumes it.
    # Correlated NOT EXISTS: SQLite index-seeks ix_inflow_outflow_id per
    # candidate outflow (≈0–1 consuming inflows) and checks chain
    # membership only for those — instead of MATERIALIZE-ing the whole
    # inflow set + building a per-call AUTOMATIC COVERING INDEX over it.
    return ~(
        self.inflows.order_by(None)
        .where(InflowDAO.outflow_id == OutflowDAO.id)
        .exists()
    )
```

- `self.inflows` yields the chain-scoped inflow `Select` (longest or ancestry —
  unchanged routing).
- `.order_by(None)` strips the ORDER BY that `*_inflows_q()` carries — irrelevant
  inside `EXISTS` and avoids emitting dead SQL.
- `.where(InflowDAO.outflow_id == OutflowDAO.id)` correlates to the enclosing
  query's `OutflowDAO`; SQLAlchemy auto-correlates against the outer FROM.
- `~ ... .exists()` is the anti-join.

Each method drops its `aliased(InflowDAO, self.inflows.subquery())` +
`join(..., isouter=True)` + `where(inflows_alias.id.is_(None))` triplet and adds
`.where(self._unspent_clause())`. Everything else (the `<filter>`, `filter_pending`,
`address` join, `sum()` wrappers, `group_by` / UNION / `limit` scaffolding) is
**unchanged**. No public signatures change; no callers change.

### Rejected alternatives

- **Hand-written `NOT EXISTS` with an explicit block-membership join** (bypassing
  `self.inflows`): would duplicate the longest/ancestry membership logic — exactly
  the consensus-adjacent code we don't want forked. Rejected.
- **Inline `~exists()` at all six call sites** (no helper): identical SQL, six
  copies. Rejected — the helper is DRY-er and gives one place to reason about
  correctness.

## Per-method changes

All in `src/gumptionchain/models.py`, all the same swap:

- `unspent_outflows` — replace triplet with `.where(self._unspent_clause())`;
  keep the `filter_pending` `~OutflowDAO.pending.any()` clause.
- `unrescinded_outflows` — same swap; keep the `column == subject`, optional
  `address` txn-join, and `filter_pending` clauses.
- `wallet_balance` — swap inside the inner `stmt`; the outer
  `sum()`-over-subquery wrapper is unchanged. (Equivalent: the helper lets the
  inner `stmt` be summed directly; keep the existing wrapper shape to minimize
  diff.)
- `_stake_balance` — same as `wallet_balance`.
- `wallet_leaderboard` — drop `inflows_alias` + its `isouter` join + `IS NULL`;
  add `.where(self._unspent_clause())`. Keep the `txn_alias` join (it backs the
  `earliest`/`latest` filters and is independent of the anti-join), `group_by`,
  `order_by`, `limit`.
- `subject_leaderboard` — in `_leg`, drop `inflows_alias` join + `IS NULL`; add
  `.where(self._unspent_clause())`. UNION/`group_by`/`limit` unchanged.

## Testing

### Result-equivalence (correctness guard)

A focused test module builds a multi-spend fixture across a **fork** (so both
the longest-chain and ancestry routings of `self.inflows` are exercised):
funded addresses, some outflows spent by later inflows, some unspent, opposition
& support stakes with some rescinded. For each of the six methods, assert the new
result set / scalar **equals** what the old materialize-then-LEFT-JOIN-IS-NULL
produced. Since the old expression is being removed, compute the expected values
directly from the fixture (the known spent/unspent partition) rather than against
a retained copy of the old query.

The existing suite already pins exact values through these methods
(`test_unspent_outflows`, wallet/stake balance tests, the leaderboard view
tests, the oracle-fork reorg tests); **all must stay green** — they are the
primary equivalence guard. The new test adds direct multi-spend coverage.

### EXPLAIN perf guard

A test runs `EXPLAIN QUERY PLAN` (via `db.session.execute(db.text('EXPLAIN QUERY
PLAN ' + compiled_sql))`, or the SQLAlchemy compiled form) on each rewritten
query against a seeded chain and asserts **no plan row contains `MATERIALIZE`
or `AUTOMATIC`** (case-insensitive substring match — loose, to stay robust across
SQLite versions where exact `anon_N` / index names vary). This is the durable
regression guard that the materialization does not creep back.

### Gates

`uv run ruff format src tests && uv run ruff check src tests && uv run mypy &&
uv run pytest`, plus `uv run gumptionchain db check` (schema is untouched, but
run it for safety).

## Scope / care

- **Consensus-adjacent:** these feed balance / ownership / double-spend-relevant
  reads. The equivalence tests + the untouched existing balance/stake/reorg tests
  are the safety net; the `self.inflows` routing is reused verbatim so the
  canonical-vs-fork semantics cannot drift.
- Whale addresses with many outflows stay linear in **their own** holdings
  (fundamental). The win is eliminating the per-call **whole-chain inflow**
  materialization + automatic index.
- Affects both the cached-per-tip API balance reads and the cold (uncached)
  build path.

## PR decomposition

Single implementation PR (`feat/egu-165-not-exists-antijoin`): the helper + six
mechanical swaps + the equivalence and EXPLAIN tests. The transform is tightly
coupled (one helper, six callers) — splitting would scatter it. Docs (this spec +
the plan) land first per cadence, then the implementation PR.

## Out of scope / follow-ups

- `inflows_in_chain_count` (`models.py:452`) is a **targeted** single-outflow
  lookup, not the anti-join shape — left as-is.
- #150 (application-layer N+1 of M Python round-trips) is a **distinct** sibling
  perf item, not this SQL-structure fix.
- No new index (the enabling `ix_inflow_outflow_id` shipped in #161).
- No schema/migration change.
