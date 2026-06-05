# EGU 1b-pre part 2 — read-path index pack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing indexes on the filter/FK columns that the CTE removal left SQLite scanning, so the per-inflow double-spend check (`inflows_in_chain_count`, hot path) and the balance/stake reads seek instead of scan. Guard the wins with EXPLAIN-structural tests.

**Architecture:** Add indexes to `OutflowDAO`/`InflowDAO` `__table_args__` and fold the same indexes into the single baseline Alembic migration (greenfield — no stacked revision). A test helper captures the real SQL a production call emits (via a SQLAlchemy `before_cursor_execute` listener) and runs `EXPLAIN QUERY PLAN` on it, asserting the index is used and no `AUTOMATIC` throwaway index is built. Each candidate index must be used by ≥1 real query plan or it is dropped.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0, Flask-Migrate/Alembic, pytest, uv, ruff (line-length 80, single quotes), mypy strict, SQLite.

**Spec:** `docs/superpowers/specs/2026-06-05-egu-1b-pre-part2-read-path-indexes-design.md` (issue #161)

---

## File map

| File | Change |
|---|---|
| `src/gumptionchain/models.py` | Add indexes to `OutflowDAO.__table_args__` and `InflowDAO.__table_args__`. |
| `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` | Fold the same indexes into the baseline migration (`upgrade` create + `downgrade` drop). |
| `tests/test_query_plans.py` (new) | EXPLAIN-capture helper + structural tests asserting the indexes are used and no AUTOMATIC index is built. |

No logic change, no model columns, no consensus change. `db check` stays green because models + migration are updated together.

---

## Background the implementer needs

The candidate index pack (from the spec). Names follow the existing
`ix_<table>_<cols>` style (see `ix_outflow_txid_idx` / `ix_inflow_txid_idx`):

| Index | Columns | Serves |
|---|---|---|
| `ix_inflow_outflow_txid_idx` | `inflow(outflow_txid, outflow_idx)` | **#2 hot path** — `inflows_in_chain_count` double-spend check |
| `ix_inflow_outflow_id` | `inflow(outflow_id)` | unspent anti-join in balance/stake queries |
| `ix_outflow_transaction_id` | `outflow(transaction_id)` | outflow⋈transaction FK join |
| `ix_outflow_address` | `outflow(address)` | wallet balance / leaderboard filter |
| `ix_outflow_opposition` | `outflow(opposition)` | opposition-stake filter |
| `ix_outflow_support` | `outflow(support)` | support-stake filter |

Real `EXPLAIN QUERY PLAN` already captured on the current schema confirms the
#2 query flips from `SCAN block_transaction` + `AUTOMATIC ... INDEX` to
`SEARCH inflow USING INDEX ix_inflow_outflow_txid_idx` once the index exists,
and that `AUTOMATIC` indexes appear in the BEFORE plan even on a tiny/empty DB —
so the structural test is reliable on small fixtures.

Current `__table_args__` (you are extending these):
```python
# OutflowDAO (models.py:131-134)
__table_args__ = (
    db.UniqueConstraint('txid', 'idx'),
    db.Index('ix_outflow_txid_idx', 'txid', 'idx'),
)
# InflowDAO (models.py:183-186)
__table_args__ = (
    db.UniqueConstraint('txid', 'idx'),
    db.Index('ix_inflow_txid_idx', 'txid', 'idx'),
)
```

Baseline migration index style (`63d32cd7621a_initial_schema.py`): in `upgrade()`,
`batch_op.create_index('ix_...', ['col'...], unique=False)` inside the table's
`with op.batch_alter_table('<table>', schema=None) as batch_op:` block (lines
119-120 for outflow, 135-136 for inflow); in `downgrade()`, the matching
`batch_op.drop_index('ix_...')` (lines 155-160).

Useful test fixtures/helpers (`tests/test_models.py`, importable patterns):
`_build_canonical_chain_with_spend(add_chain_block, time_stepper, wallet)` →
`(chain, block1, block2, spend_txid)` builds a 2-block chain where block2 spends
block1's coinbase (so there is a real inflow to probe). `ChainDAO.get(block_hash)`,
`BlockDAO.get(block_hash)`. The `subject` fixture exists.

---

## Task 1: Add the index pack + EXPLAIN-structural tests

**Files:**
- Create: `tests/test_query_plans.py`
- Modify: `src/gumptionchain/models.py`
- Modify: `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`

- [ ] **Step 1: Write the EXPLAIN-capture helper + failing tests**

Create `tests/test_query_plans.py`. The helper records the real SQL each production
call emits via a `before_cursor_execute` listener, then EXPLAINs each SELECT on
the raw DBAPI cursor (so qmark params bind correctly and the EXPLAIN itself isn't
re-captured).

```python
import datetime

from sqlalchemy import event

from gumptionchain.database import db
from gumptionchain.models import BlockDAO, ChainDAO

from tests.test_models import _build_canonical_chain_with_spend


def _explain_plans(fn):
    """Run fn(), capturing EXPLAIN QUERY PLAN for every SELECT it emits.

    Returns a list of (sql, plan_text). Uses a before_cursor_execute listener
    to grab the real production SQL + params, then EXPLAINs each on the raw
    DBAPI cursor (qmark params bind positionally; the raw cursor bypasses the
    listener so EXPLAIN isn't itself captured).
    """
    bind = db.session.get_bind()
    captured: list[tuple[str, object]] = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        if not executemany:
            captured.append((statement, parameters))

    event.listen(bind, 'before_cursor_execute', _rec)
    try:
        fn()
    finally:
        event.remove(bind, 'before_cursor_execute', _rec)

    raw = db.session.connection().connection
    plans = []
    for stmt, params in captured:
        if not stmt.lstrip().upper().startswith('SELECT'):
            continue
        cur = raw.cursor()
        try:
            cur.execute('EXPLAIN QUERY PLAN ' + stmt, params or ())
            detail = '\n'.join(row[3] for row in cur.fetchall())
        finally:
            cur.close()
        plans.append((stmt, detail))
    return plans


def test_inflows_in_chain_count_uses_index(
    app, add_chain_block, time_stepper, wallet
):
    """The per-inflow double-spend check must seek via
    ix_inflow_outflow_txid_idx, not SCAN block_transaction / build an
    AUTOMATIC index.
    """
    with app.app_context():
        _chain, block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        cb1 = block1.coinbase.txid

        plans = _explain_plans(lambda: tip.inflows_in_chain_count(cb1, 0))
        membership = [p for s, p in plans if 'outflow_txid' in s]
        assert membership, 'expected a query filtering on inflow.outflow_txid'
        joined = '\n'.join(membership)
        assert 'ix_inflow_outflow_txid_idx' in joined, joined
        assert 'AUTOMATIC' not in joined, joined
        assert 'SCAN block_transaction' not in joined, joined


def test_balance_read_builds_no_automatic_index(
    app, add_chain_block, time_stepper, wallet
):
    """unspent_outflows (the basis of wallet/stake balances) must not fall
    back to an AUTOMATIC index, and must use an outflow/inflow index.
    """
    with app.app_context():
        _chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        chain_dao = ChainDAO.get(block2.block_hash)
        assert chain_dao is not None

        plans = _explain_plans(
            lambda: db.session.execute(
                chain_dao.unspent_outflows(wallet.address)
            ).all()
        )
        joined = '\n'.join(p for _s, p in plans)
        assert 'AUTOMATIC' not in joined, joined
        assert ('ix_outflow_' in joined) or (
            'ix_inflow_outflow_id' in joined
        ), joined
```

Note: `_build_canonical_chain_with_spend` is defined in `tests/test_models.py`;
the `from tests.test_models import ...` works because the suite runs from the repo
root. If that import path fails in this project's pytest config, instead copy the
helper's construction inline (read it from `tests/test_models.py`). Confirm which
works before proceeding.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_query_plans.py -q`
Expected: FAIL — without the indexes, `inflows_in_chain_count`'s plan contains
`AUTOMATIC`/`SCAN block_transaction` and lacks `ix_inflow_outflow_txid_idx`; the
balance plan contains `AUTOMATIC`.

- [ ] **Step 3: Add the indexes to the models**

In `src/gumptionchain/models.py`, extend `OutflowDAO.__table_args__`:
```python
    __table_args__ = (
        db.UniqueConstraint('txid', 'idx'),
        db.Index('ix_outflow_txid_idx', 'txid', 'idx'),
        db.Index('ix_outflow_transaction_id', 'transaction_id'),
        db.Index('ix_outflow_address', 'address'),
        db.Index('ix_outflow_opposition', 'opposition'),
        db.Index('ix_outflow_support', 'support'),
    )
```
and `InflowDAO.__table_args__`:
```python
    __table_args__ = (
        db.UniqueConstraint('txid', 'idx'),
        db.Index('ix_inflow_txid_idx', 'txid', 'idx'),
        db.Index('ix_inflow_outflow_txid_idx', 'outflow_txid', 'outflow_idx'),
        db.Index('ix_inflow_outflow_id', 'outflow_id'),
    )
```

- [ ] **Step 4: Run the EXPLAIN tests, expect PASS**

Run: `uv run pytest tests/test_query_plans.py -q`
Expected: PASS — `inflows_in_chain_count` now seeks via `ix_inflow_outflow_txid_idx`
with no AUTOMATIC index/no block_transaction scan; the balance plan uses an
outflow/inflow index and builds no AUTOMATIC index.

(If `test_balance_read_builds_no_automatic_index` still shows AUTOMATIC, capture
the plan text from the assertion message and identify which column still lacks an
index; it should be covered by `ix_inflow_outflow_id` + `ix_outflow_transaction_id`.
Do not weaken the assertion — fix the index.)

- [ ] **Step 5: Fold the indexes into the baseline migration**

In `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`:

In `upgrade()`, the outflow batch block becomes:
```python
    with op.batch_alter_table('outflow', schema=None) as batch_op:
        batch_op.create_index('ix_outflow_txid_idx', ['txid', 'idx'], unique=False)
        batch_op.create_index('ix_outflow_transaction_id', ['transaction_id'], unique=False)
        batch_op.create_index('ix_outflow_address', ['address'], unique=False)
        batch_op.create_index('ix_outflow_opposition', ['opposition'], unique=False)
        batch_op.create_index('ix_outflow_support', ['support'], unique=False)
```
and the inflow batch block:
```python
    with op.batch_alter_table('inflow', schema=None) as batch_op:
        batch_op.create_index('ix_inflow_txid_idx', ['txid', 'idx'], unique=False)
        batch_op.create_index('ix_inflow_outflow_txid_idx', ['outflow_txid', 'outflow_idx'], unique=False)
        batch_op.create_index('ix_inflow_outflow_id', ['outflow_id'], unique=False)
```

In `downgrade()`, add the matching drops (mirror the existing ones). The inflow
block:
```python
    with op.batch_alter_table('inflow', schema=None) as batch_op:
        batch_op.drop_index('ix_inflow_outflow_id')
        batch_op.drop_index('ix_inflow_outflow_txid_idx')
        batch_op.drop_index('ix_inflow_txid_idx')
```
and the outflow block:
```python
    with op.batch_alter_table('outflow', schema=None) as batch_op:
        batch_op.drop_index('ix_outflow_support')
        batch_op.drop_index('ix_outflow_opposition')
        batch_op.drop_index('ix_outflow_address')
        batch_op.drop_index('ix_outflow_transaction_id')
        batch_op.drop_index('ix_outflow_txid_idx')
```

- [ ] **Step 6: Verify `db check` shows no drift**

```bash
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///_dbcheck.db uv run gumptionchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///_dbcheck.db uv run gumptionchain db check
rm -f _dbcheck.db
```
Expected: no differences. (If `db check` reports a missing/extra index, the model
`__table_args__` and the migration are out of sync — reconcile names/columns
exactly.)

- [ ] **Step 7: Full suite + lint + format + types**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all green. Indexes don't change results, so the existing suite stays green.

- [ ] **Step 8: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py tests/test_query_plans.py
git commit -m "$(cat <<'EOF'
perf(db): index read-path filter/FK columns the CTE removal left scanning (#161)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Verify every index earns its place; capture before/after EXPLAIN

**Files:**
- Possibly modify: `src/gumptionchain/models.py`, `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` (only if dropping an unused index)

This task produces the before/after EXPLAIN evidence for the PR body and enforces
the spec's "earn its place" rule: any candidate index used by **no** real query
plan is dropped (dead indexes cost writes).

- [ ] **Step 1: Confirm `ix_inflow_outflow_txid_idx`, `ix_inflow_outflow_id`, `ix_outflow_transaction_id` are used**

These three are exercised by the Task 1 tests (the #2 query and the balance
anti-join/FK join). Confirm by reading the passing assertions and the plan text
they print on the membership/balance queries. Record the AFTER plan lines for the
PR body. No action beyond confirmation.

- [ ] **Step 2: Confirm the selective-filter indexes (`ix_outflow_address`, `ix_outflow_opposition`, `ix_outflow_support`) are used by a real query on representative data**

On a tiny 2-block fixture SQLite may prefer a membership-driven plan and ignore a
selective-column index, so prove these on a seed where the filtered value is
selective. Write a TEMPORARY throwaway check (do NOT commit it) — a small script
or scratch test — that:
1. builds a chain of ~40-60 blocks via the `mill_block`/`add_chain_block` fixtures,
   with a spend that creates an `opposition` stake on a `subject` and a `support`
   stake, addressed to the test wallet;
2. uses the `_explain_plans` helper to EXPLAIN:
   - `chain_dao.wallet_balance(<address>)` → expect `ix_outflow_address` (or the
     anti-join/FK indexes) and **no** AUTOMATIC;
   - `chain_dao.opposition_balance(<subject>)` → expect `ix_outflow_opposition`;
   - `chain_dao.support_balance(<subject>)` → expect `ix_outflow_support`.

Record each AFTER plan for the PR body. For any of these three that the planner
**still** does not use on representative data, DROP it (remove from both
`OutflowDAO.__table_args__` and the baseline migration's create+drop), and note in
the PR which were dropped and why. Re-run `db check` after any drop. Delete the
throwaway script — it must not land in the commit.

- [ ] **Step 3: Capture the BEFORE plans for the PR body**

The canonical before/after for #2 and #3 is already recorded in the spec/design
doc; if you want a fresh BEFORE, run the `_explain_plans` helper on
`git stash`'d-away indexes (or a checkout of the parent commit) for the same
queries and record the `SCAN block_transaction` / `AUTOMATIC ... INDEX` lines.
This is documentation for the PR description, not a code change.

- [ ] **Step 4: Final verification gates**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
and the `db check` no-drift sequence from Task 1 Step 6.
Expected: all green; no drift.

- [ ] **Step 5: Commit (only if an index was dropped in Step 2)**

```bash
git add src/gumptionchain/models.py src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py
git commit -m "$(cat <<'EOF'
perf(db): drop read-path indexes unused by any query plan (#161)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```
If no index was dropped, there is nothing to commit in this task — report the
before/after EXPLAIN evidence for the PR body instead.

---

## Self-review notes

- **Spec coverage:** full candidate pack added (Task 1); each index's "earn its
  place" verified, unused dropped (Task 2); EXPLAIN-structural CI gate for the
  reliably-chosen indexes (#2 + anti-join/FK); before/after EXPLAIN for the PR
  body; baseline-migration fold (no stacked revision); `db check` no drift.
- **Behavior preservation:** indexes don't change results; the full existing suite
  is the correctness guard. Only schema indexes + a new test file change.
- **Test reliability:** AUTOMATIC indexes appear in the BEFORE plan even on tiny
  fixtures (verified manually), so the #2/balance structural tests are stable on
  small data. The selective-filter indexes that small data can't reliably exercise
  are proven on a representative seed in Task 2.
- **Naming consistency:** `ix_<table>_<cols>` throughout; model `__table_args__`
  names match the migration `create_index`/`drop_index` names exactly (the
  `db check` gate enforces this).

## Definition of done

- Surviving indexes defined in both `OutflowDAO`/`InflowDAO` `__table_args__` and
  the baseline migration `63d32cd7621a` (upgrade + downgrade), names matching.
- `test_query_plans.py` proves `inflows_in_chain_count` seeks via
  `ix_inflow_outflow_txid_idx` (no AUTOMATIC, no block_transaction scan) and the
  balance read builds no AUTOMATIC index.
- Every surviving index is used by ≥1 real query plan; any ignored candidate is
  dropped and noted.
- Before/after EXPLAIN captured for the PR body.
- Full suite + ruff + ruff-format + mypy green; `gumptionchain db check` no drift.
