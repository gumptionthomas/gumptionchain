# EGU 1b-pre capstone — delete the recursive `_block_chain` CTE — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the remaining Tier-2 read-path consumers of the recursive `BlockDAO._block_chain` CTE (the non-longest `ChainDAO` accessors and `address_transactions`) to the divergent-suffix + position-scoped primitive from #157, then delete the recursive CTE and its `*_chain` builders entirely — zero recursive-CTE code in the tree.

**Architecture:** Add four CTE-free query builders on `BlockDAO` (`ancestry_blocks_q` / `ancestry_transactions_q` / `ancestry_outflows_q` / `ancestry_inflows_q`) that express a block's ancestry as a composable `Select` using a divergent-suffix-OR-materialized-prefix predicate over `LongestChainBlockDAO`. Repoint the non-longest branch of the `ChainDAO` read accessors and `address_transactions` at these. Then delete `BlockDAO._block_chain`, the four `*_chain` properties, and the three classmethod `*_chain` builders. Read results stay bit-identical; the test oracle moves from the CTE to a pure-Python `prev`-walk.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0 (`Mapped[]`), pytest, uv, ruff (line-length 80, single quotes), mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-05-egu-1b-pre-capstone-delete-recursive-cte-design.md` (issue #158)

---

## File map

| File | Change |
|---|---|
| `src/gumptionchain/models.py` | Add `ancestry_blocks_q` / `ancestry_transactions_q` / `ancestry_outflows_q` / `ancestry_inflows_q` to `BlockDAO`. Rewrite `BlockDAO.address_transactions`. Repoint the non-longest branch of `ChainDAO.{blocks,transactions,outflows,inflows}`. Then delete `BlockDAO._block_chain`, `.block_chain`, `.transactions_chain`, `.outflows_chain`, `.inflows_chain`; the classmethods `TransactionDAO.transactions_chain`, `OutflowDAO.outflows_chain`, `InflowDAO.inflows_chain`; the `CTE` import; add `false, or_` to the sqlalchemy import; trim the `*_chain` mention in the module docstring. |
| `tests/test_models.py` | Add `_pythonic_ancestry_ids` + `_oracle_*` helpers. Rewrite the Phase-6 materialization tests and the #157 equivalence tests to use the Python oracle instead of the CTE. Flip `test_non_longest_chain_blocks_uses_cte` → `test_non_longest_chain_blocks_is_cte_free`. Add fork/canonical read-path equivalence tests. Replace the two `_block_chain` booby-trap guard tests with one structural-absence test. |

No `chain.py` change — `Chain.block_chain` there is a Python `prev`-walk generator, unrelated to the SQL CTE, and stays. No schema/migration; `db check` unaffected.

---

## Background the implementer needs

After #157, the recursive CTE survives only on **read paths**:

- `ChainDAO.{blocks,transactions,outflows,inflows}` (`models.py:625–647`) each have an `if self._is_longest():` fast path that returns `BlockDAO.longest_chain_*_q()` (the materialization JOIN), and a fallback `return self.block.<x>_chain` that is the **recursive CTE** — reached only for a **non-longest (fork)** chain.
- `BlockDAO.address_transactions` (`models.py:400`) returns `self.transactions_chain.where(...)` — the CTE, unconditionally. It currently has **zero live callers** but is kept and converted.

The CTE machinery to be deleted:
- `BlockDAO._block_chain` (`models.py:308–317`, recursive CTE), `.block_chain` (319–322), `.transactions_chain` (324–326), `.outflows_chain` (328–330), `.inflows_chain` (332–334).
- Classmethod builders: `TransactionDAO.transactions_chain` (`models.py:105–114`), `OutflowDAO.outflows_chain` (`models.py:175–188`), `InflowDAO.inflows_chain` (`models.py:236–249`). These are used **only** by the deleted `BlockDAO` properties (`longest_chain_*_q` build their own inline joins).
- The `CTE` import (`models.py:8`).

