# Phase 1 — Tooling Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize the cancelchain project's developer tooling per `docs/superpowers/specs/2026-05-22-phase-1-tooling-modernization-design.md`. Replace pip + hatch with uv + uv_build; bump ruff and adopt `ruff format`; introduce mypy (non-blocking) and pre-commit; overhaul GitHub Actions and Dockerfile. Runtime dependency versions stay unchanged.

**Architecture:** Linear sequence of 10 tasks, each ending in a single commit. The big bootstrap (Task 2) does the irreducible pyproject.toml rewrite + lockfile generation in one go; everything after is incremental and reversible. Acceptance criteria from the spec are verified in Task 10.

**Tech Stack:** uv (package manager + build frontend), uv_build (PEP 517 build backend), ruff (linter + formatter), mypy (type checker), pre-commit, GitHub Actions, Docker.

---

## Prerequisites

- You are on branch `modernize/phase-1-tooling-design` (created earlier; contains the design doc). All work below adds commits to this branch.
- `uv` is installed locally. Verify: `uv --version` prints `uv 0.5.x` or newer. Install instructions: <https://docs.astral.sh/uv/>.
- Docker is installed and the daemon is running (only needed for Task 8 verification).
- You have the repo root at `/home/gumptionthomas/Development/cancelchain` as the working directory unless stated otherwise.

---

## Task 1: Pre-flight — verify baseline

**Files:** none modified. This task only runs commands to confirm the existing setup works before we change it.

- [ ] **Step 1: Verify the current branch**

Run:
```bash
git status
git log --oneline -5
```
Expected: branch is `modernize/phase-1-tooling-design`; most recent commit is `Revise Phase 1 design: full-uv build backend`.

- [ ] **Step 2: Install the project into a baseline venv using uv**

We use `uv venv` rather than `python -m venv` because the latter requires the `python3-venv` apt package on Debian/Ubuntu systems, which is not always installed. `uv venv` ships its own Python and has no such dependency. Inside the venv, dependencies are still installed against the *existing* hatchling-based `pyproject.toml` and `requirements-dev.txt` — this verifies the legacy setup before we replace it.

Run:
```bash
uv venv --python 3.10 .venv-baseline
uv pip install --python .venv-baseline/bin/python -e .
uv pip install --python .venv-baseline/bin/python -r requirements-dev.txt
```
Expected: uv downloads CPython 3.10 (or uses a locally cached copy), creates `.venv-baseline/`, then installs the project editably along with all dev deps. No errors.

- [ ] **Step 3: Run the existing test suite**

Run:
```bash
.venv-baseline/bin/pytest
```
Expected: all tests pass (or you note exactly which tests fail; if any fail in the baseline, capture the names — they are the regression bar, not new failures we need to fix).

- [ ] **Step 4: Capture baseline test count**

Run:
```bash
.venv-baseline/bin/pytest --collect-only -q | tail -5
```
Expected: prints a total like `N tests collected`. Record `N` — every later task must collect the same number.

- [ ] **Step 5: Clean up the baseline venv**

Run:
```bash
rm -rf .venv-baseline
```
Expected: no output, directory gone. This venv was only used to confirm "main works"; we will use uv-managed envs from here on.

- [ ] **Step 6: No commit**

This task changes no files. Move to Task 2.

---

## Task 2: Bootstrap uv + uv_build + dependency groups

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (complete rewrite of build-system, project metadata, and tool sections)
- Modify: `/home/gumptionthomas/Development/cancelchain/src/cancelchain/__init__.py` (version shim)
- Delete: `/home/gumptionthomas/Development/cancelchain/requirements.txt`
- Delete: `/home/gumptionthomas/Development/cancelchain/requirements-dev.txt`
- Create: `/home/gumptionthomas/Development/cancelchain/uv.lock` (generated)
- Create: `/home/gumptionthomas/Development/cancelchain/.python-version` (sets local default Python)

This is the irreducible "switch the foundation" task. After it lands, `uv sync && uv run pytest` is the workflow.

- [ ] **Step 1: Rewrite `pyproject.toml`**

Replace the entire contents of `pyproject.toml` with:

