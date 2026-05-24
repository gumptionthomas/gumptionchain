# Phase 2 — Library Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the seven-PR library upgrade train laid out in `docs/superpowers/specs/2026-05-24-phase-2-library-upgrades-design.md`. After this plan completes, the project runs on Python 3.12+, Flask 3, SQLAlchemy 2.0 (legacy API), pymerkle 5, and argon2-cffi, with all routine runtime + dev dependencies on current floors.

**Architecture:** Each PR is one task. Tasks run sequentially on `main` (after the docs PR lands first), each starting from a clean `main` pull and ending with a squash-merge + branch deletion. Most tasks are dependency-pin edits in `pyproject.toml` plus a `uv lock` + a focused test run; only PR-4 (pymerkle) and PR-5 (passlib) touch source code meaningfully.

**Tech Stack:** uv (package manager + lock), GitHub CLI (`gh`), Flask 3, Werkzeug 3, Flask-SQLAlchemy 3.1, SQLAlchemy 2.0, pymerkle 5, argon2-cffi, Celery 5.4+, gunicorn 23+, PyJWT 2.9+, pytest 8.3+, ruff 0.7+, mypy 1.13+, pre-commit 4+.

---

## Prerequisites

- You are working in `/home/gumptionthomas/Development/cancelchain`. Use absolute paths or `cd` once at the start of a session.
- `uv --version` prints `0.4.x` or newer (the Dockerfile uses the `0.4` image; local CLI should match or exceed).
- `gh --version` works and `gh auth status` shows you're authenticated as the repo owner.
- `docker --version` works and the daemon is running (only needed for Task 9 acceptance).
- Python 3.12 is installable via uv. Verify ahead of time: `uv python install 3.12 && uv python install 3.13`.
- The branch `docs/phase-2-design` exists locally and contains commit `572cca4` (the design spec). This plan adds the second commit on that branch, then ships both as the docs PR.
- Each impl PR ends with `wor` (wait-on-review) and `mwg` (merge-when-green); see "Notes on the wor / mwg workflow" near the end of this document for the mechanics. Never merge without Copilot review.
- Never push directly to `main`. Every change in this plan goes through a branch + PR.

---

## File Map

### Files touched per task

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-24-phase-2-library-upgrades.md` (this file) |
| 2 | PR-1 Python floor | `pyproject.toml`, `Dockerfile`, `.github/workflows/tests.yml`, `.python-version`, `README.rst`, `CLAUDE.md` |
| 3 | PR-2 Flask 3 | `pyproject.toml`, `uv.lock` |
| 4 | PR-3 SA 2.0 | `pyproject.toml`, `uv.lock`, possibly `tests/.test.env` |
| 5 | PR-4 pymerkle 5 | `pyproject.toml`, `uv.lock`, `src/cancelchain/block.py`, possibly test fixtures |
| 6 | PR-5 passlib swap | `pyproject.toml`, `uv.lock`, `src/cancelchain/models.py` |
| 7 | PR-6 runtime bumps | `pyproject.toml`, `uv.lock` |
| 8 | PR-7 dev-tool bumps | `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml` |
| 9 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:**
- Modify: nothing (the plan you are reading is created by this task and committed alongside the already-committed spec)

The design spec is already committed to `docs/phase-2-design` as `572cca4`. This task adds the implementation plan and ships them together as a single docs PR.

- [ ] **Step 1: Confirm branch state**

Run:
```bash
git rev-parse --abbrev-ref HEAD
git log --oneline main..HEAD
```
Expected: branch is `docs/phase-2-design`; one commit above main: `572cca4 docs(phase-2): add Phase 2 library-upgrades design spec`.

- [ ] **Step 2: Verify the plan file is present**

Run:
```bash
ls -la docs/superpowers/plans/2026-05-24-phase-2-library-upgrades.md
git status docs/superpowers/plans/
```
Expected: file exists, `git status` shows it as untracked.

- [ ] **Step 3: Stage and commit the plan**

Run:
```bash
git add docs/superpowers/plans/2026-05-24-phase-2-library-upgrades.md
git commit -m "$(cat <<'EOF'
docs(phase-2): add Phase 2 library-upgrades implementation plan

Spells out the 7 sequential impl PRs (Python floor, Flask 3, SQLAlchemy
2.0, pymerkle 5, passlib swap, runtime bumps, dev-tool bumps) with
exact files, commands, expected outputs, and the wor/mwg cycle between
each PR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```
Expected: pre-commit runs trailing-whitespace / end-of-file / yaml / toml / merge-conflict hooks (ruff hooks skip — no .py files in this commit). Commit succeeds.

- [ ] **Step 4: Push the branch**

Run:
```bash
git push -u origin docs/phase-2-design
```
Expected: branch is created on remote.

- [ ] **Step 5: Open the docs PR**

Run:
```bash
gh pr create --base main --head docs/phase-2-design --title "docs(phase-2): add Phase 2 design + implementation plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 2 design spec (`docs/superpowers/specs/2026-05-24-phase-2-library-upgrades-design.md`).
- Adds the Phase 2 implementation plan (`docs/superpowers/plans/2026-05-24-phase-2-library-upgrades.md`).
- No code changes. Subsequent impl PRs reference these documents.

Phase 2 ships as seven small PRs in sequence:
1. Python floor 3.9 → 3.12
2. Flask 2 → 3 + Werkzeug 3
3. SQLAlchemy 1.4 → 2.0 + Flask-SQLAlchemy 3.1
4. pymerkle 4 → 5
5. passlib → argon2-cffi
6. Routine runtime bumps (gunicorn, celery, pyjwt, …)
7. Dev-tool bumps (pytest, ruff, mypy, pre-commit)

## Test plan
- [ ] Spec self-review passes (already done in the brainstorming session).
- [ ] Plan self-review passes (already done in the planning session).
- [ ] Reviewer confirms PR list matches the spec's "Changes" section.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: prints the new PR URL.

