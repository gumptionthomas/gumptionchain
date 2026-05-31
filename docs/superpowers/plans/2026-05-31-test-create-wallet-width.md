# Fix `test_create_wallet` Terminal-Width Dependency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `test_create_wallet` width-independent so the full suite passes at any terminal width (no `COLUMNS=200`).

**Architecture:** The test currently reconstructs a wallet path from Rich-wrapped CLI output; a narrow terminal inserts newlines mid-path and `Wallet.from_file` fails. Replace the output-parsing with a directory glob that asserts the command's real contract: exactly one loadable `*.pem` was written to the requested walletdir. Test-only change; no source change.

**Tech Stack:** pytest, Click `CliRunner`, Rich console, uv.

**Spec:** `docs/superpowers/specs/2026-05-31-test-create-wallet-width-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `tests/test_command.py` | CLI command tests | Add `from pathlib import Path`; rewrite `test_create_wallet` (lines 251-257) to glob the walletdir |

No source files change.

---

### Task 1: Make `test_create_wallet` width-independent

**Files:**
- Modify: `tests/test_command.py` (imports at top; `test_create_wallet` at lines 251-257)

- [ ] **Step 1: Confirm the bug reproduces on a narrow terminal**

Run: `COLUMNS=40 uv run pytest tests/test_command.py::test_create_wallet -q`
Expected: FAIL with `FileNotFoundError` on a path containing literal `\n` (Rich wrapped the long temp-dir path at width 40, and `result.output.strip()` left the internal newline in the reconstructed filename).

- [ ] **Step 2: Add the `pathlib.Path` import**

In `tests/test_command.py`, the imports currently are:
```python
import os
from tempfile import NamedTemporaryFile, TemporaryDirectory

from cancelchain.chain import CURMUDGEON_PER_GRUMBLE, REWARD
from cancelchain.database import db
from cancelchain.wallet import Wallet
```
Add `from pathlib import Path` to the stdlib import group (after the `tempfile` import):
```python
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
```
> Let ruff settle the exact ordering if it differs; `ruff check --fix` will normalize the import block.

- [ ] **Step 3: Rewrite the test body**

The current test (lines 251-257) is:
```python
def test_create_wallet(app, runner):
    with app.app_context(), TemporaryDirectory() as walletdir:
        result = runner.invoke(
            args=['wallet', 'create', '--walletdir', walletdir]
        )
        wallet_filename = result.output.strip()[len('Created ') :]
        assert Wallet.from_file(wallet_filename) is not None
```
Replace it with:
```python
def test_create_wallet(app, runner):
    with app.app_context(), TemporaryDirectory() as walletdir:
        result = runner.invoke(
            args=['wallet', 'create', '--walletdir', walletdir]
        )
        assert result.exit_code == 0
        assert 'Created' in result.output
        pem_files = list(Path(walletdir).glob('*.pem'))
        assert len(pem_files) == 1
        assert Wallet.from_file(str(pem_files[0])) is not None
```
The glob finds the created wallet without parsing the formatted output, so terminal-width wrapping can never break it. `exit_code == 0` and the short-literal `'Created' in result.output` retain cheap success signals (the substring is wrap-safe).

- [ ] **Step 4: Verify the fix on a narrow terminal**

Run: `COLUMNS=40 uv run pytest tests/test_command.py::test_create_wallet -v`
Expected: PASS (this is the case that failed in Step 1).

- [ ] **Step 5: Verify the full command-test module + width-independence**

Run: `COLUMNS=40 uv run pytest tests/test_command.py -q`
Expected: PASS (the whole module is fine at narrow width).

- [ ] **Step 6: Lint, then commit**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
```
Expected: clean (run `ruff check --fix tests/test_command.py` if the import order is flagged, then re-check). `mypy` is unaffected (tests aren't in its target set, and no source changed).
Then:
```bash
git add tests/test_command.py
git commit -m "test: make test_create_wallet width-independent"
```

---

### Task 2: Final gates

**Files:** none (verification only)

- [ ] **Step 1: Full suite with NO `COLUMNS` override**

Run: `uv run pytest -q`
Expected: all pass (256 passed / 1 skipped, give or take — the key point is it passes **without** `COLUMNS=200`, which is the whole purpose of this fix).

- [ ] **Step 2: Lint + format**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
```
Expected: clean.

- [ ] **Step 3: No migration / schema drift (sanity)**

Run: `git status --porcelain src/cancelchain/`
Expected: empty — this is a test-only change; no source files modified.

---

## Notes for the implementer

- Do NOT change `src/cancelchain/command.py` or the Rich console — the CLI's wrapping of long paths is correct for human use; the test was the bug.
- Keep the change scoped to `test_create_wallet`. The other `test_command.py` tests use short-substring `in result.output` checks that don't wrap.
- The `COLUMNS=200` references in `CLAUDE.md` and plan/gate templates can be cleaned up in a separate follow-up once this lands — out of scope here.
