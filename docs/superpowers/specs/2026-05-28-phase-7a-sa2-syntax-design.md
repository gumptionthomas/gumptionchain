# Phase 7a — SQLAlchemy 2.0 call-site syntax migration

**Status:** Draft for review
**Date:** 2026-05-28
**Scope:** Translate all legacy `Model.query` / `db.session.query(...)` call sites (36 in `src/cancelchain/models.py`, 1 in `src/cancelchain/api.py`, 1 in `src/cancelchain/browser.py`, 38 in `tests/test_models.py`, 18 in `tests/test_chain.py`) to the SA 2.0 idiom (`db.session.execute(db.select(...))` + `.scalar()` / `.scalars()` / `.scalar_one_or_none()` extractors). Migrate the 21 `Query[X]` chain-factory return type annotations (and 3 parameter annotations on the same chain-factory methods) to `Select[tuple[X]]` (SA 2.x's `Select` is parameterized by row shape, not by the scalar entity). Update the three `chain.py` caller sites and assorted test-suite consumer sites that previously iterated, `.count()`-ed, or `.paginate()`-d a `Query` and now need explicit execution wrappers (`db.session.execute(...).scalars()` for iteration, `db.session.scalar(db.select(db.func.count()).select_from(stmt.subquery()))` for count, `db.paginate(stmt)` for pagination). The Phase 7 sequencing — split per ROADMAP — is "7a: syntax migration, then 7b: DeclarativeBase + mypy override removal." This spec covers 7a only; 7b gets its own spec/plan after 7a lands.

## Goal

Bring `src/cancelchain/models.py`, `src/cancelchain/api.py`, and `tests/test_models.py` onto the SQLAlchemy 2.0 idiomatic query API. The legacy 1.x `Model.query` / `db.session.query` syntax still works under Flask-SQLAlchemy 3.1.1 + SQLAlchemy 2.0.50, but is deprecated for new code and is one of the two blockers (alongside the dynamic `db.Model` base) preventing removal of the `mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` block at the top of `models.py`. Phase 7a removes that blocker by retiring the legacy query syntax from active call sites while preserving the `db.Model` infrastructure (Phase 7b switches to typed `DeclarativeBase`).

## Non-goals

- **No DeclarativeBase migration.** Phase 7b. `db.Model` stays the base class; `Model.query` remains defined but unused inside the codebase post-7a.
- **No `mypy: disable-error-code` block removal.** The block stays at the top of `models.py` through 7a; Phase 7b removes it.
- **No behavior changes.** This is purely a syntax pass. All 236 existing tests stay green. The generated SQL should be plan-equivalent (or at most equivalent up to SA 2.0's compiler optimizations).
- **No work outside the listed target files.** `api_client.py`, `wallet.py`, `node.py`, `miller.py`, `command.py`, `tasks.py` are untouched. In addition to the three primary targets (`models.py`, `api.py`, `tests/test_models.py`), the migration touches `chain.py` (three caller-side updates for `Chain.unspent_outflows` / `unforgiven_outflows` / `unforgiven_address_outflows`), `browser.py` (one `paginate()` call on `ChainDAO.chains()`), and `tests/test_chain.py` (twelve consumer sites on `ChainDAO.blocks` / `.transactions` / `.outflows` / `.inflows` / `.wallet_leaderboard`). See Changes / Files.
- **No new chain-membership materialization changes.** Phase 6.6 closed those; 7a is purely a syntax pass.
- **No new tests.** The translation is API-equivalent; existing tests catch regressions.
- **No performance work.** The benchmark harness (PR #74) is available to verify equivalence.

## Decisions taken during brainstorming

- **Two-PR Phase 7 sequencing.** 7a (this spec) handles call-site syntax translation; 7b handles DeclarativeBase + mypy override removal. Three-PR split (7a / 7b / 7c) was rejected as overhead for a tiny mypy-removal-only PR.
- **Chain-factory return types migrate to `Select[tuple[X]]` in 7a.** Same composability as `Query[X]` (Select supports `.where()` / `.filter()` / `.subquery()` / `.join()`). The row-shape `tuple[X]` parameterization matches SA 2.x's actual typing of `db.select(Model)`; see the translation-table note. Keeping `Query[X]` as a public return type while internally using `db.session.execute(db.select(...))` was rejected as half-measure.
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
| `Query[X]` (return type annotation) | `Select[tuple[X]]` — SA 2.x's `Select` is parameterized by row shape, not the scalar entity. `db.select(BlockDAO)` is typed `Select[tuple[BlockDAO]]`; using `Select[BlockDAO]` would surface `return-value`/`arg-type` mypy errors not covered by the existing per-file override block. Fall back to `Select[Any]` (matches the existing `wallet_leaderboard` precedent) only if `Select[tuple[X]]` proves awkward at a specific call site. |
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

The 21 methods returning `Query[X]` become `Select[tuple[X]]` (`Select` is parameterized by row shape, not by the scalar entity — see the translation table note). Plus 3 parameter annotations on the same chain factories also migrate from `Query[BlockDAO]` / `Query[TransactionDAO]` to `Select[tuple[BlockDAO]]` / `Select[tuple[TransactionDAO]]` (lines 111, 176, 232 — the `block_chain` and `transactions_chain` parameters). Sites (line numbers approximate):

**`TransactionDAO`:**
- `transactions_chain(cls, block_chain: Query[BlockDAO]) -> Query[TransactionDAO]` (line 110-116) → both annotations become `Select[tuple[BlockDAO]]` / `Select[tuple[TransactionDAO]]`.

**`OutflowDAO`:**
- `outflows_chain(cls, transactions_chain: Query[TransactionDAO]) -> Query[OutflowDAO]` (line 175-184).

**`InflowDAO`:**
- `inflows_chain(cls, transactions_chain: Query[TransactionDAO]) -> Query[InflowDAO]` (line 231-240).

**`BlockDAO`:**
- `block_chain` property (line 304-306) → `Select[tuple[BlockDAO]]`.
- `transactions_chain` property (line 308-310) → `Select[tuple[TransactionDAO]]`.
- `outflows_chain` property (line 312-314) → `Select[tuple[OutflowDAO]]`.
- `inflows_chain` property (line 316-318) → `Select[tuple[InflowDAO]]`.
- `address_transactions(self, address: str)` (line 329-330) → `Select[tuple[TransactionDAO]]`.
- `longest_chain_blocks_q(cls)` (line 381-389) → `Select[tuple[BlockDAO]]`.
- `longest_chain_transactions_q(cls)` (line 398-407) → `Select[tuple[TransactionDAO]]`.
- `longest_chain_outflows_q(cls)` (line 411-422) → `Select[tuple[OutflowDAO]]`.
- `longest_chain_inflows_q(cls)` (line 427-438) → `Select[tuple[InflowDAO]]`.

**`ChainDAO`:**
- `blocks` property (line 497-501) → `Select[tuple[BlockDAO]]`.
- `transactions` property (line 503-507) → `Select[tuple[TransactionDAO]]`.
- `outflows` property (line 509-513) → `Select[tuple[OutflowDAO]]`.
- `inflows` property (line 515-519) → `Select[tuple[InflowDAO]]`.
- `address_transactions(self, address: str) -> Query[TransactionDAO]` (line 764-765, the `ChainDAO` delegate that calls into `BlockDAO.address_transactions`) → `Select[tuple[TransactionDAO]]`.
- `unspent_outflows(self, address, filter_pending=False) -> Query[OutflowDAO]` (line 521-535) → `Select[tuple[OutflowDAO]]`.
- `unforgiven_outflows(self, subject, address=None, filter_pending=False)` → `Select[tuple[OutflowDAO]]`.
- `chains(cls) -> Query[ChainDAO]` (line 792-796) → `Select[tuple[ChainDAO]]`.
- `wallet_leaderboard(self, earliest=None, latest=None, limit=None)` → `Select[Any]` (returns `(address, sum)` rows; tuple-shape typing here adds noise without value — falls back to the spec's documented `Select[Any]` escape hatch).

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

- Modify: `src/cancelchain/models.py` — 36 call-site translations + 21 return-type annotation changes (`Query[X]` → `Select[tuple[X]]`, covering the 4 previously-undercounted `ChainDAO` factories: `unspent_outflows`, `unforgiven_outflows`, `chains`, `wallet_leaderboard`) + 3 parameter annotation changes on the chain-factory methods. Includes the previously-missed `ChainDAO.get` legacy `cls.query` site, the `ChainDAO.address_transactions` delegate annotation, the `PendingTxnDAO.json_datas` `cls.query.with_entities(...)` site (this is the actual method name; an earlier draft mis-called it `txn_jsons`), and the `ApiToken.get` `cls.query.filter_by(...)` site (an earlier draft mis-called it `WalletDAO.get` — there is no `WalletDAO` class in this codebase). Plus an updated import: `from sqlalchemy import Select` (replacing or augmenting the existing `Query` import).
- Modify: `src/cancelchain/api.py` — 1 site (`lc_dao.address_transactions(address).first()` → `db.session.execute(lc_dao.address_transactions(address)).scalars().first()`).
- Modify: `src/cancelchain/browser.py` — 1 site. `ChainDAO.chains().paginate()` (line 37, currently carries a `# type: ignore[attr-defined]` comment because `Select` has no `.paginate()` method) → `db.paginate(ChainDAO.chains())` using Flask-SQLAlchemy 3.x's SA-2.0-compatible top-level `db.paginate(stmt)` helper. The `type: ignore` comment goes away.
- Modify: `src/cancelchain/chain.py` — 3 caller-side updates. `Chain.unspent_outflows` (line 342), `Chain.unforgiven_outflows` (line 361), and `Chain.unforgiven_address_outflows` (line 380) iterate the return value of `self.to_dao().unspent_outflows(...)` / `unforgiven_outflows(...)` directly. After migration those DAO methods return a `Select`, which is a SQL expression — iterating it would yield column clauses, not `OutflowDAO` rows. Each call site wraps with `db.session.execute(...).scalars()` to recover the row iterator. Requires adding `from cancelchain.database import db` to `chain.py` if not already present.
- Modify: `tests/test_models.py` — 38 site translations total (27 original + 3 + 4 + 2 + 2): the 27 original legacy-query patterns; 3 `[b.id for b in longest.block.block_chain]` iteration sites (lines 216, 335, 359 — wrap with `db.session.execute(longest.block.block_chain).scalars()`); 4 `dao.unspent_outflows(...).count()` assertions (lines 30, 61, 80, 85 — wrap with `db.session.scalar(db.select(db.func.count()).select_from(stmt.subquery()))` via a small `_count_select(stmt)` helper); 2 `longest.blocks.statement.compile(...)` / `non_longest.blocks.statement.compile(...)` sites (lines 237, 298 — `Select` has no `.statement` wrapper, so the call becomes `longest.blocks.compile(...)` directly); 2 `(d for d in ChainDAO.chains() if ...)` generator-expression iterations (lines 182, 289 — wrap with `(d for d in db.session.execute(ChainDAO.chains()).scalars() if ...)`). No new tests; no test removed.
- Modify: `tests/test_chain.py` — 18 site translations (8 + 8 + 2). `test_dao` (lines 299-325) reads `chain.to_dao(create=True).blocks` / `.transactions` / `.outflows` / `.inflows` (and the analogous `alt_*` variants), then calls `.count()` and `.all()` on each — 8 `.count()` + 8 `.all()` patterns mapped to the `_count_select` helper and `db.session.execute(stmt).scalars().all()` respectively. Plus 2 `list(chain.to_dao(create=True).wallet_leaderboard(...))` calls (lines 327, 335 — wrap with `list(db.session.execute(stmt))` since `wallet_leaderboard` returns row tuples, not scalar models). The shared `_count_select` helper goes in a new `tests/_sa_helpers.py` module (or at the top of `tests/conftest.py`) to keep both test files DRY; final placement decided during impl.

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

- `grep -rn 'Model\.query\|\.query\.\|\.query\b' src/cancelchain/ tests/` returns nothing — verify by eye against false positives like the `requests_proxy` fixture name. Note: the existing `Model.query` mention in the `mypy: disable-error-code` header comment block at the top of `src/cancelchain/models.py` is rewritten as part of Step 1 (the comment's "Phase 6 modernizes those call sites" phrasing literally describes this PR's work and is stale post-7a), so the grep is clean.
- `grep -rn 'db\.session\.query' src/cancelchain/ tests/` returns nothing.
- `grep -n 'Query\[' src/cancelchain/models.py` returns nothing (all 24 occurrences — 21 returns + 3 params — migrated to `Select[tuple[X]]`, or `Select[Any]` for the one row-tuple leaderboard case).
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

- **Test fixtures using `Model.query`.** The 27 original `tests/test_models.py` legacy-query sites include things like `BlockDAO.query.count()` inside `assert` lines. The translation works but the new form is more verbose. The 7a impl adds two small helpers in `tests/_sa_helpers.py` (or `tests/conftest.py`) to keep both test files DRY:
  - `_count(model)` for `SELECT COUNT(*) FROM <model>` — used by `BlockDAO.query.count()` → `_count(BlockDAO)` etc.
  - `_count_select(stmt)` for `SELECT COUNT(*) FROM (<stmt>)` — used by `dao.unspent_outflows(...).count()` → `_count_select(dao.unspent_outflows(...))` and the analogous `tests/test_chain.py` consumer sites on `dao.blocks` / `.transactions` / `.outflows` / `.inflows`. Without this helper, every assert line bloats to a `db.session.scalar(db.select(db.func.count()).select_from(stmt.subquery()))` call, which is noisy in tests.

- **`.statement.compile(...)` is gone in 2.0.** SA 1.x `Query` exposed `.statement` to get the underlying `Select`; SA 2.0 `Select` *is* the statement, so there's no `.statement` attribute. The two `tests/test_models.py` assertions at lines 237 and 298 (`longest.blocks.statement.compile(...)` and `non_longest.blocks.statement.compile(...)`) must drop the `.statement` access and call `.compile(...)` directly on the Select.

- **`Query.paginate()` is not on `Select`.** `src/cancelchain/browser.py:37` calls `ChainDAO.chains().paginate()` to drive the `/chains` view template, currently with a `# type: ignore[attr-defined]` comment because Flask-SQLAlchemy 3.x's legacy `Query.paginate()` shim is what makes that work. The 2.0 equivalent is the top-level `db.paginate(stmt)` helper, which works on a `Select` and returns the same `Pagination` object used by `chains.html`. The migration drops the `type: ignore` comment as a bonus.

- **`wallet_leaderboard` returns row tuples, not Models.** The current `tests/test_chain.py:327` and `:335` do `list(chain.to_dao(create=True).wallet_leaderboard(...))` and then index `[0][0]` / `[0][1]` for `(address, sum)`. After migration the wrapping pattern is `list(db.session.execute(stmt))` (NOT `.scalars()`), preserving Row-tuple semantics so the existing indexing still works.

- **`api.py:196` lazy import.** That site uses `lc_dao.address_transactions(address).first()`. After migration, `lc_dao.address_transactions(address)` returns a `Select`; the caller wraps it in `db.session.execute(...).scalars().first()`. Verify the `db` import is available in `api.py` (it isn't currently — `api.py` doesn't import db). Add `from cancelchain.database import db` if needed.

## Open decisions

None at design time. Brainstorming resolved:
- Two-PR sequencing (7a syntax, then 7b DeclarativeBase + mypy ignore removal).
- Chain factories migrate to `Select[tuple[X]]` in 7a, not stay `Query[X]`.
- Both production code and tests migrate (no carve-out).

## What comes next

- **Phase 7b — typed DeclarativeBase + mypy override removal.** Define `class Base(DeclarativeBase): pass`, wire `db = SQLAlchemy(model_class=Base)`, switch all DAO inheritance to `db.Model` (which IS now `Base`), remove the `# mypy: disable-error-code` block at the top of `models.py`, address any new mypy errors that surface. Small mechanical PR; landmark milestone (closes Phase 3's explicit sunset commitment for the mypy override).
- **Phase 7+ — Other ROADMAP items.** Generalize materialization to all chains; cross-worker `_is_longest()` cache invalidation; Alembic migration framework. See `docs/superpowers/ROADMAP.md`.