- [ ] **Step 6: Wait for Copilot review and CI (`wor`)**

Use the `wor` workflow (poll PR reviews in background, address comments). For a docs-only PR, Copilot may flag prose nits; address them by amending and force-pushing if minor, or by additional commits if substantive.

Expected: CI green (this PR only touches `docs/`; the tests workflow still runs but should pass trivially). Copilot review either approves or comments addressed.

- [ ] **Step 7: Merge when green (`mwg`)**

Run:
```bash
PR_NUM=$(gh pr view docs/phase-2-design --json number -q .number)
gh pr checks "$PR_NUM" --watch
gh pr merge "$PR_NUM" --squash --delete-branch
```
Expected: checks pass; PR squash-merges; branch deleted on both remote and local.

- [ ] **Step 8: Sync local main**

Run:
```bash
git checkout main
git pull --ff-only
git branch -D docs/phase-2-design 2>/dev/null || true
git log --oneline -3
```
Expected: latest commit is the squash-merge of the docs PR. The local feature branch is gone.

---

## Task 2: PR-1 — Python floor 3.9 → 3.12

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (`requires-python`, classifiers, ruff `target-version`, mypy `python_version`)
- Modify: `/home/gumptionthomas/Development/cancelchain/Dockerfile` (`ARG PYTHON_VERSION`)
- Modify: `/home/gumptionthomas/Development/cancelchain/.github/workflows/tests.yml` (matrix)
- Modify: `/home/gumptionthomas/Development/cancelchain/.python-version`
- Modify: `/home/gumptionthomas/Development/cancelchain/README.rst` (line 17)
- Modify: `/home/gumptionthomas/Development/cancelchain/CLAUDE.md` (Style section line 109; "Python ≥ 3.9" → "Python ≥ 3.12")

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main
git pull --ff-only
git checkout -b feat/python-floor-3-12
```
Expected: new branch from latest main.

- [ ] **Step 2: Edit `pyproject.toml`**

Open `pyproject.toml`. Make these exact changes:

Change line 12 from:
```toml
requires-python = ">=3.9"
```
to:
```toml
requires-python = ">=3.12"
```

In the classifiers list (lines 20-31), remove these three lines:
```toml
  "Programming Language :: Python :: 3.9",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
```
Keep `3.12` and `3.13`.

Change `[tool.ruff] target-version = "py39"` (line 80) to:
```toml
target-version = "py312"
```

Change `[tool.mypy] python_version = "3.9"` (line 156) to:
```toml
python_version = "3.12"
```

- [ ] **Step 3: Edit `.python-version`**

Replace the entire file contents with:
```
3.12
```
(One line, trailing newline.)

- [ ] **Step 4: Edit `Dockerfile` line 2**

Change:
```dockerfile
ARG PYTHON_VERSION=3.10
```
to:
```dockerfile
ARG PYTHON_VERSION=3.12
```

- [ ] **Step 5: Edit `.github/workflows/tests.yml`**

Change line 10 from:
```yaml
        python-version: ['3.10', '3.11', '3.12', '3.13']
```
to:
```yaml
        python-version: ['3.12', '3.13']
```

- [ ] **Step 6: Edit `README.rst` line 17**

Change `Python >= 3.9` to:
```
Python >= 3.12
```

- [ ] **Step 7: Edit `CLAUDE.md` Style section**

Find the line that currently reads:
```
- Python ≥ 3.9 (CI matrix: 3.10, 3.11, 3.12, 3.13; 3.9 is the floor but not actively tested post-EOL). Avoid 3.10-only syntax in `src/`.
```

Replace with:
```
- Python ≥ 3.12 (CI matrix: 3.12, 3.13). 3.13 is the highest actively tested version.
```

Also find the earlier ruff line in CLAUDE.md's Style section that says "target Python 3.9" and update it to "target Python 3.12".

- [ ] **Step 8: Refresh the lockfile against the new floor**

Run:
```bash
uv lock
```
Expected: `uv.lock` may rewrite with a new `requires-python` metadata block. Most resolution should be unchanged because no version constraints in `[project.dependencies]` moved — only the Python floor.

- [ ] **Step 9: Local install + smoke**

Run:
```bash
uv sync --group dev
uv run python --version
```
Expected: Python 3.12.x (uv selects the highest compatible installed).

- [ ] **Step 10: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: same number of tests as before, all passing. If any test fails due to a 3.12-specific behavior, that is in scope to fix in this PR (likely a deprecation that became a removal — e.g. `datetime.datetime.utcnow()` is deprecated since 3.12 but still works).

- [ ] **Step 11: Format and lint check**

Run:
```bash
uv run ruff format --check src tests
uv run ruff check src tests
```
Expected: `ruff format` passes. `ruff check` may emit pre-existing lint debt (Phase 3 cleans it up); CI's `continue-on-error: true` absorbs.

- [ ] **Step 12: Docker build smoke (target builder only)**

Run:
```bash
docker build --target builder -t cc-phase2-pr1-test .
```
Expected: builds successfully. The full multi-stage build is verified in CI; this catches Dockerfile syntax errors locally.

- [ ] **Step 13: Commit**

Run:
```bash
git add pyproject.toml uv.lock Dockerfile .github/workflows/tests.yml .python-version README.rst CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(python): raise Python floor to 3.12

Drops 3.9, 3.10, 3.11 from `requires-python` and the CI matrix. Bumps
ruff `target-version`, mypy `python_version`, Dockerfile default
`PYTHON_VERSION` ARG, and `.python-version` to 3.12. Trims classifiers
and updates README + CLAUDE.md prose.

