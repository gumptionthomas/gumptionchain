# Phase 7a — SQLAlchemy 2.0 call-site syntax migration

**Status:** Draft for review
**Date:** 2026-05-28
**Scope:** Translate all 63 legacy `Model.query` / `db.session.query(...)` call sites to the SA 2.0 idiom (`db.session.execute(db.select(...))` + `.scalar()` / `.scalars()` / `.scalar_one_or_none()` extractors). Migrate the 16 `Query[X]` chain-factory return type annotations to `Select[X]`. The Phase 7 sequencing — split per ROADMAP — is "7a: syntax migration, then 7b: DeclarativeBase + mypy override removal." This spec covers 7a only; 7b gets its own spec/plan after 7a lands.

## Goal

Bring `src/cancelchain/models.py`, `src/cancelchain/api.py`, and `tests/test_models.py` onto the SQLAlchemy 2.0 idiomatic query API. The legacy 1.x `Model.query` / `db.session.query` syntax still works under Flask-SQLAlchemy 3.1.1 + SQLAlchemy 2.0.50, but is deprecated for new code and is one of the two blockers (alongside the dynamic `db.Model` base) preventing removal of the `mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` block at the top of `models.py`. Phase 7a removes that blocker by retiring the legacy query syntax from active call sites while preserving the `db.Model` infrastructure (Phase 7b switches to typed `DeclarativeBase`).

## Non-goals

