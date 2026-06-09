# EGU #165 unspent/balance anti-join → NOT EXISTS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the materialize-then-LEFT-JOIN-IS-NULL anti-join in six `ChainDAO` methods with a correlated `NOT EXISTS` (one shared `_unspent_clause()` helper), eliminating the per-call whole-chain inflow materialization while producing byte-identical result sets.

**Architecture:** Add a private `ChainDAO._unspent_clause()` that builds `~ self.inflows.order_by(None).where(InflowDAO.outflow_id == OutflowDAO.id).exists()` — a correlated anti-join over the existing chain-scoped inflow query (which already routes longest-chain vs ancestry). The six methods drop their `aliased(InflowDAO, self.inflows.subquery())` + outer-join + `IS NULL` triplet and filter with `.where(self._unspent_clause())`. No schema change, no signature change, no caller change.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (`Select.exists()`, correlated subqueries), Flask-SQLAlchemy `db` facade, pytest, SQLite (`EXPLAIN QUERY PLAN`).

**Spec:** `docs/superpowers/specs/2026-06-09-egu-165-not-exists-antijoin-design.md`

**Reference (read first):**
- `src/gumptionchain/models.py` — the six anti-join methods: `unspent_outflows` (`:758`), `wallet_balance` (`:771`), `unrescinded_outflows` (`:782`), `_stake_balance` (`:804`), `wallet_leaderboard` (`:824`), `subject_leaderboard` (`:851`). Also `self.inflows`/`self.outflows`/`self.transactions` properties (`:746`–`:756`) and the `ix_inflow_outflow_id` index (`:198`). The `from sqlalchemy import (...)` block is at `:9`–`:19` (it does **not** yet import `ColumnElement`).
- `tests/test_models.py` — `test_unspent_outflows` (`:38`) is the canonical fork fixture builder (chain_a with a spend + chain_b fork). `_sa_helpers.py` provides `_count(model)` and `_count_select(stmt)`.
- `tests/conftest.py` — fixtures: `app`, `subject` (`:166`), `wallet` (`:176`), `time_stepper` (`:115`), `mill_block` (`:306`), `add_chain_block` (`:316`).
- Memory: greenfield (no migration here — schema unchanged); the existing balance/stake/reorg tests are the equivalence safety net.

---

## PR 1 — `_unspent_clause()` helper + NOT EXISTS rewrite (single PR)

Branch: `feat/egu-165-not-exists-antijoin` off fresh `main`.

### Task 1: result-equivalence test (pins current correct behavior — passes now, stays green through the refactor)

**Files:**
- Test: `tests/test_models.py` (append a new test function)

This test asserts exact values for all six methods over a multi-spend fork
fixture. It must PASS on the current (pre-refactor) code — it documents the
behavior the rewrite must preserve, and is the safety net while the SQL changes.

- [ ] **Step 1: Write the equivalence test**

Append to `tests/test_models.py`:

