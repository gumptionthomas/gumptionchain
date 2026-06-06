# Fix the N+1 in the unspent/unrescinded outflow generators — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the N+1 in `Chain.unspent_outflows` / `unrescinded_outflows` / `unrescinded_address_outflows` by converting each matched `OutflowDAO` directly to a domain `Outflow`, instead of rebuilding the entire parent `Transaction` (≈3 lazy-load queries) per row just to extract one outflow.

**Architecture:** Add `Outflow.from_dao` / `Inflow.from_dao` classmethods (payload.py); make `Transaction.from_dao` reuse them (DRY); replace the per-row `Transaction.from_dao(outflow_dao.transaction).get_outflow(idx)` round-trip in the three generators with `Outflow.from_dao(outflow_dao)`. Behavior-identical, zero extra queries per row. Guarded by a query-count regression test.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0, pytest, uv, ruff (line-length 80, single quotes), mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-05-fix-unrescinded-outflows-n-plus-1-design.md` (issue #150)

---

## File map

| File | Change |
|---|---|
| `src/gumptionchain/payload.py` | Add `Outflow.from_dao` and `Inflow.from_dao` classmethods (+ `Any`, `cast` to the typing import). |
| `src/gumptionchain/transaction.py` | Refactor `Transaction.from_dao` to build inflows/outflows via the new converters (DRY, no behavior change). |
| `src/gumptionchain/chain.py` | Replace the round-trip in `unspent_outflows`, `unrescinded_outflows`, `unrescinded_address_outflows` with `Outflow.from_dao(outflow_dao)`; drop the dead `None` guard. |
| `tests/test_chain.py` (or a new `tests/test_n_plus_1.py`) | Query-count regression test + converter-equivalence test. |

No schema, no consensus change, no public return-shape change.

---

## Background the implementer needs

The conversion `OutflowDAO → Outflow` is the flat copy already inlined in
`Transaction.from_dao` (`transaction.py:348-360`):
```python
Outflow(
    amount=outflow_dao.amount,
    address=outflow_dao.address,
    opposition=outflow_dao.opposition,
    rescind=outflow_dao.rescind,
    support=outflow_dao.support,
    rescind_kind=cast('StakeKind | None', outflow_dao.rescind_kind),
)
```
and `Inflow` (`transaction.py:341-347`):
```python
Inflow(outflow_txid=inflow_dao.outflow_txid, outflow_idx=inflow_dao.outflow_idx)
```
`StakeKind = Literal['opposition', 'support']` is defined in `payload.py:77`
(in-module). `payload.py` currently imports `from typing import Annotated,
Literal, Self` — add `Any` and `cast`. `Outflow` / `Inflow` are `@dataclass`es
(payload.py:115, 137). `chain.py` already imports `Outflow` (`chain.py:38`), so
the generators need no new import.

The three generators (`chain.py:447-512`) share this loop body — the waste:
```python
for outflow_dao in outflow_daos:
    txn = Transaction.from_dao(outflow_dao.transaction)  # lazy-load txn + its inflows + outflows
    index = outflow_dao.idx
    outflow = txn.get_outflow(index=index)
    if outflow is None:
        continue
    ...
    yield (outflow_dao.txid, outflow_dao.idx, outflow)