- **No DeclarativeBase migration.** Phase 7b. `db.Model` stays the base class; `Model.query` remains defined but unused inside the codebase post-7a.
- **No `mypy: disable-error-code` block removal.** The block stays at the top of `models.py` through 7a; Phase 7b removes it.
- **No behavior changes.** This is purely a syntax pass. All 236 existing tests stay green. The generated SQL should be plan-equivalent (or at most equivalent up to SA 2.0's compiler optimizations).
- **No work outside the three target files.** `api_client.py`, `wallet.py`, `chain.py`, `node.py`, `miller.py`, `command.py`, `tasks.py` are untouched.
- **No new chain-membership materialization changes.** Phase 6.6 closed those; 7a is purely a syntax pass.
- **No new tests.** The translation is API-equivalent; existing tests catch regressions.
- **No performance work.** The benchmark harness (PR #74) is available to verify equivalence.

## Decisions taken during brainstorming

- **Two-PR Phase 7 sequencing.** 7a (this spec) handles call-site syntax translation; 7b handles DeclarativeBase + mypy override removal. Three-PR split (7a / 7b / 7c) was rejected as overhead for a tiny mypy-removal-only PR.
- **Chain-factory return types migrate to `Select[X]` in 7a.** Same composability as `Query[X]` (Select supports `.where()` / `.filter()` / `.subquery()` / `.join()`). Keeping `Query[X]` as a public return type while internally using `db.session.execute(db.select(...))` was rejected as half-measure.
- **`.filter()` allowed alongside `.where()` in composed chains.** SA 2.0's Select accepts `.filter()` as an alias. Where mechanical search-and-replace gave us `.filter()`, leave it; new sites default to `.where()`. Don't churn for stylistic uniformity.
- **`db.aliased` calls stay as-is.** SA 2.0's `aliased(Mapped, subquery)` (from `sqlalchemy.orm`) has the same signature as `db.aliased` (which delegates to it). No changes to the 5+ `db.aliased(...)` sites in `models.py`.
- **Test query patterns also migrate.** Some other repos accept a "tests stay legacy" carve-out, but here the tests directly mirror what the production code looks like. Consistent style benefits readability and reduces future drift.

## Architecture

### Translation table (per pattern)

| Legacy (1.x Query API) | SA 2.0 idiom |
|---|---|
| `cls.query.filter_by(x=v).one_or_none()` | `db.session.execute(db.select(cls).filter_by(x=v)).scalar_one_or_none()` |
| `cls.query.filter_by(x=v).first()` | `db.session.execute(db.select(cls).filter_by(x=v)).scalars().first()` |
| `cls.query.filter(cls.x == v).first()` | `db.session.execute(db.select(cls).where(cls.x == v)).scalars().first()` |
| `cls.query.count()` | `db.session.scalar(db.select(db.func.count()).select_from(cls))` |
| `cls.query.filter(...)` (returned for composition) | `db.select(cls).where(...)` — caller composes further with `.where()` / `.subquery()` / `.join()` |
| `cls.query.with_entities(cls.col).order_by(...)` | `db.select(cls.col).order_by(...)` |
| `db.session.query(cls).filter(...)` | `db.select(cls).where(...)` (executed by caller via `db.session.execute(...)`) |
| `db.session.query(db.func.count(cls.id)).one_or_none()` | `db.session.scalar(db.select(db.func.count(cls.id)))` |
| `db.session.query(db.func.sum(cls.amount)).join(...)` (composed) | `db.select(db.func.sum(cls.amount)).join(...)`; execute via `db.session.scalar(...)` |
| `Query[X]` (return type annotation) | `Select[X]` |
| `.subquery()` on Query | `.subquery()` on Select (identical method) |
| `db.aliased(Model, subq)` | unchanged (still `db.aliased(...)`) |
| `q.one_or_none()` after composition | `db.session.execute(q).scalar_one_or_none()` |
| `q.first()` after composition | `db.session.execute(q).scalars().first()` |
| `q.all()` after composition | `db.session.execute(q).scalars().all()` |
| `q.count()` after composition | `db.session.scalar(db.select(db.func.count()).select_from(q.subquery()))` |

### Recursive CTE in `BlockDAO._block_chain`

Existing (lines ~301-302):
```python
q = BlockDAO.query.filter(BlockDAO.id == self.id).cte(recursive=True)
return q.union_all(BlockDAO.query.filter(BlockDAO.id == q.c.prev_id))
```

After:
```python
base = (
    db.select(BlockDAO)
    .where(BlockDAO.id == self.id)
    .cte(recursive=True)
)
return base.union_all(
    db.select(BlockDAO).where(BlockDAO.id == base.c.prev_id)
)
```

Return type stays `CTE`. Same SQL output (verify post-migration by re-running `tests/test_models.py::test_longest_chain_block_property_matches_cte`).

### Chain-factory return types

The 16 methods returning `Query[X]` become `Select[X]`. Sites (line numbers approximate):

**`TransactionDAO`:**
- `transactions_chain(cls, block_chain: Query[BlockDAO]) -> Query[TransactionDAO]` (line 110-116) → `Select[BlockDAO]` / `Select[TransactionDAO]`.

**`OutflowDAO`:**
- `outflows_chain(cls, transactions_chain: Query[TransactionDAO]) -> Query[OutflowDAO]` (line 175-184).

**`InflowDAO`:**
- `inflows_chain(cls, transactions_chain: Query[TransactionDAO]) -> Query[InflowDAO]` (line 231-240).

**`BlockDAO`:**
- `block_chain` property (line 304-306) → `Select[BlockDAO]`.
- `transactions_chain` property (line 308-310) → `Select[TransactionDAO]`.
- `outflows_chain` property (line 312-314) → `Select[OutflowDAO]`.
- `inflows_chain` property (line 316-318) → `Select[InflowDAO]`.
- `address_transactions(self, address: str)` (line 329-330) → `Select[TransactionDAO]`.
- `longest_chain_blocks_q(cls)` (line 381-389) → `Select[BlockDAO]`.
- `longest_chain_transactions_q(cls)` (line 398-407) → `Select[TransactionDAO]`.
- `longest_chain_outflows_q(cls)` (line 411-422) → `Select[OutflowDAO]`.
- `longest_chain_inflows_q(cls)` (line 427-438) → `Select[InflowDAO]`.

**`ChainDAO`:**
- `blocks` property (line 497-501) → `Select[BlockDAO]`.
- `transactions` property (line 503-507) → `Select[TransactionDAO]`.
- `outflows` property (line 509-513) → `Select[OutflowDAO]`.
- `inflows` property (line 515-519) → `Select[InflowDAO]`.
- `unspent_outflows(self, address, filter_pending=False) -> Query[OutflowDAO]` (line 521-535) → `Select[OutflowDAO]`.
- `unforgiven_outflows(self, subject, address=None, filter_pending=False)` → `Select[OutflowDAO]`.
- `wallet_leaderboard(self, earliest=None, latest=None, limit=None)` → `Select[tuple[str, int]]` or similar (returns address + sum).

### Composed-method updates

The 6 downstream `ChainDAO` methods (`unspent_outflows`, `wallet_balance`, `unforgiven_outflows`, `subject_balance`, `subject_support`, `wallet_leaderboard`) compose on the chain-factory properties. Their internal `.filter(...)` calls work unchanged (Select accepts `.filter()`) but new instances default to `.where(...)`. Where the function calls `.one_or_none()` / `.first()` / `.all()` directly on what is now a Select, it must wrap with `db.session.execute(...)` and use the appropriate Result extractor.

For example, `ChainDAO.wallet_balance`:
```python
# Before
amount = q2.one_or_none()
return (amount[0] or 0) if amount is not None else 0

# After
amount = db.session.execute(q2).one_or_none()
return (amount[0] or 0) if amount is not None else 0
```

Or more idiomatically with `.scalar()` since we're extracting a single value:
```python
amount = db.session.scalar(q2)
return amount or 0
```

Prefer the second form where the query yields a single scalar (sums, counts).

## Changes

### Files

- Modify: `src/cancelchain/models.py` — 35 call-site translations + 16 return-type annotation changes (`Query[X]` → `Select[X]`). Plus an updated import: `from sqlalchemy import Select` (replacing or augmenting the existing `Query` import).
- Modify: `src/cancelchain/api.py` — 1 site (`lc_dao.address_transactions(address).first()` → `db.session.execute(lc_dao.address_transactions(address)).scalars().first()`).
- Modify: `tests/test_models.py` — 27 call-site translations. No new tests; no test removed.

No schema changes. No `database.py` changes. No dependency changes.

### Imports

`src/cancelchain/models.py` currently imports `Query` from `sqlalchemy.orm`. After 7a, `Query` is no longer referenced — remove the import. Add `from sqlalchemy import Select`.

The `from cancelchain.database import db` import stays; `db.select` / `db.session.execute` / `db.func` / `db.aliased` all still resolve via Flask-SQLAlchemy's facade.

## Test plan

- **Regression: all 236 existing tests stay green.** This is the primary verification.
- **Property-against-CTE re-run.** `tests/test_models.py::test_longest_chain_block_property_matches_cte` exercises the recursive CTE path end-to-end; if the translation of `_block_chain` breaks anything, this test catches it.
- **Benchmark equivalence.** Run `bench/rebuild_walk_bench.py` before and after; per-step times should be within noise (~0.25 ms/step on local SQLite). Document the numbers in the impl PR body.
- **All 4 CI gates clean.** `uv run ruff check src tests` + `uv run ruff format --check src tests` + `uv run mypy` + `uv run pytest`.

Test count: 236 (unchanged).

## Acceptance

- `grep -rn 'Model\.query\|\.query\.\|\.query\b' src/cancelchain/ tests/` returns nothing (or only matches for `requests_proxy` fixture-name false positives — verify by eye).
- `grep -rn 'db\.session\.query' src/cancelchain/ tests/` returns nothing.
- `grep -n 'Query\[' src/cancelchain/models.py` returns nothing (all annotations migrated to `Select[X]`).
- `uv run mypy` exits 0 (the existing `mypy: disable-error-code` block at the top of `models.py` stays; no new errors introduced).
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run pytest` exits 0; test count is 236.
- `uv run pytest --runmulti` exits 0.
- `bench/rebuild_walk_bench.py --sizes 1000 10000 100000` per-step times match Phase 6.5/6.6's baseline (~0.25 ms/step on local SQLite).
- `docker build --target builder -t cc-phase7a .` succeeds.

## Risks

- **`db.session.execute(stmt).scalars()` returns a `ScalarResult` iterator, NOT a list.** Iterating it twice is undefined behavior; assigning it to a variable and reusing it would break. The migration must wrap with `.all()` or `list(...)` before reuse — verify by `grep -B2 -A2 '\.scalars()' src/cancelchain/`.

- **`.one_or_none()` vs `.scalar_one_or_none()` mismatch.** The legacy `query.one_or_none()` returns the Model instance (or None). The SA 2.0 `Result.one_or_none()` returns a Row tuple (or None) — you'd index `[0]` to get the model. The correct translation is usually `.scalar_one_or_none()` which returns the model directly. Mechanical search-and-replace can get this wrong; review each `.one_or_none()` site individually.

- **Aggregate queries with multiple columns.** `db.session.query(db.func.sum(OutflowDAO.amount)).join(...)` returns a Row containing one column; the caller indexes `[0]`. The 2.0 translation can be either:
  - `db.session.execute(stmt).one_or_none()` → returns Row → caller still does `[0]`.
  - `db.session.scalar(stmt)` → returns the single value directly → caller drops `[0]`.
  Where the existing code does `(amount[0] or 0) if amount is not None else 0`, the cleaner 2.0 form is `db.session.scalar(stmt) or 0`. Migrate to the cleaner form unless it changes semantics.

- **Recursive CTE column-access subtleties.** `q.c.prev_id` (legacy) and `base.c.prev_id` (2.0) both work, but the 2.0 form may expose subtly different column proxy types in edge cases. The property-against-CTE test is the safety net.

- **Mypy errors surfacing despite the existing override block.** The translation might trip new error codes not covered by the existing `# mypy: disable-error-code` block. If that happens, add the new code to the block in 7a (temporary; 7b removes the whole block). Don't add per-line ignores.

- **Test fixtures using `Model.query`.** The 27 call sites in `tests/test_models.py` include things like `BlockDAO.query.count()` inside `assert` lines. The translation works but the new form is more verbose:
  - Before: `assert BlockDAO.query.count() == 2`
  - After: `assert db.session.scalar(db.select(db.func.count()).select_from(BlockDAO)) == 2`
  Acceptable for consistency; reviewers may flag verbosity. Alternative: extract a `_count(model)` test helper. Skip the helper for 7a unless ruff complains; revisit if a Copilot reviewer asks for it.

- **`api.py:196` lazy import.** That site uses `lc_dao.address_transactions(address).first()`. After migration, `lc_dao.address_transactions(address)` returns a `Select`; the caller wraps it in `db.session.execute(...).scalars().first()`. Verify the `db` import is available in `api.py` (it isn't currently — `api.py` doesn't import db). Add `from cancelchain.database import db` if needed.

## Open decisions

None at design time. Brainstorming resolved:
- Two-PR sequencing (7a syntax, then 7b DeclarativeBase + mypy ignore removal).
- Chain factories migrate to `Select[X]` in 7a, not stay `Query[X]`.
- Both production code and tests migrate (no carve-out).

## What comes next

- **Phase 7b — typed DeclarativeBase + mypy override removal.** Define `class Base(DeclarativeBase): pass`, wire `db = SQLAlchemy(model_class=Base)`, switch all DAO inheritance to `db.Model` (which IS now `Base`), remove the `# mypy: disable-error-code` block at the top of `models.py`, address any new mypy errors that surface. Small mechanical PR; landmark milestone (closes Phase 3's explicit sunset commitment for the mypy override).
- **Phase 7+ — Other ROADMAP items.** Generalize materialization to all chains; cross-worker `_is_longest()` cache invalidation; Alembic migration framework. See `docs/superpowers/ROADMAP.md`.