```toml
[build-system]
requires = ["uv_build>=0.5,<1.0"]
build-backend = "uv_build"

# PROJECT
[project]
name = "cancelchain"
version = "1.4.1"
description = "A Blockchain of Accountability, Forgiveness, and Support"
readme = "README.rst"
license = "MIT"
requires-python = ">=3.9"
authors = [
  { name = "Thomas Bohmbach Jr", email = "tom@cancelchain.org" }
]
keywords = [
  "blockchain",
  "flask",
]
classifiers = [
  "Development Status :: 5 - Production/Stable",
  "Framework :: Flask",
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3.9",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Topic :: Sociology",
  "Operating System :: OS Independent",
]
dependencies = [
  "base58check>=1.0",
  "blinker>=1.6",
  "celery>=5.3",
  "click>=8.1",
  "Flask>=2.3",
  "Flask-Caching>=2.0",
  "Flask-SQLAlchemy>=3.0",
  "gunicorn>=20.1",
  "humanfriendly>=10.0",
  "marshmallow>=3.19",
  "millify>=0.1",
  "passlib[argon2]>=1.7",
  "pg8000>=1.29",
  "pycryptodome>=3.18",
  "pyjwt>=2.7",
  "pymerkle>=4.0,<5.0",
  "python-dotenv>=1.0",
  "requests>=2.31",
  "rich>=13.4",
  "sqlalchemy<2.0",
]

[project.scripts]
cancelchain = "cancelchain:cli"

[project.urls]
Homepage = "https://cancelchain.org"
Documentation = "https://docs.cancelchain.org"
Source = "https://github.com/cancelchain/cancelchain"
Tracker = "https://github.com/cancelchain/cancelchain/issues"

# DEPENDENCY GROUPS (PEP 735)
[dependency-groups]
dev = [
  "pytest>=7.4",
  "pytest-cov>=4.1",
  "pytest-dotenv>=0.5",
  "requests-mock>=1.11",
  "time-machine>=2.10",
  "coverage[toml]>=7.2",
  "ruff>=0.0.275",
]

# RUFF
[tool.ruff]
target-version = "py39"
line-length = 80
select = [
  "A",
  "B",
  "C",
  "DTZ",
  "E",
  "EM",
  "F",
  "FBT",
  "I",
  "ICN",
  "ISC",
  "N",
  "PLC",
  "PLE",
  "PLR",
  "PLW",
  "Q",
  "RUF",
  "S",
  "SIM",
  "T",
  "TID",
  "UP",
  "W",
  "YTT",
]
ignore = [
  "A002",
  "A003",
  "B904",
  "C901",
  "DTZ007",
  "FBT002",
  "PLR0912",
  "PLR0913",
  "PLR0915",
  "PLR2004",
  "Q000",
  "S101",
  "S105",
  "S110",
]

# PYTEST
[tool.pytest.ini_options]
testpaths = [
  "tests",
]
filterwarnings = [
  "ignore:.*SelectableGroups dict interface is deprecated.*"
]
env_files = [
  "tests/.test.env",
]
env_override_existing_values = 1

# COVERAGE
[tool.coverage.html]
directory = "coverage_html_report"

[tool.coverage.report]
exclude_lines = [
  "no cov",
  "if __name__ == .__main__.:",
]
```

Notes on what changed vs the current file:
- `[build-system]`: `hatchling` → `uv_build`.
- `[project]`: `dynamic = ["version"]` removed; `version = "1.4.1"` added.
- All `[tool.hatch.*]` blocks deleted (build config + envs + scripts).
- New `[dependency-groups]` block holds what `requirements-dev.txt` and `[tool.hatch.envs.test].dependencies` used to hold. Dev dep *versions* are preserved at their current floors in this task — we bump them in Task 3.
- The ruff config layout (top-level `select`/`ignore`) is preserved for now. Task 3 migrates it to the `[tool.ruff.lint]` namespace required by current ruff.
- `[tool.pytest.ini_options]` and `[tool.coverage.*]` are unchanged from the original.

- [ ] **Step 2: Update `src/cancelchain/__init__.py` for `importlib.metadata`-based version**

Open `/home/gumptionthomas/Development/cancelchain/src/cancelchain/__init__.py`. Replace the line:

```python
__version__ = "1.4.1"
```

with:

```python
from importlib.metadata import version as _pkg_version

__version__ = _pkg_version("cancelchain")
```

Leave everything else in `__init__.py` untouched.

- [ ] **Step 3: Delete the old requirements files**

Run:
```bash
git rm requirements.txt requirements-dev.txt
```
Expected: both files removed and staged for deletion.

- [ ] **Step 4: Create `.python-version`**

Write a file at `/home/gumptionthomas/Development/cancelchain/.python-version` containing exactly:

```
3.10
```

(One line, no trailing whitespace beyond a newline.) This tells uv which Python to use locally; CI will override per-matrix-entry. We pick 3.10 because that is the Dockerfile's current runtime version; Phase 2 will bump it.

- [ ] **Step 5: Generate the lockfile**

Run:
```bash
uv lock
```
Expected: `uv.lock` created at the repo root. The command resolves every dep in `[project.dependencies]` and `[dependency-groups].dev` against the constraints, then writes a complete lockfile.

If `uv lock` fails because a transitive dep has no wheel for Python 3.9: re-run with `uv lock --python 3.10`. If it still fails, surface the error — do **not** edit `[project.dependencies]` constraints in this task.

- [ ] **Step 6: Sync the dev environment**

Run:
```bash
uv sync --group dev
```
Expected: creates `.venv/` at the repo root, installs the project editably along with all dev deps. Final line should be `Installed N packages in <time>`.

- [ ] **Step 7: Verify the test suite still passes**

Run:
```bash
uv run pytest
```
Expected: all tests pass; the collected test count matches the baseline number recorded in Task 1 Step 4.

- [ ] **Step 8: Verify the `cancelchain` CLI still works**

Run:
```bash
uv run cancelchain --version
```
Expected: prints `cancelchain, version 1.4.1` (the value comes from `importlib.metadata.version("cancelchain")`, which reads it from the installed wheel metadata, which reads it from the static `version = "1.4.1"` in `pyproject.toml`). This is the critical end-to-end check that the version chain works.

- [ ] **Step 9: Verify a wheel builds and installs cleanly**

Run:
```bash
uv build
ls dist/
```
Expected: `dist/cancelchain-1.4.1-py3-none-any.whl` and `dist/cancelchain-1.4.1.tar.gz` exist.