```python
def test_antijoin_equivalence_all_methods(app, subject, time_stepper, wallet):
    """Pin exact results for every unspent/balance method over a chain with
    a spent coinbase, an unspent coinbase, and an opposition stake — plus a
    fork so both longest-chain and ancestry routing of self.inflows run.

    This is the equivalence guard for the NOT EXISTS rewrite (#165): the
    values below are computed from the known spent/unspent partition and must
    not change when the anti-join SQL is restructured.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)

        # block_1: coinbase cb_1 to wallet.
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, wallet, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        cb_1 = block_1.coinbase
        cb_1_amount = next(iter(cb_1.outflows)).amount
        chain_a.to_db()
        reward = chain_a.block_reward()

        # block_2a: spend cb_1 entirely into an opposition stake on `subject`.
        _ = next(time_step)
        t_2a = Transaction()
        t_2a.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
        t_2a.add_outflow(Outflow(amount=cb_1_amount, opposition=subject))
        t_2a.set_wallet(wallet)
        t_2a.seal()
        t_2a.sign()
        _ = next(time_step)
        block_2a = Block()
        block_2a.add_txn(t_2a)
        chain_a.link_block(block_2a)
        metrics_2a = sum(
            (chain_a.validate_block_txn(block_2a, txn) for txn in block_2a.txns),
            CoinbaseMetrics(),
        )
        chain_a.seal_block(block_2a, wallet, metrics_2a)
        block_2a.mill()
        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()
        dao_a = chain_a.to_dao()
        assert dao_a is not None

        # On chain_a: cb_1 is SPENT (consumed by t_2a); cb_2a (block_2a's
        # coinbase) is UNSPENT. So wallet has 1 unspent transfer outflow
        # worth one reward, and the subject carries one opposition stake.
        assert _count_select(dao_a.unspent_outflows(wallet.address)) == 1
        assert dao_a.wallet_balance(wallet.address) == reward
        assert dao_a.opposition_balance(subject) == cb_1_amount
        assert dao_a.support_balance(subject) == 0
        assert _count_select(
            dao_a.unrescinded_outflows(subject, 'opposition')
        ) == 1
        assert _count_select(
            dao_a.unrescinded_outflows(subject, 'support')
        ) == 0
        # Leaderboards (longest chain = chain_a).
        wl = db.session.execute(dao_a.wallet_leaderboard()).all()
        assert wl == [(wallet.address, reward)]
        sl = db.session.execute(dao_a.subject_leaderboard()).all()
        # (subject, opposition, support, total)
        assert sl == [(subject, cb_1_amount, 0, cb_1_amount)]

        # Fork: chain_b off block_1 (a sibling of block_2a) — exercises the
        # ancestry routing of self.inflows on the non-longest chain.
        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)  # links onto block_1 (current tip pre-2a)
        chain_a.seal_block(block_2b, wallet, CoinbaseMetrics())
        block_2b.mill()
        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()
        dao_b = chain_b.to_dao()
        assert dao_b is not None

        # On chain_b: cb_1 is UNSPENT (t_2a is not in chain_b), cb_2b unspent
        # → 2 unspent transfers, no stake on subject.
        assert _count_select(dao_b.unspent_outflows(wallet.address)) == 2
        assert dao_b.wallet_balance(wallet.address) == 2 * reward
        assert dao_b.opposition_balance(subject) == 0
        assert _count_select(
            dao_b.unrescinded_outflows(subject, 'opposition')
        ) == 0

        # chain_a values are unchanged by chain_b's existence.
        assert dao_a.wallet_balance(wallet.address) == reward
        assert dao_a.opposition_balance(subject) == cb_1_amount
```

- [ ] **Step 2: Run it — expect PASS on current code**

Run: `uv run pytest tests/test_models.py::test_antijoin_equivalence_all_methods -v`
Expected: PASS (the current anti-join already produces these values). If it fails, the fixture's spent/unspent expectations are wrong — fix the test, not the source.

- [ ] **Step 3: Commit**

```bash
git add tests/test_models.py
git commit -m "test(models): pin unspent/balance results across a fork (#165 guard)"
```

### Task 2: EXPLAIN perf test (the failing test that drives the rewrite)

**Files:**
- Test: `tests/test_models.py` (append a new test function)

Asserts the rewritten queries' plans contain no `MATERIALIZE` / `AUTOMATIC`.
This FAILS on the current code (which materializes) and is what the
implementation makes pass.

- [ ] **Step 1: Write the EXPLAIN test**

Append to `tests/test_models.py`:

