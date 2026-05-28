# Phase 7b — typed `DeclarativeBase` + mypy override removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch Flask-SQLAlchemy's `db = SQLAlchemy()` to a typed `DeclarativeBase` subclass via `db = SQLAlchemy(model_class=Base)`, then remove the `# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` directive and the 6-line stale header comment block from `src/cancelchain/models.py`. Fix any mypy errors that surface; if FSA's stubs don't propagate `model_class=Base` through to `db.Model`, fall back to direct `(Base):` subclassing across all 11 `db.Model` subclasses (8 `DAO`-suffixed + `ChainFill`, `ChainFillBlock`, `ApiToken`).

**Architecture:** Pure typing change. Two production files touched (`database.py` adds `Base(DeclarativeBase)` + the `model_class=Base` kwarg; `models.py` loses the per-file mypy disable header). No schema, no behavior, no test count changes. The fallback (direct `Base` subclassing) is mechanical and decided after the first mypy run — no upfront commitment needed.

**Tech Stack:** SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1.1 (existing — both already support typed `DeclarativeBase` via `SQLAlchemy(model_class=...)`). No dependency changes. The companion design spec is `docs/superpowers/specs/2026-05-28-phase-7b-declarative-base-design.md`.

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 7a merged. Verify with `git log --oneline -3 main` showing `4070978 feat(models): SA 2.0 query syntax migration (#76)` near the top.
- The branch `docs/phase-7b-design` exists locally with one commit:
  - `6742711 docs(phase-7b): add typed DeclarativeBase migration design spec`
  This plan adds a second commit on that branch (the plan file itself) and ships both as the docs PR.
- CI hard-gates `ruff check`, `ruff format --check`, and `mypy` (strict).
- Test baseline: **236 passed, 1 skipped**. Phase 7b adds zero new tests; the count stays 236.
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-28-phase-7b-declarative-base.md` (this file) + spec already on branch |
| 2 | impl PR | `src/cancelchain/database.py`, `src/cancelchain/models.py`. Possibly `src/cancelchain/models.py` DAO declarations only if the FSA-stubs fallback triggers (Step 6 below). |
| 3 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** The design spec is committed on `docs/phase-7b-design` (`6742711`). This task adds the implementation plan as a second commit and ships both as one docs PR.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-28-phase-7b-declarative-base-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/phase-7b-design`; spec file is tracked; commit count above main is `1`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-28-phase-7b-declarative-base.md
git status docs/superpowers/plans/
```

Expected: file exists; shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-28-phase-7b-declarative-base.md
git commit -m "$(cat <<'EOF'
docs(phase-7b): add typed DeclarativeBase migration implementation plan

Spells out the single-PR impl: branch off main, edit database.py
to add Base(DeclarativeBase) + the model_class=Base kwarg, edit
models.py to remove the 6-line stale header comment block and the
# mypy: disable-error-code directive, run mypy to see what
surfaces, fix surfaced errors inline (per-line # type: ignore[code]
is the explicit escape hatch), fall back to direct
(Base): subclassing across all 11 db.Model subclasses if FSA
stubs don't propagate model_class through to db.Model (8
DAO-suffixed classes + ChainFill, ChainFillBlock, ApiToken). Pure typing
pass — no schema, no behavior, no test count changes (236 stays
236).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/phase-7b-design
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/phase-7b-design --title "docs(phase-7b): Phase 7b typed DeclarativeBase design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 7b design spec (\`docs/superpowers/specs/2026-05-28-phase-7b-declarative-base-design.md\`).
- Adds the Phase 7b implementation plan (\`docs/superpowers/plans/2026-05-28-phase-7b-declarative-base.md\`).
- No code changes.

Phase 7b closes Phase 3's explicit sunset commitment for the per-file \`mypy: disable-error-code\` block on \`src/cancelchain/models.py\` — the last remaining blocker after Phase 7a (commit 4070978) removed every legacy \`Model.query\` / \`db.session.query(...)\` call site and migrated every \`Query[X]\` annotation to \`Select[tuple[X]]\`. Switches Flask-SQLAlchemy's \`db = SQLAlchemy()\` to use a typed \`DeclarativeBase\` subclass via \`db = SQLAlchemy(model_class=Base)\`, then removes the per-file override directive. Pure typing pass — no schema, no behavior, no test count change.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Phase 7b impl — typed `DeclarativeBase` + override removal

**Files:**
- Modify: `src/cancelchain/database.py` (3 changes: add `DeclarativeBase` import, define `Base` class, add `model_class=Base` kwarg)
- Modify: `src/cancelchain/models.py` (remove the 6-line header comment block + the `# mypy: disable-error-code` directive; possibly add narrow per-line ignores or rewrite DAO declarations to `class XDAO(Base):` depending on the Step 6 mypy run)

