# GumptionChain Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project from CancelChain to GumptionChain across the Python package, CLI, config prefix, signing protocol, currency units, and wallet address tag — keeping the `miller` convention and all behavior unchanged.

**Architecture:** Five independent branches/PRs. Phase 1 (package namespace) lands first because everything imports the package; Phases 2–4 are mutually independent and land in any order after Phase 1; Phase 5 lands after Phase 2 (shared file `tests/.test.env`). This is a pure rename — **no behavior changes** — so the safety net is the *existing* test suite staying green plus a per-phase residual-grep gate, not new red→green tests.

**Tech Stack:** Python 3.12+, uv + uv_build, Flask, SQLAlchemy 2.0, pytest, ruff, mypy.

---

## Conventions for every task

- **One branch + PR per phase.** Branch off the latest `main` (after the prerequisite phase merged). Never push to `main`.
- **The completion gate for every phase is all four:**
  1. `uv run pytest` — full suite green
  2. `uv run ruff check src tests` and `uv run ruff format --check src tests` — clean
  3. `uv run mypy` — no *new* errors (pre-existing errors are expected per CLAUDE.md; compare against baseline)
  4. Residual grep returns **only** historical-doc hits (`docs/superpowers/{plans,specs,audits}/`, `docs/superpowers/ROADMAP.md`)
- **Historical docs are never rewritten.** `docs/superpowers/plans|specs|audits` and `ROADMAP.md` keep their CancelChain references as a true record. A residual grep is *expected* to return hits there — that is not a failure.
- **`uv run mypy` baseline:** before starting, capture the current error count so you can tell pre-existing errors from regressions:
  ```bash
  uv run mypy 2>&1 | tail -1   # e.g. "Found N errors" — record N
  ```

---

## Task 1: Phase 1 — Package + branding + CLI

**Files:**
- Move: `src/cancelchain/` → `src/gumptionchain/` (the whole directory)
- Modify (bulk): all `*.py` under `src/` and `tests/`
- Modify (surgical): `pyproject.toml`, `app.py`, `src/gumptionchain/templates/base.html`, `README.rst`, `CLAUDE.md`
- Regenerate: `uv.lock`
- Modify: `.git-blame-ignore-revs`

- [ ] **Step 1: Branch from main**

```bash
git checkout main && git pull
git checkout -b refactor/rename-phase-1-package
```

- [ ] **Step 2: Move the package directory (preserve history)**

```bash
git mv src/cancelchain src/gumptionchain
```

- [ ] **Step 3: Bulk-rewrite the package name in all Python files under src/ and tests/**

This rewrites the 90 absolute imports (`from cancelchain.x import y`) plus the literal string uses (`_pkg_version('cancelchain')`, `package_name='cancelchain'`, the "valid cancelchain address" docstrings/messages, and code comments). All of these correctly become `gumptionchain`.

```bash
git grep -lz 'cancelchain' -- 'src/**/*.py' 'tests/**/*.py' \
  | xargs -0 sed -i 's/cancelchain/gumptionchain/g'
```

- [ ] **Step 4: Verify no `cancelchain` remains in Python source**

```bash
git grep -n 'cancelchain' -- 'src/**/*.py' 'tests/**/*.py'
```
Expected: **no output** (every Python reference is now `gumptionchain`).

- [ ] **Step 5: Fix the package's own templates (manual — the only non-`.py` content ref)**

`src/gumptionchain/templates/base.html:34` contains a CancelChain domain URL, which must become the GumptionChain site, NOT `gumptionchain.org`. Edit the line:

From:
```html
    Version {{ cc_version }} | <a href="https://cancelchain.org" class="link-dark">cancelchain.org</a>
```
To:
```html
    Version {{ cc_version }} | <a href="https://gumption.com/chain" class="link-dark">gumption.com/chain</a>
