# Phase 7a — SA 2.0 call-site syntax migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate all 85 legacy `Model.query` / `db.session.query(...)` call sites (36 in `src/cancelchain/models.py`, 1 in `src/cancelchain/api.py`, 1 in `src/cancelchain/browser.py`, 35 in `tests/test_models.py`, 12 in `tests/test_chain.py`) to the SQLAlchemy 2.0 idiom (`db.session.execute(db.select(...))` + `.scalar()` / `.scalars()` / `.scalar_one_or_none()` extractors). Migrate the 21 `Query[X]` chain-factory return-type annotations (and 3 parameter annotations on the same methods) to `Select[tuple[X]]` (SA 2.x's `Select` is parameterized by row shape, not by the scalar entity — see the spec's translation table). Update three `chain.py` caller sites, one `browser.py` `paginate()` site, and assorted test-suite consumer sites that previously iterated / `.count()`-ed / `.paginate()`-d a `Query` and now need explicit execution wrappers (`db.session.execute(...).scalars()` for iteration, `db.session.scalar(db.select(db.func.count()).select_from(stmt.subquery()))` for count — wrapped in a `_count_select(stmt)` helper for test readability, `db.paginate(stmt)` for pagination). Keep `db.Model` and the `mypy: disable-error-code` block at the top of `models.py` — both leave with Phase 7b.

**Architecture:** Pure syntax pass; no schema, no behavior, no test count changes. Replace `Query` import with `Select`; rewrite every legacy query site per the spec's translation table; rewrite the recursive CTE in `BlockDAO._block_chain` to build the base via `db.select(...).cte(recursive=True)`; chain factories on `TransactionDAO` / `OutflowDAO` / `InflowDAO` / `BlockDAO` / `ChainDAO` return `Select[tuple[X]]`; the 6 downstream `ChainDAO` methods (`unspent_outflows`, `wallet_balance`, `unforgiven_outflows`, `subject_balance`, `subject_support`, `wallet_leaderboard`) compose on the new Select factories. Three iteration sites in `chain.py` (`Chain.unspent_outflows`, `Chain.unforgiven_outflows`, `Chain.unforgiven_address_outflows`) wrap the DAO call with `db.session.execute(...).scalars()` to recover the row iterator. The one `browser.py` `ChainDAO.chains().paginate()` site moves to `db.paginate(ChainDAO.chains())` (Flask-SQLAlchemy 3.x's SA-2.0-compatible top-level helper) and loses its `# type: ignore[attr-defined]` comment. Tests migrate the same way; three `block_chain` list comprehensions and four `unspent_outflows.count()` assertions in `tests/test_models.py`, plus the 12 ChainDAO-property consumer sites in `tests/test_chain.py::test_dao`, also get the appropriate execute/count/list wrappers. Two new test helpers (`_count(model)` and `_count_select(stmt)`) live in `tests/_sa_helpers.py` (or atop `tests/conftest.py`) to keep both test files DRY.

**Tech Stack:** SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1.1 (existing). `from sqlalchemy import Select` replaces `from sqlalchemy.orm import Query`. `db.select` / `db.session.execute` / `db.session.scalar` / `db.func` / `db.aliased` all resolve via Flask-SQLAlchemy's existing facade. No dependency changes; no `database.py` changes.

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 6.6 + the bench harness merged. Verify with `git log --oneline -5 main` showing `5d31a48 bench: add chain-walk rebuild benchmark harness (#74)` and `0775063 feat(models): smart-reorg rebuild for longest_chain_block (#72)` near the top.
- The branch `docs/phase-7a-design` exists locally with one commit:
  - `882880a docs(phase-7a): add SA 2.0 syntax migration design spec`
  This plan adds a second commit on that branch (the plan file) and ships both as the docs PR.
- CI hard-gates `ruff check`, `ruff format --check`, and `mypy` (strict; `models.py` has an explicit per-file disable block that stays through 7a).
- Test baseline: **236 passed, 1 skipped**. Phase 7a adds zero new tests; the count stays 236.
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-28-phase-7a-sa2-syntax.md` (this file) + spec already on branch |
| 2 | impl PR | `src/cancelchain/models.py`, `src/cancelchain/api.py`, `src/cancelchain/browser.py`, `src/cancelchain/chain.py`, `tests/_sa_helpers.py` (new — or top of `tests/conftest.py`), `tests/test_models.py`, `tests/test_chain.py` |
| 3 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** The design spec is committed on `docs/phase-7a-design` (`882880a`). This task adds the implementation plan as a second commit and ships both as one docs PR.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-28-phase-7a-sa2-syntax-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/phase-7a-design`; spec file is tracked; commit count above main is `1`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-28-phase-7a-sa2-syntax.md
git status docs/superpowers/plans/
```

Expected: file exists; shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-28-phase-7a-sa2-syntax.md
git commit -m "$(cat <<'EOF'
docs(phase-7a): add SA 2.0 syntax migration implementation plan

Spells out the single-PR impl: branch off main, swap Query → Select
import, walk through models.py class-by-class (TransactionDAO →
OutflowDAO → InflowDAO → BlockDAO → ChainDAO → PendingTxnDAO →
ApiToken) translating every legacy Model.query / db.session.query
call site to the SA 2.0 idiom, change the 21 Query[X] return-type
annotations (and 3 parameter annotations) to Select[tuple[X]]
(SA 2.x's Select is parameterized by row shape, not the scalar
entity), rewrite the recursive CTE in BlockDAO._block_chain via
db.select(...).cte(recursive=True), migrate api.py:196 (one site),
migrate browser.py:37 (Query.paginate → db.paginate, drops a
# type: ignore comment), update three caller sites in chain.py
(Chain.unspent_outflows / unforgiven_outflows /
unforgiven_address_outflows) that previously iterated a Query and
now need db.session.execute(...).scalars() wrappers, migrate
tests/test_models.py (27 query sites + 3 block_chain iterations +
4 unspent_outflows.count + 2 statement.compile + 2 ChainDAO.chains
iterations) and tests/test_chain.py (12 ChainDAO-property
consumer sites in test_dao), add a small tests/_sa_helpers.py
module with _count(model) and _count_select(stmt) helpers to keep
assert lines readable, verify all 236 existing tests stay green,
run the benchmark harness (PR #74) to confirm perf is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/phase-7a-design
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/phase-7a-design --title "docs(phase-7a): Phase 7a SA 2.0 syntax migration design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 7a design spec (\`docs/superpowers/specs/2026-05-28-phase-7a-sa2-syntax-design.md\`).
- Adds the Phase 7a implementation plan (\`docs/superpowers/plans/2026-05-28-phase-7a-sa2-syntax.md\`).
- No code changes.

Phase 7 splits per ROADMAP guidance: **7a translates the 85 legacy query call sites** to the SA 2.0 idiom and migrates the 21 \`Query[X]\` chain-factory return types (and 3 param annotations) to \`Select[tuple[X]]\` (SA 2.x's \`Select\` is parameterized by row shape, not the scalar entity); 7b (separate spec) switches to typed \`DeclarativeBase\` and removes the \`mypy: disable-error-code\` block from \`models.py\`. Also updates three caller sites in \`chain.py\`, one \`paginate()\` call in \`browser.py\`, and assorted test-suite consumer sites in \`tests/test_models.py\` and \`tests/test_chain.py\` that previously consumed iterable / \`.count()\` / \`.paginate()\` \`Query\` results and now need explicit execution wrappers. Pure syntax pass — no schema, no behavior, no test-count change.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Phase 7a impl — SA 2.0 syntax migration

**Files:**
- Modify: `src/cancelchain/models.py` (36 call-site translations + 21 return-type annotation changes + 3 parameter annotation changes + import swap)
- Modify: `src/cancelchain/api.py` (1 call-site translation + 1 new import)
- Modify: `src/cancelchain/browser.py` (1 `paginate()` → `db.paginate(stmt)` swap; drop `# type: ignore[attr-defined]` comment)
- Modify: `src/cancelchain/chain.py` (3 caller-side wrappers around migrated DAO methods + 1 new import)
- Create: `tests/_sa_helpers.py` with `_count(model)` and `_count_select(stmt)` helpers — or alternatively, add them to the top of `tests/conftest.py` (decide during impl based on what feels least intrusive)
- Modify: `tests/test_models.py` (27 original query sites + 3 `block_chain` iteration sites + 4 `unspent_outflows.count()` assertions + 2 `statement.compile()` sites + 2 `ChainDAO.chains()` iteration sites = 38 total — but several of the originals collapse onto the new `_count` helper so the diff is smaller than the count implies; also imports the new helpers)
- Modify: `tests/test_chain.py` (12 site translations in `test_dao`: 8 `.count()` calls on `ChainDAO` properties via `_count_select`, 8 `.all()` calls wrapped with `db.session.execute(...).scalars().all()`, 2 `list(wallet_leaderboard(...))` calls wrapped with `list(db.session.execute(...))` to preserve Row-tuple semantics; imports the new helpers and `db`)