Phase 2 / PR 1 of 7 (see docs/superpowers/specs/2026-05-24-phase-2-…).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```
Expected: commit succeeds (pre-commit hooks pass).

- [ ] **Step 14: Push and open PR**

Run:
```bash
git push -u origin feat/python-floor-3-12
gh pr create --base main --title "feat(python): raise Python floor to 3.12" --body "$(cat <<'EOF'
## Summary
- `requires-python = ">=3.12"`; classifiers trim to 3.12/3.13.
- `[tool.ruff] target-version = "py312"`, `[tool.mypy] python_version = "3.12"`.
- CI matrix drops to `['3.12', '3.13']`.
- Dockerfile default `ARG PYTHON_VERSION=3.12`.
- `.python-version` → `3.12`.
- README + CLAUDE.md prose updated.

Phase 2 / PR 1 of 7. Spec: `docs/superpowers/specs/2026-05-24-phase-2-library-upgrades-design.md`.

## Test plan
- [x] `uv sync --group dev` resolves cleanly on 3.12.
- [x] `uv run pytest` passes locally.
- [x] `uv run ruff format --check` passes.
- [x] `docker build --target builder` succeeds.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR URL prints.

- [ ] **Step 15: wor + mwg**

Run `wor` to wait for Copilot review. Address any comments. Then run `mwg`:
```bash
PR_NUM=$(gh pr view feat/python-floor-3-12 --json number -q .number)
gh pr checks "$PR_NUM" --watch
gh pr merge "$PR_NUM" --squash --delete-branch
```
Expected: squash-merge, branch deleted.

- [ ] **Step 16: Sync main**

Run:
```bash
git checkout main && git pull --ff-only
git branch -D feat/python-floor-3-12 2>/dev/null || true
```

---

## Task 3: PR-2 — Flask 3 + Werkzeug 3

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (Flask, Flask-Caching, add explicit werkzeug)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock` (generated by `uv lock`)

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b feat/flask-3
```

- [ ] **Step 2: Edit `pyproject.toml` dependency lines**

In the `[project] dependencies` list:

Change:
```toml
  "Flask>=2.3",
  "Flask-Caching>=2.0",
```
to:
```toml
  "Flask>=3.0",
  "Flask-Caching>=2.3",
  "werkzeug>=3.0",
```

(Insert the `werkzeug>=3.0` line; keep alphabetical-ish ordering — the existing list is grouped logically, so put `werkzeug>=3.0` near the bottom, after `sqlalchemy<2.0`, or after `requests>=2.31` if that reads better.)

- [ ] **Step 3: Resolve the lockfile**

Run:
```bash
uv lock --upgrade-package flask --upgrade-package flask-caching --upgrade-package werkzeug
```
Expected: uv recomputes the lock with Flask 3.x and Werkzeug 3.x. If a transitive constraint forces an older Flask, uv will print an error; in that case, identify the blocking package and either pin its newer version or open an issue.

- [ ] **Step 4: Sync the venv**

Run:
```bash
uv sync --group dev
uv run python -c "from importlib.metadata import version; print(version('flask'), version('werkzeug'), version('Flask-Caching'))"
```
Expected: Flask ≥ 3.0, Werkzeug ≥ 3.0, Flask-Caching ≥ 2.3.

- [ ] **Step 5: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: all tests pass. If a Flask 3 deprecation-removal bites (e.g. `flask.json.JSONEncoder` was removed), the failure will be in `tests/` or in `src/cancelchain/api.py` / `src/cancelchain/browser.py`. Likely call-sites:
  - `flask.Blueprint`, `flask.MethodView`, `flask.abort`, `flask.current_app`, `flask.make_response`, `flask.request`, `flask.render_template` — all stable in 3.x.
  - `flask.cli.FlaskGroup`, `flask.cli.AppGroup`, `flask.cli.with_appcontext` — stable.
  - `Flask.config.from_prefixed_env`, `Flask.config.from_object` — stable.

If pytest fails, read the traceback, narrow to the call-site, and apply the minimal fix. Document the fix in the commit message.

- [ ] **Step 6: Format and lint**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes.

- [ ] **Step 7: Commit**

Run:
```bash
git add pyproject.toml uv.lock
# If source edits were needed:
# git add src/cancelchain/<file>.py
git commit -m "$(cat <<'EOF'
feat(deps): upgrade to Flask 3 + Werkzeug 3

Bumps Flask>=3.0 and Flask-Caching>=2.3 (first line to support Flask 3),
pins werkzeug>=3.0 explicitly (no longer rely on transitive resolution).

Phase 2 / PR 2 of 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Push, open PR, wor, mwg**

Run:
```bash
git push -u origin feat/flask-3
gh pr create --base main --title "feat(deps): upgrade to Flask 3 + Werkzeug 3" --body "$(cat <<'EOF'
## Summary
- `Flask>=3.0` (was `>=2.3`).
- `Flask-Caching>=2.3` (was `>=2.0`).
- Pin `werkzeug>=3.0` explicitly.

Phase 2 / PR 2 of 7.

## Test plan
- [x] `uv lock --upgrade-package flask …` resolves cleanly.
- [x] `uv run pytest` passes.
- [x] `uv run ruff format --check` passes.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Then `wor`, then `mwg`. Sync main:
```bash
git checkout main && git pull --ff-only
git branch -D feat/flask-3 2>/dev/null || true
```

---

## Task 4: PR-3 — SQLAlchemy 2.0 + Flask-SQLAlchemy 3.1

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (drop SA cap, bump Flask-SQLAlchemy)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock`
- Possibly modify: `/home/gumptionthomas/Development/cancelchain/tests/.test.env` (warning suppression)
- Possibly modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` `[tool.pytest.ini_options] filterwarnings` block

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b feat/sqlalchemy-2
```

- [ ] **Step 2: Edit `pyproject.toml`**

In the `[project] dependencies` list:

Change:
```toml
  "Flask-SQLAlchemy>=3.0",
```
to:
```toml
  "Flask-SQLAlchemy>=3.1",
```

Change:
```toml
  "sqlalchemy<2.0",
```
to:
```toml
  "sqlalchemy>=2.0",