```
(The Jinja variable `cc_version` is a template-local name, not a package reference — leave it; it is passed in by `application.py` and renaming it is out of scope for this rename.)

- [ ] **Step 6: Rewrite `app.py`**

`app.py` lives at the repo root, so Step 3's `src/`+`tests/` pathspec did **not** touch it. Apply the rename explicitly:
```bash
sed -i 's/from cancelchain import/from gumptionchain import/' app.py
```
Result: `app.py:3` reads `from gumptionchain import create_app`. The module name `app` itself is unchanged, so gunicorn `app:app` and the Dockerfile `CMD` stay correct.

- [ ] **Step 7: Update `pyproject.toml` — package name, scripts (with `gc` alias), URLs, tool paths**

Apply these exact edits:

`name` (line 7):
```toml
name = "gumptionchain"
```

Author email (line 14) — set to the gumption.com address (confirm during infra pass):
```toml
  { name = "Thomas Bohmbach Jr", email = "tom@gumption.com" }
```

`[project.scripts]` (lines 52-53) — primary command **plus** the `gc` alias, both pointing at the same callable:
```toml
[project.scripts]
gumptionchain = "gumptionchain:cli"
gc = "gumptionchain:cli"
```

`[project.urls]` (lines 55-59):
```toml
[project.urls]
Homepage = "https://gumption.com/chain"
Documentation = "https://gumption.com/chain/docs"
Source = "https://github.com/gumptionthomas/gumptionchain"
Tracker = "https://github.com/gumptionthomas/gumptionchain/issues"
```

`[tool.ruff] extend-exclude` (line 78):
```toml
extend-exclude = ["src/gumptionchain/migrations/versions"]
```

`[tool.mypy] files` and `exclude` (lines 159-160):
```toml
files = ["src/gumptionchain"]
exclude = ["src/gumptionchain/migrations/"]
```

(Note: `description` on line 9 contains no name reference — leave it as-is. The `[tool.coverage]` section has no package-path setting — the `--cov=cancelchain` form lives only in CLAUDE.md docs, handled in Step 9.)

- [ ] **Step 8: Regenerate the lockfile and re-sync the environment**

The distribution name changed, so the locked project entry and the installed console scripts must be rebuilt.

```bash
uv lock
uv sync
```
Expected: `uv.lock` shows `name = "gumptionchain"`; `uv sync` installs the `gumptionchain` and `gc` entry points.

- [ ] **Step 9: Rebrand `README.rst` and the Phase-1 portions of `CLAUDE.md`**

In **`README.rst`**, apply this mapping (use your editor or targeted `sed`, scoped to README only):
- `CancelChain` → `GumptionChain`
- `https://github.com/gumptionthomas/cancelchain` → `https://github.com/gumptionthomas/gumptionchain` (including the `.git` clone URL and the `/blob/...` asset links)
- `https://cancelchain.org` → `https://gumption.com/chain`
- `https://docs.cancelchain.org` → `https://gumption.com/chain/docs` (the `/en/latest/...` path suffixes are Read-the-Docs-specific; keep the suffixes for now and flag for the infra pass)
- `https://blog.cancelchain.org` → `https://gumption.com/chain/blog`
- `storage.googleapis.com/blocks.cancelchain.org/cancelchain.jsonl` → `storage.googleapis.com/blocks.gumption.com/gumptionchain.jsonl` (bucket + filename are external; flag for infra pass)
- `contact@cancelchain.org` → `contact@gumption.com`
- Leave every `miller` reference unchanged.

In **`CLAUDE.md`**, update only the **Phase-1-scoped** references (leave `CC_`, `cc-sig`, `CCG`/grumble/curmudgeon, and `CC…CC` addresses for their own phases):
- `CancelChain` → `GumptionChain` (prose/brand)
- every `uv run cancelchain …` / `cancelchain` CLI command → `gumptionchain` (and note the new `gc` alias where the CLI is introduced)
- every `src/cancelchain/...` path → `src/gumptionchain/...`
- `--cov=cancelchain` → `--cov=gumptionchain`
- `cancelchain = "cancelchain:cli"` entry-point mention → `gumptionchain = "gumptionchain:cli"` (+ `gc` alias)
- the `cancelchain.jsonl` import example → `gumptionchain.jsonl`

- [ ] **Step 10: Run the full completion gate**

```bash
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1   # compare error count to the baseline from "Conventions"
uv run gumptionchain db check
```
Expected: pytest all-green; ruff clean; mypy error count == baseline (no new errors); `db check` reports the schema is up to date.

- [ ] **Step 11: Manual CLI smoke check (both entry points)**

```bash
uv run gumptionchain --help
uv run gc --help
```
Expected: both print the same CLI tree (`txn`, `wallet`, `subject`, `mill`, `sync`, `validate`, `export`, `import`, `db`, …).

