# Phase 3 — Lint, Typing, and CI Hard-Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the nine-PR train laid out in `docs/superpowers/specs/2026-05-24-phase-3-lint-typing-ci-gating-design.md`. After this plan completes, `uv run ruff check src tests` exits 0, `uv run mypy src` exits 0 under `[tool.mypy] strict = true`, and CI's `tests.yml` no longer carries any `continue-on-error: true` directives.

**Architecture:** Each impl PR is one task. Tasks run sequentially on `main`, each starting from a clean `main` pull and ending with a squash-merge + branch deletion. PRs 1, 2, 3, 8, 9 are config or mechanical-fix changes. PRs 4, 5, 6, 7 are typing-pass PRs where the implementer adds annotations across multiple files following a documented pattern; their diff size is moderate-to-large but the per-file work is mechanical.

**Tech Stack:** Python 3.12+, mypy strict mode, ruff 0.15+, SQLAlchemy 2.0 `Mapped[]` annotations, Flask 3, Flask-SQLAlchemy 3.1, Marshmallow 3.x (typed but not yet swapped), pre-commit.

---

## Prerequisites

- Working directory: `/home/gumptionthomas/Development/cancelchain`. Use absolute paths or `cd` once at session start.
- `uv --version` is 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 2 is fully merged. `git log --oneline -10` should show `e989c40 chore(deps): bump dev-tooling floors (#38)` at or near the top.
- The branch `docs/phase-3-design` exists locally and contains commit `e88c89f` (the design spec). This plan adds the second commit on that branch, then ships both as the docs PR.
- Each impl PR ends with `wor` (wait-on-review) and `mwg` (merge-when-green); see the Phase 2 plan's "Notes on the wor / mwg workflow" section for mechanics. The controller (the orchestrator running this plan) handles `wor`/`mwg`; the implementer subagent stops after `gh pr create`.
- Never push directly to `main`. Every change in this plan goes through a branch + PR.

---

## File Map

