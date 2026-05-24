# Phase 2 — Library Upgrades

**Status:** Draft for review
**Date:** 2026-05-24
**Scope:** Runtime and dev dependency version bumps, plus the minimum source edits required to consume the new APIs. No new abstractions, no schema changes, no consensus-affecting refactors beyond what each library bump itself requires.

## Goal

Bring CancelChain's dependency baseline from "Phase 1 just landed, libraries are still on their 2022-era pins" to a current-as-of-2026-Q2 baseline. Phase 3 (typing pass, Marshmallow → Pydantic, requests → httpx, pycryptodome → cryptography, Alembic) lands against this newer foundation.

Concretely: after Phase 2, `[project.dependencies]` has no remaining major-version-back pins, every transitive resolution under `uv.lock` is on a maintained release line, and the codebase compiles + tests pass on Python 3.12 and 3.13.

## Non-goals (deferred to Phase 3 or later)

- SQLAlchemy 2.0 `Mapped[]` annotations and `db.session.execute(db.select(X))` query-style modernization. Phase 2 bumps the SA pin but keeps the legacy `Model.query` API that Flask-SQLAlchemy 3.1+ continues to expose.
- Marshmallow bump. Phase 3 replaces Marshmallow with Pydantic v2, so Phase 2 leaves the existing `marshmallow>=3.19` constraint alone.
- `requests` → `httpx` swap (Phase 3).
- `pycryptodome` → `cryptography` swap (Phase 3). Phase 2 may bump pycryptodome's floor, but only as part of the routine bumps PR.
- Alembic migrations.
- Removing `continue-on-error` from `ruff check` or `mypy` in CI. Phase 3 cleans up the existing lint backlog and tightens those gates.
- Any change to wallet file format, block header layout, or hash construction beyond what the pymerkle 5 API itself produces.

## Decisions taken during brainstorming

- **PR strategy:** series of small PRs, not one monolithic Phase 2 PR. Aligns with the project's "no scope creep" rule and the `wor`-before-merge habit; each step is independently reviewable and revertible.
- **Python floor:** raise from 3.9 to **3.12**. Drops 3.10 and 3.11 from the CI matrix. Aggressive but bounded — 3.12 is still in active support, 3.13 stays on the matrix.
- **pymerkle 5:** include in Phase 2. No hash-compat fixture gate is required because there is no production CancelChain to preserve (the `pymerkle>=4,<5` pin was defensive). If v5's API doesn't admit the existing `tree.prove_inclusion` / `verify_inclusion` usage in any reasonable way, the fallback is a small in-tree Merkle implementation (the use case is leaves = txids, no other complexity).
- **Marshmallow:** not bumped. Phase 3 deletes it.
- **SQLAlchemy 2.0:** bump only. Query-style modernization (`.query.filter_by(...)` → `db.session.execute(db.select(...))`) bundles with Phase 3's `Mapped[]` work because both touch the same files.
- **passlib:** swap to `argon2-cffi` in Phase 2 (not deferred). passlib has been unmaintained since 2020. The codebase uses only `passlib.hash.argon2` and `passlib.hash.pbkdf2_sha256` from a single class (`ApiToken`); replacing both is a small, contained change. Drop the pbkdf2 fallback path entirely — its only purpose was to cover passlib's missing argon2 backend, which is moot once argon2-cffi is a direct dep.

## Changes — the PR train

Phase 2 ships as **seven sequential PRs**. Each is a squash merge, individually revertible, individually reviewable. Order matters only at the `uv.lock` level (each PR rewrites the lockfile); functional coupling between PRs is minimal.

### PR-1. Python floor bump: 3.9 → 3.12

**Files touched:** `pyproject.toml`, `Dockerfile`, `.github/workflows/tests.yml`, `.python-version`, `README.rst`, `CLAUDE.md` (the "Python ≥ 3.9" line in the Style section).