```python
def _query_plan_text(stmt):
    """EXPLAIN QUERY PLAN for a Select, as one uppercased string.

    Compiles with literal binds so the SQL can be wrapped verbatim; SQLite's
    EXPLAIN QUERY PLAN returns rows whose last column is the human-readable
    `detail` (e.g. 'MATERIALIZE anon_3', 'SEARCH ... USING AUTOMATIC ...').
    """
    compiled = stmt.compile(
        db.engine, compile_kwargs={'literal_binds': True}
    )
    rows = db.session.execute(db.text(f'EXPLAIN QUERY PLAN {compiled}')).all()
    return ' '.join(str(col) for row in rows for col in row).upper()


def test_antijoin_no_materialization(app, subject, time_stepper, wallet):
    """The unspent/balance reads must not MATERIALIZE the whole-chain inflow
    set nor build a per-call AUTOMATIC index over it (#165). EXPLAIN QUERY
    PLAN over the Select-returning methods must show neither token. The two
    int-returning methods (wallet_balance / _stake_balance) share the exact
    same _unspent_clause(), so the Select-returners are sufficient witness.
    """
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, wallet, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()
        dao_a = chain_a.to_dao()
        assert dao_a is not None

        plans = [
            _query_plan_text(dao_a.unspent_outflows(wallet.address)),
            _query_plan_text(
                dao_a.unrescinded_outflows(subject, 'opposition')
            ),
            _query_plan_text(dao_a.wallet_leaderboard()),
            _query_plan_text(dao_a.subject_leaderboard()),
        ]
        for plan in plans:
            assert 'MATERIALIZE' not in plan, plan
            assert 'AUTOMATIC' not in plan, plan
```

- [ ] **Step 2: Run it — expect FAIL on current code**

Run: `uv run pytest tests/test_models.py::test_antijoin_no_materialization -v`
Expected: FAIL — at least one plan contains `MATERIALIZE` (the current `self.inflows.subquery()` is materialized) and/or `AUTOMATIC`. (Do **not** commit yet — this red test is fixed by Task 3.)

### Task 3: implement `_unspent_clause()` + rewrite the six methods

**Files:**
- Modify: `src/gumptionchain/models.py` (import + the six methods + the helper)

- [ ] **Step 1: Import `ColumnElement`**

In `src/gumptionchain/models.py`, extend the `from sqlalchemy import (...)` block (currently lines `:9`–`:19`) to include `ColumnElement`. The new block:

```python
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    DateTime,
    ForeignKey,
    Integer,
    Select,
    String,
    Text,
    false,
    or_,
)
```

- [ ] **Step 2: Add the `_unspent_clause()` helper**

In `ChainDAO`, immediately **above** `unspent_outflows` (currently `:758`), add:

```python
def _unspent_clause(self) -> ColumnElement[bool]:
    # An outflow is unspent iff no inflow in this chain consumes it.
    # Correlated NOT EXISTS: SQLite index-seeks ix_inflow_outflow_id per
    # candidate outflow (≈0–1 consuming inflows) and checks chain
    # membership only for those — instead of MATERIALIZE-ing the whole
    # inflow set + building a per-call AUTOMATIC COVERING INDEX over it.
    # self.inflows already routes longest-chain vs ancestry; .order_by(None)
    # strips its (irrelevant inside EXISTS) ordering.
    return ~(
        self.inflows.order_by(None)
        .where(InflowDAO.outflow_id == OutflowDAO.id)
        .exists()
    )
```

- [ ] **Step 3: Rewrite `unspent_outflows`**

Replace the current body (`:758`–`:769`) with:

```python
def unspent_outflows(
    self,
    address: str,
    filter_pending: bool = False,  # noqa: FBT001
) -> Select[tuple[OutflowDAO]]:
    stmt = self.outflows.where(OutflowDAO.address == address)
    stmt = stmt.where(self._unspent_clause())
    if filter_pending:
        stmt = stmt.where(~OutflowDAO.pending.any())
    return stmt
```

- [ ] **Step 4: Rewrite `wallet_balance`**

Replace the current body (`:771`–`:780`) with:

```python
def wallet_balance(self, address: str) -> int:
    stmt = self.outflows.where(OutflowDAO.address == address)
    stmt = stmt.where(self._unspent_clause())
    outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
    sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
        outflows_alias, OutflowDAO.id == outflows_alias.id
    )
    return db.session.scalar(sum_stmt) or 0
```

- [ ] **Step 5: Rewrite `unrescinded_outflows`**

Replace the current body (`:782`–`:802`) with:

