# Phase 7b — typed `DeclarativeBase` + mypy override removal

**Status:** Draft for review
**Date:** 2026-05-28
**Scope:** Switch Flask-SQLAlchemy's `db = SQLAlchemy()` to use a typed `DeclarativeBase` subclass via `db = SQLAlchemy(model_class=Base)`, then remove the `# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` block at the top of `src/cancelchain/models.py` and the stale 6-line header comment that explained it. Fix any mypy errors that surface from removing the override. Phase 7b closes Phase 3's explicit sunset commitment for the per-file mypy override and is the second half of the two-PR Phase 7 split that began with Phase 7a's SA 2.0 call-site syntax migration (merged as commit `4070978`).

## Goal

Make `src/cancelchain/models.py` mypy-strict-clean without any per-file disable block. After Phase 7a, every legacy `Model.query` / `db.session.query(...)` call site is gone and every `Query[X]` annotation is now `Select[tuple[X]]`, so the only remaining blocker for the override removal is the dynamic `db.Model` base class — Flask-SQLAlchemy attaches `db.Model` at runtime via `SQLAlchemy()`, which mypy strict cannot resolve (it sees `Any`), triggering `name-defined` on every `class XDAO(db.Model):` declaration and `misc` on every "Class cannot subclass 'Model' of type 'Any'" follow-on. Switching to `SQLAlchemy(model_class=Base)` with `Base(DeclarativeBase)` gives mypy a concrete typed base to resolve against, at which point the four disabled error codes should go quiet without further intervention on most DAO sites.

## Non-goals