```

- [ ] **Step 3: Resolve the lockfile**

Run:
```bash
uv lock --upgrade-package sqlalchemy --upgrade-package flask-sqlalchemy
```
Expected: uv recomputes the lock with SA ≥ 2.0 and Flask-SQLAlchemy ≥ 3.1.

- [ ] **Step 4: Sync the venv**

Run:
```bash
uv sync --group dev
uv run python -c "import sqlalchemy, flask_sqlalchemy; print(sqlalchemy.__version__, flask_sqlalchemy.__version__)"
```
Expected: SQLAlchemy ≥ 2.0, Flask-SQLAlchemy ≥ 3.1.

- [ ] **Step 5: Run the test suite**

Run:
```bash
uv run pytest 2>&1 | tee /tmp/sa2-test-output.txt
```
Expected outcomes:
  - All tests pass (assertions still hold).
  - Possibly noisy `MovedIn20Warning` or `LegacyAPIWarning` emissions.

Inspect the output for:
- Test failures (must be fixed in this PR).
- Excessive `LegacyAPIWarning` noise from `Model.query.filter_by(...)`, `db.session.query(...).join(...)` etc. If the noise drowns out real output, address it in Step 6.

- [ ] **Step 6: Decide on warning suppression (only if Step 5 shows excessive noise)**

Two options. Pick one:

**Option A: pytest-scoped suppression via `pyproject.toml`.**

Edit `pyproject.toml`'s `[tool.pytest.ini_options] filterwarnings` block. Currently:
```toml
filterwarnings = [
  "ignore:.*SelectableGroups dict interface is deprecated.*"
]
```
Append:
```toml
filterwarnings = [
  "ignore:.*SelectableGroups dict interface is deprecated.*",
  "ignore::sqlalchemy.exc.LegacyAPIWarning",
  "ignore::sqlalchemy.exc.MovedIn20Warning",
]
```

**Option B: environment-variable suppression via `tests/.test.env`.**

Edit `tests/.test.env`. The existing first line is:
```
SQLALCHEMY_SILENCE_UBER_WARNING=1
```
Leave it. Add (after the existing lines):
```
SQLALCHEMY_WARN_20=0
```

If both options together still produce noise, that's fine — they cover slightly different warning categories. Use whichever single approach silences the bulk; we'll remove these in Phase 3 when the call sites are modernized.

- [ ] **Step 7: Re-run tests after suppression**

Run:
```bash
uv run pytest
```
Expected: passes cleanly with minimal warning noise.

- [ ] **Step 8: Format and lint**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes.

- [ ] **Step 9: Commit**

Run:
```bash
git add pyproject.toml uv.lock
# If you edited tests/.test.env:
# git add tests/.test.env
git commit -m "$(cat <<'EOF'
feat(deps): upgrade to SQLAlchemy 2.0 + Flask-SQLAlchemy 3.1

Drops the `sqlalchemy<2.0` cap and bumps Flask-SQLAlchemy>=3.1 (first
line with full SA 2.0 support). The codebase keeps the legacy
`Model.query` / `db.session.query` API, which Flask-SQLAlchemy 3.1
preserves on top of SA 2.0. Query-style modernization
(`db.session.execute(db.select(...))`) bundles with Phase 3's
`Mapped[]` typing pass.

Suppresses SA 2.0 legacy warnings via [pytest filterwarnings | tests/.test.env]
so the test log stays readable.

Phase 2 / PR 3 of 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```
(Edit the bracketed `[pytest filterwarnings | tests/.test.env]` to match whichever you chose in Step 6, or remove the suppression line entirely if Step 5 was already clean.)

- [ ] **Step 10: Push, open PR, wor, mwg**

Run:
```bash
git push -u origin feat/sqlalchemy-2
gh pr create --base main --title "feat(deps): SQLAlchemy 2.0 + Flask-SQLAlchemy 3.1" --body "$(cat <<'EOF'
## Summary
- Drop `sqlalchemy<2.0` cap; new constraint `sqlalchemy>=2.0`.
- Bump `Flask-SQLAlchemy>=3.1` (preserves legacy `Model.query` API on SA 2.0).
- Suppress SA 2.0 legacy warnings during tests (removed in Phase 3).

Query-style modernization (`.query.filter_by(...)` → `db.session.execute(db.select(...))`) is **not** in this PR — it lands in Phase 3 with the `Mapped[]` typing pass.

Phase 2 / PR 3 of 7.

## Test plan
- [x] `uv lock --upgrade-package sqlalchemy …` resolves cleanly.
- [x] `uv run pytest` passes.
- [x] Recursive CTE tests (`block_chain`, `transactions_chain`, `inflows_chain`, `outflows_chain` paths) pass.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Then `wor`, then `mwg`. Sync main.

---

## Task 5: PR-4 — pymerkle 4 → 5

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (drop `<5` cap)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock`
- Modify: `/home/gumptionthomas/Development/cancelchain/src/cancelchain/block.py` (Merkle API calls at lines 12-13, 156-174)
- Possibly modify: test fixtures that hard-code Merkle root values (search via grep)

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b feat/pymerkle-5
```

- [ ] **Step 2: Edit `pyproject.toml`**

Change:
```toml
  "pymerkle>=4.0,<5.0",
```
to:
```toml
  "pymerkle>=5",
```

- [ ] **Step 3: Resolve the lockfile and install**

Run:
```bash
uv lock --upgrade-package pymerkle
uv sync --group dev
uv run python -c "import pymerkle; print(pymerkle.__version__)"
```
Expected: pymerkle 5.x.

- [ ] **Step 4: Run baseline tests (expected to fail)**

Run:
```bash
uv run pytest tests/test_block.py -x 2>&1 | head -50
```
Expected: at least one failure or import error coming from `src/cancelchain/block.py:12-13` or `block.py:156-174`. Capture the failure mode — it tells you which v4 API names changed.

- [ ] **Step 5: Read the pymerkle 5 README + source**