```python
def unrescinded_outflows(
    self,
    subject: str,
    kind: StakeKind,
    address: str | None = None,
    filter_pending: bool = False,  # noqa: FBT001
) -> Select[tuple[OutflowDAO]]:
    column = (
        OutflowDAO.support if kind == 'support' else OutflowDAO.opposition
    )
    stmt = self.outflows.where(column == subject)
    stmt = stmt.where(self._unspent_clause())
    if address is not None:
        txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
        stmt = stmt.join(txn_alias, OutflowDAO.transaction)
        stmt = stmt.where(txn_alias.address == address)
    if filter_pending:
        stmt = stmt.where(~OutflowDAO.pending.any())
    return stmt
```

- [ ] **Step 6: Rewrite `_stake_balance`**

Replace the current body (`:804`–`:816`) with:

```python
def _stake_balance(self, subject: str, kind: StakeKind) -> int:
    column = (
        OutflowDAO.support if kind == 'support' else OutflowDAO.opposition
    )
    stmt = self.outflows.where(column == subject)
    stmt = stmt.where(self._unspent_clause())
    outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
    sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
        outflows_alias, OutflowDAO.id == outflows_alias.id
    )
    return db.session.scalar(sum_stmt) or 0
```

- [ ] **Step 7: Rewrite `wallet_leaderboard`**

Replace the current body (`:824`–`:849`) with (only the anti-join lines change; `txn_alias`, `group_by`, `order_by`, `limit` are preserved):

```python
def wallet_leaderboard(
    self,
    earliest: datetime.datetime | None = None,
    latest: datetime.datetime | None = None,
    limit: int | None = None,
) -> Select[Any]:
    txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
    stmt = db.select(
        OutflowDAO.address,
        db.func.sum(OutflowDAO.amount).label('ct'),
    )
    stmt = stmt.where(OutflowDAO.address.is_not(None))
    stmt = stmt.join(txn_alias, OutflowDAO.transaction)
    stmt = stmt.where(self._unspent_clause())
    if earliest is not None:
        stmt = stmt.where(txn_alias.timestamp >= earliest)
    if latest is not None:
        stmt = stmt.where(txn_alias.timestamp < latest)
    stmt = stmt.group_by(OutflowDAO.address)
    stmt = stmt.order_by(db.desc('ct'), OutflowDAO.address)
    if limit is not None:
        stmt = stmt.limit(limit)
        return db.select(db.aliased(stmt.subquery()))  # type: ignore[no-any-return]
    return stmt  # type: ignore[no-any-return]
```

- [ ] **Step 8: Rewrite `subject_leaderboard`'s `_leg`**

Replace the current body (`:851`–`:889`) — only the `inflows_alias` line and the `_leg` anti-join change; the UNION/`group_by`/`limit` scaffolding is preserved:

```python
def subject_leaderboard(
    self,
    limit: int | None = None,
) -> Select[Any]:
    def _leg(column: Any, kind: StakeKind) -> Select[Any]:
        stmt = self.outflows.where(column.is_not(None))
        stmt = stmt.where(self._unspent_clause())
        stmt = stmt.with_only_columns(
            column.label('subject'),
            OutflowDAO.amount.label('amount'),
            db.literal(kind).label('kind'),
        )
        # UNION legs must not carry their own ORDER BY (the
        # chain-scoped self.outflows select adds one); SQLite rejects
        # an ORDER BY inside a compound SELECT operand.
        return stmt.order_by(None)

    opp = _leg(OutflowDAO.opposition, 'opposition')
    sup = _leg(OutflowDAO.support, 'support')
    union = opp.union_all(sup).subquery()
    stmt = db.select(
        union.c.subject,
        db.func.sum(
            db.case((union.c.kind == 'opposition', union.c.amount), else_=0)
        ).label('opposition'),
        db.func.sum(
            db.case((union.c.kind == 'support', union.c.amount), else_=0)
        ).label('support'),
        db.func.sum(union.c.amount).label('total'),
    )
    stmt = stmt.group_by(union.c.subject)
    stmt = stmt.order_by(db.desc('total'), union.c.subject)
    if limit is not None:
        stmt = stmt.limit(limit)
        return db.select(db.aliased(stmt.subquery()))  # type: ignore[no-any-return]
    return stmt  # type: ignore[no-any-return]
```