- **No new behavior.** Every test that passes before this PR passes after. Test count stays exactly 236 (matching the 7a baseline).
- **No call-site rewrites.** Phase 7a handled all of those. If a new mypy error surfaces from removing the override, the fix is either an explicit annotation, an explicit `cast(...)`, or a narrowly-scoped per-line `# type: ignore[code]` — never a broader refactor.
- **No SQL changes.** The `__tablename__`, `mapped_column`, `relationship`, and `__table_args__` declarations are all already SA 2.0 idiomatic and stay unchanged.
- **No removal of pre-existing per-line `# type: ignore[assignment]` lines.** The five existing per-line ignores in `models.py` (lines 166, 232, 233, 519, 771 in the current diff) are about assigning `X | None` to a `Mapped[X]` non-optional slot inside a DAO `__init__`. That mismatch is a runtime invariant (the value is non-None at the assignment site) that `DeclarativeBase` does not improve. They stay unless we find a cleaner pattern, which is out of scope here.
- **No dataclass-style DAOs.** `MappedAsDataclass` was rejected as invasive (it would change every DAO's `__init__` signature, with real behavior risk on the existing constructors that do non-trivial validation and side effects inside `with db.session.no_autoflush:` blocks).
- **No dependency bumps.** SQLAlchemy 2.0.50, Flask-SQLAlchemy 3.1.1 (existing) — both already support typed `DeclarativeBase` with the `model_class=Base` pattern.

## Decisions taken during brainstorming

- **Plain typed `DeclarativeBase`.** The codebase already uses `Mapped[]` and `mapped_column` throughout, so the canonical SA 2.0 typed base is the obvious fit. `MappedAsDataclass` was considered and rejected (see Non-goals).
- **Single-PR shape.** The 7a plan called Phase 7b a "small mechanical PR; landmark milestone." We commit to that framing: one PR switches the base class, removes the override block, and fixes whatever mypy surfaces — all in one shot. If the surfaced error count balloons unexpectedly during impl, we fall back to per-line ignores within this PR (with TODOs pointing at a Phase 7c follow-up) rather than splitting into multiple PRs.
- **DAO inheritance keeps using `db.Model`.** After the base swap, `db.Model` IS `Base`. Rewriting every `class XDAO(db.Model):` to `class XDAO(Base):` is gratuitous churn — the dynamic accessor pattern stays since it's how Flask-SQLAlchemy exposes the configured base.
- **Verify on the full tree, not just `src/cancelchain`.** `uv run mypy` (the project's mypy invocation per `pyproject.toml`'s `[tool.mypy] files = ["src/cancelchain"]`) runs on the right files. We don't expand to tests in this PR — tests don't import `db.Model` and don't subclass DAOs, so they should not need changes.
- **Per-line ignores are the explicit escape hatch.** For any error code that surfaces but resists a clean type-annotation fix, the impl PR adds a narrow per-line `# type: ignore[code]` with a short comment explaining why, rather than re-adding a file-level disable. This mirrors the pre-existing pattern in models.py (5 per-line `# type: ignore[assignment]` lines from before and during 7a).

## Architecture

### Base class definition (in `database.py`)

Current `src/cancelchain/database.py` is six lines:

```python
from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
```

Post-7b:

```python
from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
```

`Base` is exported only to the extent that other modules need to reference it directly (today they don't — they use `db.Model`). Keeping it module-local is fine; Phase 7+ may export it if needed.

### `models.py` header

Current top of `src/cancelchain/models.py` (lines 3-9 in the post-7a diff — lines 3-8 are the 6-line `#` explanatory comment, line 9 is the `# mypy: disable-error-code` directive; lines 10-11 immediately below are `import datetime` and `import uuid` and are NOT touched by this PR):

```python
# Flask-SQLAlchemy's `db.Model` is dynamically attached and shows up as
# `Any` to mypy strict, which triggers `name-defined` (Name "db.Model"
# is not defined) and `misc` (Class cannot subclass "Model" of type
# "Any") errors on every DAO class declaration here. Phase 7b will
# switch to a typed `DeclarativeBase` subclass and remove this
# suppression.
# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"
```

Post-7b: both the 6-line comment block AND the `# mypy: disable-error-code` directive are removed. No replacement header — `models.py` should look like any other file in the module after this.

### Expected mypy error coverage by code

| Disabled code | After base swap | Mitigation if surfaces |
|---|---|---|
| `name-defined` (Name "db.Model" is not defined) | Disappears — `db.Model` resolves through the typed `Base` | n/a |
| `misc` (Class cannot subclass "Model" of type "Any") | Disappears — same root cause | n/a |
| `no-untyped-call` | Mostly disappears — SA 2.x's `db.select`, `db.session.execute`, `Result.scalar_one_or_none`, `db.session.scalar`, `db.aliased`, etc. are typed | Per-call `# type: ignore[no-untyped-call]` with a one-line comment, OR an explicit annotation on the surrounding helper |
| `no-any-return` | Mostly disappears — `db.session.execute(db.select(Entity)).scalar_one_or_none()` returns `Entity \| None`; DAO methods that return chain-factory `Select`s have explicit `Select[tuple[X]]` annotations from 7a | Per-line `# type: ignore[no-any-return]` for any leftover Result/Row-shape edge cases, OR explicit `cast(...)` at the return site |

### Sites with elevated risk

- **`BlockDAO._block_chain`** (returns `CTE`) — SA 2.x types CTEs as a structural type; the explicit `CTE` annotation is already present from 7a and should satisfy mypy. Watch for `no-any-return` on the `union_all(...)` chain.
- **`BlockDAO.block_chain` and friends** that return `Select[tuple[X]]` — already annotated by 7a. Any residual error would be a real typing inconsistency worth fixing inline.
- **`ChainDAO._is_longest()` and the materialization sync code** — sites that mix `LongestChainBlockDAO` reads with column-level `.position` / `.block_id` access. These are post-7a still SA 2.0-idiomatic, but the typed base may surface `no-any-return` on a `db.session.scalar(...)` call returning `int | None` where the caller treats it as `int`. Fix by explicit `or 0` (already in place at most sites) or `cast()`.
- **`PendingTxnDAO.json_datas` and `BlockDAO.block_hashes`** — generators that yield from `db.session.execute(stmt)` tuple iteration. Should already be typed correctly post-7a; verify.
- **Relationship attribute access in DAO `__init__` methods** — the existing 5 per-line `# type: ignore[assignment]` ignores cover this. They stay (Non-goal).

### What `db.Model` resolves to after the swap

Today: `db.Model` is set dynamically by `SQLAlchemy()` to a default base; mypy sees `Any`. After: `db.Model` is `Base` (the `DeclarativeBase` subclass we wired via `model_class=Base`). Mypy can resolve `Base` statically and the `class XDAO(db.Model):` declarations become typed `class XDAO(Base):` from mypy's perspective without rewriting the source. This is what makes the override removal mechanical rather than invasive.

### `Any` and `Select` imports

Phase 7a added both to the `typing` and `sqlalchemy` import groups at the top of `models.py`. Both stay; `Any` is still used by `wallet_leaderboard`'s `Select[Any]` annotation, and `Select` is used by every chain-factory return type. The 7a plan's Step 1 removed the dead `if TYPE_CHECKING:` block and the unused `TYPE_CHECKING` import; nothing further to clean up here.

## Changes

### Files

- Modify: `src/cancelchain/database.py` — add `Base(DeclarativeBase)` class definition + the `model_class=Base` keyword on the `SQLAlchemy(...)` call + one new import (`from sqlalchemy.orm import DeclarativeBase`).
- Modify: `src/cancelchain/models.py` — remove the 6-line stale header comment block (lines 3-8) and the `# mypy: disable-error-code` directive line (line 9). Lines 10-11 (`import datetime`, `import uuid`) are NOT touched. Nothing else changes unless mypy surfaces an error that needs an inline annotation or per-line ignore.
- Potentially modify: `src/cancelchain/models.py` — narrowly-scoped per-line `# type: ignore[code]` additions for any surfaced mypy errors that resist clean annotation fixes. Anticipated count: 0-5 lines. If significantly more, surface as a concern during impl and consider a Phase 7c follow-up rather than ballooning this PR.

### Imports

`src/cancelchain/database.py`: add `from sqlalchemy.orm import DeclarativeBase`.

`src/cancelchain/models.py`: no import changes anticipated. The `db = SQLAlchemy(...)` instance is still imported from `cancelchain.database`; `db.Model` still works at runtime; `from sqlalchemy import Select` and `from typing import Any, ClassVar` stay as-is from 7a.

## Test plan

- **Regression: all 236 existing tests stay green** on both Python 3.12 and 3.13. This is the primary verification — if any test depends on the dynamic `db.Model` attribute resolution surfacing as a specific type, it will fail here and we'll need a targeted fix.
- **Mypy strict on the full tree.** `uv run mypy` (which targets `src/cancelchain` per `pyproject.toml`) exits 0 with NO per-file disable block. This is the headline acceptance gate.
- **Bench harness unchanged.** `bench/rebuild_walk_bench.py --sizes 1000 10000 100000` matches the Phase 7a baseline (~0.25 ms/step on local SQLite).
- **All 4 CI gates clean.** `uv run ruff check src tests` + `uv run ruff format --check src tests` + `uv run mypy` + `uv run pytest`.

Test count: 236 (unchanged). No new tests; no test removed.

## Acceptance

- `grep -n 'mypy: disable-error-code' src/cancelchain/models.py` returns nothing.
- `grep -n 'DeclarativeBase' src/cancelchain/database.py` returns two matches: the `from sqlalchemy.orm import DeclarativeBase` import line and the `class Base(DeclarativeBase):` class definition.
- `grep -n 'model_class=Base' src/cancelchain/database.py` returns the `SQLAlchemy(...)` call.
- `uv run mypy` exits 0 — the **headline acceptance gate**. No per-file overrides on `models.py`, no per-call `# type: ignore` lines beyond the documented pre-existing 5 plus any narrowly-scoped additions documented in the PR body.
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run pytest` exits 0; test count is 236.
- `uv run pytest --runmulti` exits 0.
- `uv run --python 3.13 pytest` also exits 0 with 236 passed.
- `bench/rebuild_walk_bench.py --sizes 1000 10000 100000` per-step times match the Phase 7a baseline (~0.25 ms/step on local SQLite).
- `docker build --target builder -t cc-phase7b .` succeeds.

## Risks

- **Unknown mypy error count.** We don't know until we run mypy after removing the override block how many leftover errors will surface. The prediction is "few or zero" based on the per-code analysis above, but predictions about typing surface are notoriously wrong. Mitigation: per-line ignores are an explicit escape hatch; if the count balloons past ~5 leftover lines, that's a signal to add a TODO/Phase 7c note in the PR body and either fix them in this PR (preferred) or split per the "single-PR shape" decision's fallback (if truly necessary).

- **Flask-SQLAlchemy `model_class=Base` interaction edge cases.** The `SQLAlchemy(model_class=...)` kwarg is documented and stable in Flask-SQLAlchemy 3.1+, but there's always a small chance that a Flask-SQLAlchemy-specific feature we use (the dynamic table_args, the lazy session, the relationship back-population) interacts with the typed base in a surprising way. Mitigation: the full test suite is the safety net; if anything breaks at runtime, we surface immediately.

- **`db.Model` mypy resolution through FSA stubs.** At runtime, `db.Model` IS the `Base` class we passed via `model_class=Base` — that's how Flask-SQLAlchemy 3.x wires it. Whether mypy sees that resolution depends on FSA's type stubs. If FSA's stubs propagate `model_class` through to `Model` (FSA 3.1+ does in most cases), then `class XDAO(db.Model):` becomes typed automatically and we get the override-removal for free. If they don't, mypy still sees `db.Model` as `Any`-typed and we have two options: (a) switch every `db.Model` subclass declaration to `(Base):` directly (one-line-per-class change across 11 classes in models.py — `TransactionDAO`, `OutflowDAO`, `InflowDAO`, `BlockDAO`, `LongestChainBlockDAO`, `ChainDAO`, `PendingTxnDAO`, `PendingIOflowDAO`, `ChainFill`, `ChainFillBlock`, `ApiToken` — mechanical, but expands the diff), or (b) add a per-line `# type: ignore[misc]` on each declaration. We default to (a) if FSA's stubs don't carry the type — direct subclassing is cleaner long-term and doesn't add a sea of per-line ignores. The impl PR makes this call after running mypy once with the override removed.

- **Test fixtures that subclass DAOs or rely on `db.Model` dynamically.** A repo-wide check should confirm none exist. Mitigation: a `grep -rn 'db\.Model\|db.Model\b' tests/` before the impl PR opens; if any matches, evaluate whether the typed base breaks the pattern.

- **Mypy strict in CI vs. local resolution.** Mypy versions can disagree about a few edge cases (mostly around `Any` propagation and `cast` validity). Mitigation: pin behavior to `uv run mypy` consistently and verify both locally AND in CI before merging.

- **Phase 6 reference in the stale comment is being removed.** The 6-line header comment block we're removing in this PR mentions "Phase 6 modernizes those call sites" — that text was rewritten in 7a to point at Phase 7b instead, but the entire block goes away now. Anyone searching `git log -S` for these phrases lands on the 7a/7b commits, which is fine. Documented here only because future code archaeologists might wonder where the comment went.

## Open decisions

None at design time. Brainstorming resolved:
- Plain typed `DeclarativeBase` (not `MappedAsDataclass`).
- Single-PR shape (not split into base swap + ignore cleanup).
- Per-line ignores are the explicit escape hatch for any leftover surface.
- The 5 pre-existing `# type: ignore[assignment]` lines stay (different issue, not in 7b scope).

## What comes next

- **Phase 7 closed.** With 7b landed, the SA 2.0 modernization initiative wraps. The per-file `mypy: disable-error-code` block — flagged for removal back in Phase 3 — is gone.
- **Phase 8+ — Other ROADMAP items.** Per `docs/superpowers/ROADMAP.md`: generalize materialization to all chains, cross-worker `_is_longest()` cache invalidation, Alembic migration framework. None of these depend on 7b's typing work; they're independent.
- **Phase 7c (conditional).** Only if 7b's mypy surface balloons past the per-line-ignore threshold during impl. The fallback path is explicit in the brainstorming decision — we don't pre-create 7c; it materializes only if needed.