- [ ] **Step 12: Residual-grep gate**

```bash
git grep -n 'cancelchain' -- . ':!docs/superpowers'
```
Expected: **no output** outside historical docs. (A hit means an incomplete rename — fix it before committing.)

- [ ] **Step 13: Commit, recording the bulk-rewrite SHA in `.git-blame-ignore-revs`**

```bash
git add -A
git commit -m "refactor(rename): cancelchain package → gumptionchain (Phase 1)

Move src/cancelchain → src/gumptionchain, rewrite all imports, add gc CLI
alias, repoint URLs to gumption.com/chain, regenerate uv.lock.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Then append the commit SHA to `.git-blame-ignore-revs` so the mechanical rewrite does not pollute `git blame`, and amend it into the same commit:
```bash
git rev-parse HEAD >> .git-blame-ignore-revs   # then edit the file to add a "# rename: cancelchain → gumptionchain" comment above the SHA
git add .git-blame-ignore-revs
git commit --amend --no-edit
```

- [ ] **Step 14: Push and open the PR**

```bash
git push -u origin refactor/rename-phase-1-package
```
Open a PR titled `refactor(rename): cancelchain package → gumptionchain (Phase 1)`. Wait for CI green and the Copilot review backstop, then squash-merge with `--delete-branch`. **Phases 2–5 branch from main only after this merges.**

---

## Task 2: Phase 2 — Env prefix `CC_` → `GC_`

**Files:**
- Modify: `src/gumptionchain/config.py:32`
- Modify: `tests/.test.env`
- Modify: `CLAUDE.md` (the `CC_*` references)

- [ ] **Step 1: Branch from updated main**

```bash
git checkout main && git pull
git checkout -b refactor/rename-phase-2-env-prefix
```

- [ ] **Step 2: Flip the config prefix**

`src/gumptionchain/config.py:32`, change:
```python
    _prefix: ClassVar[str] = 'CC_'
```
to:
```python
    _prefix: ClassVar[str] = 'GC_'
```

- [ ] **Step 3: Rename the keys in the test env file**

`tests/.test.env` — only the `CC_`-prefixed key changes (the `FLASK_*` and `SQLALCHEMY_*` keys are unrelated and stay):
```bash
sed -i 's/^CC_/GC_/' tests/.test.env
```
Expected result: `CC_READER_ADDRESSES=...` becomes `GC_READER_ADDRESSES=...`.

- [ ] **Step 4: Update `CC_*` references in CLAUDE.md**

In `CLAUDE.md`, rename every environment-variable mention `CC_<NAME>` → `GC_<NAME>` (e.g. `CC_PEERS`, `CC_MILLER_ADDRESSES`, `CC_API_ASYNC_PROCESSING`, `CC_MAX_CHAIN_FILL_DEPTH`, `CC_MAX_PENDING_TXNS`, and the prose "`CC_*` env vars"). Do **not** touch `cc-sig`, `CC-` headers, `CCG`, or `CC…CC` addresses — those belong to later phases.

- [ ] **Step 5: Run the completion gate**

```bash
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Expected: pytest all-green (the suite loads `tests/.test.env`; a missed key would surface as a READER-role/auth failure in API tests). ruff/mypy clean vs. baseline.

- [ ] **Step 6: Residual-grep gate**

```bash
git grep -n 'CC_' -- . ':!docs/superpowers'
```
Expected: **no output** outside historical docs.

- [ ] **Step 7: Commit, push, PR**

```bash
git add -A
git commit -m "refactor(rename): env prefix CC_ → GC_ (Phase 2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin refactor/rename-phase-2-env-prefix
```
Open PR `refactor(rename): env prefix CC_ → GC_ (Phase 2)`; CI green + Copilot backstop; squash-merge `--delete-branch`.

---

## Task 3: Phase 3 — Signing scheme + headers `cc-sig-v1` → `gc-sig-v1`

**Files:**
- Modify: `src/gumptionchain/signing.py:12,15-19`
- Modify: `docs/api-auth-protocol.md`
- Modify: `tests/test_signing.py`, `tests/test_api_client.py`, `tests/test_auth_audit.py`
- Modify: `CLAUDE.md` (the `cc-sig` / `CC-*` references)