The v5 package is installed at `.venv/lib/python3.12/site-packages/pymerkle/`. Identify the v5 equivalents for:
  - `MerkleTree()` — likely still `MerkleTree(...)` but may take an `algorithm` kwarg.
  - `tree.append_entry(record)` — may be renamed to `tree.append(...)`, `tree.append_leaf(...)`, or `tree.update(...)`.
  - `tree.root` — may be a method now (`tree.get_root()`) or a property with different return type.
  - `tree.prove_inclusion(txid)` — may be `tree.prove(...)` or `tree.generate_proof(...)`.
  - `verify_inclusion(txid, target, proof)` — may be `proof.verify(...)` or a module-level `verify(...)`.
  - `pymerkle.proof.InvalidProof` — may have moved to `pymerkle.InvalidProof`.

Run:
```bash
uv run python -c "import pymerkle; print(dir(pymerkle))"
uv run python -c "from pymerkle import MerkleTree; print(dir(MerkleTree))"
```
Use the output to write the new call sites in Step 6.

- [ ] **Step 6: Update `src/cancelchain/block.py`**

Replace lines 12-13:
```python
from pymerkle import MerkleTree, verify_inclusion
from pymerkle.proof import InvalidProof
```
with the v5 imports identified in Step 5. Example (pending v5 verification):
```python
from pymerkle import MerkleTree
# v5 verification may be on the proof object:
#   proof.verify(target=...) returning bool / raising
```

Replace the `build_merkle_tree` / `get_merkle_root` / `in_merkle_tree` block (lines 155-174). Current:
```python
    def build_merkle_tree(self):
        tree = MerkleTree()
        for record in (t.txid for t in self.txns):
            tree.append_entry(record)
        return tree

    def get_merkle_root(self):
        root_hash = self.build_merkle_tree().root
        return root_hash.decode() if root_hash else None

    def in_merkle_tree(self, txid):
        tree = self.build_merkle_tree()
        target = tree.root
        proof = tree.prove_inclusion(txid)
        try:
            verify_inclusion(txid, target, proof)
        except InvalidProof:
            return False
        else:
            return True
```

Replace with v5 equivalents. The semantic contract:
  - `build_merkle_tree()` returns a tree containing every txid in `self.txns` as a leaf.
  - `get_merkle_root()` returns a string (current code calls `.decode()` on bytes) representing the root, or `None` if there are no transactions.
  - `in_merkle_tree(txid)` returns True if `txid` is a leaf and the proof of inclusion verifies.

Keep the same method signatures and return-type contract — these are called from elsewhere in `block.py` (`get_merkle_root` is referenced at construction and validation time; `in_merkle_tree` is used in coinbase verification).

**If pymerkle 5's API cannot satisfy the contract** (e.g. no inclusion-proof verification path, or root returned in an incompatible type), fall back to an inline Merkle tree. Inline implementation skeleton (sha256 of sha512, matching `milling.mill_hash`):
```python
from cancelchain.milling import mill_hash_str

def _merkle_root_inline(leaves):
    if not leaves:
        return None
    layer = [mill_hash_str(leaf) for leaf in leaves]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [
            mill_hash_str(layer[i] + layer[i + 1])
            for i in range(0, len(layer), 2)
        ]
    return layer[0]
```
…and a corresponding inclusion check (rebuild sibling path; verify against root). If you go inline, delete the pymerkle dependency from `pyproject.toml` and update PR-4's title/body to reflect "drop pymerkle, use inline Merkle implementation."

- [ ] **Step 7: Update any test fixtures with hard-coded Merkle roots**

Search:
```bash
grep -rn "merkle_root\|MerkleRoot" tests/ src/cancelchain/ | grep -i "=.*['\"]" | head
```
Any test that asserts a specific Merkle root string against a known transaction set must be regenerated. Per the spec, byte-for-byte hash compat is not a requirement (no production chain). Update assertions or regenerate fixtures as needed.

- [ ] **Step 8: Run the test suite**

Run:
```bash
uv run pytest tests/test_block.py -v
uv run pytest
```
Expected: block tests pass with the new API; full suite passes.

- [ ] **Step 9: Format and lint**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes. If `ruff format` rewrites your block.py edits, that's fine — run `uv run ruff format src tests` and re-run pytest.

- [ ] **Step 10: Commit**

Run:
```bash
git add pyproject.toml uv.lock src/cancelchain/block.py
# If you regenerated test fixtures:
# git add tests/...
git commit -m "$(cat <<'EOF'
feat(deps): upgrade to pymerkle 5

Drops the `pymerkle<5.0` cap. Ports `block.py` Merkle calls to the v5
API. No hash-output compat is preserved with v4 — there is no
production CancelChain to maintain backward compatibility with (the
<5 pin was defensive after a v4→v5 hash-incompat test four years ago).

Phase 2 / PR 4 of 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```
(If you went with the inline fallback, replace the commit message body accordingly: "Drops pymerkle dependency; uses an inline sha256-of-sha512 Merkle tree…")

- [ ] **Step 11: Push, open PR, wor, mwg**

Run:
```bash
git push -u origin feat/pymerkle-5
gh pr create --base main --title "feat(deps): upgrade to pymerkle 5" --body "$(cat <<'EOF'
## Summary
- Drop the `pymerkle<5.0` cap.
- Port `block.py` Merkle calls from v4 API (`append_entry`, `tree.root`, `prove_inclusion`, `verify_inclusion`, `InvalidProof`) to v5 equivalents.
- No hash-compat with v4 (no legacy chain to preserve).

Phase 2 / PR 4 of 7.

## Test plan
- [x] `uv run pytest tests/test_block.py` passes.
- [x] `uv run pytest` (full suite) passes.
- [x] `Block.get_merkle_root()` deterministic for a given txn set.
- [x] `Block.in_merkle_tree(txid)` correctly identifies membership.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Then `wor`, then `mwg`. Sync main.

---

## Task 6: PR-5 — Replace passlib with argon2-cffi

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (remove passlib, add argon2-cffi)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock`
- Modify: `/home/gumptionthomas/Development/cancelchain/src/cancelchain/models.py` (line 4 import; `ApiToken` class at lines 620-639)

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b refactor/passlib-to-argon2-cffi
```

- [ ] **Step 2: Edit `pyproject.toml` dependencies**

Remove this line from `[project] dependencies`:
```toml
  "passlib[argon2]>=1.7",