**Changes:**
- `[project] requires-python = ">=3.12"` (was `>=3.9`).
- `[project] classifiers` — drop the `Programming Language :: Python :: 3.9`, `3.10`, and `3.11` entries; keep 3.12 and 3.13.
- `[tool.ruff] target-version = "py312"` (was `py39`).
- `[tool.mypy] python_version = "3.12"` (was `3.9`).
- `.python-version` — `3.12` (was `3.10`).
- `Dockerfile` — `ARG PYTHON_VERSION=3.12` (was `3.10`).
- `.github/workflows/tests.yml` — matrix `['3.12', '3.13']` (was `['3.10', '3.11', '3.12', '3.13']`).
- `README.rst` and `CLAUDE.md` — update any "Python 3.9+" / "3.10+" prose to 3.12+.

**Source code:** none. No `from __future__ import annotations` deletions yet (those are a Phase 3 typing-pass concern).

**Acceptance:** CI green on 3.12 and 3.13 with the existing test suite. `uv sync` resolves cleanly. `docker build --target builder -t cc-test .` succeeds.

### PR-2. Flask 2 → 3 + Werkzeug 3

**Files touched:** `pyproject.toml`, `uv.lock`, possibly minor source edits in `application.py` or `api.py` if Flask 3 deprecation removals bite.

**Changes:**
- `Flask>=3.0` (was `>=2.3`).
- `Flask-Caching>=2.3` (was `>=2.0`) — the Flask-Caching 2.3.x line is the first to support Flask 3.
- Add `werkzeug>=3.0` as an explicit dependency (currently pulled transitively via Flask; pinning explicitly guards against future Flask versions decoupling from Werkzeug).

**Source surface to check:**
- `from flask.cli import FlaskGroup, AppGroup, with_appcontext` — all stable in 3.x.
- `from flask.views import MethodView` — stable.
- `Flask.config.from_prefixed_env` — stable, present since 2.1.
- `from werkzeug.routing import BaseConverter, ValidationError` — stable.
- `from werkzeug.exceptions import HTTPException` — stable.
- Removed in Werkzeug 3: `werkzeug.urls.url_quote`, `werkzeug.urls.url_unquote`, etc. — the codebase doesn't use these, verified by grep.

**Acceptance:** test suite green. `uv run cancelchain --help` works.

### PR-3. SQLAlchemy 2.0 + Flask-SQLAlchemy 3.1

**Files touched:** `pyproject.toml`, `uv.lock`, possibly `src/cancelchain/__init__.py` (for warning suppression).

**Changes:**
- Drop the `sqlalchemy<2.0` cap. New constraint: `sqlalchemy>=2.0`.
- `Flask-SQLAlchemy>=3.1` (was `>=3.0`). The 3.1.x line is SA-2.0-compatible while preserving the legacy `Model.query` API.

**Source surface:**
- `db.Model`, `db.Column`, `db.relationship`, `db.backref`, `db.ForeignKey`, `db.Index`, `db.UniqueConstraint`, `db.Table`, `db.aliased` — all still exposed by Flask-SQLAlchemy 3.1 on top of SA 2.0.
- `cls.query.filter_by(...).one_or_none()` style — preserved via Flask-SQLAlchemy's `Model.query` shim.
- `db.session.query(...).join(...)` — works in SA 2.0 (legacy interface). Phase 3 modernizes these to `db.session.execute(db.select(...))`.
- Recursive CTE patterns in `models.py:BlockDAO.block_chain` (around `BlockDAO.query.filter(BlockDAO.id == self.id).cte(recursive=True)`) — `.cte()` is unchanged in SA 2.0.

**Deprecation warning noise:** SA 2.0 emits `MovedIn20Warning` and `LegacyAPIWarning` for the legacy patterns we're deliberately keeping. If pytest captures these and they overwhelm the log, set `SQLALCHEMY_WARN_20=0` in `tests/.test.env` *or* add a `filterwarnings` line to `pyproject.toml`'s pytest config. Decide at PR time based on actual noise.

**Acceptance:** test suite green. Recursive CTE tests (block_chain / transactions_chain / inflows_chain / outflows_chain) all pass.