The migration is long but mechanical. Steps 2–7 walk through each affected class / file. After each section, optionally run `uv run pytest -x` to catch errors early (the test suite is a forcing function for correctness).

### Step 1: Branch off main + update imports in models.py

```bash
git checkout main && git pull --ff-only
git checkout -b feat/phase-7a-sa2-syntax
```

Open `src/cancelchain/models.py`. Locate the SQLAlchemy import block (around lines 19–29). The current state imports `Query` somewhere — find and replace.

Before (near the top of `models.py`):
```python
from sqlalchemy import (
    CTE,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
```

If `Query` is imported (check with `grep -n 'Query' src/cancelchain/models.py | head -5`), remove it. Add `Select` to the `from sqlalchemy import (...)` block, alphabetically:

After:
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
```

(If `Query` was imported from `sqlalchemy.orm`, drop it; `Select` lives in `sqlalchemy`.)

### Step 2: Migrate `TransactionDAO` (lines ~107–116)

In `src/cancelchain/models.py`, locate `TransactionDAO.get` (line 107) and `TransactionDAO.transactions_chain` (line 110–116).

Before:
```python
    @classmethod
    def get(cls, txid: str) -> TransactionDAO | None:
        return cls.query.filter_by(txid=txid).one_or_none()

    @classmethod
    def transactions_chain(
        cls, block_chain: Query[BlockDAO]
    ) -> Query[TransactionDAO]:
        block_alias = db.aliased(BlockDAO, block_chain.subquery())
        q = db.session.query(TransactionDAO)
        q = q.join(block_alias, TransactionDAO.blocks)
        return q.order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)
```

After:
```python
    @classmethod
    def get(cls, txid: str) -> TransactionDAO | None:
        return db.session.execute(
            db.select(cls).filter_by(txid=txid)
        ).scalar_one_or_none()

    @classmethod
    def transactions_chain(
        cls, block_chain: Select[tuple[BlockDAO]]
    ) -> Select[tuple[TransactionDAO]]:
        block_alias = db.aliased(BlockDAO, block_chain.subquery())
        return (
            db.select(TransactionDAO)
            .join(block_alias, TransactionDAO.blocks)
            .order_by(
                TransactionDAO.timestamp.desc(), TransactionDAO.id
            )
        )
```

### Step 3: Migrate `OutflowDAO` (lines ~168–184, ~219–229)

Locate `OutflowDAO.get` (line ~168) and `OutflowDAO.outflows_chain` (line ~175). Also the `InflowDAO.__init__` site at line 224 that references `OutflowDAO.query.filter_by(...)`.

Before:
```python
    @classmethod
    def get(cls, outflow_txid: str, outflow_idx: int) -> OutflowDAO | None:
        return cls.query.filter_by(
            outflow_txid=outflow_txid, outflow_idx=outflow_idx
        ).one_or_none()

    @classmethod
    def outflows_chain(
        cls, transactions_chain: Query[TransactionDAO]
    ) -> Query[OutflowDAO]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        q = db.session.query(OutflowDAO)
        q = q.join(txn_alias, OutflowDAO.transaction)
        q = q.order_by(
            txn_alias.timestamp.desc(), txn_alias.txid, OutflowDAO.idx
        )
        return q
```

After:
```python
    @classmethod
    def get(cls, outflow_txid: str, outflow_idx: int) -> OutflowDAO | None:
        return db.session.execute(
            db.select(cls).filter_by(
                outflow_txid=outflow_txid, outflow_idx=outflow_idx
            )
        ).scalar_one_or_none()

    @classmethod
    def outflows_chain(
        cls, transactions_chain: Select[tuple[TransactionDAO]]
    ) -> Select[tuple[OutflowDAO]]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        return (
            db.select(OutflowDAO)
            .join(txn_alias, OutflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                OutflowDAO.idx,
            )
        )
```

Then in `InflowDAO.__init__` (line ~224):

Before:
```python
            if not outflow_dao:
                outflow_dao = OutflowDAO.query.filter_by(
                    txid=outflow_txid, idx=outflow_idx
                ).one_or_none()
```

After:
```python
            if not outflow_dao:
                outflow_dao = db.session.execute(
                    db.select(OutflowDAO).filter_by(
                        txid=outflow_txid, idx=outflow_idx
                    )
                ).scalar_one_or_none()
```

### Step 4: Migrate `InflowDAO` (lines ~231–240)

Locate `InflowDAO.inflows_chain` (line ~231).

Before:
```python
    @classmethod
    def inflows_chain(
        cls, transactions_chain: Query[TransactionDAO]
    ) -> Query[InflowDAO]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        q = db.session.query(InflowDAO)
        q = q.join(txn_alias, InflowDAO.transaction)
        q = q.order_by(
            txn_alias.timestamp.desc(), txn_alias.txid, InflowDAO.idx
        )
        return q
```

After:
```python
    @classmethod
    def inflows_chain(
        cls, transactions_chain: Select[tuple[TransactionDAO]]
    ) -> Select[tuple[InflowDAO]]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        return (
            db.select(InflowDAO)
            .join(txn_alias, InflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                InflowDAO.idx,
            )
        )
```

### Step 5: Migrate `BlockDAO` (lines ~299–395)

Multiple sites. Start with the recursive CTE (line ~301).

Before (the `_block_chain` property):
```python
    @property
    def _block_chain(self) -> CTE:
        q = BlockDAO.query.filter(BlockDAO.id == self.id).cte(recursive=True)
        return q.union_all(BlockDAO.query.filter(BlockDAO.id == q.c.prev_id))
```

After:
```python
    @property
    def _block_chain(self) -> CTE:
        base = (
            db.select(BlockDAO)
            .where(BlockDAO.id == self.id)
            .cte(recursive=True)
        )
        return base.union_all(
            db.select(BlockDAO).where(BlockDAO.id == base.c.prev_id)
        )
```

Then `block_chain` property (line ~304):

Before:
```python
    @property
    def block_chain(self) -> Query[BlockDAO]:
        return db.session.query(self._block_chain)
```

After:
```python
    @property
    def block_chain(self) -> Select[tuple[BlockDAO]]:
        return db.select(self._block_chain)
```

The `transactions_chain` / `outflows_chain` / `inflows_chain` properties on `BlockDAO` (lines ~309–319) — only the return-type annotation changes:

Before:
```python
    @property
    def transactions_chain(self) -> Query[TransactionDAO]:
        return TransactionDAO.transactions_chain(self.block_chain)

    @property
    def outflows_chain(self) -> Query[OutflowDAO]:
        return OutflowDAO.outflows_chain(self.transactions_chain)

    @property
    def inflows_chain(self) -> Query[InflowDAO]:
        return InflowDAO.inflows_chain(self.transactions_chain)
```

After:
```python
    @property
    def transactions_chain(self) -> Select[tuple[TransactionDAO]]:
        return TransactionDAO.transactions_chain(self.block_chain)

    @property
    def outflows_chain(self) -> Select[tuple[OutflowDAO]]:
        return OutflowDAO.outflows_chain(self.transactions_chain)

    @property
    def inflows_chain(self) -> Select[tuple[InflowDAO]]:
        return InflowDAO.inflows_chain(self.transactions_chain)
```

Then `get_transaction_in_chain` (line ~324):

Before:
```python
    def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
        return self.transactions_chain.filter(
            TransactionDAO.txid == txid
        ).one_or_none()
```

After:
```python
    def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
        return db.session.execute(
            self.transactions_chain.where(TransactionDAO.txid == txid)
        ).scalar_one_or_none()
```

`address_transactions` (line ~329):

Before:
```python
    def address_transactions(self, address: str) -> Query[TransactionDAO]:
        return self.transactions_chain.filter(TransactionDAO.address == address)
```

After:
```python
    def address_transactions(self, address: str) -> Select[tuple[TransactionDAO]]:
        return self.transactions_chain.where(TransactionDAO.address == address)
