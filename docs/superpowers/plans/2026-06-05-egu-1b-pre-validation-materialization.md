# EGU 1b-pre — materialize the consensus-validation hot path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the recursive `BlockDAO._block_chain` CTE from the three consensus-validation hot-path lookups (`get_transaction_in_chain`, `inflows_in_chain_count`, `get_block_in_chain`), making block validation O(reorg-depth + result) instead of O(chain-height) — on both the canonical chain and forks.

**Architecture:** A new `BlockDAO._ancestry()` helper resolves a block's ancestry against the `LongestChainBlockDAO` materialization without recursion: it returns the ids of the short divergent suffix (blocks not yet materialized, walked via indexed `prev` links) plus the position of the common ancestor. The three methods answer queries as (divergent id-set query) + (position-scoped indexed query). Canonical anchors take zero divergent steps. The recursive CTE properties remain defined (still used by Tier-2 read paths) but are no longer reached by these three methods; their deletion is the capstone follow-up #158.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0 (`Mapped[]`), pytest, uv, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-05-egu-1b-pre-validation-materialization-design.md` (issue #157)

---

## File map

| File | Change |
|---|---|
| `src/gumptionchain/models.py` | Add `BlockDAO._ancestry`; rewrite `BlockDAO.get_transaction_in_chain`, `inflows_in_chain_count`, `get_block_in_chain` to use `_ancestry` (no `_block_chain` reference). |
| `tests/test_models.py` | CTE-guard test (canonical + fork), canonical/fork/bootstrap equivalence tests. |

No `chain.py` change — `Chain.get_transaction` / `get_inflows_count` / `get_block_by_reverse_index` call these BlockDAO methods, which now route internally. No schema/migration; `db check` unaffected.

---

## Background the implementer needs

These three `BlockDAO` methods (in `src/gumptionchain/models.py`) currently each
build on the recursive `_block_chain` CTE. They are called from
`Chain.validate_block` on **every** block validated — once per block
(`get_block_in_chain`, via the difficulty-retarget lookup) and twice per inflow
(`get_transaction_in_chain` + `inflows_in_chain_count`, via `validate_txn_inflow`
in `chain.py`). Each CTE call walks tip→genesis = O(chain-height), so validation
is O(inflows × height). This plan replaces that with bounded ancestry resolution.

**Current bodies (for reference — you are replacing these):**

```python
def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
    return db.session.execute(
        self.transactions_chain.where(TransactionDAO.txid == txid)
    ).scalar_one_or_none()

def get_block_in_chain(
    self, block_hash: str | None = None, idx: int | None = None
) -> BlockDAO | None:
    block_alias = db.aliased(BlockDAO, self.block_chain.subquery())
    stmt = db.select(BlockDAO).join(
        block_alias, BlockDAO.id == block_alias.id
    )
    if block_hash is not None:
        stmt = stmt.where(BlockDAO.block_hash == block_hash)
    if idx is not None:
        stmt = stmt.where(BlockDAO.idx == idx)
    return db.session.execute(stmt).scalar_one_or_none()

def inflows_in_chain_count(
    self, outflow_txid: str, outflow_idx: int
) -> int:
    stmt = self.inflows_chain.where(
        InflowDAO.outflow_txid == outflow_txid,
        InflowDAO.outflow_idx == outflow_idx,
    )
    return (
        1 if db.session.execute(stmt).scalars().first() is not None else 0
    )
```

Note `inflows_in_chain_count` returns **0/1** (existence), not a true count —
preserve that exactly. `LongestChainBlockDAO`, `TransactionDAO`, `InflowDAO` are
all defined in the same module; no new imports needed.

Useful existing test fixtures (in `tests/conftest.py`):
- `mill_block(wallet)` → `(miller, block)`: builds, mills, and adds a canonical block.
- `add_chain_block(chain=None, block=None, milling_wallet=None)` → `(chain, block)`: links, seals (computing coinbase metrics), mills, and adds a block to a chain.
- Spend pattern (from `tests/test_chain.py::test_db`): build a `Transaction`, `add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))`, `add_outflow(...)`, `set_wallet(wallet)`, `seal()`, `sign()`, `to_db()`, then `block.add_txn(t)` and add the block.
- Fork pattern (from `tests/test_models.py::test_non_longest_chain_blocks_uses_cte`): two blocks sharing a parent; add one to its chain, the other via a second `Chain`, producing a non-longest `ChainDAO`/fork block.