```
`get_outflow(index)` is `self.outflows[index]` over an idx-ordered list, so it
returns exactly the `outflow_dao` already in hand — the round-trip is equivalent
to `Outflow.from_dao(outflow_dao)`, and the `None` guard is unreachable for a
present dao.

Test fixtures: `add_chain_block(chain=None, block=None, milling_wallet=None)` →
`(chain, block)` adds a milled block; `mill_block(wallet)` → `(miller, block)`.
Stake-spend pattern (from `tests/test_models.py`): build a `Transaction`,
`add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))`,
`add_outflow(Outflow(amount=cb_amount, opposition=subject))`, `set_wallet(wallet)`,
`seal()`, `sign()`, `to_db()`, then `block.add_txn(spend)` and add the block.
The `subject` fixture exists. A stake's matching address (for
`unrescinded_address_outflows(address, ...)`) is the spend transaction's address
(set via `set_wallet`), not the outflow's.

---

## Task 1: Add the direct converters; make `Transaction.from_dao` reuse them

**Files:**
- Modify: `src/gumptionchain/payload.py`, `src/gumptionchain/transaction.py`
- Test: `tests/test_chain.py` (or new `tests/test_n_plus_1.py`)

- [ ] **Step 1: Write the converter-equivalence test (failing)**

Add to the test module. It asserts `Outflow.from_dao` produces the SAME domain
object the old `Transaction.from_dao(...).get_outflow(...)` path produced, for a
real outflow on a built chain.

```python
def test_outflow_from_dao_matches_transaction_roundtrip(
    app, add_chain_block, time_stepper, wallet, subject
):
    import datetime

    from gumptionchain.models import OutflowDAO, TransactionDAO
    from gumptionchain.payload import Inflow, Outflow
    from gumptionchain.transaction import Transaction

    with app.app_context():
        time_step = time_stepper(
            start=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(hours=1)
        )
        _ = next(time_step)
        chain, block1 = add_chain_block(milling_wallet=wallet)
        cb = block1.coinbase
        cb_amount = next(iter(cb.outflows)).amount
        _ = next(time_step)
        spend = Transaction()
        spend.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        spend.add_outflow(Outflow(amount=cb_amount, opposition=subject))
        spend.set_wallet(wallet)
        spend.seal()
        spend.sign()
        spend.to_db()
        from gumptionchain.block import Block

        block2 = Block()
        block2.add_txn(spend)
        _ = next(time_step)
        add_chain_block(chain=chain, block=block2, milling_wallet=wallet)

        txn_dao = TransactionDAO.get(spend.txid)
        assert txn_dao is not None
        outflow_dao = next(
            o for o in txn_dao.outflows if o.opposition == subject
        )

        # new direct converter
        direct = Outflow.from_dao(outflow_dao)
        # old round-trip path
        roundtrip = Transaction.from_dao(outflow_dao.transaction).get_outflow(
            index=outflow_dao.idx
        )
        assert direct == roundtrip
        assert direct.opposition == subject
        assert direct.amount == cb_amount
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_chain.py::test_outflow_from_dao_matches_transaction_roundtrip -q`
(or the path you chose)
Expected: FAIL — `Outflow.from_dao` does not exist yet (`AttributeError`).

- [ ] **Step 3: Add the converters to `payload.py`**

Add `Any` and `cast` to the typing import:
```python
from typing import Annotated, Any, Literal, Self, cast
```
Add a classmethod to the `Outflow` dataclass:
```python
@classmethod
def from_dao(cls, dao: Any) -> Self:
    return cls(
        amount=dao.amount,
        address=dao.address,
        opposition=dao.opposition,
        rescind=dao.rescind,
        support=dao.support,
        rescind_kind=cast('StakeKind | None', dao.rescind_kind),
    )
```
and to the `Inflow` dataclass:
```python
@classmethod
def from_dao(cls, dao: Any) -> Self:
    return cls(
        outflow_txid=dao.outflow_txid,
        outflow_idx=dao.outflow_idx,
    )
```

- [ ] **Step 4: Refactor `Transaction.from_dao` to reuse them (DRY)**

In `transaction.py`, replace the inline `inflows=[...]` / `outflows=[...]`
comprehensions (lines 341-360) with:
```python
            inflows=[
                Inflow.from_dao(inflow_dao) for inflow_dao in dao.inflows
            ],
            outflows=[
                Outflow.from_dao(outflow_dao) for outflow_dao in dao.outflows
            ],
```
Leave the rest of `from_dao` unchanged. The `cast` import in transaction.py may
become unused after this — remove it if ruff flags it (and confirm nothing else
in the file uses `cast`).

- [ ] **Step 5: Run the equivalence test + full suite, expect PASS**

Run: `uv run pytest tests/test_chain.py::test_outflow_from_dao_matches_transaction_roundtrip -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: green — `Transaction.from_dao` behavior is unchanged (the existing
serialization/round-trip tests cover it).