```

Add (alphabetically near the top of the list, after `base58check`):
```toml
  "argon2-cffi>=23.1",
```

- [ ] **Step 3: Resolve lockfile**

Run:
```bash
uv lock
uv sync --group dev
uv run python -c "import argon2; print(argon2.__version__)"
# Confirm passlib is gone:
uv run python -c "import passlib" 2>&1 | head -3
```
Expected: argon2-cffi 23.1+ resolves. `import passlib` raises `ModuleNotFoundError`.

- [ ] **Step 4: Edit `src/cancelchain/models.py` line 4**

Change:
```python
from passlib.hash import argon2, pbkdf2_sha256
```
to:
```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
```

- [ ] **Step 5: Add module-level `PasswordHasher` instance**

After the import section in `models.py`, before `def rollback_session()` at line 10, add:
```python
_PASSWORD_HASHER = PasswordHasher()
```

Place it at module scope so the cost-parameter defaults (`time_cost=3, memory_cost=65536, parallelism=4` in argon2-cffi 23.1) are paid once.

- [ ] **Step 6: Edit `ApiToken.refreshed_cipher` (lines 620-630)**

Current:
```python
    def refreshed_cipher(self):
        if self.expired or not (self.cipher and self.hashed):
            secret = str(uuid.uuid4())
            try:
                self.hashed = argon2.hash(secret)
            except Exception:
                self.hashed = pbkdf2_sha256.hash(secret)
            wallet = Wallet(b64ks=self.public_key)
            self.cipher = wallet.encrypt(secret.encode())
            self.commit()
        return self.cipher
```

Replace with:
```python
    def refreshed_cipher(self):
        if self.expired or not (self.cipher and self.hashed):
            secret = str(uuid.uuid4())
            self.hashed = _PASSWORD_HASHER.hash(secret)
            wallet = Wallet(b64ks=self.public_key)
            self.cipher = wallet.encrypt(secret.encode())
            self.commit()
        return self.cipher
```

(Drops the try/except fallback. argon2-cffi as a direct dep has no missing-backend mode.)

- [ ] **Step 7: Edit `ApiToken.verify` (lines 637-639)**

Current:
```python
    def verify(self, secret):
        hm = argon2 if argon2.identify(self.hashed) else pbkdf2_sha256
        return hm.verify(secret, self.hashed) and not self.expired
```

Replace with:
```python
    def verify(self, secret):
        if self.expired or not self.hashed:
            return False
        try:
            return _PASSWORD_HASHER.verify(self.hashed, secret)
        except (VerifyMismatchError, InvalidHashError):
            return False
```

(Drops the argon2/pbkdf2 discrimination. All hashes are argon2 going forward.)

- [ ] **Step 8: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: all tests pass. If any test pre-seeds an `ApiToken.hashed` value with a passlib-format hash, that fixture must be updated to use `_PASSWORD_HASHER.hash(...)` instead — but per the design's "no legacy chain" decision, that's acceptable.

Likely-affected tests live in `tests/test_api.py` and `tests/conftest.py`. Grep:
```bash
grep -rn "ApiToken\|hashed" tests/
```

- [ ] **Step 9: Format and lint**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes.

- [ ] **Step 10: Commit**

Run:
```bash
git add pyproject.toml uv.lock src/cancelchain/models.py
# If tests changed:
# git add tests/
git commit -m "$(cat <<'EOF'
refactor(auth): replace passlib with argon2-cffi

passlib is unmaintained since 2020. This PR removes it from
`[project.dependencies]` and swaps the only consumer (`ApiToken` in
`models.py`) to argon2-cffi's `PasswordHasher`.

Drops the pbkdf2_sha256 fallback path: its sole purpose was covering
passlib's missing-argon2-backend mode, which is irrelevant once
argon2-cffi is a direct dep. All API token hashes going forward are
argon2id.

No legacy `api_token` rows need to verify (per project decision: no
production chain to preserve).

Phase 2 / PR 5 of 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 11: Push, open PR, wor, mwg**