### PR-4. pymerkle 4 → 5

**Files touched:** `pyproject.toml`, `uv.lock`, `src/cancelchain/block.py`, any test under `tests/` that asserts a specific merkle root value.

**Changes:**
- `pymerkle>=5` (was `>=4.0,<5.0`). Drop the upper bound.
- Port `block.py:144-174` to the v5 API. The current code uses:
  - `MerkleTree()` constructor
  - `tree.append_entry(record)`
  - `tree.root` (bytes)
  - `tree.prove_inclusion(txid)`
  - `verify_inclusion(txid, target, proof)` (module-level function)
  - `InvalidProof` exception

  pymerkle 5 reorganized the public API. The exact v5 spelling will be verified at PR time; if any of `append_entry` / `tree.root` / `prove_inclusion` / `verify_inclusion` lacks a usable v5 equivalent, the fallback is to inline a small Merkle tree implementation (sha256-of-sha512 leaves, keyed on txid strings) directly in `block.py`. The use case has no requirements that pymerkle uniquely satisfies.

**No hash-compat gate.** Per the brainstorming decision: there is no persisted chain to preserve. The Merkle root values in existing test fixtures will be regenerated as part of this PR.

**Acceptance:** test suite green. `Block.in_merkle_tree(txid)` returns True for txids in the block's transaction set and False otherwise. `Block.get_merkle_root()` is deterministic for a given txn set.

### PR-5. Replace passlib with argon2-cffi

**Files touched:** `pyproject.toml`, `uv.lock`, `src/cancelchain/models.py` (the `ApiToken` class plus its `from passlib.hash import argon2, pbkdf2_sha256` line), possibly a test file if any test currently constructs hashes with passlib directly.

**Changes:**
- Remove `passlib[argon2]>=1.7` from `[project.dependencies]`.
- Add `argon2-cffi>=23.1`.
- `src/cancelchain/models.py:ApiToken`:
  - `from passlib.hash import argon2, pbkdf2_sha256` → `from argon2 import PasswordHasher, exceptions as argon2_exceptions`
  - Replace the try/except `self.hashed = argon2.hash(secret)` / `self.hashed = pbkdf2_sha256.hash(secret)` with a single `self.hashed = _PH.hash(secret)`, where `_PH = PasswordHasher()` is a module-level singleton.
  - Drop the `argon2.identify(self.hashed)` discrimination in `verify` — only argon2 hashes will ever be stored. Replace with `_PH.verify(self.hashed, secret)` wrapped in a try/except `argon2_exceptions.VerifyMismatchError` returning False.
- Drop the pbkdf2 fallback entirely. Its purpose was solely to cover passlib's missing argon2 backend, which is irrelevant when argon2-cffi is a direct dep.

**Test concerns:**
- argon2-cffi's default parameters (`time_cost=3, memory_cost=65536, parallelism=4`) differ from passlib's argon2 defaults but produce the same PHC string format (`$argon2id$...`). Tests should not assert specific hash *bytes* — only that hash-and-verify roundtrips work.
- If any existing test fixture stores a passlib-format hash in the `api_token.hashed` column, the fixture must be regenerated with argon2-cffi.

**Acceptance:** test suite green. `ApiToken.refreshed_cipher()` and `ApiToken.verify()` work end-to-end. `pyproject.toml` and `uv.lock` no longer reference `passlib`.

### PR-6. Routine runtime-dependency bumps

**Files touched:** `pyproject.toml`, `uv.lock`.

**Changes:** floor bumps on dependencies that don't require source edits.