The migration is short. Steps 2 and 4 are the actual edits; Step 3 is a measurement (run mypy with the override gone and inventory what surfaces); Step 6 is the remediation based on Step 3's findings. After each edit, run `uv run pytest -x 2>&1 | tail -5` to catch runtime regressions early.

### Step 1: Branch off main + sanity check baseline

```bash
git checkout main && git pull --ff-only
git checkout -b feat/phase-7b-declarative-base
git log --oneline -1
```

Expected: the top commit is `4070978 feat(models): SA 2.0 query syntax migration (#76)`.

Then confirm the baseline gates are green BEFORE any edit:

```bash
uv run mypy
uv run ruff check src tests
uv run pytest 2>&1 | tail -3
```

Expected: mypy `Success: no issues found in 24 source files`; ruff `All checks passed!`; pytest `236 passed, 1 skipped`.

If any gate fails on a clean main, stop and surface — something has drifted since 7a merged.

### Step 2: Edit `src/cancelchain/database.py`

The current file is 6 lines:

```python
from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
```

After:

```python
from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
```

Three changes:
1. New import: `from sqlalchemy.orm import DeclarativeBase`.
2. New `Base` class definition (empty body — no mixins, no overrides).
3. `SQLAlchemy()` → `SQLAlchemy(model_class=Base)`.

Verify:

```bash
grep -n 'DeclarativeBase\|model_class=Base' src/cancelchain/database.py
```

Expected: three matches — (1) the `from sqlalchemy.orm import DeclarativeBase` import, (2) the `class Base(DeclarativeBase):` class definition, and (3) the `db = SQLAlchemy(model_class=Base)` call.

### Step 3: Run pytest + mypy to measure the surface

```bash
uv run pytest 2>&1 | tail -3
uv run mypy 2>&1 | tail -20
```

`pytest` is the runtime safety net — if Flask-SQLAlchemy's `model_class=Base` interaction breaks anything (relationship back-population, table args, lazy session), it surfaces here. **All 236 tests must still pass at this step.** If anything fails, stop and investigate — the failure is the `Base` swap interacting with runtime behavior, not a mypy issue.

`mypy` tells us what the `Base` swap did to the type surface. There are three possible outcomes:

- **Outcome A — mypy now passes** (zero errors with `models.py` still carrying its disable block): the base swap alone resolves the typing concerns. Proceed to Step 4 (remove the disable block). The expected case for FSA 3.1+ with the stubs we have.
- **Outcome B — mypy reports new errors that were previously masked** but the disable block is still in place: this means the disable block was actively suppressing real errors that the base swap newly created (rare; would indicate the dynamic `db.Model` was masking something the typed `Base` exposes). Capture the error list and surface as a concern — this is unexpected enough to escalate.
- **Outcome C — no change** (mypy still says zero errors because the disable block is still in place): the expected case where Step 3 doesn't tell us much; the real measurement is Step 5 after the disable block comes out.

In practice the most likely outcome is C — the disable block is still hiding everything, so we proceed to Step 4 to remove it and run mypy again in Step 5.

### Step 4: Remove the `models.py` header comment + mypy disable directive