### Files touched per task

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-24-phase-3-lint-typing-ci-gating.md` (this file) |
| 2 | PR-1 ruff config | `pyproject.toml` |
| 3 | PR-2 src/ lint | `src/cancelchain/*.py` (selected files only — driven by ruff output) |
| 4 | PR-3 tests/ lint | `tests/*.py` (selected files only — driven by ruff output) |
| 5 | PR-4 utility typing | `src/cancelchain/util.py`, `schema.py`, `milling.py`, `signals.py`, `exceptions.py`, `console.py`, `database.py`, `cache.py`, `config.py` |
| 6 | PR-5 domain typing | `src/cancelchain/wallet.py`, `payload.py`, `transaction.py`, `block.py`, `chain.py` |
| 7 | PR-6 `Mapped[]` | `src/cancelchain/models.py` |
| 8 | PR-7 infra typing | `src/cancelchain/api.py`, `api_client.py`, `browser.py`, `command.py`, `node.py`, `miller.py`, `tasks.py`, `application.py`, `__init__.py` |
| 9 | PR-8 CI hard gate | `.github/workflows/tests.yml`, `pyproject.toml` (`[tool.mypy]`) |
| 10 | PR-9 test fixture | `tests/.test.env`, `CLAUDE.md` (if any prose references the test secret) |
| 11 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** Modify: nothing. The design spec is already committed on `docs/phase-3-design` as `e88c89f`. This task adds the implementation plan and ships them together as a single docs PR.

- [ ] **Step 1: Confirm branch state**

Run:
```bash
git rev-parse --abbrev-ref HEAD
git log --oneline main..HEAD
```
Expected: branch is `docs/phase-3-design`; one commit above main: `e88c89f docs(phase-3): add Phase 3 lint+typing+CI-gating design spec`.

- [ ] **Step 2: Verify the plan file is present**

Run:
```bash
ls -la docs/superpowers/plans/2026-05-24-phase-3-lint-typing-ci-gating.md
git status docs/superpowers/plans/
```
Expected: file exists, untracked.

- [ ] **Step 3: Stage and commit**

Run:
```bash
git add docs/superpowers/plans/2026-05-24-phase-3-lint-typing-ci-gating.md
git commit -m "$(cat <<'EOF'
docs(phase-3): add Phase 3 lint+typing+CI-gating implementation plan

Spells out the 9 sequential impl PRs (ruff config, src/ lint cleanup,
tests/ lint cleanup, typing leaves, typing domain, Mapped[] for SA
entities, typing infra, mypy/ruff hard CI gates, test-fixture key
hardening) with file lists, commands, and the wor/mwg cycle between
each PR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

Run:
```bash
git push -u origin docs/phase-3-design
```

- [ ] **Step 5: Open the docs PR**

Run:
```bash
gh pr create --base main --head docs/phase-3-design --title "docs(phase-3): add Phase 3 design + implementation plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 3 design spec (`docs/superpowers/specs/2026-05-24-phase-3-lint-typing-ci-gating-design.md`).
- Adds the Phase 3 implementation plan (`docs/superpowers/plans/2026-05-24-phase-3-lint-typing-ci-gating.md`).
- No code changes. Subsequent impl PRs reference these documents.

Phase 3 ships as nine focused PRs:
1. Ruff config alignment (quote-style mismatch fix)
2. src/ ruff debt cleanup
3. tests/ ruff debt cleanup
4. Typing pass: utility + schema layer
5. Typing pass: domain layer
6. SQLAlchemy `Mapped[]` for `models.py`
7. Typing pass: infra layer
8. Make `ruff check` and `mypy` hard CI gates
9. Test-fixture `FLASK_SECRET_KEY` hardening

Library swaps (Marshmallow → Pydantic, requests → httpx, pycryptodome → cryptography), SA query-style modernization, and Alembic are explicitly deferred to Phase 4+ — each will get its own design.

## Test plan
- [ ] Spec self-review passes (already done in the brainstorming session).
- [ ] Plan self-review passes (already done in the planning session).
- [ ] Reviewer confirms PR list matches the spec's "Changes" section.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: wor + mwg + sync (controller-driven)**

The controller handles `wor` (Copilot review + replies) and `mwg` (squash-merge + delete branch). After merge, the controller runs:
```bash
git checkout main && git pull --ff-only
git branch -D docs/phase-3-design 2>/dev/null || true
```

---

## Task 2: PR-1 — Ruff config alignment

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (`[tool.ruff.lint]` section and optionally a new `[tool.ruff.lint.flake8-quotes]` table)

Fixes the persistent ruff warning:
```
warning: The `flake8-quotes.inline-quotes="double"` option is incompatible with the formatter's `format.quote-style="single"`.
```

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b chore/ruff-quote-style-align
```
Expected: new branch from latest main.

- [ ] **Step 2: Reproduce the warning**

Run:
```bash
uv sync --group dev
uv run ruff check src tests 2>&1 | grep -i quotes | head -3
```
Expected: prints the `flake8-quotes.inline-quotes="double"` incompatibility warning.

- [ ] **Step 3: Add an explicit flake8-quotes config block**

Open `pyproject.toml`. After the existing `[tool.ruff.lint]` table block (with its `select` and `ignore` lists), add this new sub-table:

```toml
[tool.ruff.lint.flake8-quotes]
inline-quotes = "single"
```

Place it AFTER `[tool.ruff.lint]` and BEFORE `[tool.ruff.format]`. The complete relevant section after the edit should look like:

```toml
[tool.ruff.lint]
select = [
  ...
]
ignore = [
  ...
]

[tool.ruff.lint.flake8-quotes]
inline-quotes = "single"

[tool.ruff.format]
quote-style = "single"
```

- [ ] **Step 4: Verify the warning is gone**

Run:
```bash
uv run ruff check src tests 2>&1 | grep -i quotes
```
Expected: no output (no warning).

- [ ] **Step 5: Decide whether `Q000` can come out of `ignore`**

Run:
```bash
# Temporarily try removing Q000 from the ignore list and see if findings appear.
grep -n "Q000" pyproject.toml
# Manually edit to remove "Q000" from the ignore list.
uv run ruff check src tests 2>&1 | grep Q000 | head -3
```

If `ruff check` produces any Q000 findings, restore the `Q000` ignore. Otherwise, drop it (one less explicit ignore is cleaner).

The expected outcome: with `inline-quotes = "single"` matching the formatter's `quote-style`, Q000 should never fire on properly-formatted code, so the ignore is redundant.

- [ ] **Step 6: Confirm test suite still passes**

Run:
```bash
uv run pytest
```
Expected: 162 passed, 1 skipped.

- [ ] **Step 7: Commit**

Run:
```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
chore(ruff): align flake8-quotes config with formatter quote-style

Adds explicit `[tool.ruff.lint.flake8-quotes] inline-quotes = "single"`
to match the formatter's `quote-style = "single"`. Removes the ruff
config-mismatch warning that fired on every ruff invocation.

Phase 3 / PR 1 of 9 (prep for hard CI gating).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```
(If you removed `Q000` from the ignore list in Step 5, mention that in the commit body.)

- [ ] **Step 8: Push and open PR**

Run:
```bash
git push -u origin chore/ruff-quote-style-align
gh pr create --base main --title "chore(ruff): align flake8-quotes config with formatter quote-style" --body "$(cat <<'EOF'
## Summary
Adds `[tool.ruff.lint.flake8-quotes] inline-quotes = "single"` to silence the persistent config-mismatch warning. Optionally drops the now-redundant `Q000` ignore.

Phase 3 / PR 1 of 9.

## Test plan
- [x] `uv run ruff check src tests` no longer emits the config-mismatch warning.
- [x] `uv run pytest` passes.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9: Stop — controller handles wor + mwg + sync**

---

## Task 3: PR-2 — Clear src/ ruff debt

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/src/cancelchain/*.py` (selected files based on ruff output)

Current findings (as of plan-writing, may differ slightly at PR time):

| Rule | Count | Source |
|---|---|---|
| PLC0415 | 5 | import not at top-level — usually conditional imports that can be hoisted, or genuinely deferred imports that need `# noqa: PLC0415` |
| RUF059 | 2 | unused unpacked variable — prefix with `_` |
| PLW0108 | 1 | unnecessary lambda |
| F811 | 1 | redefined import |
| PLR1704 | 1 | redefining argument |
| PLW1641 | 1 | `__eq__` defined without `__hash__` |

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b chore/lint-src-cleanup
```

- [ ] **Step 2: Capture the current findings**

Run:
```bash
uv run ruff check src --output-format=json > /tmp/ruff-src-before.json
uv run ruff check src --statistics
```
Expected: lists the rule codes and counts (matches the table above, modulo ruff version drift).

- [ ] **Step 3: Apply auto-fixable changes**

Run:
```bash
uv run ruff check src --fix --unsafe-fixes
```
This auto-applies fixes for RUF059, RUF015, UP017, and similar mechanical rules. `--unsafe-fixes` is required because some rules (e.g., RUF059's underscore-prefix rename) aren't fix-safe by default.

Run:
```bash
git diff --stat src/
```
Expected: a handful of files changed with small per-file edits.

- [ ] **Step 4: Address remaining findings manually**

Run:
```bash
uv run ruff check src
```
For each remaining finding:

- **PLC0415 (import not at top-level):** if the import can be hoisted to module level without circular-import issues, do so. If it's a genuinely deferred import (e.g., a heavy dependency only needed for one code path), add `# noqa: PLC0415` with a comment explaining why.
- **PLW0108 (unnecessary lambda):** replace `lambda x: f(x)` with `f` directly.
- **F811 (redefined import):** remove the duplicate import.
- **PLR1704 (redefining argument):** rename the local variable that shadows an argument.
- **PLW1641 (`__eq__` without `__hash__`):** either add `__hash__ = None` (explicit unhashable) or implement `__hash__` returning a hash that respects `__eq__`. Decide based on whether instances are used as dict keys or in sets. (Grep for usage if unclear.)

- [ ] **Step 5: Verify clean**

Run:
```bash
uv run ruff check src
```
Expected: `All checks passed!` or zero errors.

- [ ] **Step 6: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: 162 passed, 1 skipped. Zero regressions.

- [ ] **Step 7: Run ruff format check**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes.

- [ ] **Step 8: Commit**

Stage only the files actually modified:
```bash
git add -u src/
git commit -m "$(cat <<'EOF'
chore(lint): clear src/ ruff findings

Applies auto-fixable rules (RUF059, RUF015, UP017, etc.) and addresses
the remaining manual cases (PLC0415, PLW0108, F811, PLR1704, PLW1641).
No semantic changes — every fix is a refactor-equivalent rewrite.

Phase 3 / PR 2 of 9 (prep for ruff check hard gating in PR-8).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Push and open PR**

Run:
```bash
git push -u origin chore/lint-src-cleanup
gh pr create --base main --title "chore(lint): clear src/ ruff findings" --body "$(cat <<'EOF'
## Summary
- Applies auto-fixable rules: RUF059, RUF015, UP017, etc.
- Addresses manual cases: PLC0415 (import hoisting or `# noqa`), PLW0108 (lambda removal), F811 (duplicate import), PLR1704 (arg shadow rename), PLW1641 (explicit `__hash__`).
- No semantic changes.

Phase 3 / PR 2 of 9.

## Test plan
- [x] `uv run ruff check src` exits 0.
- [x] `uv run pytest` passes (162/163).
- [x] `uv run ruff format --check` passes.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10: Stop — controller handles wor + mwg + sync**

---

## Task 4: PR-3 — Clear tests/ ruff debt

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/tests/*.py` (selected files based on ruff output)

Current findings: 25 total, all in two rules:

| Rule | Count | Source |
|---|---|---|
| RUF059 | 16 | unused unpacked variable — `m, b = mill_block(...)` where `b` is unused; rename `b` → `_b` |
| RUF015 | 9 | `list(x)[0]` → `next(iter(x))` |

Both rule sets have hidden fixes available via `--unsafe-fixes`.

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b chore/lint-tests-cleanup
```

- [ ] **Step 2: Apply auto-fixable changes**

Run:
```bash
uv run ruff check tests --fix --unsafe-fixes
git diff --stat tests/
```
Expected: ~10-15 test files touched with small per-file edits. RUF059 renames `b` → `_b` and similar; RUF015 rewrites `list(...)[0]` → `next(iter(...))`.

- [ ] **Step 3: Verify clean**

Run:
```bash
uv run ruff check tests
```
Expected: `All checks passed!`.

- [ ] **Step 4: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: 162 passed, 1 skipped. The `--unsafe-fixes` renames are safe by definition (they apply to unused variables and equivalent expressions), but verify.

- [ ] **Step 5: Run ruff format check**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes.

- [ ] **Step 6: Commit**

```bash
git add -u tests/
git commit -m "$(cat <<'EOF'
chore(lint): clear tests/ ruff findings

Auto-fixed via `ruff check --fix --unsafe-fixes`:
- RUF059 (16x): unused unpacked variables prefixed with `_`.
- RUF015 (9x): `list(x)[0]` → `next(iter(x))`.

No test behavior changes.

Phase 3 / PR 3 of 9 (prep for ruff check hard gating in PR-8).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin chore/lint-tests-cleanup
gh pr create --base main --title "chore(lint): clear tests/ ruff findings" --body "$(cat <<'EOF'
## Summary
Auto-fixable cleanups in tests/:
- RUF059 ×16: unused-variable renames to `_<name>`.
- RUF015 ×9: `list(x)[0]` → `next(iter(x))`.

Mechanical, no behavior changes.

Phase 3 / PR 3 of 9.

## Test plan
- [x] `uv run ruff check tests` exits 0.
- [x] `uv run pytest` passes (162/163).
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Stop — controller handles wor + mwg + sync**

---

## Task 5: PR-4 — Type the utility + schema layer

**Files:**
- Modify (9 files, ~600 lines combined):
  - `src/cancelchain/util.py` (44 lines)
  - `src/cancelchain/schema.py` (106 lines)
  - `src/cancelchain/milling.py` (92 lines)
  - `src/cancelchain/signals.py` (7 lines)
  - `src/cancelchain/exceptions.py` (148 lines)
  - `src/cancelchain/console.py` (20 lines)
  - `src/cancelchain/database.py` (3 lines)
  - `src/cancelchain/cache.py` (3 lines)
  - `src/cancelchain/config.py` (41 lines)

These are the leaf modules with minimal inter-module dependencies. Adding types here establishes the patterns the rest of the project follows.

### Typing patterns to apply

**Pattern 1: `from __future__ import annotations` at top of every typed file.**

Add as the first import in every file in this PR's list (and subsequent typing PRs). This makes `int | None`, `list[X]`, etc. resolve as strings at module load time, avoiding forward-reference issues in class bodies.

**Pattern 2: Prefer `X | None` over `Optional[X]`.**

```python
def find_user(uid: str) -> User | None:
    ...
```
Not:
```python
from typing import Optional
def find_user(uid: str) -> Optional[User]:
    ...
```

**Pattern 3: Import abstract types from `collections.abc`, not `typing`.**

```python
from collections.abc import Iterable, Iterator, Mapping, Sequence, Callable
```
Not:
```python
from typing import Iterable, Iterator, ...
```

**Pattern 4: Type all function signatures, including `-> None`.**

```python
def configure(app: Flask) -> None:
    ...
```
Even one-arg helpers get explicit return types.

**Pattern 5: Marshmallow Schema classes.**

For Schema subclasses with `@post_dump`, `@post_load`, `@validates_schema` decorated methods:
```python
from marshmallow import Schema, post_dump
from typing import Any

class SansNoneSchema(Schema):
    @post_dump
    def remove_none_values(self, data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return {k: v for k, v in data.items() if v is not None}
```
The `**kwargs: Any` is needed because Marshmallow passes extra context kwargs that aren't statically typed.

### Marshmallow typing strategy

In `src/cancelchain/schema.py`, the Schema subclasses and custom field types interact with mypy strict mode. Two strategies:

**Strategy A: Use `marshmallow-stubs` (preferred).**

In Step 3, add to `[dependency-groups].dev`:
```toml
"marshmallow-stubs>=0.0.4",
```
Run `uv lock --upgrade-package marshmallow-stubs && uv sync --group dev`.

**Strategy B: Ignore the marshmallow module.**

In Step 3, add to `pyproject.toml`'s `[tool.mypy]` section:
```toml
[[tool.mypy.overrides]]
module = ["marshmallow", "marshmallow.*"]
ignore_missing_imports = true
```

Try Strategy A first. If `marshmallow-stubs` is unavailable on PyPI or causes more friction than it saves (e.g., outdated against marshmallow 3.21 and producing false positives), fall back to Strategy B.

### Task steps

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b feat/types-utility-layer
```

- [ ] **Step 2: Enable strict typing for THIS PR only via per-file mypy overrides**

This PR doesn't flip `[tool.mypy] strict = true` globally yet (PR-8 does that). Instead, run mypy with strict-like flags scoped to the files in this PR to validate progress.

Run a baseline:
```bash
uv run mypy --strict src/cancelchain/util.py src/cancelchain/schema.py src/cancelchain/milling.py src/cancelchain/signals.py src/cancelchain/exceptions.py src/cancelchain/console.py src/cancelchain/database.py src/cancelchain/cache.py src/cancelchain/config.py 2>&1 | tail -5
```
Expected: many errors (likely 50+). This is the bar to drive to zero.

- [ ] **Step 3: Add marshmallow-stubs to dev deps**

Edit `pyproject.toml`'s `[dependency-groups].dev` list. After the existing `mypy>=...` line, add:
```toml
  "marshmallow-stubs>=0.0.4",
```

Run:
```bash
uv lock --upgrade-package marshmallow-stubs
uv sync --group dev
uv run python -c "import marshmallow_stubs" 2>&1 || echo "(stub package may not be importable directly — that's fine)"
```

If `uv lock` fails because marshmallow-stubs is not on PyPI or no compatible version exists, abort Strategy A and apply Strategy B (add `[[tool.mypy.overrides]]` for `marshmallow.*` instead). Document the choice in the commit message.

- [ ] **Step 4: Type each file**

Work through the file list in this order (smallest first, builds confidence):

1. **`src/cancelchain/database.py`** (3 lines) — just a `db = SQLAlchemy()` declaration. Add `from __future__ import annotations` and a return-type for any function (there should be none).

2. **`src/cancelchain/cache.py`** (3 lines) — just a `cache = Cache()` declaration. Same treatment.

3. **`src/cancelchain/signals.py`** (7 lines) — blinker signal declarations. Add `from __future__ import annotations`. Signals don't need typing themselves.

4. **`src/cancelchain/console.py`** (20 lines) — typically a rich Console singleton. Add `from __future__ import annotations` and type any helpers.

5. **`src/cancelchain/config.py`** (41 lines) — `EnvAppSettings` dataclass. Add type hints to class attributes (they may already exist if it's a dataclass) and any helper functions.

6. **`src/cancelchain/util.py`** (44 lines) — utility helpers like `iso_2_dt`, `dt_2_iso`, `now`, `host_address`. Type each function with its parameter and return types.

7. **`src/cancelchain/milling.py`** (92 lines) — `mill_hash`, `mill_hash_str`, `milling_generator`. The generator function returns `Iterator[...]`; the hash functions return `bytes` and `str` respectively.

8. **`src/cancelchain/exceptions.py`** (148 lines) — exception subclasses. Most need no changes (exception classes are auto-typed). Any `__init__` methods get type hints.

9. **`src/cancelchain/schema.py`** (106 lines) — Marshmallow field types and validators. Apply Pattern 5 for `SansNoneSchema`. Validators like `validate_address`, `validate_base64`, etc. get type hints. Field subclasses (`Address`, `Base64`, `MillHash`, etc.) inherit from `fields.String`; their `__init__` accepts `*args, **kwargs` — type as `*args: Any, **kwargs: Any`.

After each file: `uv run mypy --strict src/cancelchain/<file>.py` and aim for zero errors in that file.

- [ ] **Step 5: Verify the whole PR set is clean under strict**

Run:
```bash
uv run mypy --strict src/cancelchain/util.py src/cancelchain/schema.py src/cancelchain/milling.py src/cancelchain/signals.py src/cancelchain/exceptions.py src/cancelchain/console.py src/cancelchain/database.py src/cancelchain/cache.py src/cancelchain/config.py
```
Expected: `Success: no issues found in N source files.`

- [ ] **Step 6: Verify the rest of mypy still works (non-strict)**

Run:
```bash
uv run mypy src
```
Expected: error count strictly less than the pre-PR count (which was 37 before strict, less after — the typed-now files will contribute zero, the others contribute as much as before).

- [ ] **Step 7: Run full test suite**

```bash
uv run pytest
```
Expected: 162 passed, 1 skipped. Typing is a metadata layer; runtime behavior is unchanged.

- [ ] **Step 8: Ruff format + check**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
```
Both should pass cleanly.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/cancelchain/util.py src/cancelchain/schema.py src/cancelchain/milling.py src/cancelchain/signals.py src/cancelchain/exceptions.py src/cancelchain/console.py src/cancelchain/database.py src/cancelchain/cache.py src/cancelchain/config.py
git commit -m "$(cat <<'EOF'
feat(types): annotate utility + schema layer with strict types

Adds strict type hints to the leaf modules: util, schema, milling,
signals, exceptions, console, database, cache, config. Establishes
the project's typing patterns:

- `from __future__ import annotations` in every typed module
- `X | None` over `Optional[X]`
- `collections.abc` for abstract types, not `typing`
- Explicit return types on all functions

Adds `marshmallow-stubs` (or `[[tool.mypy.overrides]]` for marshmallow,
choose whichever applies) to the dev group so Marshmallow Schema
subclasses type-check cleanly.

mypy --strict on this PR's file list: 0 errors.

Phase 3 / PR 4 of 9 (foundation for the typing pass).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: Push and open PR**

```bash
git push -u origin feat/types-utility-layer
gh pr create --base main --title "feat(types): annotate utility + schema layer" --body "$(cat <<'EOF'
## Summary
Strict typing pass on the 9 utility/schema-layer modules:
- util.py, schema.py, milling.py, signals.py, exceptions.py
- console.py, database.py, cache.py, config.py

Establishes project typing patterns (`from __future__ import annotations`, `X | None`, `collections.abc`). Adds Marshmallow stub support for mypy.

Phase 3 / PR 4 of 9. Spec: `docs/superpowers/specs/2026-05-24-phase-3-lint-typing-ci-gating-design.md`.

## Test plan
- [x] `uv run mypy --strict` on the PR's file set: 0 errors.
- [x] `uv run mypy src` overall error count strictly lower than before.
- [x] `uv run pytest` passes (162/163).
- [x] `uv run ruff format --check` + `ruff check` pass.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 11: Stop — controller handles wor + mwg + sync**

---

## Task 6: PR-5 — Type the domain layer

**Files:**
- Modify (5 files, ~1700 lines combined):
  - `src/cancelchain/wallet.py` (187 lines) — RSA/AES wallet crypto via pycryptodome
  - `src/cancelchain/payload.py` (132 lines) — Inflow/Outflow dataclasses + Marshmallow schemas
  - `src/cancelchain/transaction.py` (346 lines) — Transaction dataclass + TransactionSchema
  - `src/cancelchain/block.py` (327 lines) — Block dataclass + BlockSchema + Merkle helpers
  - `src/cancelchain/chain.py` (462 lines) — Chain dataclass with longest-chain selection logic

These are the domain dataclasses. They import from the utility layer (Task 5) and are imported by the infra layer (Task 8).

### Domain typing patterns

**Pattern 1: `@dataclass` classes already have typed attributes.**

Dataclass attributes are typed at definition. The work is typing the methods on those classes (`to_dict`, `to_json`, `from_json`, `to_dao`, `from_dao`, `to_db`, `from_db`, etc.).

**Pattern 2: Marshmallow Schema methods get `dict[str, Any]` for the data dict.**

```python
class BlockSchema(SansNoneSchema):
    @validates_schema
    def check_proof(self, data: dict[str, Any], **kwargs: Any) -> None:
        ...

    @post_load
    def make_block(self, data: dict[str, Any], **kwargs: Any) -> Block:
        return Block(**data)
```

**Pattern 3: pycryptodome typing.**

`pycryptodome` has incomplete stubs. In `wallet.py`, the RSA/AES types may produce `Any` leaks. Either:
- Add per-module `ignore_missing_imports = true` for `Crypto.*` (most pragmatic).
- Or use `from typing import Any` and accept Any returns from pycryptodome calls.

Recommend: add this to `pyproject.toml`'s `[tool.mypy]`:
```toml
[[tool.mypy.overrides]]
module = ["Crypto", "Crypto.*"]
ignore_missing_imports = true
```

This is temporary — Phase 5 swaps pycryptodome for `cryptography` (which has full stubs).

**Pattern 4: Conversion methods (`to_dao`, `from_dao`).**

```python
def to_dao(self, create: bool = False) -> BlockDAO:
    ...

@classmethod
def from_dao(cls, dao: BlockDAO) -> Block:
    ...
```

Forward-reference issue: `BlockDAO` is in `models.py` which imports from `block.py`. The `from __future__ import annotations` directive at the top of every typed file makes this work — annotations are strings at runtime.

If you need to actually reference `BlockDAO` in the module body (e.g., for `isinstance` checks), use `TYPE_CHECKING`:
```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cancelchain.models import BlockDAO
```

### Task steps

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/types-domain-layer
```

- [ ] **Step 2: Add pycryptodome mypy override**

Edit `pyproject.toml`. After the existing `[tool.mypy]` block, add:
```toml
[[tool.mypy.overrides]]
module = ["Crypto", "Crypto.*"]
ignore_missing_imports = true
```

Note: `[[tool.mypy.overrides]]` (double brackets) — it's a TOML array of tables.

- [ ] **Step 3: Type each domain file in dependency order**

Order: wallet → payload → transaction → block → chain.

For each file:
1. Add `from __future__ import annotations` as the first import.
2. Annotate every function and method with parameter and return types.
3. Run `uv run mypy --strict <file>` and address findings.

**`wallet.py`:** RSA/AES helpers, address generation, signature verification. Most return `bytes` or `str`. Watch for `Crypto.PublicKey.RSA.RsaKey` types — those become `Any` via the override; that's fine for Phase 3.

**`payload.py`:** `Inflow` and `Outflow` dataclasses with `OutflowSchema` and `InflowSchema`. Apply Marshmallow Schema pattern.

**`transaction.py`:** `Transaction` dataclass with multiple `@classmethod` constructors (`coinbase`, `from_json`, `from_dao`, etc.) and `TransactionSchema`. Constructor return types are `-> Transaction`.

**`block.py`:** `Block` dataclass with `BlockSchema`, Merkle helpers (pymerkle 6 API adopted in Phase 2 / PR-4). The `build_merkle_tree` method returns `InmemoryTree` from pymerkle — annotate as such.

**`chain.py`:** `Chain` dataclass with longest-chain logic, block-target computation, balance queries. Heaviest typing burden in this PR (longest file, most methods).

- [ ] **Step 4: Verify**

```bash
uv run mypy --strict src/cancelchain/wallet.py src/cancelchain/payload.py src/cancelchain/transaction.py src/cancelchain/block.py src/cancelchain/chain.py
```
Expected: 0 errors.

- [ ] **Step 5: Verify overall mypy improvement**

```bash
uv run mypy src 2>&1 | tail -1
```
Expected: error count strictly less than after PR-4.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest
```
Expected: 162/163 passing.

- [ ] **Step 7: Ruff format + check**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
```
Both should pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/cancelchain/wallet.py src/cancelchain/payload.py src/cancelchain/transaction.py src/cancelchain/block.py src/cancelchain/chain.py
git commit -m "$(cat <<'EOF'
feat(types): annotate domain layer with strict types

Strict typing on the 5 domain modules: wallet, payload, transaction,
block, chain. Adds:

- Type hints to every dataclass method (`to_dict`, `from_json`,
  `to_dao`, `from_dao`, etc.).
- Marshmallow Schema method signatures (`@validates_schema`,
  `@post_load`).
- pycryptodome `[[tool.mypy.overrides]] ignore_missing_imports`
  (temporary — Phase 5 swaps for `cryptography`).

mypy --strict on this PR's file list: 0 errors.

Phase 3 / PR 5 of 9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Push and open PR**

```bash
git push -u origin feat/types-domain-layer
gh pr create --base main --title "feat(types): annotate domain layer" --body "$(cat <<'EOF'
## Summary
Strict typing on the 5 domain modules:
- wallet.py, payload.py, transaction.py, block.py, chain.py

Adds pycryptodome mypy override (temporary — Phase 5 replaces pycryptodome with `cryptography`).

Phase 3 / PR 5 of 9.

## Test plan
- [x] `uv run mypy --strict` on the PR's file set: 0 errors.
- [x] `uv run mypy src` overall error count strictly lower than after PR-4.
- [x] `uv run pytest` passes (162/163).
- [x] `uv run ruff format --check` + `ruff check` pass.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10: Stop — controller handles wor + mwg + sync**

---

## Task 7: PR-6 — SQLAlchemy `Mapped[]` for `models.py`

**Files:**
- Modify: `src/cancelchain/models.py` (651 lines)

The trickiest PR in Phase 3. Converts every `db.Column(...)` to `Mapped[X] = mapped_column(...)`, every `db.relationship(...)` to `Mapped[list[X]] = relationship(...)` (or `Mapped[X] = relationship(...)` for many-to-one), preserves `db.Table` association tables, and keeps the existing `Model.query` API working (Phase 6 modernizes that).

### Mapped[] conversion pattern

**Before (Phase 2 state):**
```python
class TransactionDAO(db.Model):
    __tablename__ = 'transaction'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    txid = db.Column(db.String(100), unique=True, nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False)
    address = db.Column(db.String(100), nullable=True)
    blocks = db.relationship(
        'BlockDAO', secondary=block_transactions, back_populates='transactions'
    )
```

**After:**
```python
from __future__ import annotations
import datetime
from sqlalchemy import ForeignKey, String, DateTime, Integer, BigInteger, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

class TransactionDAO(db.Model):
    __tablename__ = 'transaction'

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    txid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime)
    address: Mapped[str | None] = mapped_column(String(100))
    blocks: Mapped[list[BlockDAO]] = relationship(
        secondary=block_transactions, back_populates='transactions'
    )
```

### Key rules

1. **Nullability:** `Mapped[X]` is non-nullable; `Mapped[X | None]` is nullable. Drop `nullable=False`/`nullable=True` from `mapped_column(...)` — SA 2.0 infers from the annotation.

2. **Column types:** Move column type into the `mapped_column(...)` first argument: `mapped_column(String(100))`. SA 2.0 can also infer types from annotations (e.g., `Mapped[int]` → `Integer`), but explicit is clearer for non-primitive types like `BigInteger`.

3. **Imports:** `from sqlalchemy.orm import Mapped, mapped_column, relationship`. Other SA types (`String`, `Integer`, `ForeignKey`, etc.) still come from `sqlalchemy` directly. The `db.Column`, `db.relationship`, `db.String`, etc. accessors on the Flask-SQLAlchemy proxy continue to work — use either style consistently, but prefer the SA 2.0 native imports for clarity in typed code.

4. **`db.Model`:** Keep `class TransactionDAO(db.Model):` — Flask-SQLAlchemy 3.1 supports `Mapped[]` on `db.Model` subclasses.

5. **Association tables:** `block_transactions = db.Table(...)` stays as-is (no `Mapped[]` for plain tables).

6. **Many-to-one with `db.backref`:** convert to `back_populates` with explicit Mapped[] on both sides. Existing code uses `db.backref('outflows', order_by='OutflowDAO.idx')` — translate to:

```python
# On OutflowDAO:
transaction: Mapped[TransactionDAO] = relationship(back_populates='outflows')

# On TransactionDAO:
outflows: Mapped[list[OutflowDAO]] = relationship(
    back_populates='transaction', order_by='OutflowDAO.idx'
)
```

The implicit-backref idiom is deprecated in SA 2.0; the explicit `back_populates` on both sides is the modern equivalent and is required for `Mapped[]` to work cleanly.

7. **Recursive CTE methods:** Methods like `BlockDAO.block_chain` use `cls.query.filter(...).cte(recursive=True)`. The return type is `sqlalchemy.CTE`. Annotate:
```python
from sqlalchemy import CTE

@classmethod
def block_chain(cls, block_hash: str) -> CTE:
    ...
```

8. **`with_entities` queries:** Methods using `.query.with_entities(...)` return rows with the requested columns. The return type can be `Iterable[Row[tuple[str]]]` or for simple cases just `Iterable[tuple[str]]`.

### Task steps

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/sa-mapped-annotations
```

- [ ] **Step 2: Establish baseline**

```bash
uv run mypy --strict src/cancelchain/models.py 2>&1 | tail -5
```
Record the baseline error count (likely 30+).

- [ ] **Step 3: Convert one class at a time**

The DAO classes in `models.py` (in roughly this order, smallest first):

1. `OutflowDAO` (lines ~80-114) — simple columns + many-to-one to TransactionDAO.
2. `InflowDAO` (lines ~115-148) — similar shape.
3. `PendingIOflowDAO` (lines ~150-200) — pending pool storage.
4. `PendingTxnDAO` (lines ~200-260) — pending transactions.
5. `TransactionDAO` (lines ~28-78) — already-edited above.
6. `BlockDAO` (lines ~260-450) — biggest, with recursive CTE methods.
7. `ChainFillBlock` (lines ~450-525) — chain-fill staging row.
8. `ChainFill` (lines ~525-590) — chain-fill metadata.
9. `ApiToken` (lines ~593-650) — API token rows (already touched by PR-5 of Phase 2).

After each class conversion:
```bash
uv run mypy --strict src/cancelchain/models.py 2>&1 | tail -3
uv run pytest tests/test_models.py tests/test_chain.py tests/test_block.py -v 2>&1 | tail -15
```

If a test fails, the conversion broke something. The most likely causes:
- Forgot `back_populates` on the other side of a relationship.
- Annotation says non-nullable but the column is actually nullable in the schema.
- A `default=...` or `server_default=...` got dropped from the `mapped_column(...)` call.

Fix and continue. Do NOT commit per-class; the whole conversion lands in one commit at the end of the task.

- [ ] **Step 4: Verify strict mypy on models.py**

```bash
uv run mypy --strict src/cancelchain/models.py
```
Expected: 0 errors.

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest
```
Expected: 162/163 passing. The recursive CTE tests in `test_chain.py` are the highest-risk; they exercise `BlockDAO.block_chain`, `BlockDAO.transactions_chain`, etc.

If any test fails, debug the specific failure. The most likely culprits:
- A column lost a constraint (`unique=True`, `index=True`).
- An association table got accidentally `Mapped[]`'d (it shouldn't be).
- A relationship lost `back_populates` or `order_by`.

- [ ] **Step 6: Ruff format + check**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
```

- [ ] **Step 7: Commit**

```bash
git add src/cancelchain/models.py
git commit -m "$(cat <<'EOF'
feat(sa): adopt Mapped[] for all DAO classes in models.py

Converts every `db.Column(...)` to `Mapped[X] = mapped_column(...)`
and every `db.relationship(...)` to typed `Mapped[list[X]]` or
`Mapped[X]` relationships across all DAO classes:

- TransactionDAO, OutflowDAO, InflowDAO
- BlockDAO (including recursive CTE methods)
- PendingTxnDAO, PendingIOflowDAO
- ChainFill, ChainFillBlock
- ApiToken

Replaces implicit `db.backref(...)` with explicit `back_populates`
on both sides (SA 2.0 idiom). Association tables (`db.Table`) and
the legacy `Model.query` API are preserved — query-style
modernization is Phase 6.

mypy --strict on models.py: 0 errors.

Phase 3 / PR 6 of 9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Push and open PR**

```bash
git push -u origin feat/sa-mapped-annotations
gh pr create --base main --title "feat(sa): adopt Mapped[] for DAO classes in models.py" --body "$(cat <<'EOF'
## Summary
Migrates `src/cancelchain/models.py` to SQLAlchemy 2.0's `Mapped[]` annotations:
- Every `db.Column(...)` → `Mapped[X] = mapped_column(...)`.
- Every `db.relationship(...)` → typed `Mapped[list[X]] = relationship(...)` (or `Mapped[X]` for many-to-one).
- Implicit `db.backref(...)` replaced with explicit `back_populates` on both sides.
- Association tables (`db.Table`) unchanged.
- Legacy `Model.query` API preserved (Flask-SQLAlchemy 3.1 compat shim) — query modernization is Phase 6.

Phase 3 / PR 6 of 9.

## Test plan
- [x] `uv run mypy --strict src/cancelchain/models.py` exits 0.
- [x] `uv run pytest tests/test_chain.py tests/test_block.py tests/test_models.py` passes (recursive-CTE coverage).
- [x] `uv run pytest` passes (162/163).
- [x] No new SA deprecation warnings introduced.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9: Stop — controller handles wor + mwg + sync**

---

## Task 8: PR-7 — Type the infra layer

**Files:**
- Modify (9 files, ~2400 lines combined):
  - `src/cancelchain/__init__.py` (72 lines) — Flask app factory
  - `src/cancelchain/application.py` (101 lines) — template filters, error handlers, blueprint registration
  - `src/cancelchain/api.py` (568 lines) — JSON API views (MethodView subclasses)
  - `src/cancelchain/api_client.py` (237 lines) — Peer API client (challenge/response auth)
  - `src/cancelchain/browser.py` (101 lines) — Browser views (HTML templates)
  - `src/cancelchain/command.py` (948 lines) — Click CLI commands
  - `src/cancelchain/miller.py` (116 lines) — Block miller
  - `src/cancelchain/node.py` (262 lines) — Peer coordination
  - `src/cancelchain/tasks.py` (22 lines) — Celery task definitions

Largest single PR by file count and line count.

### Infra typing patterns

**Pattern 1: Flask view handlers.**

`flask.Response | tuple[Any, int]` is the common return type for views. Specific patterns:

```python
from flask import Response, make_response

class TokenView(MethodView):
    def get(self, address: str) -> Response:
        ...

    def post(self, address: str) -> Response:
        ...
```

For views that return a tuple `(body, status_code)`:
```python
def view() -> tuple[dict[str, Any], int]:
    return {'ok': True}, 200
```

**Pattern 2: Click CLI commands.**

```python
import click

@cli.command()
@click.argument('path')
@click.option('--force', is_flag=True)
def import_chain(path: str, force: bool) -> None:
    ...
```

Click 8.1+ uses the type hints for argument validation. Annotate every `@click.argument`/`@click.option` parameter.

**Pattern 3: Celery task functions.**

```python
@celery.task()
def post_process(host: str, txid: str, visited_hosts: list[str]) -> None:
    ...
```

**Pattern 4: Flask app factory.**

```python
def create_app(config: type | None = None) -> Flask:
    app = Flask(__name__)
    ...
    return app
```

The `config` parameter is a class object; `type` is the right type for "any class."

**Pattern 5: Peer coordination methods.**

`Node` and `Miller` methods take `Block`, `Transaction`, `Chain` types from the domain layer (typed in PR-5).

```python
def receive_block(self, block: Block, peer_hosts: list[str] | None = None) -> None:
    ...
```

### Task steps

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/types-infra-layer
```

- [ ] **Step 2: Decide on split**

Run:
```bash
wc -l src/cancelchain/api.py src/cancelchain/command.py
```

If both `api.py` and `command.py` are large (which they are), and you anticipate the combined diff exceeding 800 lines, consider splitting PR-7 into:

- **PR-7a: Flask + app factory** — `api.py`, `browser.py`, `application.py`, `__init__.py`.
- **PR-7b: CLI + networking** — `command.py`, `node.py`, `miller.py`, `tasks.py`, `api_client.py`.

If splitting, do 7a first (Flask views import less than CLI). Otherwise, proceed as one PR.

For this plan, assume the single-PR path. The implementer can split at their discretion before pushing.

- [ ] **Step 3: Type each file in dependency order**

Smallest → largest:

1. `database.py` and `cache.py` (already done in PR-4).
2. `tasks.py` (22 lines) — Celery task decorators.
3. `__init__.py` (72 lines) — `create_app` factory.
4. `application.py` (101 lines) — template filters (already partially typed in Phase 2 PR-7 follow-up).
5. `browser.py` (101 lines) — browser blueprint views.
6. `miller.py` (116 lines) — `Miller` class methods.
7. `api_client.py` (237 lines) — `ApiClient` class.
8. `node.py` (262 lines) — `Node` class.
9. `api.py` (568 lines) — API blueprint + MethodView classes.
10. `command.py` (948 lines) — CLI commands (largest file).

For each file:
1. Add `from __future__ import annotations`.
2. Annotate every function and method.
3. Run `uv run mypy --strict <file>` and address findings.

- [ ] **Step 4: Verify**

```bash
uv run mypy --strict src/cancelchain/
```
Expected: 0 errors across all source files (PRs 4 + 5 + 6 + 7 together should produce a fully strict-clean tree).

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest
```
Expected: 162/163 passing.

- [ ] **Step 6: Ruff format + check**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
```

- [ ] **Step 7: Commit**

```bash
git add src/cancelchain/__init__.py src/cancelchain/application.py src/cancelchain/api.py src/cancelchain/api_client.py src/cancelchain/browser.py src/cancelchain/command.py src/cancelchain/miller.py src/cancelchain/node.py src/cancelchain/tasks.py
git commit -m "$(cat <<'EOF'
feat(types): annotate infra layer with strict types

Strict typing on the 9 infra modules: __init__, application, api,
api_client, browser, command, miller, node, tasks.

Patterns applied:
- Flask views return `flask.Response` or `tuple[Any, int]`.
- Click CLI commands have parameter types matching @click.argument/@click.option.
- Celery tasks annotated with full signatures.
- Peer coordination methods take typed domain objects (Block, Transaction, Chain).

mypy --strict on the entire src/cancelchain/ tree: 0 errors.

Phase 3 / PR 7 of 9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Push and open PR**

```bash
git push -u origin feat/types-infra-layer
gh pr create --base main --title "feat(types): annotate infra layer" --body "$(cat <<'EOF'
## Summary
Strict typing on the 9 infra modules: __init__, application, api, api_client, browser, command, miller, node, tasks.

After this PR, `mypy --strict src/cancelchain/` returns 0 errors.

Phase 3 / PR 7 of 9.

## Test plan
- [x] `uv run mypy --strict src/cancelchain/` exits 0 (full tree clean).
- [x] `uv run pytest` passes (162/163).
- [x] `uv run ruff format --check` + `ruff check` pass.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9: Stop — controller handles wor + mwg + sync**

---

## Task 9: PR-8 — Make ruff check and mypy hard CI gates

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/.github/workflows/tests.yml`
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (`[tool.mypy]`)
- Modify: `/home/gumptionthomas/Development/cancelchain/CLAUDE.md` (Style section's "ruff check non-blocking" prose)

The "definition of done" gate. After this PR merges, CI fails on any new lint or typing regression.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/ci-strict-gates
```

- [ ] **Step 2: Edit `.github/workflows/tests.yml`**

Find the `uv run ruff check src tests` step (currently with `continue-on-error: true` and a comment block above it). Remove both the comment and the `continue-on-error: true` line. The new lines:
```yaml
      - run: uv run ruff check src tests
      - run: uv run ruff format --check src tests
```

Find the `uv run mypy src` step (currently with `continue-on-error: true` and a comment). Remove the comment and the `continue-on-error: true` line:
```yaml
      - run: uv run mypy src
```

- [ ] **Step 3: Edit `pyproject.toml` `[tool.mypy]`**

Current section:
```toml
[tool.mypy]
python_version = "3.12"
warn_unused_ignores = true
warn_redundant_casts = true
strict_optional = true
files = ["src/cancelchain"]
```

Replace with:
```toml
[tool.mypy]
python_version = "3.12"
files = ["src/cancelchain"]
strict = true
warn_unused_ignores = true
warn_redundant_casts = true
```

`strict = true` implies (and supersedes):
- `disallow_untyped_defs`
- `disallow_any_generics`
- `disallow_untyped_calls`
- `disallow_subclassing_any`
- `disallow_incomplete_defs`
- `check_untyped_defs`
- `no_implicit_optional`
- `warn_redundant_casts`
- `warn_unused_ignores`
- `warn_return_any`
- `no_implicit_reexport`
- `strict_equality`

(Keeping `warn_unused_ignores` and `warn_redundant_casts` explicit because they're already there; `strict_optional` is now implied by `strict` so it can come out.)

Keep any `[[tool.mypy.overrides]]` blocks added in PRs 4 and 5 (marshmallow, Crypto.*).

- [ ] **Step 4: Edit `CLAUDE.md` Style section**

Find the paragraph that begins with:
```
- `ruff` config in `pyproject.toml`: ...
```
and ends with:
```
... Phase 3 removes the `continue-on-error` once the existing lint is cleaned up.
```

Replace the trailing sentence ("In Phase 1 CI, `ruff check` is `continue-on-error: true`... Phase 3 removes the `continue-on-error` once the existing lint is cleaned up.") with:
```
Both `ruff check` and `ruff format --check` are hard CI gates as of Phase 3 / PR 8.
```

If there's another paragraph referencing the non-blocking nature of mypy, update similarly:
```
- `mypy` runs under `[tool.mypy] strict = true` against `src/cancelchain/` and is a hard CI gate as of Phase 3.
```

- [ ] **Step 5: Verify locally**

```bash
uv sync --group dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest
```
All four should exit 0. If `mypy src` produces ANY error, stop — that means PR-4/5/6/7 left something unfinished. Fix the underlying issue in a separate PR before this one.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/tests.yml pyproject.toml CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(ci): make ruff check and mypy hard CI gates

After Phase 3 PRs 1-7, the codebase is lint-clean (`ruff check` passes)
and type-clean (`mypy --strict` passes). This PR removes the
`continue-on-error: true` directives that absorbed the pre-existing
debt in Phase 1.

- `.github/workflows/tests.yml`: drop `continue-on-error: true` on
  both `ruff check` and `mypy` steps; remove the now-stale comment
  blocks above them.
- `pyproject.toml [tool.mypy]`: set `strict = true`; remove
  `strict_optional` (now implied).
- CLAUDE.md: update the Style section prose to reflect current state.

Phase 3 / PR 8 of 9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin feat/ci-strict-gates
gh pr create --base main --title "feat(ci): make ruff check and mypy hard CI gates" --body "$(cat <<'EOF'
## Summary
- Removes `continue-on-error: true` from `ruff check` and `mypy` in `.github/workflows/tests.yml`.
- Sets `[tool.mypy] strict = true`.
- Updates `CLAUDE.md` Style section prose.

PRs 1-7 cleared the underlying debt; this is the gate flip.

Phase 3 / PR 8 of 9.

## Test plan
- [x] `uv run ruff check src tests` exits 0.
- [x] `uv run mypy src` exits 0 under `strict = true`.
- [x] `uv run pytest` passes.
- [ ] CI green on 3.12 and 3.13 (both as hard gates).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Stop — controller handles wor + mwg + sync**

---

## Task 10: PR-9 — Test-fixture `FLASK_SECRET_KEY` hardening

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/tests/.test.env`
- Possibly modify: any file referencing the literal `'testkey'` string

Eliminates pyjwt 2.13's `InsecureKeyLengthWarning` during tests.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b fix/test-secret-key-length
```

- [ ] **Step 2: Audit references to the existing secret**

```bash
grep -rn "testkey\|FLASK_SECRET_KEY" tests/ src/ docs/ 2>&1 | head -20
```
Identify any file other than `tests/.test.env` that has the literal `'testkey'` hardcoded. Most likely there are none — Flask reads it from the env via `from_prefixed_env`.

- [ ] **Step 3: Choose a new secret value**

Pick a 64-byte string (HS256 minimum is 32 bytes; double for safety margin). Suggested:
```
test-secret-key-for-phase-3-must-be-at-least-32-bytes-long-1234
```

This is 63 ASCII chars — well over the 32-byte minimum. The value doesn't need to be cryptographically random for tests, just long enough to satisfy pyjwt's check.

- [ ] **Step 4: Update `tests/.test.env`**

Open `tests/.test.env`. Change:
```
FLASK_SECRET_KEY=testkey
```
to:
```
FLASK_SECRET_KEY=test-secret-key-for-phase-3-must-be-at-least-32-bytes-long-1234
```

Leave the other lines (`SQLALCHEMY_SILENCE_UBER_WARNING`, `CC_READER_ADDRESSES`) unchanged.

- [ ] **Step 5: Run the test suite**

```bash
uv run pytest 2>&1 | tee /tmp/phase3-pr9-test.txt
grep -i "InsecureKeyLength" /tmp/phase3-pr9-test.txt && echo "✗ warning still present" || echo "✓ no InsecureKeyLength warning"
tail -3 /tmp/phase3-pr9-test.txt
```
Expected: 162/163 passing, no `InsecureKeyLengthWarning` output.

- [ ] **Step 6: Commit**

```bash
git add tests/.test.env
git commit -m "$(cat <<'EOF'
fix(tests): harden FLASK_SECRET_KEY against pyjwt insecure-key warning

pyjwt 2.13 (landed in Phase 2 PR-7) emits `InsecureKeyLengthWarning`
when the HS256 secret is shorter than 32 bytes. The test fixture's
`FLASK_SECRET_KEY=testkey` was 7 bytes.

Replace with a 63-byte deterministic test value. Test behavior is
unchanged; warning is gone.

Phase 3 / PR 9 of 9 (final PR of Phase 3).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin fix/test-secret-key-length
gh pr create --base main --title "fix(tests): harden FLASK_SECRET_KEY against pyjwt insecure-key warning" --body "$(cat <<'EOF'
## Summary
Bumps `tests/.test.env`'s `FLASK_SECRET_KEY` from 7 to 63 bytes to silence pyjwt 2.13's `InsecureKeyLengthWarning`.

Phase 3 / PR 9 of 9 (final PR).

## Test plan
- [x] `uv run pytest 2>&1 | grep InsecureKeyLength` is empty.
- [x] `uv run pytest` passes (162/163).
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Stop — controller handles wor + mwg + sync**

---

## Task 11: Phase 3 acceptance verification

**Files:** none modified. Final verification after all 9 impl PRs land.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -12
```
Expected: 9 Phase 3 squash commits visible at the top of main.

- [ ] **Step 2: Fresh-clone simulation**

```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```
Expected: Python 3.12.

- [ ] **Step 3: ruff check (hard gate)**

```bash
uv run ruff check src tests
echo "exit: $?"
```
Expected: `All checks passed!` and exit code 0.

- [ ] **Step 4: ruff format check**

```bash
uv run ruff format --check src tests
echo "exit: $?"
```
Expected: exit 0.

- [ ] **Step 5: mypy strict (hard gate)**

```bash
uv run mypy src
echo "exit: $?"
```
Expected: `Success: no issues found in N source files.` and exit 0.

- [ ] **Step 6: pytest on 3.12**

```bash
uv sync --group dev --python 3.12
uv run pytest
```
Expected: 162 passed, 1 skipped, NO `InsecureKeyLengthWarning` in output.

- [ ] **Step 7: pytest on 3.13**

```bash
UV_PYTHON=3.13 uv sync --group dev --python 3.13 --reinstall
UV_PYTHON=3.13 uv run --python 3.13 pytest
```
Expected: 162 passed, 1 skipped.

- [ ] **Step 8: Verify spec acceptance criteria**

```bash
echo "=== continue-on-error check ==="
grep -c "continue-on-error" .github/workflows/tests.yml
# Expected: 0 (no continue-on-error directives remain)

echo "=== mypy strict ==="
grep "strict = true" pyproject.toml
# Expected: matches in [tool.mypy] block

echo "=== Mapped[] check ==="
grep -c "Mapped\[" src/cancelchain/models.py
# Expected: high number (one per column + relationship)

echo "=== FLASK_SECRET_KEY length ==="
awk -F= '/^FLASK_SECRET_KEY=/ { print length($2) }' tests/.test.env
# Expected: ≥ 32
```

- [ ] **Step 9: Smoke-test CLI**

```bash
uv run cancelchain --help
```
Expected: full subcommand tree prints.

- [ ] **Step 10: Docker build smoke**

```bash
docker build -t cc-phase3-final .
```
Expected: build succeeds. Optionally:
```bash
docker run --rm cc-phase3-final cancelchain --help
```

- [ ] **Step 11: Acceptance complete**

If all of Steps 1-10 pass, Phase 3 is done. No commit. Phase 4 (Marshmallow → Pydantic v2) is the next milestone.

---

## Notes on the wor / mwg workflow

Each impl PR (Tasks 2–10) ends with the controller running `wor` and `mwg`:

1. **`wor` (Wait On Review):** Poll the PR until Copilot review completes. Use GraphQL `reviewThreads` with `isResolved:false` to find unresolved threads. Respond to each in the original comment thread, verifying `in_reply_to_id` on each reply. User manually resolves threads — do not auto-resolve.

2. **`mwg` (Merge When Green):** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

Never skip `wor`, even when CI is green and local tests pass cleanly. Copilot catches what implementers miss.

If Copilot review requests a substantive change, push a new commit to the PR branch (do not amend) and re-run `wor`.

---

## Notes on Dependabot interaction

Same posture as Phase 2:

- Close or "hold" any Dependabot PR that touches `[project.dependencies]` or `[dependency-groups].dev` while Phase 3 is in flight.
- Dependabot PRs against GitHub Actions and Docker base images can land normally.
- After Task 11 acceptance, reopen / unhold Dependabot's deferred PRs.

---

## Roll-back posture

Every PR in this train is independently revertible via `git revert <merge-sha>` because they're squash-merged. If a defect is found after later PRs have landed on top, prefer a forward-fix PR over a revert (which may conflict with later PRs' typed-code changes).

Specifically:
- A typing-PR revert may conflict with PR-8's CI-gate flip; if PR-8 has merged, revert PR-8 first to restore non-blocking CI, then fix the typing-PR's defect on a new PR, then re-merge PR-8.
- A Mapped[] revert in PR-6 conflicts with any later PR-7 or PR-8 that imports types from `models.py`; forward-fix is strongly preferred.