Then verify the wheel is installable into a throwaway venv and reports its version:
```bash
uv venv /tmp/cc-wheel-check
/tmp/cc-wheel-check/bin/pip install dist/cancelchain-1.4.1-py3-none-any.whl
/tmp/cc-wheel-check/bin/python -c "import cancelchain; print(cancelchain.__version__)"
rm -rf /tmp/cc-wheel-check dist
```
Expected: prints `1.4.1`. Then the dist directory is cleaned up.

- [ ] **Step 10: Commit**

Run:
```bash
git add pyproject.toml src/cancelchain/__init__.py uv.lock .python-version
git status
```

Confirm `git status` shows the staged additions plus the staged deletions of `requirements.txt` and `requirements-dev.txt` from Step 3, and nothing else. Then:

```bash
git commit -m "Switch build/dep tooling to uv + uv_build

Drops hatchling and pip/requirements files in favor of uv as the
package manager and uv_build as the PEP 517 build backend. Moves dev
deps into [dependency-groups]. The package version is now static in
pyproject.toml and exposed at runtime via importlib.metadata, which
keeps cancelchain.__version__ working unchanged for callers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Bump dev deps and migrate ruff to current config namespace

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (dev group versions + `[tool.ruff.lint]` namespace + `[tool.ruff.format]` block)
- Modify: `/home/gumptionthomas/Development/cancelchain/uv.lock` (regenerated)

- [ ] **Step 1: Update the dev dependency group**

In `pyproject.toml`, replace the `[dependency-groups]` block with:

```toml
[dependency-groups]
dev = [
  "pytest>=8",
  "pytest-cov>=5",
  "pytest-dotenv>=0.5",
  "requests-mock>=1.12",
  "time-machine>=2.14",
  "coverage[toml]>=7.5",
  "ruff>=0.6",
  "mypy>=1.10",
  "pre-commit>=3.7",
]
```

Changes from Task 2's version: pytest 7.4→8, pytest-cov 4.1→5, requests-mock 1.11→1.12, time-machine 2.10→2.14, coverage 7.2→7.5, ruff 0.0.275→0.6, plus new `mypy` and `pre-commit` entries.

- [ ] **Step 2: Migrate the ruff config to the new namespace**

In `pyproject.toml`, replace the entire `# RUFF` block with:

```toml
# RUFF
[tool.ruff]
target-version = "py39"
line-length = 80

[tool.ruff.lint]
select = [
  "A",
  "B",
  "C",
  "DTZ",
  "E",
  "EM",
  "F",
  "FBT",
  "I",
  "ICN",
  "ISC",
  "N",
  "PLC",
  "PLE",
  "PLR",
  "PLW",
  "Q",
  "RUF",
  "S",
  "SIM",
  "T",
  "TID",
  "UP",
  "W",
  "YTT",
]
ignore = [
  "A002",
  "A003",
  "B904",
  "C901",
  "DTZ007",
  "FBT002",
  "PLR0912",
  "PLR0913",
  "PLR0915",
  "PLR2004",
  "Q000",
  "S101",
  "S105",
  "S110",
]

[tool.ruff.format]
quote-style = "single"
```

The `select` and `ignore` arrays moved from `[tool.ruff]` to `[tool.ruff.lint]`. The new `[tool.ruff.format]` block preserves the project's single-quote convention through the formatter.

- [ ] **Step 3: Re-lock**

Run:
```bash
uv lock
```
Expected: `uv.lock` regenerated with the new dev dep pins resolved. uv prints which packages changed.

- [ ] **Step 4: Re-sync**

Run:
```bash
uv sync --group dev
```
Expected: installs the bumped versions; uninstalls anything that became unused.

- [ ] **Step 5: Verify ruff runs on the new config**

Run:
```bash
uv run ruff check src tests
```
Expected: ruff completes. It may report errors that the old version did not catch (the rule set is larger across ruff 0.0.275 → 0.6). **Do not fix those errors in this task** — they are the existing code's problem, not the tooling's. Just confirm ruff *runs* and exits without a config-parse error.

If ruff exits 0, great. If it exits non-zero with lint findings (not config errors), capture the output for later:
```bash
uv run ruff check src tests > /tmp/ruff-baseline.txt 2>&1 || true
```

- [ ] **Step 6: Verify the test suite still passes**

Run:
```bash
uv run pytest
```
Expected: same collected count and pass rate as Task 1 baseline.

- [ ] **Step 7: Commit**

Run:
```bash
git add pyproject.toml uv.lock
git commit -m "Bump dev tooling and migrate ruff config to new namespace

Bumps pytest, ruff, coverage, requests-mock, time-machine to current
floors and adds mypy + pre-commit to the dev group. Migrates the ruff
config from the legacy top-level select/ignore to the [tool.ruff.lint]
namespace required by ruff >=0.6. Adds [tool.ruff.format] with
single-quote style to preserve the project convention.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Apply ruff format as a one-time pass and pin the commit for blame

**Files:**
- Modify: every `.py` file under `src/cancelchain/` and `tests/` that `ruff format` decides to touch.
- Create: `/home/gumptionthomas/Development/cancelchain/.git-blame-ignore-revs`

This task produces two commits: one for the formatter pass, one for `.git-blame-ignore-revs` pinning that pass.

- [ ] **Step 1: Confirm the format diff is non-empty (sanity check)**

Run:
```bash
uv run ruff format --check src tests
```
Expected: ruff exits non-zero and lists files that would change.

If ruff exits 0 — meaning the codebase is already formatted to current ruff defaults — there is no format commit to make. Skip Steps 2 through 5 entirely; jump to Step 6 and create `.git-blame-ignore-revs` with only the header comment (no SHA line), then commit it as documented in Step 7 with a message reflecting that no format pass was needed.

- [ ] **Step 2: Apply the formatter**

Run:
```bash
uv run ruff format src tests
```
Expected: ruff reformats files and prints `N files reformatted, M files left unchanged`.

- [ ] **Step 3: Verify the test suite still passes after formatting**

Run:
```bash
uv run pytest
```
Expected: same collected count and pass rate as Task 1 baseline. (Formatting changes whitespace and quote style but never behavior; if a test fails here, the formatter touched something it shouldn't have — investigate before committing.)

- [ ] **Step 4: Commit the format pass on its own**

Run:
```bash
git add -A src tests
git commit -m "Apply ruff format as a one-time pass