Note: the `_unspent_clause()` correlates on `OutflowDAO.id`; inside `_leg` the
enclosing query still has `OutflowDAO` in its FROM (via `self.outflows`), so the
correlation resolves correctly even within the UNION leg.

- [ ] **Step 9: Run the two new tests — both PASS**

Run: `uv run pytest tests/test_models.py::test_antijoin_no_materialization tests/test_models.py::test_antijoin_equivalence_all_methods -v`
Expected: BOTH PASS — the EXPLAIN test now finds no `MATERIALIZE`/`AUTOMATIC`, and equivalence is unchanged.

If `test_antijoin_no_materialization` still shows `MATERIALIZE`: it will be the chain-**transactions** subquery (membership), not the inflow set — but the spec's target is specifically the inflow materialization. If a `MATERIALIZE` remains, inspect the failing `plan` string: confirm it is over `block_transaction`/`transaction` (membership, acceptable and shared) and **not** over `inflow`. If it is the transaction-membership subquery only, narrow the assertion to `assert 'inflow' not in plan or 'MATERIALIZE' not in plan` is **wrong** — instead assert the inflow table is reached by index seek: `assert 'IX_INFLOW_OUTFLOW_ID' in plan` for each plan, and drop the blanket `MATERIALIZE` assertion to `assert 'AUTOMATIC' not in plan`. Decide based on the actual EXPLAIN output and document the chosen assertion in a comment. (Expected on SQLite ≥3.8: the correlated EXISTS over an indexed `outflow_id` yields a `CORRELATED SCALAR SUBQUERY` with `SEARCH inflow USING INDEX ix_inflow_outflow_id` and no `MATERIALIZE`/`AUTOMATIC`.)

- [ ] **Step 10: Run the existing balance/reorg suite — no regressions**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS — `test_unspent_outflows`, the `test_longest_chain_block_*` reorg tests, and the leaderboard/stake tests are the equivalence safety net.

- [ ] **Step 11: Commit**

```bash
git add src/gumptionchain/models.py tests/test_models.py
git commit -m "feat(models): unspent/balance anti-join → correlated NOT EXISTS (#165)"
```

### Task 4: full gates + open PR

- [ ] **Step 1: Format, lint, type-check, full suite, db check**

```bash
uv run ruff format src tests
uv run ruff check src tests
uv run mypy
uv run pytest
uv run gumptionchain db check
```
Expected: ruff format leaves files unchanged (or restage if it reformats), ruff check clean, mypy clean (`_unspent_clause` is annotated `ColumnElement[bool]`), full suite green, `db check` reports the schema in sync (it is untouched).

- [ ] **Step 2: Commit any format restage (if ruff reformatted)**

```bash
git add -A && git commit -m "style: ruff format" --no-edit 2>/dev/null || true
```

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin feat/egu-165-not-exists-antijoin
gh pr create --fill --title "feat(models): unspent/balance anti-join → NOT EXISTS (#165)"
```
PR body: summarize the six rewritten methods, the shared helper, the two test guards (equivalence + EXPLAIN no-materialization), and "no schema change — leans on ix_inflow_outflow_id (#161)". How to test: `uv run pytest tests/test_models.py -k antijoin`.

---

## Self-Review (controller, after implementation)

- The new `longest()`/materialization tests and the oracle-fork tests still pass (equivalence).
- EXPLAIN assertion settled to the actual SQLite plan (Step 9 contingency resolved + commented).
- Final reviewer over the diff: focus the correlation correctness (`OutflowDAO.id` resolves to the enclosing query in every method, including the UNION legs and grouped leaderboards) and equivalence on the fork (longest vs ancestry routing).

## Out of scope / follow-ups

- `inflows_in_chain_count` (`:452`) — targeted single-outflow lookup, not the anti-join; untouched.
- #150 (app-layer N+1) — distinct sibling perf item.
- No index/migration change (enabling `ix_inflow_outflow_id` shipped in #161).
- After merge: tick #165 on the EGU checklist (#190).