- [ ] **Step 1: Branch from updated main**

```bash
git checkout main && git pull
git checkout -b refactor/rename-phase-3-sig-scheme
```

- [ ] **Step 2: Rename the scheme id and header-name constants**

`src/gumptionchain/signing.py` — change the scheme id (line 12) and the five header-name constants (lines 15-19). Leave `SIG_VERSION = '1'` (line 11) untouched — the wire *version* is independent of the scheme name.

From:
```python
SIG_SCHEME = 'cc-sig-v1'  # scheme id bound into the signed canonical
...
H_VERSION = 'CC-Sig-Version'
H_ADDRESS = 'CC-Address'
H_PUBKEY = 'CC-Public-Key'
H_TIMESTAMP = 'CC-Timestamp'
H_SIGNATURE = 'CC-Signature'
```
To:
```python
SIG_SCHEME = 'gc-sig-v1'  # scheme id bound into the signed canonical
...
H_VERSION = 'GC-Sig-Version'
H_ADDRESS = 'GC-Address'
H_PUBKEY = 'GC-Public-Key'
H_TIMESTAMP = 'GC-Timestamp'
H_SIGNATURE = 'GC-Signature'
```
(api.py and api_client.py consume these via the `H_*` constants — confirmed centralized — so no edits there.)

- [ ] **Step 3: Update the tests that assert scheme/header literals**

In `tests/test_signing.py`, `tests/test_api_client.py`, and `tests/test_auth_audit.py`, replace any literal `'cc-sig-v1'` → `'gc-sig-v1'` and any literal `'CC-Sig-Version'` / `'CC-Address'` / `'CC-Public-Key'` / `'CC-Timestamp'` / `'CC-Signature'` → the `GC-` equivalents. (Tests that reference the `signing.H_*` constants rather than literals need no change.) Find them with:
```bash
git grep -n "cc-sig\|CC-Sig\|CC-Address\|CC-Public\|CC-Timestamp\|CC-Signature" -- tests
```

- [ ] **Step 4: Rewrite `docs/api-auth-protocol.md` (scheme + headers + branding)**

Update throughout: the scheme id `cc-sig-v1` → `gc-sig-v1` (title, canonical-string blocks, header table, worked example), the `CC-*` header names → `GC-*`, and the document's `CancelChain` branding → `GumptionChain`. (This file's branding is folded here, per the spec, to avoid touching it in Phase 1.)
```bash
sed -i 's/cc-sig-v1/gc-sig-v1/g; s/CC-Sig-Version/GC-Sig-Version/g; s/CC-Address/GC-Address/g; s/CC-Public-Key/GC-Public-Key/g; s/CC-Timestamp/GC-Timestamp/g; s/CC-Signature/GC-Signature/g; s/cc_signature/gc_signature/g; s/CancelChain/GumptionChain/g' docs/api-auth-protocol.md
```
Then read the file once to confirm the worked example still reads coherently.

- [ ] **Step 5: Update the `cc-sig` / `CC-*` references in CLAUDE.md**

In `CLAUDE.md`, the "API authentication" section: `cc-sig-v1` → `gc-sig-v1` and the `CC-*` header names → `GC-*`.

- [ ] **Step 6: Run the completion gate**