```

`get_block_in_chain` (line ~332):

Before:
```python
    def get_block_in_chain(
        self, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        block_alias = db.aliased(BlockDAO, self.block_chain.subquery())
        q = db.session.query(BlockDAO)
        q = q.join(block_alias, BlockDAO.id == block_alias.id)
        if block_hash is not None:
            q = q.filter(BlockDAO.block_hash == block_hash)
        if idx is not None:
            q = q.filter(BlockDAO.idx == idx)
        return q.one_or_none()
```

After:
```python
    def get_block_in_chain(
        self, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        block_alias = db.aliased(BlockDAO, self.block_chain.subquery())
        stmt = (
            db.select(BlockDAO)
            .join(block_alias, BlockDAO.id == block_alias.id)
        )
        if block_hash is not None:
            stmt = stmt.where(BlockDAO.block_hash == block_hash)
        if idx is not None:
            stmt = stmt.where(BlockDAO.idx == idx)
        return db.session.execute(stmt).scalar_one_or_none()
```

`inflows_in_chain_count` (line ~344):

Before:
```python
    def inflows_in_chain_count(
        self, outflow_txid: str, outflow_idx: int
    ) -> int:
        return (
            1
            if self.inflows_chain.filter(
                InflowDAO.outflow_txid == outflow_txid,
                InflowDAO.outflow_idx == outflow_idx,
            ).first()
            is not None
            else 0
        )
```

After:
```python
    def inflows_in_chain_count(
        self, outflow_txid: str, outflow_idx: int
    ) -> int:
        stmt = self.inflows_chain.where(
            InflowDAO.outflow_txid == outflow_txid,
            InflowDAO.outflow_idx == outflow_idx,
        )
        return 1 if db.session.execute(stmt).scalars().first() is not None else 0
```

`BlockDAO.count` classmethod (line ~357):

Before:
```python
    @classmethod
    def count(cls) -> int:
        result = db.session.query(db.func.count(cls.id)).one_or_none()
        return result[0] if result is not None else 0
```

After:
```python
    @classmethod
    def count(cls) -> int:
        return db.session.scalar(
            db.select(db.func.count()).select_from(cls)
        ) or 0
```

`BlockDAO.block_hashes` classmethod (line ~362):

Before:
```python
    @classmethod
    def block_hashes(cls) -> Generator[str, None, None]:
        for r in cls.query.with_entities(cls.block_hash).order_by(
            cls.timestamp.desc(), cls.block_hash
        ):
            yield r[0]
```

After:
```python
    @classmethod
    def block_hashes(cls) -> Generator[str, None, None]:
        stmt = db.select(cls.block_hash).order_by(
            cls.timestamp.desc(), cls.block_hash
        )
        for (block_hash,) in db.session.execute(stmt):
            yield block_hash
```

`BlockDAO.get` classmethod (line ~369):

Before:
```python
    @classmethod
    def get(
        cls, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        q = cls.query
        if block_hash:
            q = q.filter_by(block_hash=block_hash)
        else:
            q = q.filter_by(idx=idx)
        return q.one_or_none()
```

After:
```python
    @classmethod
    def get(
        cls, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        stmt = db.select(cls)
        if block_hash:
            stmt = stmt.filter_by(block_hash=block_hash)
        else:
            stmt = stmt.filter_by(idx=idx)
        return db.session.execute(stmt).scalar_one_or_none()
```

`BlockDAO.longest_chain_blocks_q` (line ~380):

Before:
```python
    @classmethod
    def longest_chain_blocks_q(cls) -> Query[BlockDAO]:
        """Blocks in the longest chain, ordered tip→genesis."""
        return (
            db.session.query(BlockDAO)
            .join(
                LongestChainBlockDAO,
                BlockDAO.id == LongestChainBlockDAO.block_id,
            )
            .order_by(LongestChainBlockDAO.position.desc())
        )
```

After:
```python
    @classmethod
    def longest_chain_blocks_q(cls) -> Select[tuple[BlockDAO]]:
        """Blocks in the longest chain, ordered tip→genesis."""
        return (
            db.select(BlockDAO)
            .join(
                LongestChainBlockDAO,
                BlockDAO.id == LongestChainBlockDAO.block_id,
            )
            .order_by(LongestChainBlockDAO.position.desc())
        )
```

`BlockDAO.longest_chain_transactions_q` (line ~397):

Before:
```python
    @classmethod
    def longest_chain_transactions_q(cls) -> Query[TransactionDAO]:
        """Transactions in the longest chain, ordered tip→genesis."""
        blocks_subq = cls.longest_chain_blocks_q().subquery()
        block_alias = db.aliased(BlockDAO, blocks_subq)
        q = db.session.query(TransactionDAO)
        q = q.join(block_alias, TransactionDAO.blocks)
        return q.order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)
```

After:
```python
    @classmethod
    def longest_chain_transactions_q(cls) -> Select[tuple[TransactionDAO]]:
        """Transactions in the longest chain, ordered tip→genesis."""
        blocks_subq = cls.longest_chain_blocks_q().subquery()
        block_alias = db.aliased(BlockDAO, blocks_subq)
        return (
            db.select(TransactionDAO)
            .join(block_alias, TransactionDAO.blocks)
            .order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)
        )
```

`BlockDAO.longest_chain_outflows_q` (line ~411):

Before:
```python
    @classmethod
    def longest_chain_outflows_q(cls) -> Query[OutflowDAO]:
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        q = db.session.query(OutflowDAO)
        q = q.join(txn_alias, OutflowDAO.transaction)
        return q.order_by(
            txn_alias.timestamp.desc(),
            txn_alias.txid,
            OutflowDAO.idx,
        )
```

After:
```python
    @classmethod
    def longest_chain_outflows_q(cls) -> Select[tuple[OutflowDAO]]:
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        return (
            db.select(OutflowDAO)
            .join(txn_alias, OutflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                OutflowDAO.idx,
            )
        )
```

`BlockDAO.longest_chain_inflows_q` (line ~427) — analogous to outflows_q.

Before:
```python
    @classmethod
    def longest_chain_inflows_q(cls) -> Query[InflowDAO]:
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        q = db.session.query(InflowDAO)
        q = q.join(txn_alias, InflowDAO.transaction)
        return q.order_by(
            txn_alias.timestamp.desc(),
            txn_alias.txid,
            InflowDAO.idx,
        )
```

After:
```python
    @classmethod
    def longest_chain_inflows_q(cls) -> Select[tuple[InflowDAO]]:
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        return (
            db.select(InflowDAO)
            .join(txn_alias, InflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                InflowDAO.idx,
            )
        )
```

After Step 5, run `uv run pytest -x 2>&1 | tail -10` to catch errors in the `BlockDAO` migration before proceeding. If any test fails, fix it now before continuing.

### Step 6: Migrate `ChainDAO` properties + downstream methods (lines ~496–620)

`ChainDAO.blocks` / `.transactions` / `.outflows` / `.inflows` properties (lines ~497–519) — only the return-type annotation changes; the body still calls `BlockDAO.longest_chain_*_q()` or `self.block.*_chain` which now return Select:

Before (each of the 4 properties looks like this):
```python
    @property
    def blocks(self) -> Query[BlockDAO]:
        if self._is_longest():
            return BlockDAO.longest_chain_blocks_q()
        return self.block.block_chain
```

After:
```python
    @property
    def blocks(self) -> Select[tuple[BlockDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_blocks_q()
        return self.block.block_chain
```

Apply the same `Query[X]` → `Select[tuple[X]]` swap to `transactions`, `outflows`, `inflows`.

`ChainDAO.unspent_outflows` (line ~521):

Before:
```python
    def unspent_outflows(
        self,
        address: str,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Query[OutflowDAO]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.address == address)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if filter_pending:
            q = q.filter(~OutflowDAO.pending.any())
        return q
```

After:
```python
    def unspent_outflows(
        self,
        address: str,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Select[tuple[OutflowDAO]]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.address == address)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        if filter_pending:
            stmt = stmt.where(~OutflowDAO.pending.any())
        return stmt
```

`ChainDAO.wallet_balance` (line ~534):

Before:
```python
    def wallet_balance(self, address: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.address == address)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0
```

After:
```python
    def wallet_balance(self, address: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.address == address)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0
```

`ChainDAO.unforgiven_outflows` (line ~552):

Before:
```python
    def unforgiven_outflows(
        self,
        subject: str,
        address: str | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Query[OutflowDAO]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.subject == subject)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if address is not None:
            txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
            q = q.join(txn_alias, OutflowDAO.transaction)
            q = q.filter(txn_alias.address == address)
        if filter_pending:
            q = q.filter(~OutflowDAO.pending.any())
        return q
```

After:
```python
    def unforgiven_outflows(
        self,
        subject: str,
        address: str | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Select[tuple[OutflowDAO]]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.subject == subject)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        if address is not None:
            txn_alias = db.aliased(
                TransactionDAO, self.transactions.subquery()
            )
            stmt = stmt.join(txn_alias, OutflowDAO.transaction)
            stmt = stmt.where(txn_alias.address == address)
        if filter_pending:
            stmt = stmt.where(~OutflowDAO.pending.any())
        return stmt
```

`ChainDAO.subject_balance` (line ~571):

Before:
```python
    def subject_balance(self, subject: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.subject == subject)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0
```

After:
```python
    def subject_balance(self, subject: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.subject == subject)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0
```

`ChainDAO.subject_support` (line ~582):

Before:
```python
    def subject_support(self, subject: str) -> int:
        q = self.outflows.filter(OutflowDAO.support == subject)
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0
```

After:
```python
    def subject_support(self, subject: str) -> int:
        stmt = self.outflows.where(OutflowDAO.support == subject)
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0
```

`ChainDAO.wallet_leaderboard` (line ~591):

Before:
```python
    def wallet_leaderboard(
        self,
        earliest: datetime.datetime | None = None,
        latest: datetime.datetime | None = None,
        limit: int | None = None,
    ) -> Query[OutflowDAO]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
        q = db.session.query(
            OutflowDAO.address, db.func.sum(OutflowDAO.amount).label('ct')
        )
        q = q.filter(OutflowDAO.address.is_not(None))
        q = q.join(txn_alias, OutflowDAO.transaction)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if earliest is not None:
            q = q.filter(txn_alias.timestamp >= earliest)
        if latest is not None:
            q = q.filter(txn_alias.timestamp < latest)
        q = q.group_by(OutflowDAO.address)
        q = q.order_by(db.desc('ct'), OutflowDAO.address)
        if limit is not None:
            q = q.limit(limit)
            return db.session.query(db.aliased(q.subquery()))
        return q
```

After:
```python
    def wallet_leaderboard(
        self,
        earliest: datetime.datetime | None = None,
        latest: datetime.datetime | None = None,
        limit: int | None = None,
    ) -> Select[Any]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        txn_alias = db.aliased(
            TransactionDAO, self.transactions.subquery()
        )
        stmt = db.select(
            OutflowDAO.address,
            db.func.sum(OutflowDAO.amount).label('ct'),
        )
        stmt = stmt.where(OutflowDAO.address.is_not(None))
        stmt = stmt.join(txn_alias, OutflowDAO.transaction)
        stmt = stmt.join(
            inflows_alias, OutflowDAO.inflows, isouter=True
        )
        stmt = stmt.where(inflows_alias.id.is_(None))
        if earliest is not None:
            stmt = stmt.where(txn_alias.timestamp >= earliest)
        if latest is not None:
            stmt = stmt.where(txn_alias.timestamp < latest)
        stmt = stmt.group_by(OutflowDAO.address)
        stmt = stmt.order_by(db.desc('ct'), OutflowDAO.address)
        if limit is not None:
            stmt = stmt.limit(limit)
            return db.select(db.aliased(stmt.subquery()))
        return stmt
```

`Any` may need to be imported at the top of `models.py` if not already (`from typing import TYPE_CHECKING, Any` — check first with grep). Wallet_leaderboard returns a tuple-style row (address, sum), so `Select[Any]` is the most permissive accurate type without inventing a NamedTuple wrapper.

### Step 7: Migrate `ChainDAO.sync_longest_chain_blocks` + `_rebuild_longest_chain_blocks` (lines ~636–740)

These methods have several `db.session.query(LongestChainBlockDAO)...` sites. They're delete / count / exists / scalar lookups. Translate per the table.

`sync_longest_chain_blocks` — the bootstrap EXISTS check (line ~665–670):

Before:
```python
        if not db.session.query(
            db.session.query(LongestChainBlockDAO).exists()
        ).scalar():
            self._rebuild_longest_chain_blocks()
            return
```

After:
```python
        if not db.session.scalar(
            db.select(db.exists(db.select(LongestChainBlockDAO)))
        ):
            self._rebuild_longest_chain_blocks()
            return
```

(Equivalent SQL: `SELECT EXISTS (SELECT 1 FROM longest_chain_block)`.)

The smart-reorg walk's lookup (line ~675–681):

Before:
```python
        while current is not None:
            pos = (
                db.session.query(LongestChainBlockDAO.position)
                .filter(LongestChainBlockDAO.block_id == current.id)
                .scalar()
            )
```

After:
```python
        while current is not None:
            pos = db.session.scalar(
                db.select(LongestChainBlockDAO.position).where(
                    LongestChainBlockDAO.block_id == current.id
                )
            )
```

The DELETE pattern (line ~696 and ~709):

Before:
```python
            db.session.query(LongestChainBlockDAO).delete()
```

After:
```python
            db.session.execute(db.delete(LongestChainBlockDAO))
```

And for the filtered DELETE (line ~709):

Before:
```python
        db.session.query(LongestChainBlockDAO).filter(
            LongestChainBlockDAO.position > common_ancestor_position
        ).delete()
```

After:
```python
        db.session.execute(
            db.delete(LongestChainBlockDAO).where(
                LongestChainBlockDAO.position > common_ancestor_position
            )
        )
```

`_rebuild_longest_chain_blocks` (line ~731) — same DELETE replacement:

Before:
```python
        db.session.query(LongestChainBlockDAO).delete()
```

After:
```python
        db.session.execute(db.delete(LongestChainBlockDAO))
```

### Step 8: Migrate `ChainDAO.address_transactions` + `.count` + `.get` + `.ids` + `.chains` + `.longest` + `PendingTxnDAO.count` + remaining sites

`ChainDAO.address_transactions` (line ~764) — annotation only; the body just delegates to `BlockDAO.address_transactions`:

Before:
```python
    def address_transactions(self, address: str) -> Query[TransactionDAO]:
        return self.block.address_transactions(address)
```

After:
```python
    def address_transactions(self, address: str) -> Select[tuple[TransactionDAO]]:
        return self.block.address_transactions(address)
```

`ChainDAO.count` (line ~773):

Before:
```python
    @classmethod
    def count(cls) -> int:
        result = db.session.query(db.func.count(cls.id)).one_or_none()
        return result[0] if result is not None else 0
```

After:
```python
    @classmethod
    def count(cls) -> int:
        return db.session.scalar(
            db.select(db.func.count()).select_from(cls)
        ) or 0
```

`ChainDAO.get` (line ~777) — the `cls.query` site:

Before:
```python
    @classmethod
    def get(
        cls, block_hash: str | None = None, id: int | None = None
    ) -> ChainDAO | None:
        q = cls.query
        if block_hash:
            q = q.filter_by(block_hash=block_hash)
        else:
            q = q.filter_by(id=id)
        return q.one_or_none()
```

After:
```python
    @classmethod
    def get(
        cls, block_hash: str | None = None, id: int | None = None
    ) -> ChainDAO | None:
        stmt = db.select(cls)
        if block_hash:
            stmt = stmt.filter_by(block_hash=block_hash)
        else:
            stmt = stmt.filter_by(id=id)
        return db.session.execute(stmt).scalar_one_or_none()
```

`ChainDAO.ids` (line ~787) — the `with_entities` site:

Before:
```python
    @classmethod
    def ids(cls) -> Generator[int, None, None]:
        for r in cls.query.with_entities(cls.id).order_by(cls.id):
            yield r[0]
```

After:
```python
    @classmethod
    def ids(cls) -> Generator[int, None, None]:
        stmt = db.select(cls.id).order_by(cls.id)
        for (cid,) in db.session.execute(stmt):
            yield cid
```

`ChainDAO.chains` (line ~793):

Before:
```python
    @classmethod
    def chains(cls) -> Query[ChainDAO]:
        return cls.query.join(cls.block).order_by(
            BlockDAO.idx.desc(), BlockDAO.timestamp
        )
```

After:
```python
    @classmethod
    def chains(cls) -> Select[tuple[ChainDAO]]:
        return (
            db.select(cls)
            .join(cls.block)
            .order_by(BlockDAO.idx.desc(), BlockDAO.timestamp)
        )
```

`ChainDAO.longest` (line ~798):

Before:
```python
    @classmethod
    def longest(cls) -> ChainDAO | None:
        return cls.chains().first()
```

After:
```python
    @classmethod
    def longest(cls) -> ChainDAO | None:
        return db.session.execute(cls.chains()).scalars().first()
```

Also update any internal callers of `cls.chains()` that previously did `.first()` / `.all()` directly on the Query — search via `grep -nA2 'cls\.chains\|ChainDAO\.chains' src/cancelchain/models.py`.

`PendingTxnDAO.count` classmethod (line ~832) — same translation as ChainDAO.count:

Before:
```python
    @classmethod
    def count(cls) -> int:
        result = db.session.query(db.func.count(cls.id)).one_or_none()
        return result[0] if result is not None else 0
```

After:
```python
    @classmethod
    def count(cls) -> int:
        return db.session.scalar(
            db.select(db.func.count()).select_from(cls)
        ) or 0
```

`PendingTxnDAO.json_datas` (line ~836 — actual method name; an earlier draft mis-called it `txn_jsons`) — the `with_entities` site, which also composes `.filter(...)` and `.order_by(...)` calls before iteration:

Before:
```python
    @classmethod
    def json_datas(
        cls,
        earliest: datetime.datetime | None = None,
        expired: datetime.datetime | None = None,
    ) -> Generator[str, None, None]:
        q = cls.query.with_entities(cls.json_data)
        if earliest is not None:
            q = q.filter(cls.received >= earliest)
        if expired is not None:
            q = q.filter(cls.timestamp >= expired)
        q = q.order_by(cls.timestamp, cls.txid)
        for r in q:
            yield r[0]
```

After:
```python
    @classmethod
    def json_datas(
        cls,
        earliest: datetime.datetime | None = None,
        expired: datetime.datetime | None = None,
    ) -> Generator[str, None, None]:
        stmt = db.select(cls.json_data)
        if earliest is not None:
            stmt = stmt.where(cls.received >= earliest)
        if expired is not None:
            stmt = stmt.where(cls.timestamp >= expired)
        stmt = stmt.order_by(cls.timestamp, cls.txid)
        for (json_data,) in db.session.execute(stmt):
            yield json_data
```

`PendingTxnDAO.get` (line ~852):

Before:
```python
    @classmethod
    def get(cls, txid: str) -> PendingTxnDAO | None:
        return cls.query.filter_by(txid=txid).one_or_none()
```

After:
```python
    @classmethod
    def get(cls, txid: str) -> PendingTxnDAO | None:
        return db.session.execute(
            db.select(cls).filter_by(txid=txid)
        ).scalar_one_or_none()
```

`ApiToken.get` (line ~980 — there is no `WalletDAO` class in this codebase; `ApiToken` is the one with the `get(address)` classmethod):

Before:
```python
    @classmethod
    def get(cls, address: str) -> ApiToken | None:
        return cls.query.filter_by(address=address).one_or_none()
```

After:
```python
    @classmethod
    def get(cls, address: str) -> ApiToken | None:
        return db.session.execute(
            db.select(cls).filter_by(address=address)
        ).scalar_one_or_none()
```

Verify no remaining sites in models.py:

```bash
grep -n 'Model\.query\|\.query\.\|db\.session\.query\|with_entities' src/cancelchain/models.py
```

Expected: returns nothing (or only the docstring comment at line 7 referring to `Model.query` API). Any remaining match means a site was missed.

```bash
grep -n 'Query\[' src/cancelchain/models.py
```

Expected: returns nothing — all `Query[X]` annotations migrated.

### Step 9: Migrate `api.py` (line ~196)

In `src/cancelchain/api.py`, locate the one legacy call site:

Before:
```python
                if txn := lc_dao.address_transactions(address).first():
                    wallet = Wallet(b64ks=txn.public_key)
```

After:
```python
                txn = db.session.execute(
                    lc_dao.address_transactions(address)
                ).scalars().first()
                if txn:
                    wallet = Wallet(b64ks=txn.public_key)
```

If `db` is not already imported in `api.py`, add `from cancelchain.database import db` near the other `cancelchain.*` imports. Verify:

```bash
grep -n 'from cancelchain.database import db\|^from cancelchain' src/cancelchain/api.py | head -5
```

If `db` isn't imported, add the import at the appropriate alphabetical position.

### Step 9b: Update `chain.py` callers of migrated DAO methods (3 sites)

`ChainDAO.unspent_outflows` and `ChainDAO.unforgiven_outflows` previously returned `Query[OutflowDAO]`, which is iterable and yields `OutflowDAO` rows on iteration. After Step 6 they return `Select[tuple[OutflowDAO]]`, which is a SQL expression — iterating it directly would yield column clauses, not rows. The three `Chain.*` methods in `src/cancelchain/chain.py` that consume those DAO methods must wrap the call with `db.session.execute(...).scalars()`.

First, verify `db` is imported in `chain.py`:

```bash
grep -n 'from cancelchain.database import db' src/cancelchain/chain.py
```

If missing, add the import alphabetically next to the other `cancelchain.*` imports.

`Chain.unspent_outflows` (line ~335):

Before:
```python
    def unspent_outflows(
        self,
        address: str,
        limit: int | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Iterator[tuple[str, int, Outflow]]:
        amount = 0
        outflow_daos = self.to_dao().unspent_outflows(
            address, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            ...
```

After:
```python
    def unspent_outflows(
        self,
        address: str,
        limit: int | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Iterator[tuple[str, int, Outflow]]:
        amount = 0
        outflow_daos = db.session.execute(
            self.to_dao().unspent_outflows(
                address, filter_pending=filter_pending
            )
        ).scalars()
        for outflow_dao in outflow_daos:
            ...
```

`Chain.unforgiven_outflows` (line ~356) — same pattern:

Before:
```python
        outflow_daos = self.to_dao().unforgiven_outflows(
            subject, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            ...
```

After:
```python
        outflow_daos = db.session.execute(
            self.to_dao().unforgiven_outflows(
                subject, filter_pending=filter_pending
            )
        ).scalars()
        for outflow_dao in outflow_daos:
            ...
```

`Chain.unforgiven_address_outflows` (line ~372) — same pattern, with the `address=address` keyword preserved:

Before:
```python
        outflow_daos = self.to_dao().unforgiven_outflows(
            subject, address=address, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            ...
```

After:
```python
        outflow_daos = db.session.execute(
            self.to_dao().unforgiven_outflows(
                subject, address=address, filter_pending=filter_pending
            )
        ).scalars()
        for outflow_dao in outflow_daos:
            ...
```

Each loop consumes the iterator exactly once (it `break`s on a limit condition or runs to completion), so the single-pass `ScalarResult` iterator is safe — no `.all()` wrapping needed.

### Step 9c: Migrate `browser.py` (1 site)

In `src/cancelchain/browser.py`, locate the one `paginate()` call (line ~37):

Before:
```python
        chains_page = ChainDAO.chains().paginate()  # type: ignore[attr-defined]
```

After:
```python
        chains_page = db.paginate(ChainDAO.chains())
```

Flask-SQLAlchemy 3.x provides `db.paginate(stmt)` as the SA-2.0-compatible top-level helper; it accepts a `Select` and returns the same `Pagination` object the template (`chains.html`) iterates. The `# type: ignore[attr-defined]` comment goes away — `db.paginate` is properly typed, unlike the legacy `Query.paginate()` shim that motivated the ignore in the first place.

Verify `db` is imported in `browser.py`:
```bash
grep -n 'from cancelchain.database import db\|from cancelchain.database' src/cancelchain/browser.py
```

If missing, add the import alphabetically. The existing `from cancelchain.models import ChainDAO` line is fine.

### Step 9d: Create shared test helpers module

Create `tests/_sa_helpers.py`:

```python
"""Test-only SA 2.0 helpers — keep the verbose 2.0 patterns out of asserts."""

from cancelchain.database import db


def _count(model: type) -> int:
    """SELECT COUNT(*) FROM <model>. Used by `Model.query.count()` translations."""
    return db.session.scalar(
        db.select(db.func.count()).select_from(model)
    ) or 0


def _count_select(stmt) -> int:  # type: ignore[no-untyped-def]
    """SELECT COUNT(*) FROM (<stmt>). Used by composed `.count()` translations
    where stmt is a Select returned by a chain factory or DAO method."""
    return db.session.scalar(
        db.select(db.func.count()).select_from(stmt.subquery())
    ) or 0
```

(Alternative: park these atop `tests/conftest.py` instead. Decide during impl based on whether `conftest.py` already has unrelated helpers — if it's clean fixtures-only, the dedicated `_sa_helpers.py` module reads better; if conftest.py already mixes helpers and fixtures, putting them there avoids a new file.)

### Step 10: Migrate `tests/test_models.py` (27 query sites + 3 `block_chain` iterations + 4 `unspent_outflows.count()` + 2 `statement.compile()` + 2 `ChainDAO.chains()` iterations)

Import the helpers at the top of the file:
```python
from tests._sa_helpers import _count, _count_select
```

The test sites are mostly:
- `BlockDAO.query.count()` → `_count(BlockDAO)` via the new helper
- `LongestChainBlockDAO.query.count()` → analogous `_count(LongestChainBlockDAO)`
- `db.session.query(LongestChainBlockDAO).all()` → `db.session.execute(db.select(LongestChainBlockDAO)).scalars().all()`
- `db.session.query(LongestChainBlockDAO).order_by(...)` (composed) → `db.select(LongestChainBlockDAO).order_by(...)` then executed
- `db.session.query(LongestChainBlockDAO).count()` → `_count(LongestChainBlockDAO)` (when unfiltered) or `_count_select(stmt)` (when composed)
- `db.session.query(LongestChainBlockDAO).delete()` → `db.session.execute(db.delete(LongestChainBlockDAO))`

Then test sites become `assert _count(BlockDAO) == 1` instead of the verbose 2.0 form. This is a deliberate convenience helper for tests; production code uses the explicit form.

Walk through each test site in `tests/test_models.py` and translate per the patterns above. Since the test count is 27 and many are similar, use ripgrep+sed-style mass edits with care:

```bash
# Don't blind-apply; visually inspect each before/after.
grep -nB1 -A1 'BlockDAO.query.count\|LongestChainBlockDAO.query.count' tests/test_models.py
```

For each `<Model>.query.count() == N` line, replace with `_count(<Model>) == N` (assuming the `_count` helper exists at module scope).

For each `db.session.query(LongestChainBlockDAO)` site, walk through the composition and translate per the table.

The composed `db.session.query(LongestChainBlockDAO).order_by(...).all()` pattern (most common in this file) becomes:
```python
db.session.execute(
    db.select(LongestChainBlockDAO).order_by(...)
).scalars().all()
```

Additionally, three list comprehensions iterate `longest.block.block_chain` directly (`test_longest_chain_block_property_matches_cte` line ~216, `test_longest_chain_block_rebuild_on_reorg` line ~335, `test_iterative_walk_matches_cte` line ~359). After Step 5, `BlockDAO.block_chain` returns `Select[tuple[BlockDAO]]`, which is a SQL expression — iterating it would yield column clauses, not `BlockDAO` rows. Each site needs an `execute(...).scalars()` wrapper:

Before:
```python
cte_ids = [b.id for b in longest.block.block_chain]
```

After:
```python
cte_ids = [
    b.id for b in db.session.execute(longest.block.block_chain).scalars()
]
```

Additionally, four `assert dao.unspent_outflows(wallet.address).count() == N` lines (`test_unspent_outflows_*` at lines ~30, 61, 80, 85) call `.count()` on the migrated `ChainDAO.unspent_outflows` return value. After Step 6 that's a Select, so `.count()` is gone. Wrap with the helper:

Before:
```python
assert dao_a.unspent_outflows(wallet.address).count() == 1
```

After:
```python
assert _count_select(dao_a.unspent_outflows(wallet.address)) == 1
```

Two `longest.blocks.statement.compile(...)` / `non_longest.blocks.statement.compile(...)` sites (lines ~237, ~298 in `test_longest_chain_blocks_q_fast_path_skips_cte` and `test_non_longest_chain_blocks_uses_cte`) extract the underlying statement from a `Query` via `.statement`. After Step 6, `.blocks` IS a `Select` — `Select` has no `.statement` (it is the statement). Drop the `.statement` attribute access:

Before:
```python
compiled_sql = str(
    longest.blocks.statement.compile(
        compile_kwargs={'literal_binds': True}
    )
)
```

After:
```python
compiled_sql = str(
    longest.blocks.compile(
        compile_kwargs={'literal_binds': True}
    )
)
```

Same edit for the `non_longest.blocks.statement.compile(...)` site.

Two generator expressions iterate `ChainDAO.chains()` directly (lines ~182, ~289 — `next((d for d in ChainDAO.chains() if d.id != longest.id), None)`). After Step 8, `ChainDAO.chains()` returns `Select[tuple[ChainDAO]]`. Wrap the iteration:

Before:
```python
non_longest = next(
    (d for d in ChainDAO.chains() if d.id != longest.id),
    None,
)
```

After:
```python
non_longest = next(
    (
        d for d in db.session.execute(ChainDAO.chains()).scalars()
        if d.id != longest.id
    ),
    None,
)
```

After all translations, verify:

```bash
grep -n 'Model\.query\|\.query\.\|db\.session\.query\|with_entities' tests/test_models.py
grep -n 'for b in longest\.block\.block_chain\|for b in .*\.block_chain[^.]' tests/test_models.py
grep -n '\.unspent_outflows([^)]*)\.count\|\.statement\.compile\|for d in ChainDAO\.chains()' tests/test_models.py
```

Expected: all three return nothing. The second grep catches un-wrapped `block_chain` iteration (regex `[^.]` avoids flagging `.block_chain.subquery()`). The third catches the round-2-discovered patterns: `.unspent_outflows(...).count()`, `.statement.compile(...)`, and bare `ChainDAO.chains()` iteration.

### Step 10b: Migrate `tests/test_chain.py` (12 sites in `test_dao`)

`tests/test_chain.py::test_dao` (lines ~299-346) exercises the `ChainDAO` properties extensively. After Step 6, `.blocks` / `.transactions` / `.outflows` / `.inflows` all return `Select`, so `.count()` and `.all()` calls on them no longer work. Also, the two `list(chain.to_dao(create=True).wallet_leaderboard(...))` calls (lines ~327, ~335) iterate the migrated `Select[Any]` directly.

Import the helpers + `db` at the top of `tests/test_chain.py`:
```python
from cancelchain.database import db
from tests._sa_helpers import _count_select
```

Each `.count()` / `.all()` pair gets the same treatment. The pattern, for the 4 `(primary, alt)` property pairs:

Before:
```python
        blocks = chain.to_dao(create=True).blocks
        assert blocks.count() == 8
        assert [b.id for b in blocks.all()] == [10, 9, 8, 7, 6, 4, 2, 1]
        alt_blocks = alt_chain.to_dao(create=True).blocks
        assert alt_blocks.count() == 4
        assert [b.id for b in alt_blocks.all()] == [5, 3, 2, 1]
```

After:
```python
        blocks = chain.to_dao(create=True).blocks
        assert _count_select(blocks) == 8
        assert [
            b.id for b in db.session.execute(blocks).scalars().all()
        ] == [10, 9, 8, 7, 6, 4, 2, 1]
        alt_blocks = alt_chain.to_dao(create=True).blocks
        assert _count_select(alt_blocks) == 4
        assert [
            b.id for b in db.session.execute(alt_blocks).scalars().all()
        ] == [5, 3, 2, 1]
```

Apply the same shape to the `transactions` / `outflows` / `inflows` blocks (lines 306-325 in the current file).

The two `wallet_leaderboard` calls need different treatment because the result is a Row tuple `(address, sum)`, not a scalar model. Wrap with `list(db.session.execute(...))` (NOT `.scalars()`), preserving Row-tuple semantics so the existing `wallet_leaders[0][0]` / `[0][1]` indexing keeps working:

Before:
```python
        wallet_leaders = list(chain.to_dao(create=True).wallet_leaderboard())
        ...
        wallet_leaders = list(
            chain.to_dao(create=True).wallet_leaderboard(
                earliest=block2.timestamp_dt,
                latest=last_block.timestamp_dt,
                limit=2,
            )
        )
```

After:
```python
        wallet_leaders = list(
            db.session.execute(
                chain.to_dao(create=True).wallet_leaderboard()
            )
        )
        ...
        wallet_leaders = list(
            db.session.execute(
                chain.to_dao(create=True).wallet_leaderboard(
                    earliest=block2.timestamp_dt,
                    latest=last_block.timestamp_dt,
                    limit=2,
                )
            )
        )
```

Verify:
```bash
grep -n '\.blocks\.count\|\.blocks\.all\|\.transactions\.count\|\.transactions\.all\|\.outflows\.count\|\.outflows\.all\|\.inflows\.count\|\.inflows\.all\|list(.*\.wallet_leaderboard' tests/test_chain.py
```

Expected: returns nothing.

### Step 11: Verify all gates

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

All four must exit 0. Test count: 236 (unchanged).

If `mypy` reports new errors, they should be covered by the existing `# mypy: disable-error-code` block at the top of `models.py`. If a NEW error code surfaces (not covered), add it to the block — Phase 7b will remove the whole block anyway.

Likely failure modes and fixes:
- `scalar_one_or_none()` returned a Row instead of the model — wrong extractor. Use `.scalar_one_or_none()` (singular noun) when extracting a single Model instance from a `db.select(Model)` query.
- `Result.scalars()` iterator consumed twice — wrap with `.all()` or `list(...)` if you need to iterate twice.
- `.where()` on a Select-from-CTE column — sometimes `.where(CTE.c.col == ...)` needs explicit column reference; if mypy or ruff complains, try `.where(getattr(cte.c, 'col') == ...)` as a workaround (rare).
- A test fixture that does `BlockDAO.query.count() == 0` — `_count(BlockDAO) == 0` is the replacement.

### Step 12: Run the benchmark harness for sanity

To confirm no perf regression:

```bash
uv run python bench/rebuild_walk_bench.py --sizes 1000 10000 100000 2>&1 | tail -10
```

Expected: per-step times ~0.25 ms/step (matching the Phase 6.6 baseline within noise). If significantly slower, investigate before committing.

### Step 13: Commit

```bash
git add \
    src/cancelchain/models.py \
    src/cancelchain/api.py \
    src/cancelchain/browser.py \
    src/cancelchain/chain.py \
    tests/_sa_helpers.py \
    tests/test_models.py \
    tests/test_chain.py
git commit -m "$(cat <<'EOF'
feat(models): SA 2.0 query syntax migration

Phase 7a. Translates all 85 legacy Model.query / db.session.query
call sites to the SQLAlchemy 2.0 idiom (db.session.execute(
db.select(...)) + .scalar() / .scalars() / .scalar_one_or_none()
extractors). Migrates the 21 Query[X] chain-factory return-type
annotations (and 3 parameter annotations on the same methods) to
Select[tuple[X]] (SA 2.x's Select is parameterized by row shape,
not the scalar entity).

Pure syntax pass: no schema changes, no behavior changes, no new
tests, test count stays 236. The benchmark harness (PR #74)
confirms per-step rebuild perf is unchanged within noise.

src/cancelchain/models.py:
- Import: drop Query (from sqlalchemy.orm), add Select (from
  sqlalchemy).
- TransactionDAO.get + .transactions_chain: db.session.execute +
  scalar_one_or_none; chain factory returns
  Select[tuple[TransactionDAO]].
- OutflowDAO.get + .outflows_chain: same pattern.
- InflowDAO.inflows_chain: Select[tuple[InflowDAO]]; the __init__'s
  outflow_dao lookup also migrates.
- BlockDAO._block_chain: recursive CTE via db.select(...).cte(
  recursive=True).union_all(...); same SQL output.
- BlockDAO.block_chain / .transactions_chain / .outflows_chain /
  .inflows_chain / .address_transactions / .longest_chain_*_q:
  return Select[tuple[X]].
- BlockDAO.get / .count / .block_hashes / .get_transaction_in_chain
  / .get_block_in_chain / .inflows_in_chain_count: 2.0 execution
  pattern with appropriate extractors.
- ChainDAO.blocks / .transactions / .outflows / .inflows / .chains
  / .address_transactions (the delegate at line 764): Select[tuple[X]].
- ChainDAO.unspent_outflows / .wallet_balance / .unforgiven_outflows
  / .subject_balance / .subject_support / .wallet_leaderboard:
  Select composition (.where instead of .filter for new sites,
  .filter preserved where it was part of a longer chain since
  Select accepts both); aggregate queries use db.session.scalar(
  db.select(db.func.sum(...))). wallet_leaderboard returns
  Select[Any] since it yields (address, sum) row tuples rather
  than a single Model.
- ChainDAO.sync_longest_chain_blocks + _rebuild_longest_chain_blocks:
  bootstrap EXISTS check, walk lookup, DELETE statements migrated
  to db.session.execute(db.delete(...)) and db.session.scalar(
  db.select(db.exists(...))).
- ChainDAO.count / .get / .ids / .longest + PendingTxnDAO.count
  / .json_datas / .get + ApiToken.get: 2.0 idioms throughout.

src/cancelchain/api.py:
- One site (lc_dao.address_transactions(address).first()) wrapped
  with db.session.execute(...).scalars().first(); added missing
  `from cancelchain.database import db` import.

src/cancelchain/browser.py:
- ChainDAO.chains().paginate() (line 37, previously needed
  # type: ignore[attr-defined] because Select has no .paginate())
  becomes db.paginate(ChainDAO.chains()) using Flask-SQLAlchemy
  3.x's SA-2.0-compatible top-level helper. The type: ignore goes
  away.

src/cancelchain/chain.py:
- Three caller-side updates. After ChainDAO.unspent_outflows /
  unforgiven_outflows return a Select, the iteration sites in
  Chain.unspent_outflows (line 342), Chain.unforgiven_outflows
  (line 361), and Chain.unforgiven_address_outflows (line 380)
  wrap the DAO call with db.session.execute(...).scalars() to
  recover the row iterator. Adds `from cancelchain.database import
  db` import.

tests/_sa_helpers.py (new module):
- _count(model) and _count_select(stmt) helpers shared by both
  test files. Without these the assert lines bloat with the full
  db.session.scalar(db.select(db.func.count()).select_from(...))
  pattern. (Alternative location: top of conftest.py — decided
  during impl.)

tests/test_models.py:
- Imports the new helpers.
- All 27 original legacy call sites migrated to 2.0 idiom
  (composed queries use db.session.execute + .scalars().all() /
  .scalars().first(); count sites use the _count helper; deletes
  use db.session.execute + db.delete).
- Three `[b.id for b in longest.block.block_chain]` iteration sites
  (lines 216, 335, 359) wrap with db.session.execute(
  longest.block.block_chain).scalars() now that block_chain is a
  Select.
- Four `dao.unspent_outflows(...).count()` assertions (lines 30,
  61, 80, 85) use the new _count_select helper.
- Two `longest.blocks.statement.compile(...)` /
  `non_longest.blocks.statement.compile(...)` sites (lines 237,
  298) drop .statement (Select IS the statement) and call
  .compile(...) directly.
- Two `(d for d in ChainDAO.chains() if ...)` generator
  expressions (lines 182, 289) wrap with
  `db.session.execute(ChainDAO.chains()).scalars()`.

tests/test_chain.py:
- Imports db and _count_select.
- test_dao (lines ~299-346): the 8 `.count()` assertions on
  ChainDAO.blocks / .transactions / .outflows / .inflows (4
  primary + 4 alt) use _count_select; the 8 `.all()` calls wrap
  with db.session.execute(...).scalars().all().
- Two list(chain.to_dao(create=True).wallet_leaderboard(...))
  calls (lines 327, 335) wrap with list(db.session.execute(...))
  — NOT .scalars(), since wallet_leaderboard yields Row tuples
  (address, sum) and the existing wallet_leaders[0][0] /
  wallet_leaders[0][1] indexing relies on Row-tuple semantics.

Phase 7a explicitly defers (per spec):
- Phase 7b: typed DeclarativeBase + remove mypy override block at
  the top of models.py.
- The mypy override block stays in 7a (still needed since db.Model
  remains the dynamic untyped base).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 14: Push and open PR

```bash
git push -u origin feat/phase-7a-sa2-syntax
gh pr create --base main --title "feat(models): SA 2.0 query syntax migration" --body "$(cat <<'EOF'
## Summary
- Translates all 85 legacy \`Model.query\` / \`db.session.query(...)\` call sites across \`models.py\`, \`api.py\`, \`browser.py\`, \`tests/test_models.py\`, and \`tests/test_chain.py\` to the SQLAlchemy 2.0 idiom (\`db.session.execute(db.select(...))\` + \`.scalar()\` / \`.scalars()\` / \`.scalar_one_or_none()\` extractors).
- Migrates the 21 \`Query[X]\` chain-factory return-type annotations (and 3 parameter annotations on the same methods) to \`Select[tuple[X]]\` (SA 2.x's \`Select\` is parameterized by row shape, not the scalar entity).
- Updates three caller sites in \`chain.py\`, one \`paginate()\` call in \`browser.py\` (to \`db.paginate(stmt)\` — drops a \`# type: ignore\` comment as a bonus), and assorted test-suite consumer sites (\`block_chain\` iterations, \`unspent_outflows.count()\`, \`statement.compile()\`, \`ChainDAO.chains()\` iterations, \`ChainDAO.blocks/.transactions/.outflows/.inflows\` \`.count()\`/\`.all()\` calls, and \`list(wallet_leaderboard(...))\` calls).
- Adds a small \`tests/_sa_helpers.py\` (or top-of-\`conftest.py\`) module with \`_count(model)\` and \`_count_select(stmt)\` helpers to keep assert lines readable.
- Pure syntax pass — no schema changes, no behavior changes, no new tests, no test-count change (236 stays 236).

## Why
Phase 7a per the split decided during brainstorming. Phase 7b (separate spec) switches to typed \`DeclarativeBase\` and removes the \`mypy: disable-error-code\` block from \`models.py\`. This PR removes one of the two blockers (legacy query syntax in active use); the other (dynamic \`db.Model\` base) goes in 7b.

## Out of scope (per spec)
- Phase 7b: typed DeclarativeBase + remove the \`mypy: disable-error-code\` block at the top of \`models.py\`.
- No changes outside the files listed above (\`models.py\`, \`api.py\`, \`browser.py\`, \`chain.py\`, \`tests/_sa_helpers.py\`, \`tests/test_models.py\`, \`tests/test_chain.py\`).

## Test plan
- [x] \`uv run mypy\` exits 0.
- [x] \`uv run pytest\` passes 236 (unchanged).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [x] \`bench/rebuild_walk_bench.py --sizes 1000 10000 100000\` matches the Phase 6.6 baseline (~0.25 ms/step on local SQLite).
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 15: Stop — controller handles wor + mwg + sync

---

## Task 3: Phase 7a acceptance verification

**Files:** none modified. Final verification after the impl PR lands on main.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

Expected: top two commits are the docs PR squash and the impl PR squash.

- [ ] **Step 2: Fresh sync**

```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```

Expected: Python 3.12.x and a fresh venv.

- [ ] **Step 3: Legacy query syntax eradicated**

```bash
grep -rn 'Model\.query\|\.query\.\|db\.session\.query\|with_entities' src/cancelchain/ tests/
grep -n 'Query\[' src/cancelchain/models.py
```

Expected: both grep results are empty (or contain only docstring/comment references — verify by eye).

- [ ] **Step 4: Hard CI gates pass**

```bash
uv run ruff check src tests; echo "ruff check exit: $?"
uv run ruff format --check src tests; echo "ruff format exit: $?"
uv run mypy; echo "mypy exit: $?"
```

All three exit 0.

- [ ] **Step 5: Tests pass on 3.12 and 3.13**

```bash
uv run --python 3.12 pytest 2>&1 | tail -3
uv run --python 3.13 pytest 2>&1 | tail -3
```

Expected: both print `236 passed, 1 skipped`.

- [ ] **Step 6: Benchmark perf unchanged**

```bash
uv run python bench/rebuild_walk_bench.py --sizes 1000 10000 100000 2>&1 | tail -10
```

Expected: per-step times ~0.25 ms/step on local SQLite (matching Phase 6.6 baseline within noise).

- [ ] **Step 7: CLI smoke**

```bash
uv run cancelchain --help
```

Expected: prints the full command tree.

- [ ] **Step 8: Docker build smoke**

```bash
docker build --target builder -t cc-phase7a-final .
```

Expected: succeeds.

- [ ] **Step 9: Acceptance complete**

If Steps 1–8 all pass, Phase 7a is done. No commit.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 2) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory).
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend) and post a `/copilot review` comment on the PR — Copilot's auto-review only fires on the initial push; subsequent rounds need the manual trigger (per the user's memory).

---

## Risks and watchpoints

### Risk: `Result.scalars()` returns an iterator, not a list

Iterating `db.session.execute(stmt).scalars()` twice is undefined. The migration must wrap with `.all()` or pass through `list(...)` if the result needs reuse. Specific watchpoints:

- `BlockDAO.block_hashes` — iterates `db.session.execute(stmt)` (Row tuples, not scalars) once via a `for` loop. Single iteration → safe.
- `PendingTxnDAO.json_datas` — same single-iteration pattern.
- Test loops that consume the same query twice — almost certainly need `.scalars().all()` instead.

Grep after migration: `grep -B1 -A2 '\.scalars()' src/cancelchain/ tests/` and visually verify each site either calls `.all()` / `.first()` / `.one()` next, or iterates exactly once.

### Risk: `.one_or_none()` returning Row vs Model

`Query.one_or_none()` returns the Model instance (or None). `Result.one_or_none()` returns a Row (or None) — to get the model, index `[0]`. The correct 2.0 idiom for "fetch single Model" is `.scalar_one_or_none()`. The plan uses `.scalar_one_or_none()` consistently — verify by grep:

```bash
grep -n '\.one_or_none()\|\.scalar_one_or_none()' src/cancelchain/ tests/
```

After migration, `.one_or_none()` should only appear inside the implementation of `_smart_reorg`'s sync flow if at all — every "fetch a Model" site uses `.scalar_one_or_none()`.

### Risk: aggregate-query Row indexing

The legacy pattern `db.session.query(db.func.sum(col)).one_or_none()` returns `(amount,)` Row or None; the caller indexes `[0]`. The 2.0 cleaner form is `db.session.scalar(db.select(db.func.sum(col)))` returning the scalar directly. The plan migrates to the cleaner form (`db.session.scalar(...) or 0`). If a test depends on the Row tuple shape, fix the test to consume the scalar.

### Risk: `.filter()` vs `.where()` on Select

SA 2.0's Select accepts both `.filter()` and `.where()` as aliases. The migration defaults to `.where()` for new sites, preserves `.filter()` where it was part of a multi-line composition chain. If ruff complains about either, swap to the other freely — semantics are identical for non-keyword filter forms.

### Risk: mypy errors surface despite the existing override block

The mypy override at the top of `models.py` covers `no-untyped-call,no-any-return,name-defined,misc`. SA 2.0 typing improvements may surface different error codes (e.g., `arg-type` on `Select[tuple[X]]` parameter mismatches, `return-value` on chain-factory returns). The choice of `Select[tuple[X]]` (instead of the naive `Select[X]`) is what keeps these clean in the common case — `db.select(BlockDAO)` is typed `Select[tuple[BlockDAO]]` in SA 2.x, and using the entity-only form would itself surface `return-value`/`arg-type` errors not covered by the existing block. If new error codes still appear after migration, add them to the existing block in 7a — Phase 7b removes the whole block anyway, so don't waste cycles fixing individual ignores.

### Risk: `db.exists` vs `db.session.query(...).exists()`

The legacy `db.session.query(SomeModel).exists()` returns an `exists()` clause expression. The 2.0 form: `db.exists(db.select(SomeModel))` — passes the select inward. The bootstrap fast-path in `sync_longest_chain_blocks` uses this; verify post-migration that the SQL is `SELECT EXISTS (SELECT 1 FROM longest_chain_block)`.

### Risk: `db.session.query` showing up in unexpected files

Wide grep to catch any miss:

```bash
grep -rn 'Model\.query\|\.query\.\|db\.session\.query\|with_entities' src/ tests/ bench/
```

Expected: empty after Step 11 across `models.py`, `api.py`, `chain.py`, and `tests/test_models.py` (all 7a-scope files). If any other file (`node.py`, `miller.py`, `command.py`, `tasks.py`, anything under `bench/`) surfaces a legacy site, that's out-of-scope — surface it to the user before expanding scope. The most likely additional surface is `bench/`, which is intentionally not in scope here (see the next risk).

### Risk: wallet_leaderboard's Select[Any] type

The leaderboard returns tuples of `(address, sum)`, not a single Model. `Select[Any]` is the most permissive accurate type — narrower options would be `Select[tuple[str, int]]` (modern Python) or defining a NamedTuple. `Select[Any]` is fine; don't over-engineer the typing here.

### Risk: `bench/rebuild_walk_bench.py` references models APIs that changed

The bench script uses `BlockDAO`, `LongestChainBlockDAO`, `ChainDAO` directly, and uses `db.session.query(LongestChainBlockDAO).delete()` in the wipe helper. **The bench script is OUT OF SCOPE** for Phase 7a per the spec (it's not in the file list), but since the spec mandates `grep` returning empty for legacy patterns across `src/`, we should also migrate bench/ for consistency. Add to Step 7 if reviewers flag it; otherwise call out in the PR body that the bench script is intentionally left as-is for now and will migrate as part of Phase 7b's broader cleanup.