The replacement primitive (`BlockDAO._ancestry()`, added in #157, `models.py:343`) returns `(divergent_ids, cap_position)`: ids of the short divergent suffix not in the materialization (empty for a canonical anchor), and the common-ancestor position (`None` only in the empty-materialization bootstrap). The materialized query helpers `longest_chain_blocks_q` etc. (`models.py:502–567`) show the join structure to mirror.

Useful existing test fixtures/helpers (`tests/test_models.py`):
- `_build_canonical_chain_with_spend(add_chain_block, time_stepper, wallet)` → `(chain, block1, block2, spend_txid)`.
- `_build_fork(time_stepper, wallet, subject)` → dict with `fork` (the non-longest tip BlockDAO), `block_1`, `block_2a`, `block_2b`, `fork_cb_txid`, `ancestor_cb_txid`, `spend_txid`, `spend_outflow_txid`.
- Fixtures: `app`, `mill_block`, `add_chain_block`, `time_stepper`, `wallet`, `subject`, `_count`.

---

## Task 1: CTE-free ancestry builders + convert the read paths

**Files:**
- Modify: `src/gumptionchain/models.py` (`BlockDAO`, `ChainDAO`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Flip the SQL-shape test to expect no CTE (failing test)**

Replace the whole `test_non_longest_chain_blocks_uses_cte` function (`tests/test_models.py:291–349`) with this. It keeps the identical fork-construction body and only changes the final assertions — the non-longest `.blocks` SQL must now be CTE-free and reference the materialization predicate.

```python
def test_non_longest_chain_blocks_is_cte_free(app, time_stepper, wallet):
    """A non-longest ChainDAO's .blocks must NOT emit a recursive CTE after
    #158 — it resolves ancestry via the divergent-suffix + materialization
    predicate. Verified by emitted SQL.

    Builds a real fork (chain_a + chain_b sharing block_1) so that a
    non-longest ChainDAO row genuinely exists.
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

        longest = ChainDAO.longest()
        assert longest is not None
        non_longest = next(
            (
                d
                for d in db.session.execute(ChainDAO.chains()).scalars()
                if d.id != longest.id
            ),
            None,
        )
        assert non_longest is not None, (
            'fixture did not produce a non-longest ChainDAO row'
        )
        assert non_longest._is_longest() is False

        compiled_sql = str(
            non_longest.blocks.compile(compile_kwargs={'literal_binds': True})
        )
        assert 'RECURSIVE' not in compiled_sql.upper(), (
            f'non-longest .blocks should be CTE-free, got:\n{compiled_sql}'
        )
        assert 'longest_chain_block' in compiled_sql.lower()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_models.py::test_non_longest_chain_blocks_is_cte_free -q`
Expected: FAIL — the non-longest `.blocks` path still returns the recursive CTE, so the SQL contains `RECURSIVE` and the assertion trips.

- [ ] **Step 3: Add `false, or_` to the sqlalchemy import**

In `src/gumptionchain/models.py`, edit the `from sqlalchemy import (...)` block (lines 7–16) to add `false` and `or_` (keep `CTE` for now — it is removed in Task 2). Resulting import, alphabetically consistent with the existing style:

```python
from sqlalchemy import (
    CTE,
    BigInteger,
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

- [ ] **Step 4: Add the four `ancestry_*_q` builders to `BlockDAO`**

In `src/gumptionchain/models.py`, place these immediately **after** `_ancestry` (i.e. just before `get_transaction_in_chain`, around line 372):

```python
def ancestry_blocks_q(self) -> Select[tuple[BlockDAO]]:
    """Blocks in this block's ancestry, CTE-free.

    Combines the short divergent suffix (ids not in the materialization)
    with the canonical prefix (`LongestChainBlockDAO.position <= cap`) as a
    single composable predicate. Degenerates to materialized membership for
    a canonical anchor (`divergent` empty, `cap` = tip position). Unordered:
    every consumer wraps this in `.subquery()` for membership/aggregation.
    """
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
    # or_(false(), *clauses) is always-false when clauses is empty (the
    # unreachable divergent-empty + cap-None case) and a no-op wrapper
    # otherwise.
    return db.select(BlockDAO).where(or_(false(), *clauses))  # type: ignore[no-any-return]

def ancestry_transactions_q(self) -> Select[tuple[TransactionDAO]]:
    blocks_subq = self.ancestry_blocks_q().subquery()
    block_alias = db.aliased(BlockDAO, blocks_subq)
    return db.select(TransactionDAO).join(  # type: ignore[no-any-return]
        block_alias, TransactionDAO.blocks
    )

def ancestry_outflows_q(self) -> Select[tuple[OutflowDAO]]:
    txn_subq = self.ancestry_transactions_q().subquery()
    txn_alias = db.aliased(TransactionDAO, txn_subq)
    return db.select(OutflowDAO).join(  # type: ignore[no-any-return]
        txn_alias, OutflowDAO.transaction
    )

def ancestry_inflows_q(self) -> Select[tuple[InflowDAO]]:
    txn_subq = self.ancestry_transactions_q().subquery()
    txn_alias = db.aliased(TransactionDAO, txn_subq)
    return db.select(InflowDAO).join(  # type: ignore[no-any-return]
        txn_alias, InflowDAO.transaction
    )
```

- [ ] **Step 5: Rewrite `address_transactions`**

Replace the current body (`models.py:400–403`):

```python
def address_transactions(
    self, address: str
) -> Select[tuple[TransactionDAO]]:
    return self.ancestry_transactions_q().where(
        TransactionDAO.address == address
    )
```

- [ ] **Step 6: Repoint the non-longest `ChainDAO` accessors**

In `ChainDAO` (`models.py:625–647`), change only the **fallback** (non-longest) return of each of the four accessors. The `if self._is_longest():` branch stays untouched.

```python
@property
def blocks(self) -> Select[tuple[BlockDAO]]:
    if self._is_longest():
        return BlockDAO.longest_chain_blocks_q()
    return self.block.ancestry_blocks_q()

@property
def transactions(self) -> Select[tuple[TransactionDAO]]:
    if self._is_longest():
        return BlockDAO.longest_chain_transactions_q()
    return self.block.ancestry_transactions_q()

@property
def outflows(self) -> Select[tuple[OutflowDAO]]:
    if self._is_longest():
        return BlockDAO.longest_chain_outflows_q()
    return self.block.ancestry_outflows_q()

@property
def inflows(self) -> Select[tuple[InflowDAO]]:
    if self._is_longest():
        return BlockDAO.longest_chain_inflows_q()
    return self.block.ancestry_inflows_q()
```

- [ ] **Step 7: Run the SQL-shape test, expect PASS**

Run: `uv run pytest tests/test_models.py::test_non_longest_chain_blocks_is_cte_free -q`
Expected: PASS — the non-longest `.blocks` SQL is now CTE-free and references `longest_chain_block`.

- [ ] **Step 8: Add read-path equivalence tests (Python oracle)**

Add to `tests/test_models.py`. First the oracle helper (place it near the top of the module's helper section, after the imports):

```python
def _pythonic_ancestry_ids(block_dao):
    """Ground-truth ancestry: block ids from this block back to genesis via
    `prev`, computed in pure Python — independent of the CTE and the
    materialization, so it cannot share a bug with the code under test.
    """
    ids = []
    current = block_dao
    while current is not None:
        ids.append(current.id)
        current = current.prev
    return ids


def _oracle_block_ids(select_stmt):
    return sorted(
        b.id for b in db.session.execute(select_stmt).scalars().all()
    )
```

Then the equivalence tests:

```python
def test_ancestry_read_paths_match_oracle_canonical(
    app, add_chain_block, time_stepper, wallet
):
    """ChainDAO read accessors + address_transactions on a canonical tip
    return exactly the ancestry computed by the Python prev-walk oracle.
    """
    with app.app_context():
        chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        oracle_ids = sorted(_pythonic_ancestry_ids(tip))

        chain_dao = ChainDAO.get(block2.block_hash)
        assert chain_dao is not None
        assert _oracle_block_ids(chain_dao.blocks) == oracle_ids

        # transactions/outflows/inflows: every returned row belongs to a
        # block in the oracle ancestry, and the row sets are non-empty.
        txn_ids = {
            t.id
            for t in db.session.execute(chain_dao.transactions).scalars().all()
        }
        assert txn_ids
        for t in db.session.execute(chain_dao.transactions).scalars().all():
            assert {b.id for b in t.blocks} & set(oracle_ids)

        # address_transactions filters the same ancestry by address.
        addr_txns = list(
            db.session.execute(
                tip.address_transactions(wallet.address)
            ).scalars()
        )
        assert all(t.address == wallet.address for t in addr_txns)


def test_ancestry_read_paths_match_oracle_fork(
    app, time_stepper, wallet, subject
):
    """The non-longest (fork) read accessors resolve the fork tip's ancestry
    (divergent suffix + shared prefix) identically to the Python oracle, and
    fork balances/outflows are correct.
    """
    with app.app_context():
        f = _build_fork(time_stepper, wallet, subject)
        fork = f['fork']
        assert fork is not None
        assert fork._ancestry()[0]  # genuine non-empty divergent suffix
        oracle_ids = sorted(_pythonic_ancestry_ids(fork))

        chain_dao = ChainDAO.get(f['block_2b'].block_hash)
        assert chain_dao is not None
        assert chain_dao._is_longest() is False
        assert _oracle_block_ids(chain_dao.blocks) == oracle_ids

        # The fork tip's coinbase outflow lives only on the divergent suffix;
        # the opposition stake it created is visible on the fork chain.
        opp = chain_dao.opposition_balance(subject)
        assert opp > 0

        # unspent_outflows on the fork: every row's parent block is in the
        # oracle ancestry.
        unspent = list(
            db.session.execute(
                chain_dao.unspent_outflows(wallet.address)
            ).scalars()
        )
        for o in unspent:
            assert {b.id for b in o.transaction.blocks} & set(oracle_ids)


def test_ancestry_read_paths_match_oracle_bootstrap(
    app, add_chain_block, time_stepper, wallet
):
    """With an empty materialization, ancestry_*_q resolve via the
    all-divergent predicate and still match the oracle.
    """
    with app.app_context():
        _chain, _block1, block2, _spend = _build_canonical_chain_with_spend(
            add_chain_block, time_stepper, wallet
        )
        db.session.execute(db.delete(LongestChainBlockDAO))
        db.session.commit()
        assert _count(LongestChainBlockDAO) == 0

        tip = BlockDAO.get(block2.block_hash)
        assert tip is not None
        oracle_ids = sorted(_pythonic_ancestry_ids(tip))
        assert _oracle_block_ids(tip.ancestry_blocks_q()) == oracle_ids
```

- [ ] **Step 9: Run the new tests + full suite, expect PASS**

Run: `uv run pytest tests/test_models.py -k "ancestry_read_paths or is_cte_free" -q`
Expected: PASS (4 tests).
Run: `uv run pytest -q`
Expected: green — the CTE-oracle tests still pass here because the CTE is still defined (Task 2 swaps their oracle).

- [ ] **Step 10: Lint, format, types**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add src/gumptionchain/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
perf(models): CTE-free ancestry read paths via divergent-suffix predicate (#158)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Swap the test oracle to the Python prev-walk, then delete the CTE

**Files:**
- Modify: `src/gumptionchain/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Repoint the Phase-6 materialization tests off the CTE oracle**

In `tests/test_models.py`, three tests use `db.session.execute(longest.block.block_chain).scalars()` as ground truth. Replace each `cte_ids = [...]` block with the Python oracle. The oracle returns tip→genesis order (self first, then `prev`), matching `position.desc()`.

In `test_longest_chain_block_property_matches_cte` (around lines 255–258), replace:

```python
        cte_ids = [
            b.id
            for b in db.session.execute(longest.block.block_chain).scalars()
        ]
```
with:
```python
        oracle_ids = _pythonic_ancestry_ids(longest.block)
```
and change the final assertion `assert cte_ids == mat_ids` → `assert oracle_ids == mat_ids`. Rename the function to `test_longest_chain_block_property_matches_prev_walk` and update its docstring to say "prev-walk" instead of "CTE".

In `test_longest_chain_block_rebuild_on_reorg` (around lines 381–384), replace the same `cte_ids = [...]` block with `oracle_ids = _pythonic_ancestry_ids(longest.block)` and `assert cte_ids == mat_ids` → `assert oracle_ids == mat_ids`.

In `test_iterative_walk_matches_cte` (around lines 412–415), replace the `cte_ids = [...]` capture with `oracle_ids = _pythonic_ancestry_ids(longest.block)` (capture it before the rebuild, same position) and update both `assert cte_ids == mat_ids` → `assert oracle_ids == mat_ids`. Rename the function to `test_iterative_walk_matches_prev_walk` and update its docstring.

- [ ] **Step 2: Repoint the #157 equivalence tests + `_cte_get_block_in_chain` off the CTE oracle**

Replace the `_cte_get_block_in_chain` helper (`tests/test_models.py:792–800`) with an oracle-based version:

```python
def _oracle_get_block_in_chain(block_dao, block_hash=None, idx=None):
    """Ground-truth get_block_in_chain via the Python prev-walk ancestry."""
    ids = _pythonic_ancestry_ids(block_dao)
    stmt = db.select(BlockDAO).where(BlockDAO.id.in_(ids))
    if block_hash is not None:
        stmt = stmt.where(BlockDAO.block_hash == block_hash)
    if idx is not None:
        stmt = stmt.where(BlockDAO.idx == idx)
    return db.session.execute(stmt).scalar_one_or_none()


def _oracle_txn_in_chain(block_dao, txid):
    ids = _pythonic_ancestry_ids(block_dao)
    return db.session.execute(
        db.select(TransactionDAO)
        .join(TransactionDAO.blocks)
        .where(BlockDAO.id.in_(ids))
        .where(TransactionDAO.txid == txid)
    ).scalar_one_or_none()


def _oracle_inflow_exists(block_dao, outflow_txid, outflow_idx):
    ids = _pythonic_ancestry_ids(block_dao)
    hit = (
        db.session.execute(
            db.select(InflowDAO)
            .join(InflowDAO.transaction)
            .join(TransactionDAO.blocks)
            .where(BlockDAO.id.in_(ids))
            .where(InflowDAO.outflow_txid == outflow_txid)
            .where(InflowDAO.outflow_idx == outflow_idx)
        )
        .scalars()
        .first()
    )
    return 1 if hit is not None else 0
```

Then update the three equivalence tests to call these helpers instead of the CTE properties:

In `test_hot_path_methods_match_cte_canonical` (lines 875–923):
- The txn loop: replace the `cte = db.session.execute(tip.transactions_chain.where(...)).scalar_one_or_none()` with `cte = _oracle_txn_in_chain(tip, txid)`.
- The inflow loop: replace the `cte_exists = (1 if db.session.execute(tip.inflows_chain.where(...)).scalars().first() is not None else 0)` block with `cte_exists = _oracle_inflow_exists(tip, otxid, oidx)`.
- The block loop: replace `_cte_get_block_in_chain(tip, **kwargs)` with `_oracle_get_block_in_chain(tip, **kwargs)`.

In `test_hot_path_methods_match_cte_fork` (lines 926–981):
- txn loop: `cte = db.session.execute(fork.transactions_chain.where(...)).scalar_one_or_none()` → `cte = _oracle_txn_in_chain(fork, txid)`.
- block loop: `_cte_get_block_in_chain(fork, **kwargs)` → `_oracle_get_block_in_chain(fork, **kwargs)`.
- inflow loop: replace the `cte_exists = (1 if db.session.execute(fork.inflows_chain.where(...)).scalars().first() is not None else 0)` block with `cte_exists = _oracle_inflow_exists(fork, otxid, oidx)`.

In `test_hot_path_methods_match_cte_empty_materialization` (lines 984–1013):
- txn loop: `cte = db.session.execute(tip.transactions_chain.where(...)).scalar_one_or_none()` → `cte = _oracle_txn_in_chain(tip, txid)`.

(Optionally rename these three tests' `_cte_` suffix to `_oracle_` for accuracy; not required.)

- [ ] **Step 3: Replace the two booby-trap guard tests with one structural-absence test**

Delete `test_hot_path_methods_never_touch_recursive_cte` (lines 1016–1053) and `test_hot_path_methods_never_touch_recursive_cte_fork` (lines 1056–1093) — their behavioral coverage is now fully provided by the oracle equivalence tests, and they patch `_block_chain`, which is about to be deleted. Add in their place:

```python
def test_recursive_cte_is_deleted():
    """#158 capstone: the recursive CTE and its *_chain builders must be
    gone from every DAO — no reachable recursive-CTE code remains.
    """
    for attr in (
        '_block_chain',
        'block_chain',
        'transactions_chain',
        'outflows_chain',
        'inflows_chain',
    ):
        assert not hasattr(BlockDAO, attr), (
            f'BlockDAO.{attr} should be deleted in #158'
        )
    assert not hasattr(TransactionDAO, 'transactions_chain')
    assert not hasattr(OutflowDAO, 'outflows_chain')
    assert not hasattr(InflowDAO, 'inflows_chain')
```

Also check `PropertyMock` is still used elsewhere in the file; if these were its only uses, drop it from the `from unittest.mock import PropertyMock, patch` import (and `patch` if likewise unused) to keep ruff happy.

- [ ] **Step 4: Run the absence test, expect FAIL**

Run: `uv run pytest tests/test_models.py::test_recursive_cte_is_deleted -q`
Expected: FAIL — the CTE attributes still exist (deletion happens next).

- [ ] **Step 5: Delete the CTE properties and classmethod builders**

In `src/gumptionchain/models.py`:

1. Delete `TransactionDAO.transactions_chain` (the classmethod, lines 105–114).
2. Delete `OutflowDAO.outflows_chain` (the classmethod, lines 175–188).
3. Delete `InflowDAO.inflows_chain` (the classmethod, lines 236–249).
4. Delete the five `BlockDAO` CTE members (lines 308–334): the `_block_chain` property, `block_chain`, `transactions_chain`, `outflows_chain`, `inflows_chain`.
5. Remove `CTE` from the `from sqlalchemy import (...)` block (added `false, or_` remain).
6. Trim the module docstring (lines 22–27): it explains the `# type: ignore[no-any-return]` on "Chain-factory returns". Keep the explanation (still applies to `longest_chain_*_q` and the new `ancestry_*_q`), but it needs no edit unless it names the deleted methods — leave as-is if generic.

- [ ] **Step 6: Run the absence test + full suite, expect PASS**

Run: `uv run pytest tests/test_models.py::test_recursive_cte_is_deleted -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: green — all oracle-based equivalence tests pass against the Python prev-walk; no test references the deleted CTE.

- [ ] **Step 7: grep clean + lint + format + types**

Run:
```bash
grep -n "_block_chain\|\.transactions_chain\|\.outflows_chain\|\.inflows_chain\|block_alias.*_block_chain" src/gumptionchain/models.py
```
Expected: no matches (the only remaining `block_chain` token in the codebase is `chain.py`'s domain `Chain.block_chain` generator, which is intentionally separate).

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all green. (`CTE` no longer imported; `false`/`or_` used; no unused `# type: ignore`.)

- [ ] **Step 8: Confirm `db check` is unaffected (no schema drift)**

```bash
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///_dbcheck.db uv run gumptionchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///_dbcheck.db uv run gumptionchain db check
rm -f _dbcheck.db
```
Expected: no differences.

- [ ] **Step 9: Commit**

```bash
git add src/gumptionchain/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
perf(models): delete the recursive _block_chain CTE — capstone (#158)

Remove BlockDAO._block_chain and the block_chain/transactions_chain/
outflows_chain/inflows_chain builders now that all read paths resolve
ancestry via the materialization + divergent-suffix predicate. Equivalence
tests' oracle moves to a pure-Python prev-walk; a structural-absence test
gates that no recursive-CTE code remains.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** non-longest `ChainDAO` accessors + `address_transactions` converted (Task 1); CTE + builders deleted (Task 2); Python-oracle equivalence on canonical/fork/bootstrap (Task 1 Step 8); structural-absence "CTE is gone" gate (Task 2 Step 3); grep-clean (Task 2 Step 7). `chain.py` `Chain.block_chain` correctly left alone.
- **Behavior preservation:** only the non-longest branch and the (dead) `address_transactions` change; the canonical `_is_longest()` → `longest_chain_*_q` fast path is byte-for-byte unchanged. Consumers (`wallet_balance`, `unspent_outflows`, `unrescinded_outflows`, `_stake_balance`, `wallet_leaderboard`) inherit the conversion through `self.inflows/outflows/transactions` and are exercised by the fork equivalence test.
- **Ordering:** `ancestry_*_q` are deliberately unordered (consumers wrap in `.subquery()`); documented in the `ancestry_blocks_q` docstring.
- **Type consistency:** new builders return `Select[tuple[...]]` with the module's `# type: ignore[no-any-return]` convention; `false`/`or_` imported from sqlalchemy; `CTE` removed.
- **TDD drivers:** Task 1 is driven red→green by the SQL-shape test (`is_cte_free`); Task 2's deletion is driven red→green by the structural-absence test. The oracle swap (Task 2 Steps 1–2) stays green throughout (oracle returns identical answers while the CTE is still defined), so the deletion in Step 5 is safe.

## Definition of done

- `ancestry_blocks_q` / `ancestry_transactions_q` / `ancestry_outflows_q` / `ancestry_inflows_q` added; non-longest `ChainDAO` accessors and `address_transactions` route through them; **no** `_block_chain`/`*_chain` reference remains.
- `BlockDAO._block_chain`, the four `*_chain` properties, the three classmethod builders, and the `CTE` import are deleted; grep clean in `models.py`.
- `test_non_longest_chain_blocks_is_cte_free`, the oracle-based equivalence tests (canonical/fork/bootstrap), and `test_recursive_cte_is_deleted` pass; the Phase-6 and #157 tests pass against the Python prev-walk oracle.
- Full suite + ruff + ruff-format + mypy green; `db check` shows no drift.