Single noisy commit; quote style and whitespace only. Pinned in
.git-blame-ignore-revs in a follow-up commit so git blame skips
this revision.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Capture the SHA of the format commit**

Run:
```bash
FORMAT_SHA=$(git rev-parse HEAD)
echo "$FORMAT_SHA"
```
Expected: prints a 40-character hex SHA. You will use it in Step 6.

- [ ] **Step 6: Create `.git-blame-ignore-revs`**

Run (uses the `$FORMAT_SHA` variable captured in Step 5):

```bash
cat > .git-blame-ignore-revs <<EOF
# Commits listed here are skipped by \`git blame\` with --ignore-revs-file.
# Configure once locally with:
#   git config blame.ignoreRevsFile .git-blame-ignore-revs

# One-time ruff format pass (Phase 1 tooling modernization)
$FORMAT_SHA
EOF
cat .git-blame-ignore-revs
```

Expected: file written; final `cat` prints the contents with the SHA on its own line at the bottom.

- [ ] **Step 7: Commit `.git-blame-ignore-revs`**

Run:
```bash
git add .git-blame-ignore-revs
git commit -m "Add .git-blame-ignore-revs pinning the ruff format pass

Local developers can opt in with:
  git config blame.ignoreRevsFile .git-blame-ignore-revs
GitHub honors this file automatically in its web blame view.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Add mypy configuration (non-blocking)

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/pyproject.toml` (add `[tool.mypy]`)

- [ ] **Step 1: Add the `[tool.mypy]` block**

Append the following block to `pyproject.toml` (after the `[tool.coverage.report]` block):

```toml
# MYPY
[tool.mypy]
python_version = "3.9"
warn_unused_ignores = true
warn_redundant_casts = true
strict_optional = true
files = ["src/cancelchain"]
```

- [ ] **Step 2: Run mypy to see the baseline**

Run:
```bash
uv run mypy src
```
Expected: mypy runs and almost certainly reports errors (untyped functions, missing stubs for some libraries, etc.). **That is expected and acceptable.** This task only establishes the runner; we do not fix type errors here.

Capture the count for visibility:
```bash
uv run mypy src 2>&1 | tail -5
```
Record the "Found N errors in M files" line in your scratchpad. It is the baseline that Phase 3 will reduce.

- [ ] **Step 3: Verify the test suite still passes**

Run:
```bash
uv run pytest
```
Expected: same collected count and pass rate as Task 1 baseline. (Mypy config does not affect test execution; this is a sanity check.)

- [ ] **Step 4: Commit**

Run:
```bash
git add pyproject.toml
git commit -m "Introduce mypy with a permissive baseline config

Configures mypy to analyze src/cancelchain only with strict_optional,
warn_unused_ignores, and warn_redundant_casts. Errors are expected on
the existing code; the runner is non-blocking in CI (see Task 7).
Tightening is deferred to Phase 3 after library upgrades land.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Add pre-commit configuration

**Files:**
- Create: `/home/gumptionthomas/Development/cancelchain/.pre-commit-config.yaml`

- [ ] **Step 1: Create `.pre-commit-config.yaml`**

Write a file at `/home/gumptionthomas/Development/cancelchain/.pre-commit-config.yaml` containing exactly:

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types_or: [python, pyi]
        require_serial: true
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types_or: [python, pyi]
        require_serial: true
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
```

Notes:
- The ruff hooks use **local** entries that shell out to `uv run ruff …`. This guarantees pre-commit and the project use the **same** ruff binary (the one resolved by `uv sync --group dev`), eliminating version drift between pre-commit's pinned ruff and the project's installed ruff.
- mypy intentionally omitted — too slow per-commit. CI handles it (Task 7).
- The `pre-commit-hooks` `rev` is a known-good pin; `pre-commit autoupdate` can bump it later.

- [ ] **Step 2: Install the git hook locally**