Run:
```bash
git push -u origin refactor/passlib-to-argon2-cffi
gh pr create --base main --title "refactor(auth): replace passlib with argon2-cffi" --body "$(cat <<'EOF'
## Summary
- Remove `passlib[argon2]>=1.7` from runtime deps (passlib has been unmaintained since 2020).
- Add `argon2-cffi>=23.1`.
- Swap `ApiToken.refreshed_cipher` / `ApiToken.verify` in `src/cancelchain/models.py` to use `argon2.PasswordHasher`.
- Drop pbkdf2_sha256 fallback (was only there because passlib's argon2 backend was optional).

Phase 2 / PR 5 of 7.

## Test plan
- [x] `uv run pytest` passes.
- [x] `import passlib` no longer succeeds in the dev venv.
- [x] `ApiToken.refreshed_cipher` / `ApiToken.verify` round-trip works.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Then `wor`, then `mwg`. Sync main.

---

## Task 7: PR-6 — Routine runtime-dependency bumps

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (multiple `[project] dependencies` lines)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock`

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b chore/deps-runtime-bumps
```

- [ ] **Step 2: Edit `pyproject.toml`**

In `[project] dependencies`, apply these floor bumps:

| Line currently | New line |
|---|---|
| `"base58check>=1.0",` | `"base58check>=1.0.2",` |
| `"celery>=5.3",` | `"celery>=5.4",` |
| `"gunicorn>=20.1",` | `"gunicorn>=23",` |
| `"millify>=0.1",` | `"millify>=0.1.1",` |
| `"pg8000>=1.29",` | `"pg8000>=1.31",` |
| `"pycryptodome>=3.18",` | `"pycryptodome>=3.20",` |
| `"pyjwt>=2.7",` | `"pyjwt>=2.9",` |
| `"requests>=2.31",` | `"requests>=2.32",` |
| `"rich>=13.4",` | `"rich>=13.7",` |
| `"blinker>=1.6",` | `"blinker>=1.8",` |
| `"click>=8.1",` | `"click>=8.1.7",` |

Leave `humanfriendly>=10.0`, `python-dotenv>=1.0`, and `marshmallow>=3.19` unchanged. Phase 3 deletes marshmallow.

- [ ] **Step 3: Resolve and install**

Run:
```bash
uv lock --upgrade
uv sync --group dev
```
`--upgrade` (without a package name) lets uv pick the latest compatible versions for every package, within the new floors. Expected: lockfile rewrites with newer versions.

- [ ] **Step 4: Print installed versions for the bumped packages**

Run:
```bash
uv run python -c "
import importlib.metadata as m
for name in ('base58check', 'celery', 'gunicorn', 'millify', 'pg8000', 'pycryptodome', 'pyjwt', 'requests', 'rich', 'blinker', 'click'):
    try:
        print(f'{name}: {m.version(name)}')
    except m.PackageNotFoundError:
        print(f'{name}: NOT INSTALLED')
"
```
Expected: each package at or above the new floor.

- [ ] **Step 5: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: all tests pass. If any of these bumps surfaces a runtime break, split that package's bump into its own PR (per the spec's risk-mitigation rule: PR-6 stays "no source code changes").

- [ ] **Step 6: Format and lint**

Run:
```bash
uv run ruff format --check src tests
```
Expected: passes.

- [ ] **Step 7: Commit**

Run:
```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): bump runtime dependency floors

Bumps the routine runtime deps to current stable lines:
- base58check 1.0 → 1.0.2
- blinker 1.6 → 1.8 (Flask 3 requires 1.7+)
- celery 5.3 → 5.4
- click 8.1 → 8.1.7 (Flask 3 requires 8.1.3+)
- gunicorn 20.1 → 23
- millify 0.1 → 0.1.1
- pg8000 1.29 → 1.31
- pycryptodome 3.18 → 3.20 (full swap to `cryptography` deferred to Phase 3)
- pyjwt 2.7 → 2.9
- requests 2.31 → 2.32 (full swap to httpx deferred to Phase 3)
- rich 13.4 → 13.7

humanfriendly, python-dotenv, and marshmallow are deliberately not touched.

Phase 2 / PR 6 of 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Push, open PR, wor, mwg**

Run:
```bash
git push -u origin chore/deps-runtime-bumps
gh pr create --base main --title "chore(deps): bump runtime dependency floors" --body "$(cat <<'EOF'
## Summary
Routine floor bumps for runtime deps that don't need source edits. See commit message for the full list.

humanfriendly, python-dotenv, marshmallow unchanged.

Phase 2 / PR 6 of 7.

## Test plan
- [x] `uv lock --upgrade` resolves cleanly.
- [x] `uv run pytest` passes.
- [x] Each bumped package reports the new floor or newer.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Then `wor`, then `mwg`. Sync main.

---

## Task 8: PR-7 — Dev-tooling bumps

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (`[dependency-groups].dev`)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock`
- Modify: `/home/gumptionthomas/Development/cancelchain/.pre-commit-config.yaml` (`pre-commit-hooks` rev)

- [ ] **Step 1: Branch off main**

Run:
```bash
git checkout main && git pull --ff-only
git checkout -b chore/deps-dev-tooling-bumps
```

- [ ] **Step 2: Edit `pyproject.toml` dev group**

In `[dependency-groups] dev`, apply:

| Line currently | New line |
|---|---|
| `"pytest>=8",` | `"pytest>=8.3",` |
| `"pytest-cov>=5",` | `"pytest-cov>=5.0",` |
| `"ruff>=0.6",` | `"ruff>=0.7",` |
| `"mypy>=1.10",` | `"mypy>=1.13",` |
| `"pre-commit>=3.7",` | `"pre-commit>=4.0",` |

Leave `pytest-dotenv>=0.5`, `requests-mock>=1.12`, `time-machine>=2.14`, and `coverage[toml]>=7.5` unchanged.

- [ ] **Step 3: Edit `.pre-commit-config.yaml`**

The current file uses `local` for the ruff hooks (they invoke `uv run ruff ...` directly, so the version is governed by the dev group) and pins `pre-commit-hooks` at `v4.6.0`.

Change `rev: v4.6.0` to `rev: v5.0.0` (verify this is the current release at PR time; if newer, use newer).

- [ ] **Step 4: Resolve and install**

Run:
```bash
uv lock --upgrade-package pytest --upgrade-package pytest-cov --upgrade-package ruff --upgrade-package mypy --upgrade-package pre-commit
uv sync --group dev
```
Expected: lockfile rewrites with newer dev-tool versions.

- [ ] **Step 5: Verify tool versions**

Run:
```bash
uv run pytest --version
uv run ruff --version
uv run mypy --version
uv run pre-commit --version
```
Expected: pytest ≥ 8.3, ruff ≥ 0.7, mypy ≥ 1.13, pre-commit ≥ 4.0.

- [ ] **Step 6: Re-install pre-commit hooks**

Run:
```bash
uv run pre-commit clean
uv run pre-commit install
uv run pre-commit run --all-files
```
Expected: hooks install cleanly. The `--all-files` run may surface new ruff lint findings under the newer ruff (CI absorbs these via `continue-on-error: true`); `ruff format`, `trailing-whitespace`, `end-of-file-fixer`, etc. should all pass.