Open `src/cancelchain/models.py`. The current top of file (post-7a) is:

```python
from __future__ import annotations

# Flask-SQLAlchemy's `db.Model` is dynamically attached and shows up as
# `Any` to mypy strict, which triggers `name-defined` (Name "db.Model"
# is not defined) and `misc` (Class cannot subclass "Model" of type
# "Any") errors on every DAO class declaration here. Phase 7b will
# switch to a typed `DeclarativeBase` subclass and remove this
# suppression.
# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"
import datetime
```

Delete lines 3-9 (the 6-line `#` explanatory comment block at lines 3-8 plus the `# mypy: disable-error-code` directive at line 9). **Do NOT delete lines 10-11** — those are `import datetime` and `import uuid`, which stay. The result:

```python
from __future__ import annotations

import datetime
```

That's the only change in Step 4. The DAO declarations (`class XDAO(db.Model):` at multiple sites in this file) stay unchanged for now — those become typed automatically IF Flask-SQLAlchemy's stubs propagate `model_class=Base` through to `db.Model` (the canonical FSA 3.x pattern that this PR is exercising).

Verify:

```bash
grep -n 'mypy: disable-error-code\|Phase 7b will' src/cancelchain/models.py
```

Expected: returns nothing.

### Step 5: Run mypy to measure what surfaces

```bash
uv run mypy 2>&1
```

Inventory the output. This is the moment of truth.

**Expected case (clean):** `Success: no issues found in 24 source files`. The base swap + override removal is enough; no further edits to `models.py` needed. Proceed to Step 7.

**Likely case (a handful of leftover errors):** 1-5 errors surface, typically `no-any-return` on a `Result.scalar_one_or_none()` call where mypy can't see through the typed Select, or `no-untyped-call` on a specific `db.session.execute(...)` chain. Proceed to Step 6 to remediate.

**Concerning case (many errors, or `class XDAO(db.Model):` declarations still trigger `name-defined` / `misc`):** Flask-SQLAlchemy's stubs aren't propagating `model_class=Base` through `db.Model`. Proceed to Step 6 with the fallback path (direct `Base` subclassing).

Capture the full mypy output for the next step:

```bash
uv run mypy 2>&1 | tee /tmp/phase7b-mypy.log
```

### Step 6: Remediate surfaced errors

Three remediation patterns, applied based on the Step 5 inventory:

**Pattern A — narrow per-line ignore.** For an isolated error that resists a clean annotation fix:

```python
# Before (line N triggers no-any-return)
return db.session.execute(stmt).scalar_one_or_none()

# After
return db.session.execute(stmt).scalar_one_or_none()  # type: ignore[no-any-return]
```

Add a short comment on the ignore line OR the line above explaining why, so a future reader doesn't have to re-derive the reasoning. The format follows the pre-existing per-line ignores in models.py (lines 166, 232, 233, 519, 771 from the post-7a state).

**Pattern B — explicit annotation or `cast()`.** For a return-type mismatch that has a clean fix:

```python
from typing import cast

# Before
def get(...) -> XDAO | None:
    return db.session.execute(stmt).scalar_one_or_none()

# After (if cast is cleaner than a per-line ignore)
def get(...) -> XDAO | None:
    return cast('XDAO | None', db.session.execute(stmt).scalar_one_or_none())
```

Prefer this over Pattern A when the cast clearly improves readability or types-check verification at the call site. Add `cast` to the `typing` imports if needed.