Run:
```bash
uv run pre-commit install
```
Expected: prints `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 3: Run pre-commit on all files**

Run:
```bash
uv run pre-commit run --all-files
```

**Expected outcome (this is a nuanced step — read carefully before reacting):**

- The `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, and `check-merge-conflict` hooks should `Passed` (or briefly `Failed` once and auto-fix small newline/whitespace issues; re-stage and re-run those).
- The `ruff-format` hook will report `Failed` *the first time* if `app.py` is reformatted. Task 4 scoped its format pass to `src/ tests/` and missed `app.py` at the repo root. Pre-commit's broader file selection catches it: the project's ruff will collapse a multi-line `app.run(...)` call onto a single line. Stage the resulting `app.py` and continue — this small fix legitimately belongs in Task 6's commit, since Task 6 is what surfaced it.
- The `ruff` (check) hook will report `Failed` due to ~47 **pre-existing** lint errors in the codebase (S104 in `app.py`, F811 in `command.py`, PLR1704 in `node.py`, RUF015 in tests, etc.). With `--fix` set, ruff will **auto-fix the safe ones** in place. Expect a handful of files under `tests/` to be modified:
  - `tests/test_config.py` gets a SIM300 yoda-condition rewrite (`[list] == var` → `var == [list]`).
  - Eight `tests/*.py` files get an I001 isort fix (a blank line added between `import pytest` and `from cancelchain.*` because `cancelchain` is first-party in this project's import graph).
  These auto-fixes are safe and idiomatic. They are NOT a sign of version drift — they are real lint debt that ruff is now cleaning up. Stage them as part of Task 6's commit. The remaining unsafe errors (S104, F811, PLR1704, the remaining RUF015s, etc.) stay until Phase 3.

So the acceptance criterion for Step 3 is:
- The filesystem-hygiene hooks (`trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-merge-conflict`) ultimately report `Passed`.
- `ruff` (check) is **allowed to remain `Failed`** after auto-fixing what it can — the unfixed errors are the pre-existing debt that Phase 3 will clean up. The hook is still useful in normal use because pre-commit defaults to staged-only on a regular `git commit`.
- pytest still reports 163 collected, 162 passed, 1 skipped after the auto-fixes (verifying nothing semantic was broken).

If `ruff-format` modifies any file OTHER than `app.py` (i.e., a file that Task 4 already formatted), STOP and report BLOCKED — that would indicate a version drift between pre-commit's ruff and the project's ruff that the local-hooks config should have prevented. Note: changes from `ruff check --fix` to `tests/*.py` are NOT the same as `ruff format` changes — the former are lint auto-fixes (expected, see above), the latter would be a real bug.

- [ ] **Step 4: Verify the test suite still passes**

Run:
```bash
uv run pytest
```
Expected: 163 collected, 162 passed, 1 skipped.

- [ ] **Step 5: Commit**

Run:
```bash
git status
```

Expected staged changes:
- `.pre-commit-config.yaml` (new)
- `app.py` (reformatted by ruff-format — Task 4 missed this)
- `tests/test_config.py` (SIM300 yoda fix from `ruff check --fix`)
- Eight other `tests/*.py` files with the I001 blank-line isort fix
- Possibly minor whitespace/EOF fixes on a small number of non-Python files (`.test.env`, `.gitignore`, etc.) if `trailing-whitespace` or `end-of-file-fixer` corrected them

If any `.py` file under `src/cancelchain/` is modified (note: `src/`, not `tests/`), STOP and report BLOCKED — Task 4 already formatted source files and ruff format should not re-touch them, and Task 6 should not unilaterally modify source code outside of what `ruff check --fix` decides on test files.

Then (note `--no-verify`: the new pre-commit hooks would re-trigger on this commit and surface the pre-existing-debt failures, blocking it; we already verified the hook configuration above, so skipping the per-commit hook for this one commit is appropriate):
```bash
git add .pre-commit-config.yaml app.py tests/
git add -A  # also pick up any whitespace/EOF fixes from filesystem hooks
git commit --no-verify -m "Add pre-commit hooks and pick up baseline ruff --fix cleanups

Adds .pre-commit-config.yaml with LOCAL ruff hooks (the entry shells
out to \`uv run ruff ...\` so pre-commit and the project always use the
same ruff binary). Plus filesystem-hygiene hooks. mypy is omitted —
too slow per-commit; CI runs it instead.

Folded into this commit are the safe auto-fixes ruff produced when
first exercised on the whole repo:
  * app.py — \`ruff format\` collapsed a 3-line app.run(...) call onto
    one line. Task 4 scoped its format pass to src/ tests/ and missed
    app.py at the repo root.
  * tests/test_config.py — \`ruff check --fix\` rewrote a SIM300 yoda
    condition (\`[list] == var\` → \`var == [list]\`).
  * tests/conftest.py + 7 other tests/*.py files — \`ruff check --fix\`
    (I001) added a blank line between \`import pytest\` and
    \`from cancelchain.*\` imports (first-party grouping).

Test suite still reports 163/162/1. The remaining ~47 pre-existing
unsafe lint errors are left for Phase 3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Rewrite the GitHub Actions workflow

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/.github/workflows/tests.yml`

- [ ] **Step 1: Replace `.github/workflows/tests.yml`**

Overwrite the file with exactly:

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ['3.10', '3.11', '3.12', '3.13']
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install ${{ matrix.python-version }}
      - run: uv sync --group dev --python ${{ matrix.python-version }}
      # ruff check is non-blocking in Phase 1 because the codebase carries
      # ~37 pre-existing lint errors (older ruff did not catch them). Phase 3
      # cleans them up alongside the type-hint campaign; at that point this
      # `continue-on-error` is removed and ruff check becomes a hard gate.
      - run: uv run ruff check src tests
        continue-on-error: true
      - run: uv run ruff format --check src tests
      - run: uv run pytest
      # mypy is non-blocking in Phase 1; Task 5 introduced the runner but the
      # codebase isn't typed yet. Phase 3 tightens this to a hard gate.
      - run: uv run mypy src
        continue-on-error: true
```

Notes on the choices:
- `fail-fast: false` lets all matrix entries finish so you can see which Python versions fail and which pass.
- `pull_request` trigger added; same-repo branch pushes get tested twice but PR-from-fork tests get coverage they wouldn't have from `push` alone.
- Python 3.9 dropped (post-EOL). 3.13 included with the caveat in Step 2.
- `continue-on-error: true` on **both** ruff check and mypy. Phase 1 introduces the runners without fixing the pre-existing lint and type debt; Phase 3 cleans both up and removes the `continue-on-error` lines.
- `ruff format --check` and `pytest` stay as hard gates — Phase 1 already meets their bar (Task 4 normalized formatting; Task 1 captured the test baseline).

- [ ] **Step 2: Confirm pymerkle 4 wheels exist for Python 3.13**

Run:
```bash
uv pip compile --python 3.13 - <<'EOF' 2>&1 | grep -E '^(pymerkle|cryptography|sqlalchemy)' || true
pymerkle>=4.0,<5.0
sqlalchemy<2.0
EOF
```
Expected: prints the resolved version of `pymerkle` (e.g. `pymerkle==4.x.y`). If the command fails with "no compatible wheel" for any package, edit `.github/workflows/tests.yml` and remove `'3.13'` from the matrix list. Note the exclusion in the commit message and re-add 3.13 in Phase 2 when library versions are bumped.

- [ ] **Step 3: Lint the workflow YAML locally**

Run:
```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"
```
Expected: no output (valid YAML). If `yaml` isn't importable in the dev env, install `pyyaml` ad-hoc with `uv pip install pyyaml` for this check, or skip — pre-commit's `check-yaml` hook will catch any structural error on the next commit.

- [ ] **Step 4: Commit**

Run:
```bash
git add .github/workflows/tests.yml
git commit -m "Rewrite CI workflow on uv with expanded Python matrix

Replaces the hatch + pip install path with astral-sh/setup-uv@v3 and
uv-driven sync/run steps. Matrix is now 3.10, 3.11, 3.12, 3.13 (3.9
dropped, post-EOL). Adds ruff format --check and mypy steps; mypy is
non-blocking via continue-on-error. fail-fast disabled so each Python
version is reported independently.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

(If you removed 3.13 in Step 2, replace the matrix line in the commit message accordingly and add: `3.13 deferred to Phase 2 pending pymerkle 4 wheel availability.`)

---

## Task 8: Rewrite the Dockerfile

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/Dockerfile`

- [ ] **Step 1: Replace `Dockerfile`**

Overwrite the file with exactly:

```dockerfile
# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.10
FROM ghcr.io/astral-sh/uv:0.4-python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock README.rst ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app app.py ./

USER app
CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
```

Notes:
- `PYTHON_VERSION=3.10` matches the previous Dockerfile's runtime; Phase 2 will bump it.
- Multi-stage: build with the uv-bundled image, run with bare `python:slim` for a small final image.
- `uv sync --frozen --no-dev` installs from `uv.lock` exactly (no resolver run), excluding dev deps.
- Non-root `app` user.
- `:8080` default port; orchestrator can override `CMD` if it needs `$PORT`.

- [ ] **Step 2: Build the image**

Run:
```bash
docker build -t cancelchain:phase-1 .
```
Expected: build succeeds, ending with a `naming to docker.io/library/cancelchain:phase-1 done` line. If it fails, the most likely cause is a hash mismatch in `uv.lock`; re-run `uv lock` and retry.

- [ ] **Step 3: Verify the CLI works inside the image**

Run:
```bash
docker run --rm cancelchain:phase-1 cancelchain --version
```
Expected: prints `cancelchain, version 1.4.1`. This confirms `importlib.metadata.version("cancelchain")` reads the wheel metadata correctly inside the container.

- [ ] **Step 4: Verify gunicorn can start**

Run (in one terminal):
```bash
docker run --rm -p 8080:8080 --name cc-phase-1-smoke cancelchain:phase-1
```
Expected: gunicorn boots and binds `:8080`. You should see `Listening at: http://0.0.0.0:8080` in its output.

In another terminal:
```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
```
Expected: prints `500` (no DB initialized) or `200`. Anything other than a connection refused indicates the server is up. A 500 here is fine — the chain is empty, the browser view raises, the server still serves.

Then stop the container:
```bash
docker stop cc-phase-1-smoke
```

- [ ] **Step 5: Clean up the local image**

Run:
```bash
docker image rm cancelchain:phase-1
```
Expected: image removed. (Not strictly necessary, but keeps your local Docker tidy.)

- [ ] **Step 6: Commit**

Run:
```bash
git add Dockerfile
git commit -m "Rewrite Dockerfile as multi-stage uv-based build

Build stage uses ghcr.io/astral-sh/uv to install deps from uv.lock
with --frozen --no-dev. Runtime stage uses python:slim and copies the
populated venv. Adds a non-root app user. Default CMD binds gunicorn
to :8080 in JSON form; orchestrators that need \$PORT can override.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Update README.rst and CLAUDE.md

**Files:**
- Modify: `/home/gumptionthomas/Development/cancelchain/README.rst`
- Modify: `/home/gumptionthomas/Development/cancelchain/CLAUDE.md`

- [ ] **Step 1: Update the README "Install" section**

Open `/home/gumptionthomas/Development/cancelchain/README.rst`. Locate the "Install" section (around line 19). Replace:

```rst
Install CancelChain using pip:

.. code-block:: console

  $ pip install cancelchain

It is recommended that a `python virtual environment`_ is used for `all <https://realpython.com/python-virtual-environments-a-primer/#avoid-system-pollution>`__ `the <https://realpython.com/python-virtual-environments-a-primer/#sidestep-dependency-conflicts>`__ `usual <https://realpython.com/python-virtual-environments-a-primer/#minimize-reproducibility-issues>`__ `reasons <https://realpython.com/python-virtual-environments-a-primer/#dodge-installation-privilege-lockouts>`_.
```

with:

```rst
Install CancelChain using pip:

.. code-block:: console

  $ pip install cancelchain

It is recommended that a `python virtual environment`_ is used for `all <https://realpython.com/python-virtual-environments-a-primer/#avoid-system-pollution>`__ `the <https://realpython.com/python-virtual-environments-a-primer/#sidestep-dependency-conflicts>`__ `usual <https://realpython.com/python-virtual-environments-a-primer/#minimize-reproducibility-issues>`__ `reasons <https://realpython.com/python-virtual-environments-a-primer/#dodge-installation-privilege-lockouts>`_.

For development on the project itself, use `uv`_ to manage the environment and dependencies:

.. code-block:: console

  $ git clone https://github.com/cancelchain/cancelchain.git
  $ cd cancelchain
  $ uv sync --group dev
  $ uv run cancelchain --help
```

- [ ] **Step 2: Add the uv link target to the README footer**

In the same file, locate the alphabetized list of link targets near the bottom of the file (it ends with `.. _transactor: https://docs.cancelchain.org/en/latest/api.html#transactor`). Append this line immediately after the `_transactor` entry (the list is case-insensitive alphabetical, and `t` < `u`):

```rst
.. _uv: https://docs.astral.sh/uv/
```

- [ ] **Step 3: Update CLAUDE.md "Common commands" section**

Open `/home/gumptionthomas/Development/cancelchain/CLAUDE.md`. Replace the entire "Common commands" section (from the `## Common commands` heading through the end of the fenced code block and its trailing paragraph) with:

````markdown
## Common commands

Tooling is driven by **uv** + **uv_build**. Runtime deps live in `[project.dependencies]`; dev deps in `[dependency-groups].dev`; both resolved into `uv.lock`.

```bash
# Tests (full suite, uses tests/.test.env via pytest-dotenv)
uv run pytest                            # full suite
uv run pytest --cov=cancelchain          # with coverage
uv run pytest tests/test_chain.py::test_name   # single test
uv run pytest --runmulti                 # opt in to multiprocessing-marked tests (skipped by default)

# Lint and format (CI runs both)
uv run ruff check src tests
uv run ruff format --check src tests

# Type check (non-blocking in CI; expect existing errors)
uv run mypy src

# Pre-commit (once per clone)
uv run pre-commit install
uv run pre-commit run --all-files

# Local app run (after `uv sync` and a populated .env)
uv run cancelchain init                  # create SQLite schema (FLASK_SQLALCHEMY_DATABASE_URI)
uv run cancelchain import path/to/cancelchain.jsonl   # bulk-load blocks from JSON Lines export
uv run cancelchain run                   # Flask dev server on :5000
uv run cancelchain --help                # full CLI tree (txn/, wallet/, subject/, mill, sync, validate, export, import)

# Production entry point (see Dockerfile)
gunicorn --bind :$PORT app:app
```

`cancelchain` is a `FlaskGroup` CLI defined in `src/cancelchain/__init__.py` (entry point `cancelchain = "cancelchain:cli"`). `python-dotenv` autoloads `.env` from CWD.
````

- [ ] **Step 4: Verify the changes**

Run:
```bash
uv run python -c "import docutils.core; docutils.core.publish_doctree(open('README.rst').read())" 2>&1 | head
```
Expected: empty output (parses cleanly) or a system-message about external links being unverifiable (harmless). A real RST syntax error would surface as a traceback.

If `docutils` isn't installed in the dev env, skip this validation — it's a nice-to-have. The README will be rendered by GitHub/PyPI on push and any error will surface there.

- [ ] **Step 5: Commit**

Run:
```bash
git add README.rst CLAUDE.md
git commit -m "Document uv-based developer workflow

Updates the README's development section and CLAUDE.md's common-commands
section to reflect uv sync / uv run. End-user pip install path is
unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Final acceptance verification

**Files:** none modified. This task only runs commands to verify the acceptance criteria listed in the spec.

For each acceptance criterion, run the command and confirm the expected outcome. If any step fails, **do not commit a fix in this task** — go back to the task that introduced the regression, fix it there, then re-run Task 10 from the top.

- [ ] **Step 1: Clean-clone simulation passes**

Run:
```bash
rm -rf .venv
uv sync --group dev
uv run pytest
```
Expected: env recreated cleanly; tests pass with the same collected count as Task 1's baseline.

- [ ] **Step 2: `uv run ruff check src tests` runs (non-zero exit allowed in Phase 1)**

Run:
```bash
uv run ruff check src tests
echo "exit=$?"
```
Expected: ruff completes without a config-parse error. **Non-zero exit is allowed and expected in Phase 1** — current-version ruff catches ~37 pre-existing errors that the old pinned ruff did not. These are documented in Task 6's commit message and are deferred to Phase 3 alongside the type-hint campaign. The CI `ruff check` step is `continue-on-error: true` for the same reason.

Verify the runner is *configured* (rather than passing/failing on lint output) by checking that the deprecation warning from Task 3's pre-migration ruff is gone (no `top-level linter settings are deprecated` lines), and that the only failures are real lint findings (not config errors).

- [ ] **Step 3: `uv run ruff format --check src tests` exits 0**

Run:
```bash
uv run ruff format --check src tests
echo "exit=$?"
```
Expected: `exit=0`. If non-zero, re-run `uv run ruff format src tests` and amend Task 4.

- [ ] **Step 4: `uv run mypy src` runs (any exit code accepted)**

Run:
```bash
uv run mypy src
echo "exit=$?"
```
Expected: command runs without a configuration error. Non-zero exit due to type errors is fine — Phase 1 establishes the runner without gating.

- [ ] **Step 5: `uv build` produces a working wheel**

Run:
```bash
rm -rf dist
uv build
ls dist/
```
Expected: `dist/` contains `cancelchain-1.4.1-py3-none-any.whl` and `cancelchain-1.4.1.tar.gz`.

Verify the wheel:
```bash
uv venv /tmp/cc-acceptance
/tmp/cc-acceptance/bin/pip install dist/cancelchain-1.4.1-py3-none-any.whl
/tmp/cc-acceptance/bin/python -c "import cancelchain; print(cancelchain.__version__)"
rm -rf /tmp/cc-acceptance dist
```
Expected: prints `1.4.1`.

- [ ] **Step 6: Verify wheel contents include templates and all modules**

Run:
```bash
uv build --wheel
python -m zipfile -l dist/cancelchain-1.4.1-py3-none-any.whl | head -40
rm -rf dist
```
Expected: listing includes `cancelchain/__init__.py`, every `.py` in `src/cancelchain/`, and `cancelchain/templates/*.html`. If templates are missing, the issue is `uv_build` not auto-including non-Python data — add `[tool.uv.build-backend].source-include = ["templates/**"]` to `pyproject.toml`, amend Task 2's commit, and rerun.

- [ ] **Step 7: Verify the deleted files stay deleted**

Run:
```bash
ls requirements.txt requirements-dev.txt 2>&1
```
Expected: both lines say `No such file or directory`.

- [ ] **Step 8: Verify `[tool.hatch.*]` is gone from pyproject.toml**

Run:
```bash
grep -c '\[tool.hatch' pyproject.toml
```
Expected: `0`.

- [ ] **Step 9: Verify the end-user install path still works**

Build and install the wheel into a clean venv as if it were from PyPI:

```bash
uv build --wheel
uv venv /tmp/cc-end-user
/tmp/cc-end-user/bin/pip install dist/cancelchain-1.4.1-py3-none-any.whl
/tmp/cc-end-user/bin/cancelchain --help | head -3
rm -rf /tmp/cc-end-user dist
```
Expected: `cancelchain --help` prints the CLI's help text starting with `Usage: cancelchain [OPTIONS] COMMAND [ARGS]...` (or similar). This confirms the wheel's entry-point script is correctly installed.

- [ ] **Step 10: Verify the Docker image still builds and runs**

Re-run Task 8 Steps 2–4 to confirm Docker remains green:

```bash
docker build -t cancelchain:acceptance .
docker run --rm cancelchain:acceptance cancelchain --version
docker image rm cancelchain:acceptance
```
Expected: build succeeds, `--version` prints `cancelchain, version 1.4.1`, image cleaned up.

- [ ] **Step 11: Push the branch and confirm CI is green**

Run:
```bash
git push -u origin modernize/phase-1-tooling-design
```

Then watch the workflow run in GitHub Actions (or via `gh run watch`). Expected: all matrix entries (3.10, 3.11, 3.12, and 3.13 if it remained in Task 7 Step 2) report green for the `ruff format --check` and `pytest` steps. The `ruff check` and `mypy` steps are allowed to be red (both are `continue-on-error: true`); Phase 3 removes the `continue-on-error` lines after cleaning up the lint and type debt.

- [ ] **Step 12: No commit needed**

This task introduces no file changes. Phase 1 is complete when Step 11 reports green CI.

---

## Post-completion

After Task 10 Step 11 reports green CI, the branch is ready for review/merge. The Phase 1 modernization is complete.

**What changed:**
- Tooling: pip + hatch → uv + uv_build.
- Dev deps: bumped (pytest 8, ruff 0.6+, coverage 7.5+, plus new mypy + pre-commit).
- Formatter: ruff format adopted; one-time noisy pass pinned in `.git-blame-ignore-revs`.
- CI: 3.10–3.13 matrix on `astral-sh/setup-uv@v3`; mypy non-blocking.
- Dockerfile: multi-stage uv build with non-root runtime user.
- Docs: README + CLAUDE.md show uv workflow; end-user pip install unchanged.

**What did NOT change:**
- Runtime dep versions in `[project.dependencies]`.
- Any code under `src/cancelchain/` other than the two-line `__version__` shim in `__init__.py` and one-time `ruff format` reflow.
- The `cancelchain` console-script entry point and end-user install instructions.

**Up next (separate plans):**
- Phase 2 — Library upgrades (Python 3.11+ floor, Flask 3, SQLAlchemy 2.0, pymerkle 5, pytest 8 if not already, gunicorn 23, …).
- Phase 3 — Type hints, SQLAlchemy 2.0 Mapped[], Marshmallow → Pydantic v2, `requests` → `httpx`, `pycryptodome` → `cryptography`, Alembic, mypy strict.
- Phase 4 (optional) — OpenTelemetry, Sentry, structured logging.