| Package | From | To | Notes |
|---|---|---|---|
| `gunicorn` | `>=20.1` | `>=23.0` | gunicorn 23 (2024) brings security fixes and Python 3.12 compat. |
| `celery` | `>=5.3` | `>=5.4` | 5.4 line is current; 5.5 may be available — pick the highest stable. |
| `pyjwt` | `>=2.7` | `>=2.9` | Stable patch line. |
| `requests` | `>=2.31` | `>=2.32` | Picks up urllib3 2.x compat fixes. (Full swap to httpx is Phase 3.) |
| `blinker` | `>=1.6` | `>=1.8` | Flask 3 requires blinker 1.7+. |
| `click` | `>=8.1` | `>=8.1.7` | Flask 3 requires click 8.1.3+; bump floor for transitive safety. |
| `base58check` | `>=1.0` | `>=1.0.2` | Latest is 1.0.2 (2018, abandoned). Phase 3 may need to fork or vendor; not in Phase 2's scope. |
| `millify` | `>=0.1` | `>=0.1.1` | Patch-level. |
| `pg8000` | `>=1.29` | `>=1.31` | Current line. |
| `rich` | `>=13.4` | `>=13.7` | Picks up Python 3.12+ compat. |
| `pycryptodome` | `>=3.18` | `>=3.20` | Security/perf patches. (Full swap to cryptography is Phase 3.) |

Constraints **not changed** by PR-6 (already at current acceptable floors): `humanfriendly>=10.0`, `python-dotenv>=1.0`. `marshmallow>=3.19` is deliberately untouched (Phase 3 replaces it).

**Source code:** none expected. If any bump above turns out to require source edits, that bump is split into its own PR rather than bundled here.

**Acceptance:** test suite green. `uv.lock` rewrites cleanly.

### PR-7. Dev-tooling bumps

**Files touched:** `pyproject.toml` ([dependency-groups].dev), `uv.lock`, `.pre-commit-config.yaml`.

**Changes:**
- `pytest>=8.3` (was `>=8`).
- `pytest-cov>=5.0` (was `>=5`).
- `pytest-dotenv>=0.5` — no change.
- `requests-mock>=1.12` — no change.
- `time-machine>=2.14` — no change.
- `coverage[toml]>=7.5` — no change.
- `ruff>=0.7` (was `>=0.6`). Will surface new lint rules; CI's `continue-on-error: true` absorbs them.
- `mypy>=1.13` (was `>=1.10`).
- `pre-commit>=4.0` (was `>=3.7`).
- `.pre-commit-config.yaml`:
  - `ruff-pre-commit` rev bumped to match the new ruff floor (e.g. `v0.7.0` or whatever Dependabot pins).
  - `pre-commit-hooks` rev bumped to match.

**Acceptance:** `uv run pytest`, `uv run ruff format --check`, `uv run pre-commit run --all-files` all behave (lint may emit more warnings under the newer ruff; not a blocker).

## Non-source changes summary

| File | Touched by |
|---|---|
| `pyproject.toml` | PR-1, 2, 3, 4, 5, 6, 7 |
| `uv.lock` | PR-1 (resolution check), 2, 3, 4, 5, 6, 7 |
| `Dockerfile` | PR-1 |
| `.github/workflows/tests.yml` | PR-1 |
| `.python-version` | PR-1 |
| `.pre-commit-config.yaml` | PR-7 |
| `README.rst` | PR-1 |
| `CLAUDE.md` | PR-1 (Python floor prose) |

## Source files touched

| File | Touched by | Reason |
|---|---|---|
| `src/cancelchain/__init__.py` | PR-3 (maybe) | SA 2.0 warning suppression |
| `src/cancelchain/block.py` | PR-4 | pymerkle 5 API port |
| `src/cancelchain/models.py` | PR-5 | passlib → argon2-cffi swap (ApiToken class only) |