```bash
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Expected: pytest all-green — pay attention to the signing round-trip, freshness-boundary, and node-binding tests (`tests/test_signing.py`). ruff/mypy clean vs. baseline.

- [ ] **Step 7: Residual-grep gate**

```bash
git grep -n "cc-sig\|CC-Sig\|CC-Address\|CC-Public-Key\|CC-Timestamp\|CC-Signature" -- . ':!docs/superpowers'
```
Expected: **no output** outside historical docs.

- [ ] **Step 8: Commit, push, PR**

```bash
git add -A
git commit -m "refactor(rename): signing scheme cc-sig-v1 → gc-sig-v1 (Phase 3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin refactor/rename-phase-3-sig-scheme
```
Open PR `refactor(rename): signing scheme cc-sig-v1 → gc-sig-v1 (Phase 3)`; CI green + Copilot backstop; squash-merge `--delete-branch`.

---

## Task 4: Phase 4 — Currency units grit / grain

**Files:**
- Modify: `src/gumptionchain/chain.py:41,44`
- Modify: `src/gumptionchain/command.py` (lines 35, 57-62, and the `CCG` strings)
- Modify: `tests/test_chain.py`, `tests/test_command.py`, `tests/test_miller.py`
- Modify: `CLAUDE.md` (the `CCG`/grumble/curmudgeon references)

- [ ] **Step 1: Branch from updated main**

```bash
git checkout main && git pull
git checkout -b refactor/rename-phase-4-units
```

- [ ] **Step 2: Rename the constant and helpers in `chain.py` and `command.py`**

A scoped sed handles the symbol renames consistently across both files and their test references. Apply to `src/` and `tests/`:
```bash
git grep -lz 'CURMUDGEON_PER_GRUMBLE\|grumble_to_curmudgeons\|human_curmudgeons' -- 'src/**/*.py' 'tests/**/*.py' \
  | xargs -0 sed -i \
    -e 's/CURMUDGEON_PER_GRUMBLE/GRAIN_PER_GRIT/g' \
    -e 's/grumble_to_curmudgeons/grit_to_grains/g' \
    -e 's/human_curmudgeons/human_grains/g'
```

- [ ] **Step 3: Rename the parameter/local names in the helper bodies**

The sed above renamed the *function* names but not their parameter/local variable names. Edit `src/gumptionchain/command.py:57-62` so the internals read cleanly:

From:
```python
def grit_to_grains(grumble: float) -> int:
    return int(GRAIN_PER_GRIT * float(grumble))


def human_grains(curmudgeons: int | float) -> str:
    balance = int(curmudgeons) / GRAIN_PER_GRIT
```
To:
```python
def grit_to_grains(grit: float) -> int:
    return int(GRAIN_PER_GRIT * float(grit))


def human_grains(grains: int | float) -> str:
    balance = int(grains) / GRAIN_PER_GRIT
```

- [ ] **Step 4: Replace the `CCG` ticker string in CLI help, output, and docstrings**

In `src/gumptionchain/command.py`, replace the ticker `CCG` → `GRIT` everywhere it appears as a user-facing string (the `AMOUNT is the amount … of CCG` docstrings at lines ~680/757/832/907, the `balance in CCG` / `support total in CCG` docstrings at ~974/1011/1046, and the `f'{human_grains(...)} CCG'` console prints at ~983/1020/1055):
```bash
sed -i 's/\bCCG\b/GRIT/g' src/gumptionchain/command.py
```

- [ ] **Step 5: Update the unit references and `CCG` literals in tests**

`tests/test_command.py` has local names `REWARD_CCG`/`SUBJECT_CCG` and `' CCG'` output assertions. Rename for clarity and correctness:
```bash
sed -i 's/REWARD_CCG/REWARD_GRIT/g; s/SUBJECT_CCG/SUBJECT_GRIT/g; s/ CCG/ GRIT/g' tests/test_command.py
```
Then scan `tests/test_chain.py` and `tests/test_miller.py` for any remaining `CCG` literal and update to `GRIT`:
```bash
git grep -n '\bCCG\b' -- tests
```
Expected after fixes: no `CCG` literals remain in `tests/`.

- [ ] **Step 6: Update the `CCG`/grumble/curmudgeon references in CLAUDE.md**

In `CLAUDE.md` (the units paragraph): `CCG` → `GRIT`, `grumble` → `grit`, `curmudgeon(s)` → `grain(s)`, `CURMUDGEON_PER_GRUMBLE` → `GRAIN_PER_GRIT`, `grumble_to_curmudgeons` → `grit_to_grains`, and the "1 CCG / grumble = 100 curmudgeons" sentence → "1 GRIT / grit = 100 grains".

- [ ] **Step 7: Run the completion gate**

```bash
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Expected: pytest all-green (`tests/test_command.py` exercises the balance/transfer output with the new ticker). ruff/mypy clean vs. baseline.

- [ ] **Step 8: Residual-grep gate**

```bash
git grep -n 'CURMUDGEON_PER_GRUMBLE\|grumble\|curmudgeon\|\bCCG\b' -- . ':!docs/superpowers'
```
Expected: **no output** outside historical docs.

- [ ] **Step 9: Commit, push, PR**