If `pre-commit run --all-files` makes file modifications (e.g. ruff-format rewrites because the newer ruff format has a slightly different output), stage those modifications and continue. The plan's commit will include them.

- [ ] **Step 7: Run the test suite**

Run:
```bash
uv run pytest
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:
```bash
git add pyproject.toml uv.lock .pre-commit-config.yaml
# If pre-commit hooks modified files:
# git add src/ tests/
git commit -m "$(cat <<'EOF'
chore(deps): bump dev-tooling floors

Bumps:
- pytest 8 → 8.3
- pytest-cov 5 → 5.0
- ruff 0.6 → 0.7
- mypy 1.10 → 1.13
- pre-commit 3.7 → 4.0
- pre-commit-hooks v4.6.0 → v5.0.0

ruff's `continue-on-error: true` in CI absorbs any new lint findings.

Phase 2 / PR 7 of 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Push, open PR, wor, mwg**

Run:
```bash
git push -u origin chore/deps-dev-tooling-bumps
gh pr create --base main --title "chore(deps): bump dev-tooling floors" --body "$(cat <<'EOF'
## Summary
- pytest 8 → 8.3, pytest-cov 5 → 5.0
- ruff 0.6 → 0.7, mypy 1.10 → 1.13
- pre-commit 3.7 → 4.0, pre-commit-hooks v4.6.0 → v5.0.0

Final PR of Phase 2.

## Test plan
- [x] `uv sync --group dev` installs the new versions.
- [x] `uv run pre-commit run --all-files` clean (ruff check non-blocking by design).
- [x] `uv run pytest` passes.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Then `wor`, then `mwg`. Sync main.

---

## Task 9: Phase 2 acceptance verification

**Files:** none modified. This task is a final verification pass on `main` after all seven impl PRs have landed.

- [ ] **Step 1: Confirm clean main**

Run:
```bash
git checkout main && git pull --ff-only
git log --oneline -10
```
Expected: seven Phase 2 squash-merge commits visible.

- [ ] **Step 2: Fresh-clone simulation**

Run:
```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```
Expected: Python 3.12.x.

- [ ] **Step 3: Run the full test suite on 3.12**

Run:
```bash
uv sync --group dev --python 3.12
uv run pytest
```
Expected: all tests pass.

- [ ] **Step 4: Run the full test suite on 3.13**

Run:
```bash
uv python install 3.13
uv sync --group dev --python 3.13
uv run pytest
```
Expected: all tests pass.

- [ ] **Step 5: Verify acceptance criteria from the spec**

Run:
```bash
grep -E 'sqlalchemy<2\.0|pymerkle.*<5\.0|passlib' pyproject.toml || echo "✓ no forbidden pins"
grep passlib uv.lock || echo "✓ no passlib in lockfile"
grep "python-version: " .github/workflows/tests.yml
grep "target-version\|python_version" pyproject.toml
grep "ARG PYTHON_VERSION" Dockerfile
```
Expected:
- `pyproject.toml`: no `sqlalchemy<2.0`, no `pymerkle<5`, no `passlib`.
- `uv.lock`: no `passlib` reference.
- CI matrix: `['3.12', '3.13']`.
- ruff `target-version = "py312"`, mypy `python_version = "3.12"`.
- Dockerfile `ARG PYTHON_VERSION=3.12`.

- [ ] **Step 6: Smoke-test the CLI**

Run:
```bash
uv run cancelchain --help
```
Expected: full subcommand tree prints (txn, wallet, subject, mill, sync, validate, export, import, init, run).

- [ ] **Step 7: Docker build smoke**

Run:
```bash
docker build -t cc-phase2-final .
```
Expected: full multi-stage build succeeds. (Optional: `docker run --rm cc-phase2-final cancelchain --help` to confirm runtime is sane.)

- [ ] **Step 8: Acceptance complete**

If all of Steps 1-7 pass, Phase 2 is done. No commit. Phase 3 is the next milestone.

If any step fails, the failure is a defect in one of the merged PRs. Open a follow-up `fix(deps): …` PR addressing the specific issue; do **not** revert the Phase 2 PRs unless the issue is unrecoverable.

---

## Notes on the wor / mwg workflow

Each impl PR (Tasks 2–8) ends with `wor` + `mwg`:

1. **`wor` (Wait On Review):** Poll the PR until Copilot review completes. Use GraphQL `reviewThreads` with `isResolved:false` to find unresolved threads. Respond to each in the original comment thread, verifying `in_reply_to_id` on each reply. The user manually resolves threads — do not auto-resolve.

2. **`mwg` (Merge When Green):** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

Never skip `wor`, even when CI is green and your local test pass looks clean. Copilot catches what you miss.

If Copilot review requests a substantive change, push a new commit to the PR branch (do not amend) and re-run `wor`.

---

## Notes on Dependabot interaction

Dependabot is configured (`.github/dependabot.yml`) to open dep-bump PRs Monday cadence with a 3-day cooldown. While the Phase 2 train is in flight:

- Close or "hold" any Dependabot PR that touches `[project.dependencies]` or `[dependency-groups].dev` until Phase 2 finishes. Add a label or comment so reviewers know why it's held.
- Dependabot PRs targeting GitHub Actions (`.github/workflows/`) and Docker base images can land normally — they don't conflict with the Phase 2 train.
- After Task 9 acceptance passes, reopen / unhold Dependabot's deferred PRs and process them as normal.

---

## Roll-back posture

Every PR in this train is independently revertible via `git revert <merge-sha>` (or GitHub's "Revert" button on the merged PR page) because they're squash-merged. If a defect is discovered after merge:

- For runtime regressions found *after* later PRs have already landed on top: prefer a forward-fix in a new PR over a revert, since reverting an earlier PR may conflict with later PRs' lockfile changes.
- For lockfile-only regressions: a targeted `uv lock --upgrade-package <name>` in a new PR usually resolves.