**Pattern C — switch DAO declarations to `class XDAO(Base):` directly.** ONLY if Step 5 surfaced `name-defined` or `misc` errors on the class declarations themselves (meaning FSA's stubs don't propagate `model_class` through `db.Model`). Locate ALL `db.Model` subclasses (note: not all of them have a `DAO` suffix — `ChainFill`, `ChainFillBlock`, and `ApiToken` don't):

```bash
grep -n '^class [A-Za-z]*(db\.Model):' src/cancelchain/models.py
```

Expected: 11 matches in the current code — `TransactionDAO`, `OutflowDAO`, `InflowDAO`, `BlockDAO`, `LongestChainBlockDAO`, `ChainDAO`, `PendingTxnDAO`, `PendingIOflowDAO`, `ChainFill`, `ChainFillBlock`, `ApiToken`. For each, change `(db.Model)` → `(Base)` and add `from cancelchain.database import Base` to the imports (alongside the existing `from cancelchain.database import db`). The runtime behavior is identical because `db.Model IS Base` after Step 2 — this change is purely for mypy's resolution. Missing any class would leave `name-defined` / `misc` errors on that line and fail the override-removal acceptance gate.

**After remediation, re-run mypy AND pytest:**

```bash
uv run mypy
uv run pytest 2>&1 | tail -3
```

Both must exit clean. Iterate Step 6 until both gates are clean. If you're applying more than ~5 per-line ignores OR more than 2 of the three patterns simultaneously, stop and surface — the spec's "single-PR shape" decision has a fallback to splitting, but that's a controller-level call, not an implementer one.

### Step 7: Verify all gates

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

All four must exit 0. Test count: 236 passed, 1 skipped (unchanged).

### Step 8: Run the benchmark harness for sanity

```bash
uv run python bench/rebuild_walk_bench.py --sizes 1000 10000 100000 2>&1 | tail -10
```

Expected: per-step times ~0.25 ms/step (matching the Phase 7a baseline). Typing changes shouldn't affect runtime performance, so this is mostly a smoke check. If significantly slower, investigate before committing — that would indicate something at runtime changed unexpectedly.

### Step 9: Commit

```bash
git add src/cancelchain/database.py src/cancelchain/models.py
git commit -m "$(cat <<'EOF'
feat(models): typed DeclarativeBase + remove mypy override block

Phase 7b. Switches Flask-SQLAlchemy's db = SQLAlchemy() to use a
typed DeclarativeBase subclass via db = SQLAlchemy(model_class=
Base), then removes the # mypy: disable-error-code=
"no-untyped-call,no-any-return,name-defined,misc" directive and
the 6-line stale header comment block from src/cancelchain/
models.py. Closes Phase 3's explicit sunset commitment for the
per-file mypy override on models.py — the last remaining blocker
after Phase 7a (commit 4070978) modernized every legacy call site
and annotation.

Pure typing pass: no schema changes, no behavior changes, no new
tests, test count stays 236. Bench harness confirms per-step
rebuild perf is unchanged from the Phase 7a baseline.

src/cancelchain/database.py:
- New import: `from sqlalchemy.orm import DeclarativeBase`.
- New `class Base(DeclarativeBase): pass` declaration.
- `db = SQLAlchemy()` → `db = SQLAlchemy(model_class=Base)`.

src/cancelchain/models.py:
- Remove the 6-line `#` comment block (lines 3-8) at the top of
  the file explaining why the disable block existed.
- Remove the `# mypy: disable-error-code="no-untyped-call,
  no-any-return,name-defined,misc"` directive.
- [Add any per-line `# type: ignore[code]` additions or DAO
  declaration rewrites from Step 6 here if they were needed.]

Phase 7 closed. The 5 pre-existing per-line # type:
ignore[assignment] lines (Mapped[X] vs X | None invariants in DAO
__init__ methods) stay; those are a separate typing concern not
covered by the typed-base swap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Note for the implementer:** The commit message bullet list under `models.py:` includes a bracketed placeholder for any Step 6 additions. Replace that bracket with a concrete list of what was added (or delete the bracketed line if Step 6 added nothing). The rest of the commit message stays as written.

### Step 10: Push and open PR

```bash
git push -u origin feat/phase-7b-declarative-base
gh pr create --base main --title "feat(models): typed DeclarativeBase + remove mypy override block" --body "$(cat <<'EOF'
## Summary
- Switches Flask-SQLAlchemy's \`db = SQLAlchemy()\` to use a typed \`DeclarativeBase\` subclass via \`db = SQLAlchemy(model_class=Base)\` (new \`Base(DeclarativeBase)\` class in \`src/cancelchain/database.py\`).
- Removes the \`# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"\` directive and the 6-line stale header comment block from \`src/cancelchain/models.py\`.
- Pure typing pass — no schema changes, no behavior changes, no new tests, no test-count change (236 stays 236).

## Why
Phase 7b closes Phase 3's explicit sunset commitment for the per-file mypy override on \`models.py\` — the last remaining blocker after Phase 7a (commit 4070978) modernized every legacy \`Model.query\` / \`db.session.query(...)\` call site and migrated every \`Query[X]\` annotation to \`Select[tuple[X]]\`. With this PR, Phase 7 closes.

## Out of scope (per spec)
- No DAO behavior changes. The DAO class declarations either stay as \`class XDAO(db.Model):\` (typed automatically through FSA's \`model_class=Base\` propagation) or move to direct \`class XDAO(Base):\` subclassing — whichever mypy needs, decided during impl.
- No removal of the 5 pre-existing per-line \`# type: ignore[assignment]\` lines (Mapped[X] vs X | None invariants in DAO __init__ methods — a separate typing concern).
- No \`MappedAsDataclass\` migration (out of scope — would rewrite every DAO \`__init__\` signature).

## Test plan
- [x] \`uv run mypy\` exits 0 with NO per-file override on \`models.py\`.
- [x] \`uv run pytest\` passes 236 (unchanged).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [x] \`bench/rebuild_walk_bench.py --sizes 1000 10000 100000\` matches the Phase 7a baseline (~0.25 ms/step).
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 11: Stop — controller handles wor + mwg + sync

---

## Task 3: Phase 7b acceptance verification

**Files:** none modified. Final verification after the impl PR lands on main.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

Expected: top two commits are the docs PR squash and the impl PR squash.

- [ ] **Step 2: Override directive eradicated**

```bash
grep -n 'mypy: disable-error-code' src/cancelchain/models.py
```

Expected: returns nothing. The override directive is gone from `models.py`.

```bash
grep -n 'DeclarativeBase\|model_class=Base' src/cancelchain/database.py
```

Expected: three matches — the `from sqlalchemy.orm import DeclarativeBase` import, the `class Base(DeclarativeBase):` class definition, and the `db = SQLAlchemy(model_class=Base)` call.

- [ ] **Step 3: Hard CI gates pass**

```bash
uv run ruff check src tests; echo "ruff check exit: $?"
uv run ruff format --check src tests; echo "ruff format exit: $?"
uv run mypy; echo "mypy exit: $?"
```

All three exit 0. **The headline acceptance gate is `mypy exit: 0`** — that confirms the typed `Base` is doing its job without any per-file overrides on `models.py`.

- [ ] **Step 4: Tests pass on 3.12 and 3.13**

```bash
uv run --python 3.12 pytest 2>&1 | tail -3
uv run --python 3.13 pytest 2>&1 | tail -3
```

Expected: both print `236 passed, 1 skipped`.

- [ ] **Step 5: Benchmark perf unchanged**

```bash
uv run python bench/rebuild_walk_bench.py --sizes 1000 10000 100000 2>&1 | tail -10
```

Expected: per-step times ~0.25 ms/step on local SQLite (matching Phase 7a baseline within noise).

- [ ] **Step 6: CLI smoke**

```bash
uv run cancelchain --help
```

Expected: prints the full command tree (an `ERROR: 'SQLALCHEMY_DATABASE_URI' must be set` message is expected if there's no `.env` in cwd, but the `--help` output still renders).

- [ ] **Step 7: Docker build smoke**

```bash
docker build --target builder -t cc-phase7b-final .
```

Expected: succeeds.

- [ ] **Step 8: Acceptance complete**

If Steps 1-7 all pass, Phase 7b is done. Phase 7 closes. No commit.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 2) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory).
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend) and post a `/copilot review` comment on the PR — Copilot's auto-review only fires on the initial push; subsequent rounds need the manual trigger (per the user's memory).

---

## Risks and watchpoints

### Risk: FSA stubs don't propagate `model_class=Base` through to `db.Model`

Per the spec's Risks section, this is the most likely source of unexpected mypy surface. If Step 5's mypy output shows `name-defined` errors on the `class XDAO(db.Model):` declarations themselves (e.g., `error: Name "db.Model" is not defined  [name-defined]`) or `misc` errors on follow-on `Class cannot subclass "Model" of type "Any"`, FSA's stubs aren't carrying the type. The fix is Step 6's Pattern C — direct `Base` subclassing across all 11 `db.Model` subclasses (not just the DAO-suffixed ones — `ChainFill`, `ChainFillBlock`, and `ApiToken` also inherit from `db.Model`). This is mechanical (find/replace `(db.Model)` → `(Base)` in class headers, plus add `Base` to the import block in `models.py`) and shouldn't take more than a few minutes. Missing any of the 11 would leave `name-defined` / `misc` errors on that line and fail the override-removal gate.

### Risk: `no-any-return` on `scalar_one_or_none()` / `scalars().first()` chains

SA 2.x's `Select[tuple[X]]` typing carries through `db.session.execute(...).scalar_one_or_none()` to return `X | None`, but `Result.scalars().first()` returns `X | None` too — the typing chain should be clean. If mypy reports `no-any-return` on a specific site, prefer Pattern A (per-line ignore with a short comment) over Pattern B (explicit cast), because a cast at every "fetch one" call site bloats the codebase without buying real type safety. Per-line ignores are the documented escape hatch.

### Risk: `no-untyped-call` on `db.aliased(...)` or specific FSA helpers

`db.aliased` from Flask-SQLAlchemy's facade delegates to `sqlalchemy.orm.aliased`, which is typed. If mypy reports `no-untyped-call` on `db.aliased(...)`, the FSA stubs aren't quite right. Two paths: (a) per-line ignore (Pattern A), or (b) import `aliased` directly from `sqlalchemy.orm` and use it instead of going through `db`. (b) is a slightly larger change but a real improvement; consider it if mypy flags this pattern at more than 2-3 sites.

### Risk: pytest fails after Step 2's `database.py` edit

Possible runtime breakage from the `model_class=Base` swap. Most likely culprit: a Flask-SQLAlchemy feature that interacts with the dynamic `Model` (e.g., automatic `__tablename__` derivation if any DAO doesn't explicitly set it — verify with `grep -L '__tablename__' src/cancelchain/models.py` to confirm all DAOs do set it explicitly; the current codebase does). If a test fails at Step 3, stop and inspect — surface the failure to the user before continuing.

### Risk: the 5 pre-existing per-line `# type: ignore[assignment]` lines start failing

The `Mapped[X]` vs `X | None` assignment issue these cover is independent of the base class. Typed `DeclarativeBase` shouldn't change which errors they suppress. If any of them stops being needed (mypy says `unused-ignore`), that's a small win — delete the ignore. If any of them needs a different code (e.g., `assignment` became `no-any-return`), update the code in the ignore.

### Risk: `# type: ignore[misc]` proliferation if Pattern C is needed

Pattern C (direct `Base` subclassing) is preferred over adding `# type: ignore[misc]` on every DAO declaration, precisely because per-line `misc` ignores would create N noisy lines for an N-DAO file. The direct-subclassing fix is one find/replace operation. If you're considering Pattern C with `# type: ignore[misc]` on >2 lines, prefer the direct-subclassing path.

### Risk: Bench harness regression

Typing changes shouldn't affect runtime. If Step 8's bench numbers differ noticeably from Phase 7a's baseline (~0.25 ms/step), something at runtime changed unexpectedly — surface it. The base class swap COULD theoretically change SA's table registration order or relationship resolution order, but in practice this is fully deterministic and shouldn't show up in bench numbers.