```bash
git add -A
git commit -m "refactor(rename): currency units grumble/curmudgeon → grit/grain (Phase 4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin refactor/rename-phase-4-units
```
Open PR `refactor(rename): currency units grumble/curmudgeon → grit/grain (Phase 4)`; CI green + Copilot backstop; squash-merge `--delete-branch`.

---

## Task 5: Phase 5 — Wallet address tag `CC` → `GC` (after Phase 2)

**Files:**
- Modify: `src/gumptionchain/wallet.py:24`
- Modify: `tests/conftest.py:83` (`WALLET_ADDRESS`)
- Modify: `tests/.test.env` (the `GC_READER_ADDRESSES` values)
- Modify: any other hardcoded `CC…CC` address literal surfaced by the suite/grep

> **Prerequisite:** Phase 2 must be merged first (both phases edit `tests/.test.env`).

- [ ] **Step 1: Branch from updated main**

```bash
git checkout main && git pull
git checkout -b refactor/rename-phase-5-address-tag
```

- [ ] **Step 2: Change the address tag**

`src/gumptionchain/wallet.py:24`, change:
```python
ADDRESS_TAG = 'CC'
```
to:
```python
ADDRESS_TAG = 'GC'
```
(`schema.py`'s `validate_address_format` reads `ADDRESS_TAG`, so format validation tracks the change automatically.)

- [ ] **Step 3: Recompute the fixed-key test address and the reader allowlist values**

The fixed test key `WALLET_PRIVATE_KEY_B58` (conftest.py:35) now yields a `GC…GC` address. Print the new value and two fresh reader addresses:
```bash
uv run python -c "
from gumptionchain.wallet import Wallet
from tests.conftest import WALLET_PRIVATE_KEY_B58
print('WALLET_ADDRESS =', Wallet(b58ks=WALLET_PRIVATE_KEY_B58).address)
print('READER_1 =', Wallet().address)
print('READER_2 =', Wallet().address)
"
```
Record the three printed values for the next two steps. (Importing `tests.conftest` triggers module-level wallet generation but not the fixtures — only the constant is needed.)

- [ ] **Step 4: Update `WALLET_ADDRESS` in conftest**

`tests/conftest.py:83`, replace the hardcoded value with the recomputed `GC…GC` address from Step 3:
```python
WALLET_ADDRESS = '<the GC… value printed for WALLET_ADDRESS>'
```

- [ ] **Step 5: Update the reader allowlist in `.test.env`**

`tests/.test.env`, replace the two addresses in `GC_READER_ADDRESSES` (key already renamed in Phase 2) with the two freshly-generated `GC…GC` reader addresses from Step 3:
```
GC_READER_ADDRESSES=["<READER_1 GC… value>", "<READER_2 GC… value>"]
```

- [ ] **Step 6: Run the suite to surface any remaining hardcoded `CC…CC` literals**

```bash
uv run pytest
```
If any test still fails on an address mismatch/format error, it holds another hardcoded `CC…CC` literal — locate and fix it:
```bash
git grep -nE 'CC[1-9A-HJ-NP-Za-km-z]{20,}CC' -- tests README.rst
```
Re-run `uv run pytest` until green.

- [ ] **Step 7: Run the rest of the completion gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Expected: clean vs. baseline.

- [ ] **Step 8: Residual-grep + manual address smoke check**

```bash
git grep -nE 'CC[1-9A-HJ-NP-Za-km-z]{20,}CC' -- . ':!docs/superpowers'
uv run gumptionchain wallet create --help   # confirm command exists
```
Expected: no `CC…CC` address-shaped literals outside historical docs. (Optionally generate a real wallet and confirm its address reads `GC…GC`.)

- [ ] **Step 9: Commit, push, PR**

```bash
git add -A
git commit -m "refactor(rename): wallet address tag CC → GC (Phase 5)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin refactor/rename-phase-5-address-tag
```
Open PR `refactor(rename): wallet address tag CC → GC (Phase 5)`; CI green + Copilot backstop; squash-merge `--delete-branch`.

---

## Task 6: Phase 6 — Residual `cc_` identifiers (after any earlier phase)

Lowercase `cc_` CancelChain stragglers discovered during Phase 2 review that aren't the `CC_` env prefix and don't belong to any other phase. Independent of Phases 3–5 (no shared files). Pure rename — no behavior change.

**Files:**
- Modify: `.github/workflows/tests.yml` (the `cc_check.db` CI temp-DB filename, 2 occurrences)
- Modify: `src/gumptionchain/application.py` (`inject_cc_version` context processor + the `'cc_version'` dict key)
- Modify: `src/gumptionchain/templates/base.html` (`{{ cc_version }}`)

> Note: `cc_signature` in `docs/api-auth-protocol.md` is NOT handled here — it's part of the signing worked example and is renamed in Phase 3 (Step 4).

- [ ] **Step 1: Branch from updated main**

```bash
git checkout main && git pull --ff-only
git checkout -b refactor/rename-phase-6-residual-cc
```

- [ ] **Step 2: Rename the CI temp-DB filename**

`.github/workflows/tests.yml` — both the comment and the `FLASK_SQLALCHEMY_DATABASE_URI` value:
```bash
sed -i 's/cc_check\.db/gc_check.db/g' .github/workflows/tests.yml
```

- [ ] **Step 3: Rename the template version context processor**

`src/gumptionchain/application.py` — rename the function and the dict key:
```python
    @app.context_processor
    def inject_gc_version() -> dict[str, str]:
        return {'gc_version': __version__}
```
`src/gumptionchain/templates/base.html` — update the interpolation:
```html
    Version {{ gc_version }} | <a href="https://gumption.com/chain" class="link-dark">gumption.com/chain</a>
```

- [ ] **Step 4: Completion gate**

```bash
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Expected: pytest all-green (browser/footer tests render the version); ruff clean; mypy 0 errors. The `cc_check.db` change is exercised by the `gumptionchain db check` CI gate when this PR runs.

- [ ] **Step 5: Residual-grep gate**

```bash
git grep -in 'cc_version\|cc_check' -- . ':!docs/superpowers'
```
Expected: no output. (`cc_signature` may still appear until Phase 3 lands — that's expected and owned by Phase 3.)

- [ ] **Step 6: Commit, push, PR**

```bash
git add -A
git commit -m "refactor(rename): residual cc_ identifiers → gc_ (Phase 6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin refactor/rename-phase-6-residual-cc
```
Open PR `refactor(rename): residual cc_ identifiers → gc_ (Phase 6)`; CI green + Copilot backstop; squash-merge `--delete-branch`.

---

## External infrastructure checklist (owner, out-of-repo — not part of any PR)

Hand off to the owner; these live outside the repository:

- [ ] Rename the GitHub repo `gumptionthomas/cancelchain` → `gumptionthomas/gumptionchain` (GitHub auto-redirects old URLs); then locally `git remote set-url origin git@github.com:gumptionthomas/gumptionchain.git`.
- [ ] Stand up the GumptionChain site at `gumption.com/chain`; confirm the `…/chain/docs` and `…/chain/blog` paths used in `pyproject.toml` and `README.rst`, adjusting if doc hosting differs (e.g. Read the Docs).
- [ ] Move/alias email `contact@` and `tom@cancelchain.org` → `gumption.com`.

**Defunct hosted infra (no rename — removed from the README in Phase 1):**
- The GCS chain-export bucket (`blocks.cancelchain.org/cancelchain.jsonl`) no longer exists. The README's hosted-download step was removed and the import quick-start reworded to import any JSON Lines export (e.g. from `gumptionchain export`). No bucket to rename.
- **The Cancel Button** (`thecancelbutton.com`) — the reference/demo node, account-registration site, and PEM-key source — is gone. Its onboarding flow and links were removed from the README and replaced with the email-request access path plus a generic `peer.example.com` / `CCYourWalletAddressCC` placeholder.

---

## Self-review notes (plan ↔ spec coverage)

- Package/CLI/branding → Task 1. Env prefix → Task 2. Signing scheme/headers → Task 3. Units → Task 4. Address tag → Task 5. External infra → checklist. Every spec section maps to a task.
- `miller`/`Miller` deliberately untouched (no task renames it) — matches spec.
- Historical docs excluded from every grep gate via `':!docs/superpowers'` — matches the spec's "expected historical hits" rule.
- Symbol consistency: `GRAIN_PER_GRIT`, `grit_to_grains`, `human_grains`, `GRIT`, `ADDRESS_TAG='GC'`, `gc-sig-v1`, `GC-*` headers, `GC_` prefix used identically across tasks and tests.