---

## Task 1: `_ancestry` helper + convert the three methods

**Files:**
- Modify: `src/gumptionchain/models.py` (`BlockDAO`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing CTE-guard test**

Add to `tests/test_models.py`. The imports `from unittest.mock import patch` and the model imports already exist at the top of the file; add `PropertyMock` to the mock import line so it reads `from unittest.mock import PropertyMock, patch`. Also confirm `Wallet` is importable — add `from gumptionchain.wallet import Wallet` if not already imported.

```python
def _build_canonical_chain_with_spend(add_chain_block, time_stepper, wallet):
    """Build a 2-block canonical chain where block 2 contains a txn that
    spends block 1's coinbase. Returns (chain, block1, block2, spend_txid)."""
    import datetime

    from gumptionchain.payload import Inflow, Outflow
    from gumptionchain.transaction import Transaction

    time_step = time_stepper(
        start=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    )
    _ = next(time_step)
    chain, block1 = add_chain_block(milling_wallet=wallet)
    cb = block1.coinbase
    cb_amount = next(iter(cb.outflows)).amount
    _ = next(time_step)
    t = Transaction()
    t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
    t.add_outflow(Outflow(amount=cb_amount, address=wallet.address))
    t.set_wallet(wallet)
    t.seal()
    t.sign()
    t.to_db()
    _ = next(time_step)
    block2 = Block()
    block2.add_txn(t)
    _, block2 = add_chain_block(
        chain=chain, block=block2, milling_wallet=wallet
    )
    chain.to_db()
    return chain, block1, block2, t.txid


def test_hot_path_methods_never_touch_recursive_cte(
    app, add_chain_block, time_stepper, wallet
):
    """get_transaction_in_chain / inflows_in_chain_count / get_block_in_chain
    must not access the recursive _block_chain CTE on canonical OR fork
    anchors. Booby-trap _block_chain to raise; the three methods must still
    return correct results.
    """
    with app.app_context():
        chain, block1, block2, spend_txid = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        cb1_txid = block1.coinbase.txid
        tip_dao = BlockDAO.get(block2.block_hash)
        genesis_dao = BlockDAO.get(block1.block_hash)
        assert tip_dao is not None
        assert genesis_dao is not None

        with patch.object(
            BlockDAO, '_block_chain', new_callable=PropertyMock
        ) as cte_mock:
            cte_mock.side_effect = AssertionError(
                'hot-path method accessed the recursive CTE'
            )
            # canonical anchor (the tip)
            assert (
                tip_dao.get_transaction_in_chain(spend_txid) is not None
            )
            assert tip_dao.get_transaction_in_chain('does-not-exist') is None
            # the spend consumed block1's coinbase outflow → counted once
            assert tip_dao.inflows_in_chain_count(cb1_txid, 0) == 1
            assert tip_dao.inflows_in_chain_count('nope', 0) == 0
            assert (
                tip_dao.get_block_in_chain(block_hash=block1.block_hash)
                is not None
            )
            assert tip_dao.get_block_in_chain(idx=0) is not None
            # an ancestor anchor still resolves its own ancestry
            assert (
                genesis_dao.get_transaction_in_chain(cb1_txid) is not None
            )
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_models.py::test_hot_path_methods_never_touch_recursive_cte -q`
Expected: FAIL — the current methods build on `_block_chain`, so accessing the booby-trapped property raises `AssertionError`.

- [ ] **Step 3: Add the `_ancestry` helper**

In `src/gumptionchain/models.py`, inside `BlockDAO`, add this method (place it just above `get_transaction_in_chain`):

```python
def _ancestry(self) -> tuple[list[int], int | None]:
    """Resolve this block's ancestry against LongestChainBlockDAO without
    recursion.

    Returns (divergent_ids, cap_position):
    - divergent_ids: ids of blocks on the divergent suffix (not in the
      materialization), nearest-first. Empty when this block is canonical.
    - cap_position: position of the common ancestor in the materialization;
      the canonical prefix is everything with position <= cap_position.
      None only when the materialization is empty (bootstrap), in which
      case divergent_ids covers the whole walked chain.

    Cost: O(divergent-suffix length) indexed `prev` lookups — 0 extra for a
    canonical anchor (first lookup hits), reorg-depth for a fork. Never
    O(chain-height) except transient bootstrap.
    """
    divergent: list[int] = []
    current: BlockDAO | None = self
    while current is not None:
        position = db.session.scalar(
            db.select(LongestChainBlockDAO.position).where(
                LongestChainBlockDAO.block_id == current.id
            )
        )
        if position is not None:
            return divergent, position
        divergent.append(current.id)
        current = current.prev
    return divergent, None
```

- [ ] **Step 4: Rewrite `get_transaction_in_chain`**

Replace the current body with:

```python
def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
    divergent, cap = self._ancestry()
    if divergent:
        hit = (
            db.session.execute(
                db.select(TransactionDAO)
                .join(TransactionDAO.blocks)
                .where(BlockDAO.id.in_(divergent))
                .where(TransactionDAO.txid == txid)
            )
            .scalars()
            .first()
        )
        if hit is not None:
            return hit
    if cap is not None:
        return db.session.execute(
            db.select(TransactionDAO)
            .join(TransactionDAO.blocks)
            .join(
                LongestChainBlockDAO,
                LongestChainBlockDAO.block_id == BlockDAO.id,
            )
            .where(LongestChainBlockDAO.position <= cap)
            .where(TransactionDAO.txid == txid)
        ).scalar_one_or_none()
    return None
```

- [ ] **Step 5: Rewrite `inflows_in_chain_count`**

Replace the current body with (preserving the 0/1 existence semantics):

```python
def inflows_in_chain_count(
    self, outflow_txid: str, outflow_idx: int
) -> int:
    divergent, cap = self._ancestry()
    if divergent:
        hit = (
            db.session.execute(
                db.select(InflowDAO)
                .join(InflowDAO.transaction)
                .join(TransactionDAO.blocks)
                .where(BlockDAO.id.in_(divergent))
                .where(InflowDAO.outflow_txid == outflow_txid)
                .where(InflowDAO.outflow_idx == outflow_idx)
            )
            .scalars()
            .first()
        )
        if hit is not None:
            return 1
    if cap is not None:
        hit = (
            db.session.execute(
                db.select(InflowDAO)
                .join(InflowDAO.transaction)
                .join(TransactionDAO.blocks)
                .join(
                    LongestChainBlockDAO,
                    LongestChainBlockDAO.block_id == BlockDAO.id,
                )
                .where(LongestChainBlockDAO.position <= cap)
                .where(InflowDAO.outflow_txid == outflow_txid)
                .where(InflowDAO.outflow_idx == outflow_idx)
            )
            .scalars()
            .first()
        )
        if hit is not None:
            return 1
    return 0
```

- [ ] **Step 6: Rewrite `get_block_in_chain`**

Replace the current body with:

```python
def get_block_in_chain(
    self, block_hash: str | None = None, idx: int | None = None
) -> BlockDAO | None:
    divergent, cap = self._ancestry()
    if divergent:
        stmt = db.select(BlockDAO).where(BlockDAO.id.in_(divergent))
        if block_hash is not None:
            stmt = stmt.where(BlockDAO.block_hash == block_hash)
        if idx is not None:
            stmt = stmt.where(BlockDAO.idx == idx)
        hit = db.session.execute(stmt).scalars().first()
        if hit is not None:
            return hit
    if cap is not None:
        stmt = (
            db.select(BlockDAO)
            .join(
                LongestChainBlockDAO,
                LongestChainBlockDAO.block_id == BlockDAO.id,
            )
            .where(LongestChainBlockDAO.position <= cap)
        )
        if block_hash is not None:
            stmt = stmt.where(BlockDAO.block_hash == block_hash)
        if idx is not None:
            stmt = stmt.where(BlockDAO.idx == idx)
        return db.session.execute(stmt).scalar_one_or_none()
    return None
```

- [ ] **Step 7: Run the guard test, expect PASS**

Run: `uv run pytest tests/test_models.py::test_hot_path_methods_never_touch_recursive_cte -q`
Expected: PASS — none of the three methods touches `_block_chain` now.

- [ ] **Step 8: Commit**

```bash
git add src/gumptionchain/models.py tests/test_models.py
git commit -m "perf(validation): resolve ancestry via materialization, not recursive CTE (#157)"
```

---

## Task 2: Equivalence + fork + bootstrap coverage

**Files:**
- Test: `tests/test_models.py`

- [ ] **Step 1: Write canonical equivalence test**

Add to `tests/test_models.py`. Ground truth is the still-defined recursive CTE
(`block.transactions_chain` / `block_chain` / `inflows_chain`); the new methods
must agree with it on the canonical chain.

```python
def test_hot_path_methods_match_cte_canonical(
    app, add_chain_block, time_stepper, wallet
):
    with app.app_context():
        chain, block1, block2, spend_txid = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        cb1_txid = block1.coinbase.txid
        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None

        # get_transaction_in_chain matches the CTE (hit + miss)
        for txid in (spend_txid, cb1_txid, 'missing'):
            cte = db.session.execute(
                tip.transactions_chain.where(TransactionDAO.txid == txid)
            ).scalar_one_or_none()
            new = tip.get_transaction_in_chain(txid)
            assert (new.id if new else None) == (cte.id if cte else None)

        # inflows_in_chain_count matches the CTE existence (0/1)
        for otxid, oidx in ((cb1_txid, 0), ('missing', 0)):
            cte_exists = (
                1
                if db.session.execute(
                    tip.inflows_chain.where(
                        InflowDAO.outflow_txid == otxid,
                        InflowDAO.outflow_idx == oidx,
                    )
                )
                .scalars()
                .first()
                is not None
                else 0
            )
            assert tip.inflows_in_chain_count(otxid, oidx) == cte_exists

        # get_block_in_chain matches the CTE (by hash and by idx)
        for kwargs in (
            {'block_hash': block1.block_hash},
            {'idx': 0},
            {'idx': 1},
            {'block_hash': 'missing'},
        ):
            cte_block = _cte_get_block_in_chain(tip, **kwargs)
            new_block = tip.get_block_in_chain(**kwargs)
            assert (new_block.id if new_block else None) == (
                cte_block.id if cte_block else None
            )


def _cte_get_block_in_chain(block_dao, block_hash=None, idx=None):
    """Ground-truth get_block_in_chain via the recursive CTE."""
    block_alias = db.aliased(BlockDAO, block_dao.block_chain.subquery())
    stmt = db.select(BlockDAO).join(
        block_alias, BlockDAO.id == block_alias.id
    )
    if block_hash is not None:
        stmt = stmt.where(BlockDAO.block_hash == block_hash)
    if idx is not None:
        stmt = stmt.where(BlockDAO.idx == idx)
    return db.session.execute(stmt).scalar_one_or_none()
```

- [ ] **Step 2: Write fork equivalence test**

```python
def test_hot_path_methods_match_cte_fork(app, time_stepper, wallet):
    """A fork (non-longest) block resolves its divergent-suffix ancestry the
    same as the recursive CTE would.
    """
    import datetime

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

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, wallet, CoinbaseMetrics())
        block_2a.mill()
        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, wallet, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()
        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()

        fork = BlockDAO.get(block_2b.block_hash)
        assert fork is not None
        # confirm it is genuinely a fork: not in the materialization
        assert fork._ancestry()[0]  # non-empty divergent suffix

        fork_cb_txid = block_2b.coinbase.txid  # only in the divergent suffix
        ancestor_cb_txid = block_1.coinbase.txid  # below the common ancestor
        for txid in (fork_cb_txid, ancestor_cb_txid, 'missing'):
            cte = db.session.execute(
                fork.transactions_chain.where(TransactionDAO.txid == txid)
            ).scalar_one_or_none()
            new = fork.get_transaction_in_chain(txid)
            assert (new.id if new else None) == (cte.id if cte else None)

        # block lookup across the fork boundary
        for kwargs in (
            {'block_hash': block_2b.block_hash},
            {'block_hash': block_1.block_hash},
            {'idx': 0},
        ):
            cte_block = _cte_get_block_in_chain(fork, **kwargs)
            new_block = fork.get_block_in_chain(**kwargs)
            assert (new_block.id if new_block else None) == (
                cte_block.id if cte_block else None
            )
```

- [ ] **Step 3: Write bootstrap (empty materialization) test**

```python
def test_hot_path_methods_match_cte_empty_materialization(
    app, add_chain_block, time_stepper, wallet
):
    """With an empty LongestChainBlockDAO (bootstrap), _ancestry walks the
    whole chain into divergent_ids and the methods still match the CTE.
    """
    with app.app_context():
        chain, block1, block2, spend_txid = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        db.session.execute(db.delete(LongestChainBlockDAO))
        db.session.commit()
        assert _count(LongestChainBlockDAO) == 0

        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        divergent, cap = tip._ancestry()
        assert cap is None
        assert len(divergent) == 2  # whole chain is "divergent"

        for txid in (spend_txid, block1.coinbase.txid, 'missing'):
            cte = db.session.execute(
                tip.transactions_chain.where(TransactionDAO.txid == txid)
            ).scalar_one_or_none()
            new = tip.get_transaction_in_chain(txid)
            assert (new.id if new else None) == (cte.id if cte else None)
        assert tip.get_block_in_chain(idx=0) is not None
        assert tip.inflows_in_chain_count(block1.coinbase.txid, 0) == 1
```

- [ ] **Step 4: Run the new tests, expect PASS**

Run: `uv run pytest tests/test_models.py -k "hot_path_methods" -q`
Expected: PASS (4 tests: guard, canonical, fork, bootstrap).

- [ ] **Step 5: Full suite + lint + format + types**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all green. The existing chain/validation/miller tests must still pass unchanged — the three methods' behavior is preserved, only their implementation changed.

- [ ] **Step 6: Confirm `db check` is unaffected (no schema drift)**

Run (requires a DB URI; mirrors the CI gate):
```bash
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///_dbcheck.db uv run gumptionchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///_dbcheck.db uv run gumptionchain db check
rm -f _dbcheck.db
```
Expected: `db check` reports no differences (this change adds no columns/tables).

- [ ] **Step 7: Commit**

```bash
git add tests/test_models.py
git commit -m "test(validation): equivalence + bootstrap coverage for materialized ancestry (#157)"
```

---

## Self-review notes

- **Spec coverage:** all three call sites converted (Task 1); CTE-free fork fallback via `_ancestry` (Task 1); equivalence + CTE-guard over canonical *and* fork + bootstrap (Tasks 1–2). Tier-2 read paths + CTE deletion are explicitly #158 (out of scope here).
- **Behavior preservation:** `inflows_in_chain_count` keeps 0/1 existence semantics; `get_block_in_chain` keeps `block_hash`/`idx` filters and `scalar_one_or_none`; `get_transaction_in_chain` keeps single-result semantics. Canonical-chain uniqueness guarantees ≤1 row on the `cap` branch.
- **Type consistency:** `_ancestry` → `tuple[list[int], int | None]`; the three methods keep their existing signatures and return types. `LongestChainBlockDAO`/`TransactionDAO`/`InflowDAO` are in-module; no new imports in `models.py`.
- **No schema/migration** — `db check` step confirms.

## Definition of done

- `_ancestry` added; the three hot-path methods resolve ancestry via materialization (canonical) + divergent-suffix (fork), with **no** `_block_chain` reference.
- CTE-guard test passes on canonical and fork anchors (recursive CTE never accessed by the three methods).
- Canonical, fork, and bootstrap equivalence tests pass (results bit-identical to the CTE).
- Full suite + ruff + ruff-format + mypy green; `db check` shows no drift.
- Recursive CTE properties left in place for #158 to delete.