All other src files are expected to be untouched. If a PR turns out to need broader edits, that's a signal to split the PR or revisit the design.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| pymerkle 5's API doesn't admit `prove_inclusion` / `verify_inclusion` semantics in any reasonable form. | At PR-4 time, fall back to an inline Merkle implementation in `block.py`. The use case is tightly scoped (leaves = txids, sha256 hashing) and a hand-rolled implementation is ~30 lines. Drop pymerkle from `[project.dependencies]` in that scenario. |
| Flask 3 / Werkzeug 3 deprecation removal that grep missed. | PR-2's CI run flags it. If the fix is non-trivial (>20 lines, multiple files), split the source fix into its own PR ahead of the version bump. |
| SA 2.0 deprecation warnings overwhelm pytest output. | Set `SQLALCHEMY_WARN_20=0` in `tests/.test.env` *or* add a `filterwarnings` entry to `pyproject.toml`. Decided at PR-3 time. Phase 3's query modernization removes the warnings entirely. |
| argon2-cffi's default parameters slow `ApiToken` operations enough to noticeably regress test wall-clock. | Tune `PasswordHasher(time_cost=..., memory_cost=...)` to match passlib's argon2 defaults if needed. The `ApiToken` cipher refresh path is not in any hot loop. |
| Python 3.12 floor breaks a deployment we haven't anticipated. | Phase 1's CI matrix is already exercising 3.12 and 3.13. PR-1 is the smallest standalone PR specifically so deployment regressions are bisectable. |
| A routine bump in PR-6 actually does require source edits. | Split it out of PR-6 into its own PR. PR-6 stays "no source code changes" by definition. |
| `uv.lock` merge conflicts between simultaneously open PRs. | Serialize merges — even if PRs are opened in parallel branches, only one merges into main at a time. Each subsequent PR rebases and re-runs `uv lock`. |
| Dependabot opens a competing bump PR mid-train. | Close or hold Dependabot PRs touching `[project.dependencies]` until the Phase 2 train finishes. Resume Monday-cadence after PR-7 lands. |
| Phase 1's `requires-python = ">=3.9"` is referenced somewhere not yet found (e.g., a build-system constraint). | PR-1's review pass will surface it; `git grep '3\.9'` over the whole repo as part of PR-1 prep. |

## Acceptance criteria for Phase 2 as a whole

- [ ] All seven PRs merged to `main` via squash-merge with branch deletion.
- [ ] `git clone … && uv sync --group dev && uv run pytest` exits 0 on Python 3.12 and 3.13.
- [ ] `uv run ruff format --check src tests` exits 0.
- [ ] `uv run cancelchain --help` works.
- [ ] `docker build .` succeeds; the resulting image runs `cancelchain --help` and accepts a `gunicorn app:app` bind.
- [ ] `pyproject.toml` no longer contains `sqlalchemy<2.0`, `pymerkle>=4.0,<5.0`, or `passlib[argon2]`.
- [ ] `uv.lock` has no `passlib` entry.
- [ ] CI matrix is `['3.12', '3.13']` only.
- [ ] `[tool.ruff] target-version = "py312"` and `[tool.mypy] python_version = "3.12"`.
- [ ] `Dockerfile` builds with `PYTHON_VERSION=3.12` as the default `ARG`.

## Open decisions (resolve at PR time)

- pymerkle 5: which v5 API spellings to use vs. when to fall back to an inline Merkle implementation. Decided at PR-4 time after a closer read of pymerkle 5's docs.
- SA 2.0 warning suppression: in-test (`tests/.test.env` env var) vs. in-config (`pyproject.toml` `filterwarnings`). Decided at PR-3 time based on noise level.
- argon2-cffi parameter tuning: defaults vs. explicit lower-cost settings to keep test runtime down. Decided at PR-5 time.
- Whether to bundle `base58check` into PR-6 or split it out if the upstream is dead and the bump is illusory. Decided at PR-6 time.

## What comes next

- **Phase 3 — Targeted refactors.** Type hints throughout the source tree; SQLAlchemy 2.0 `Mapped[]` annotations on `models.py`; `.query.X()` → `db.session.execute(db.select(X))` modernization; Marshmallow → Pydantic v2; `requests` → `httpx`; `pycryptodome` → `cryptography` with byte-for-byte compat tests where needed; Alembic; removing `continue-on-error: true` from `ruff check` and `mypy` once the existing backlog is cleaned.
- **Phase 4 — Observability.** Optional; OpenTelemetry, Sentry, structured logging.

Each subsequent phase gets its own design doc and implementation plan.