- [ ] **Step 6: Lint, format, types**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green. (Watch for an unused `cast` in transaction.py.)

- [ ] **Step 7: Commit**

```bash
git add src/gumptionchain/payload.py src/gumptionchain/transaction.py tests/
git commit -m "$(cat <<'EOF'
refactor(payload): add Outflow.from_dao / Inflow.from_dao; reuse in Transaction.from_dao (#150)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Convert the three generators; add the N+1 regression guard

**Files:**
- Modify: `src/gumptionchain/chain.py`
- Test: `tests/test_chain.py` (or the new test module)

- [ ] **Step 1: Write the N+1 query-count regression test (failing)**

Add a query-count helper (mirrors the `before_cursor_execute` pattern from
`tests/test_query_plans.py`) and a test that builds a chain with **3
distinct-transaction** unrescinded opposition stakes for one subject/address,
then asserts that draining `unrescinded_address_outflows` issues a small
**constant** number of SELECTs — not ≈3 per matched outflow.

```python
def _count_selects(fn):
    from sqlalchemy import event

    from gumptionchain.database import db

    bind = db.session.get_bind()
    count = 0

    def _rec(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        if statement.lstrip().upper().startswith('SELECT'):
            count += 1

    event.listen(bind, 'before_cursor_execute', _rec)
    try:
        fn()
    finally:
        event.remove(bind, 'before_cursor_execute', _rec)
    return count


def _build_chain_with_stakes(add_chain_block, time_stepper, wallet, subject, n):
    """Build a canonical chain where n distinct transactions each spend the
    previous block's coinbase into one opposition stake on `subject`. Returns
    the Chain. Each stake is in its own block/transaction so the pre-fix N+1
    issues a fresh lazy-load per matched outflow.
    """
    import datetime

    from gumptionchain.block import Block
    from gumptionchain.payload import Inflow, Outflow
    from gumptionchain.transaction import Transaction

    time_step = time_stepper(
        start=datetime.datetime.now(datetime.UTC)
        - datetime.timedelta(hours=2)
    )
    _ = next(time_step)
    chain, prev_block = add_chain_block(milling_wallet=wallet)
    for _i in range(n):
        cb = prev_block.coinbase
        cb_amount = next(iter(cb.outflows)).amount
        _ = next(time_step)
        spend = Transaction()
        spend.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        spend.add_outflow(Outflow(amount=cb_amount, opposition=subject))
        spend.set_wallet(wallet)
        spend.seal()
        spend.sign()
        spend.to_db()
        block = Block()
        block.add_txn(spend)
        _ = next(time_step)
        chain, prev_block = add_chain_block(
            chain=chain, block=block, milling_wallet=wallet
        )
    chain.to_db()
    return chain


def test_unrescinded_address_outflows_no_n_plus_1(
    app, add_chain_block, time_stepper, wallet, subject
):
    """Draining the generator must issue a constant number of SELECTs,
    independent of the matched-outflow count (no per-row Transaction
    reconstruction).
    """
    with app.app_context():
        chain = _build_chain_with_stakes(
            add_chain_block, time_stepper, wallet, subject, n=3
        )

        def _drain():
            return list(
                chain.unrescinded_address_outflows(
                    wallet.address, subject, 'opposition'
                )
            )

        # Sanity: all 3 stakes are matched.
        assert len(_drain()) == 3
        # No N+1: the pre-fix path issued ~3 lazy-loads per matched outflow
        # (>= ~10 SELECTs for 3 stakes); the direct convert keeps it constant.
        assert _count_selects(_drain) <= 5
```

Note: confirm the matched count is 3 (the `assert len(...) == 3`). If the build
yields a different count (e.g. coinbase indexing differs), adjust the builder so
exactly `n` distinct-transaction stakes match, then keep the bound at `<= 5`
(comfortably below the pre-fix `~3*n + overhead`). Do NOT relax the bound to make
a still-N+1 implementation pass — the bound must FAIL on the pre-fix code.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_chain.py::test_unrescinded_address_outflows_no_n_plus_1 -q`
Expected: FAIL on the `<= 5` assertion — the current generator rebuilds a
Transaction per matched outflow (≈10+ SELECTs for 3 stakes). (The `len == 3`
sanity assert should already pass.)

- [ ] **Step 3: Convert the three generators**

In `src/gumptionchain/chain.py`, in each of `unspent_outflows`,
`unrescinded_outflows`, `unrescinded_address_outflows`, replace the loop body's
round-trip. The `unspent_outflows` loop becomes:
```python
        for outflow_dao in outflow_daos:
            outflow = Outflow.from_dao(outflow_dao)
            amount += outflow.amount or 0
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
            if limit is not None and amount >= limit:
                break
```
`unrescinded_outflows` (no amount/limit):
```python
        for outflow_dao in outflow_daos:
            outflow = Outflow.from_dao(outflow_dao)
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
```
`unrescinded_address_outflows` (amount/limit, like `unspent_outflows`):
```python
        for outflow_dao in outflow_daos:
            outflow = Outflow.from_dao(outflow_dao)
            amount += outflow.amount or 0
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
            if limit is not None and amount >= limit:
                break
```
Each drops the `txn = Transaction.from_dao(...)`, `index = outflow_dao.idx`,
`outflow = txn.get_outflow(...)`, and the `if outflow is None: continue` guard.
If `Transaction` is no longer referenced anywhere else in `chain.py`, leave the
import (it is used elsewhere — `CoinbaseMetrics`, milling); do NOT remove it
without grepping.

- [ ] **Step 4: Run the regression test + full suite, expect PASS**

Run: `uv run pytest tests/test_chain.py::test_unrescinded_address_outflows_no_n_plus_1 -q`
Expected: PASS (`<= 5` SELECTs).
Run: `uv run pytest -q`
Expected: green — the yielded `(txid, idx, Outflow)` tuples are identical, so the
existing rescind/transfer/stake/balance tests pass unchanged.

- [ ] **Step 5: Lint, format, types, db check**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green.
db check (no schema change, but confirm): `FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////tmp/_dbck.db uv run gumptionchain db upgrade && FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////tmp/_dbck.db uv run gumptionchain db check && rm -f /tmp/_dbck.db`
Expected: no drift.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/chain.py tests/
git commit -m "$(cat <<'EOF'
perf(chain): convert outflow_dao directly — kill the unrescinded/unspent N+1 (#150)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** `Outflow.from_dao`/`Inflow.from_dao` added (Task 1);
  `Transaction.from_dao` reuses them (Task 1); all three generators convert
  directly with the dead `None` guard removed (Task 2); query-count regression
  guard + converter equivalence (Tasks 1-2).
- **Behavior preservation:** the equivalence test asserts the direct convert ==
  the old round-trip output; the yield tuple shape and amount/limit logic are
  unchanged; the existing suite is the broad guard.
- **Type consistency:** `from_dao(cls, dao: Any) -> Self` on both dataclasses;
  `cast('StakeKind | None', ...)` preserved; `cast` moves from transaction.py to
  payload.py (remove the now-unused transaction.py `cast` if ruff flags it).
- **No schema/consensus change.**

## Definition of done

- `Outflow.from_dao` / `Inflow.from_dao` exist; `Transaction.from_dao` uses them;
  the three generators no longer reconstruct a `Transaction` per matched outflow
  and have no dead `None` guard.
- The query-count test proves draining the generator issues O(1) SELECTs, not
  O(matched-outflows); it fails on the pre-fix code.
- Converter-equivalence test passes; existing rescind/transfer/stake/balance
  tests pass unchanged.
- Full suite + ruff + ruff-format + mypy green; `db check` no drift.
