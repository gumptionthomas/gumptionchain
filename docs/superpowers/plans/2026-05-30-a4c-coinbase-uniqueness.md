# A4.c — coinbase-txid uniqueness check implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject same-chain coinbase-txid replay at `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278`). **Note:** this plan ships in a docs-only PR alongside the design spec; the actual code changes ride a separate follow-up impl PR (`fix/a4c-coinbase-uniqueness`). When that impl PR lands it closes audit finding A4.c and the demonstration test transitions from `@pytest.mark.xfail(strict=True)` to a real pass.

**Architecture:** Add a chain-lineage uniqueness check on the candidate coinbase's `txid` inside `Chain.validate_block_coinbase`. The check computes the candidate's parent explicitly (`parent = Block.from_db(block.prev_hash)`) and calls `self.get_transaction(cb.txid, start_block=parent)`, so the walk inspects blocks strictly upstream of the candidate — never the candidate itself. (Starting from the default `self.last_block` would be wrong during `Chain.validate()` revalidation, where `self.last_block` is the chain tip and `block` is an interior block — the walk would include `block` and falsely flag every coinbase.) Found → raise a new `DuplicateCoinbaseError(InvalidCoinbaseError)`. Genesis has no findable parent (`Block.from_db` returns `None`) → check skipped. The xfail demonstration test asserts `pytest.raises(InvalidCoinbaseError)`, which matches the new subclass via inheritance, so no test body changes are needed beyond removing the decorator.

**Tech Stack:** Python 3.12 + SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1 + SQLite (test) / production-DB. The demonstration test uses `pytest` + `time_machine` + existing `tests/conftest.py` fixtures.

The companion design spec is `docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md`.

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- A2.e remediation merged. Verify with `git log --oneline -3 main` showing `d3fcd2a fix(a2e): make Node.fill_chain atomic via deferred-commits (#87)` near the top.
- The branch `docs/a4c-coinbase-uniqueness` exists locally with one commit:
  - `29fd216 docs(a4c): add coinbase-txid uniqueness check design spec`
  This plan adds a second commit on that branch (the plan file itself) and ships both as the docs PR.
- CI hard-gates (per `.github/workflows/tests.yml`): `ruff check`, `ruff format --check`, `pytest`, `mypy`, and `cancelchain db upgrade` + `cancelchain db check`.
- Test baseline (post-A2.e): **237 passed, 5 xfailed, 1 skipped**. After the impl PR lands, expect **239 passed, 4 xfailed, 1 skipped** — A4.c moves from xfail to pass (+1), and a new cross-fork non-regression test (`test_a4_c_cross_fork_coinbase_replay_accepted`, plan Task 5 Step 8) adds another pass (+1).
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent. Auto-rereview on cancelchain is inconsistent in practice (per `project_copilot_auto_rereview`) — the controller asks the user to click "Re-request review" if the polling loop times out.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md` (this file) + spec already on branch |
| 2 | impl PR | branch off main; verify baseline |
| 3 | impl PR | `src/cancelchain/exceptions.py` — add `DuplicateCoinbaseError(InvalidCoinbaseError)` |
| 4 | impl PR | `src/cancelchain/chain.py` — `Chain.validate_block_coinbase` gains the uniqueness check |
| 5 | impl PR | `tests/test_verification_audit.py` — remove xfail decorator |
| 6 | impl PR | `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` — close A4.c in 3 spots |
| 7 | impl PR | `docs/superpowers/ROADMAP.md` — move A4.c to closed |
| 8 | impl PR | run gates + single commit + push + open PR |
| 9 | acceptance | none (verification only) |

The impl PR lands as two commits: one main commit covering the fix + housekeeping (Tasks 3-7), and a small follow-up commit that fills in the PR numbers in the ROADMAP closed entry once the PR is open (Task 8 Step 7 — the impl PR number isn't known until `gh pr create` returns). Do not amend or squash locally; cancelchain convention is additive commits. No new files; all 6 modified files are existing.

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** The design spec is committed on `docs/a4c-coinbase-uniqueness` (`29fd216`). This task adds the implementation plan as a second commit and ships both as one docs PR.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/a4c-coinbase-uniqueness`; spec file is tracked; commit count above main is `1`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md
git status docs/superpowers/plans/
```

Expected: file exists; shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md
git commit -m "$(cat <<'EOF'
docs(a4c): add coinbase-txid uniqueness check implementation plan

Plan executes the A4.c remediation design from
2026-05-30-a4c-coinbase-uniqueness-design.md. Single impl PR
adding a chain-lineage uniqueness check to
Chain.validate_block_coinbase via self.get_transaction(cb.txid),
a new DuplicateCoinbaseError(InvalidCoinbaseError) exception class,
removal of the xfail decorator on the demonstration test, and
audit doc + ROADMAP updates to reflect A4.c closure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/a4c-coinbase-uniqueness
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/a4c-coinbase-uniqueness --title "docs(a4c): coinbase-txid uniqueness check design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the A4.c remediation design spec (\`docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md\`).
- Adds the A4.c remediation implementation plan (\`docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md\`).
- No code changes.

This docs-only PR adds the **design and plan** for the A4.c remediation; it makes no code change and does not itself close the finding. A4.c is closed by the follow-up impl PR (\`fix/a4c-coinbase-uniqueness\`), which adds a chain-lineage uniqueness check on the candidate coinbase's \`txid\` inside \`Chain.validate_block_coinbase\`. That check computes the candidate's parent explicitly (\`parent = Block.from_db(block.prev_hash)\`) and calls \`self.get_transaction(cb.txid, start_block=parent)\`, so the walk searches blocks strictly upstream of the candidate and preserves cross-fork legitimacy (Attack b's documented case). A new \`DuplicateCoinbaseError(InvalidCoinbaseError)\` is raised when the txid is found in the chain's lineage. A4.c is the last open Medium; once the impl PR lands, audit severity reaches 0 Critical / 0 High / 0 Medium / 4 Low.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Impl branch baseline + targeted re-read (impl PR)

**Files:** No edits. Branch off main; verify baseline gates pass; re-read the validate_block_coinbase / get_transaction surface to confirm the spec's "no cross-fork over-rejection" claim.

### Step 1: Branch off main + baseline gates

After the docs PR merges:

```bash
git checkout main && git pull --ff-only
git checkout -b fix/a4c-coinbase-uniqueness
git log --oneline -1
```

Expected: top commit is the docs PR squash (e.g., `<sha> docs(a4c): coinbase-txid uniqueness check design + plan (#<N>)`).

Confirm baseline gates are green BEFORE any edit:

```bash
uv run mypy
uv run ruff check src tests
uv run pytest 2>&1 | tail -3
```

Expected: mypy clean; ruff clean; pytest `237 passed, 5 xfailed, 1 skipped`.

If baseline gates aren't clean, STOP and report BLOCKED.

### Step 2: Confirm the A4.c demonstration test is currently xfail

```bash
uv run pytest tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -5
```

Expected: `1 xfailed`.

```bash
uv run pytest --runxfail tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -10
```

Expected: `1 failed` — the test genuinely demonstrates the gap today.

### Step 3: Targeted re-read of the validation surface

Read these source files end-to-end with the chain-lineage-check lens.

```bash
grep -n -B 2 -A 20 'def validate_block_coinbase' src/cancelchain/chain.py
grep -n -B 2 -A 18 'def get_transaction' src/cancelchain/chain.py
grep -n -B 2 -A 8 'def get_transaction_in_chain' src/cancelchain/models.py
grep -n -B 2 -A 8 'class InvalidCoinbaseError\b\|class InvalidCoinbaseErrorRewardError' src/cancelchain/exceptions.py
```

Confirm:
- `Chain.validate_block_coinbase` (chain.py:278) currently does: `block.validate_coinbase()` → compute `reward` → extract `cb` → check `outflow.amount == reward`.
- `Chain.get_transaction` (chain.py:294) walks from `start_block or self.last_block` via `Block.from_db(prev_hash)`, then defers to `BlockDAO.get_transaction_in_chain` once it hits a persisted ancestor.
- `BlockDAO.get_transaction_in_chain` (models.py:339-342) uses the per-block recursive CTE `self.transactions_chain.where(TransactionDAO.txid == txid)` — chain-scoped, not DB-wide.
- `InvalidCoinbaseError(InvalidTransactionError)` exists at exceptions.py:97; `InvalidCoinbaseErrorRewardError(InvalidCoinbaseError)` exists at exceptions.py:101.

If any of these don't hold (e.g., `get_transaction_in_chain` is actually DB-wide, breaking cross-fork legitimacy), STOP and report DONE_WITH_CONCERNS — the design may need revision.

---

## Task 3: Add `DuplicateCoinbaseError` to exceptions.py

**Files:**
- Modify: `src/cancelchain/exceptions.py` — add new exception class.

### Step 1: Locate the insertion point

```bash
grep -n 'class InvalidCoinbaseErrorRewardError' src/cancelchain/exceptions.py
```

Expected: line ~101.

### Step 2: Add the new exception class

Find (around line 101):

```python
class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass
```

Insert two blank lines and the new class immediately after it:

```python
class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass


class DuplicateCoinbaseError(InvalidCoinbaseError):
    pass
```

### Step 3: Verify

```bash
uv run ruff check src/cancelchain/exceptions.py
uv run ruff format --check src/cancelchain/exceptions.py
uv run mypy 2>&1 | tail -3
```

All three exit 0. If `ruff format --check` reports a diff, run `uv run ruff format src/cancelchain/exceptions.py`.

```bash
grep -n 'class DuplicateCoinbaseError' src/cancelchain/exceptions.py
```

Expected: one match.

---

## Task 4: Add the uniqueness check to `Chain.validate_block_coinbase`

**Files:**
- Modify: `src/cancelchain/chain.py` — `Chain.validate_block_coinbase` (line 278).

### Step 1: Verify `DuplicateCoinbaseError` is importable

```bash
grep -n 'DuplicateCoinbaseError\|InvalidCoinbaseError\|InvalidCoinbaseErrorRewardError' src/cancelchain/chain.py
```

Note which exceptions are already imported. The new check raises `DuplicateCoinbaseError`, which must be importable from `src/cancelchain/exceptions.py` (Task 3 added it there). If `DuplicateCoinbaseError` is not in the existing import block, you'll add it in Step 3.

### Step 2: Modify `validate_block_coinbase`

Find (around chain.py:278):

```python
    def validate_block_coinbase(self, block: Block) -> None:
        block.validate_coinbase()
        reward = self.block_reward(block)
        cb = block.coinbase
        if cb is not None:
            outflow = cb.get_outflow(0)
            if outflow is not None and outflow.amount != reward:
                raise InvalidCoinbaseErrorRewardError()
```

Replace with:

```python
    def validate_block_coinbase(self, block: Block) -> None:
        block.validate_coinbase()
        reward = self.block_reward(block)
        cb = block.coinbase
        if cb is not None:
            # A4.c: reject same-chain coinbase replay. Start the lookup
            # from the candidate block's PARENT, not self.last_block.
            # During Chain.validate() full-chain revalidation,
            # self.last_block is the chain tip while `block` is an
            # interior block, so a default start_block=self.last_block
            # walk would include `block` itself (and its descendants) and
            # find the candidate's own coinbase — falsely flagging every
            # block and breaking `cancelchain validate`. Searching the
            # parent's lineage instead finds the cb only if it was already
            # persisted UPSTREAM in THIS chain. Cross-fork replay (Attack
            # b) stays legitimate because the walk is chain-scoped via the
            # per-block recursive CTE in BlockDAO.get_transaction_in_chain.
            # A genesis block has no findable parent (Block.from_db returns
            # None), so the check is skipped — a genesis coinbase can't be
            # a replay of anything.
            parent = (
                Block.from_db(block.prev_hash) if block.prev_hash else None
            )
            if (
                parent is not None
                and self.get_transaction(cb.txid, start_block=parent)
                is not None
            ):
                raise DuplicateCoinbaseError()
            outflow = cb.get_outflow(0)
            if outflow is not None and outflow.amount != reward:
                raise InvalidCoinbaseErrorRewardError()
```

### Step 3: Add the import if needed

If Step 1 showed `DuplicateCoinbaseError` was NOT in the existing import block, add it — and ONLY it. `chain.py` currently imports `InvalidCoinbaseErrorRewardError` from this part of the exception hierarchy (used by the existing reward check) but NOT bare `InvalidCoinbaseError`. The new check raises `DuplicateCoinbaseError`, so that is the only symbol to add. Do NOT add `InvalidCoinbaseError` — it is unused in `chain.py` and would fail Ruff's `F401` unused-import check.

The existing import block looks like:

```python
from cancelchain.exceptions import (
    ...
    InvalidBlockError,
    InvalidCoinbaseErrorRewardError,
    ...
)
```

Add `DuplicateCoinbaseError,` in alphabetical order (it sorts before `InvalidBlockError`). The result:

```python
from cancelchain.exceptions import (
    ...
    DuplicateCoinbaseError,
    ...
    InvalidBlockError,
    InvalidCoinbaseErrorRewardError,
    ...
)
```

### Step 4: Confirm syntax + types

```bash
uv run ruff check src/cancelchain/chain.py
uv run ruff format --check src/cancelchain/chain.py
uv run mypy 2>&1 | tail -3
```

All three exit 0. If `ruff format --check` reports a diff, run `uv run ruff format src/cancelchain/chain.py`.

### Step 5: Run the A4.c demonstration test under `--runxfail`

The test still carries `@pytest.mark.xfail(strict=True)`. Under `--runxfail`, xfail is ignored — a passing test means the fix works.

```bash
uv run pytest --runxfail tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -10
```

Expected: `1 passed`. (Was `1 failed` in Task 2 Step 2.)

If the test still fails, the fix didn't take. Re-inspect: (a) `DuplicateCoinbaseError` is correctly imported, (b) the check is inside the `if cb is not None` block, (c) the check fires BEFORE the reward check, (d) the test's `with pytest.raises(InvalidCoinbaseError):` correctly catches `DuplicateCoinbaseError` (it should, since `DuplicateCoinbaseError` inherits from `InvalidCoinbaseError`).

### Step 6: Run the test under normal pytest

With the xfail decorator still in place but the fix applied, strict-mode kicks in:

```bash
uv run pytest tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -10
```

Expected: `1 failed — [XPASS(strict)]`. That's the signal to remove the decorator in Task 5.

### Step 7: Regression check — full test suite (excluding the not-yet-un-xfailed A4.c test)

The fix is applied but the A4.c xfail decorator is still in place, so the A4.c test is now XPASS(strict) — a **failure** under a plain `uv run pytest`. That single failure is expected and is removed in Task 5. To confirm nothing ELSE regressed, deselect the A4.c test for this check:

```bash
uv run pytest --deselect 'tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance' 2>&1 | tail -3
```

Expected: `237 passed, 4 xfailed, 1 skipped`. A4.c is currently one of the 5 xfailed tests (not one of the 237 passed), so deselecting it leaves the passed count UNCHANGED at 237 and drops the xfail count from 5 to 4. The 1 skip is unchanged. No `failed`.

If you run the full suite without the deselect, you will see `1 failed` on `test_a4_c_ii_coinbase_replay_inflates_balance` with `[XPASS(strict)]` — that is the expected signal, not a regression. Any OTHER failure means the new check is over-firing. Investigate before proceeding:
- **`tests/test_chain.py` failures are the canary for the `Chain.validate()` revalidation bug.** `test_chain.py` calls `chain.validate()` (which loops every block through `validate_block` → `validate_block_coinbase` with `self.last_block` pinned to the tip) and has a dedicated `test_validate_block_coinbase`. If the fix used `self.last_block` as the walk's start instead of the candidate's parent, EVERY block's coinbase would be found in the chain and these tests would fail with `DuplicateCoinbaseError`. If you see that, the `parent = Block.from_db(block.prev_hash)` + `start_block=parent` wiring in Step 2 was not applied correctly — re-check it.
- If `tests/test_miller.py` fails, the chain instance may be in an unexpected state when `validate_block_coinbase` runs. Trace the chain construction path used by that test.
- If `tests/test_block.py` fails on a coinbase-related test, the check may be running where the test doesn't expect chain context.

---

## Task 5: Remove the xfail decorator + verify test passes

**Files:**
- Modify: `tests/test_verification_audit.py` — remove the `@pytest.mark.xfail(strict=True, ...)` decorator on `test_a4_c_ii_coinbase_replay_inflates_balance`.

### Step 1: Locate the decorator

```bash
grep -n -B 1 'def test_a4_c_ii_coinbase_replay' tests/test_verification_audit.py | head -5
```

Find the `@pytest.mark.xfail(reason=..., strict=True,)` decorator block above `def test_a4_c_ii_coinbase_replay_inflates_balance`. It currently spans approximately lines 239-254 (a multi-line xfail with a detailed reason string).

### Step 2: Remove the decorator block (preserve the test body)

Delete the entire `@pytest.mark.xfail(...)` decorator block — the `@pytest.mark.xfail(` line through the closing `)`. Leave the `def test_a4_c_ii_coinbase_replay_inflates_balance(...)` definition AND its docstring/body untouched.

### Step 3: Verify the test passes as a real test

```bash
uv run pytest tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance -v 2>&1 | tail -10
```

Expected: `1 passed`. (Not xfailed; not failed.)

### Step 4: Verify the full audit test module

```bash
uv run pytest tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `2 passed, 4 xfailed`. (A2.e + A4.c passing; 4 remaining demonstration tests xfailed.)

### Step 5: Verify the remaining demonstration tests still fail as expected

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -10
```

Expected: `2 passed, 4 failed`. The passing tests are A2.e and A4.c; the 4 failing tests are A1.f, A7.b, A7.e, A7.h.

If any test other than A2.e + A4.c unexpectedly passes under `--runxfail`, that's a false-positive finding — but should not happen under this task (we only modified `Chain.validate_block_coinbase`, which other audit tests don't exercise).

### Step 6: Update the test docstring to describe post-fix behavior

The current docstring describes pre-fix behavior ("Today Chain.validate_block_coinbase passes" / "Observed today: receive_block succeeds; ..."). Since the xfail decorator was removed in Step 2, the docstring should reflect what the test verifies post-fix (consistent with the A2.e remediation's round-3 fix on PR #87).

Find the docstring inside `def test_a4_c_ii_coinbase_replay_inflates_balance`:

```python
    """A4.c.ii: replaying another miller's coinbase in a fresh block.

    Pre-state: Local chain has a single mined block B_orig whose coinbase
    T_cb pays the milling wallet REWARD. T_cb is in TransactionDAO and
    m2m-associated with B_orig.
    Attack: The adversary (acting as a MILLER) builds B_adv extending
    B_orig with txns=[T_cb] only (T_cb in the last position so
    Block.regular_txns is empty and the coinbase-positional rule
    identifies T_cb as B_adv's coinbase). They mill PoW honestly and
    invoke Node.receive_block on the constructed block. Today
    Chain.validate_block_coinbase passes (correct REWARD amount, empty
    S/G/M comps match T_cb's single-outflow shape), so B_adv is persisted
    with a new block_transactions m2m row.
    Expected after remediation: Chain.validate_block_coinbase raises
    InvalidCoinbaseError (e.g., via a new DuplicateCoinbaseError) when
    the candidate coinbase's txid is already persisted in the chain's
    lineage — analogous to the inflow-uniqueness check already enforced
    by Chain.validate_txn_inflow via get_inflows_count.
    Observed today: receive_block succeeds; T_cb is m2m'd with both
    B_orig and B_adv, so the longest_chain_outflows_q join produces two
    rows of T_cb's REWARD outflow and wallet_balance double-counts.
    """
```

Replace with:

```python
    """A4.c.ii: replaying another miller's coinbase in a fresh block.

    Pre-state: Local chain has a single mined block B_orig whose coinbase
    T_cb pays the milling wallet REWARD. T_cb is in TransactionDAO and
    m2m-associated with B_orig.
    Attack: The adversary (acting as a MILLER) builds B_adv extending
    B_orig with txns=[T_cb] only (T_cb in the last position so
    Block.regular_txns is empty and the coinbase-positional rule
    identifies T_cb as B_adv's coinbase). They mill PoW honestly and
    invoke Node.receive_block on the constructed block.
    Behavior (post-remediation, verified by this test):
    Chain.validate_block_coinbase computes the candidate's parent
    (Block.from_db(block.prev_hash)) and calls
    self.get_transaction(cb.txid, start_block=parent), walking the
    parent's lineage backward via Block.from_db(prev_hash) and the
    per-block recursive CTE in BlockDAO.get_transaction_in_chain —
    scoped to this chain's lineage (and never the candidate itself, so
    Chain.validate() revalidation is unaffected), so cross-fork replay
    stays legitimate per audit Attack b. When T_cb is found in the
    lineage, DuplicateCoinbaseError (a subclass of InvalidCoinbaseError)
    is raised; receive_block propagates the failure and B_adv is not
    persisted.
    """
```

### Step 7: Verify after docstring update

```bash
uv run ruff check tests/test_verification_audit.py
uv run ruff format --check tests/test_verification_audit.py
uv run pytest tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance -v 2>&1 | tail -5
```

All exit 0; test still passes.

### Step 8: Add the cross-fork non-regression test

The new check's primary edge case is over-rejecting a coinbase that exists only on a competing fork (the structurally-legitimate cross-fork replay documented in audit Attack b). Add a non-regression test that builds a competing sibling fork, replays a canonical-fork coinbase onto it, and asserts `validate_block_coinbase` does NOT raise `DuplicateCoinbaseError`.

First, extend the test module's imports. The current import block (around tests/test_verification_audit.py:28-40) imports `Block`, `GENESIS_HASH`, `REWARD`, `Miller`, `Transaction`, etc., but NOT `Chain` or `DuplicateCoinbaseError`. Add them:

- In the `from cancelchain.chain import (...)` line, add `Chain` (alphabetically: `Chain, GENESIS_HASH, REWARD`).
- In the `from cancelchain.exceptions import (...)` block, add `DuplicateCoinbaseError` (alphabetically before `InvalidBlockError`).

The result:

```python
from cancelchain.chain import Chain, GENESIS_HASH, REWARD
from cancelchain.database import db
from cancelchain.exceptions import (
    DuplicateCoinbaseError,
    InvalidBlockError,
    InvalidCoinbaseError,
    InvalidTransactionError,
)
```

(Let `ruff format` / `ruff check --fix` settle final import ordering if it differs.)

Then append this test function to the end of `tests/test_verification_audit.py`:

```python
def test_a4_c_cross_fork_coinbase_replay_accepted(
    app, time_machine, wallet, miller_2_wallet
) -> None:
    """A4.c cross-fork non-regression: a coinbase replayed onto a
    COMPETING fork (where its txid is NOT in that fork's lineage) is
    NOT rejected by the new duplicate-coinbase check.

    Guards the new check's main edge case: over-rejecting the
    structurally-legitimate cross-fork replay documented in audit
    Attack b. The duplicate-coinbase check walks only the candidate
    block's own chain lineage (self.get_transaction → the per-block
    recursive CTE), so a coinbase that exists solely on a sibling fork
    must remain acceptable.

    Pre-state: canonical chain g1 -> block_1, where block_1's coinbase
    is T_cb. A competing sibling fork block_1p (idx 1, also extending
    g1, coinbase paying miller_2_wallet) is persisted as a parallel
    chain. T_cb is in block_1's lineage but NOT in block_1p's.
    Action: build block_2p extending block_1p whose coinbase REPLAYS
    T_cb; call chain_yp.validate_block_coinbase(block_2p).
    Expected: no DuplicateCoinbaseError — T_cb is not in the Y fork's
    lineage, so the chain-scoped get_transaction walk returns None.
    """
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)

        # Canonical chain: g1 (genesis) -> block_1 (coinbase T_cb).
        m = Miller(milling_wallet=wallet)
        g1 = m.create_block()
        m.mill_block(g1)
        assert g1.block_hash is not None
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        block_1 = m.create_block()
        m.mill_block(block_1)
        t_cb = block_1.coinbase
        assert t_cb is not None
        assert t_cb.address == wallet.address

        # Competing sibling fork: block_1p (idx 1, extends g1) with a
        # DIFFERENT coinbase (miller_2_wallet). _hostile_block links to
        # g1 at idx g1.idx+1 == 1, seals a fresh coinbase, and mills.
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        block_1p = _hostile_block(g1, miller_2_wallet)
        assert block_1p.block_hash is not None
        assert block_1p.block_hash != block_1.block_hash
        assert block_1p.idx == 1

        # Persist the sibling fork. Node.add_block's create_chain
        # fallback fires because g1 is no longer a chain tip (block_1
        # advanced the canonical chain), so block_1p lands as its own
        # ChainDAO retrievable via Chain.from_db(block_hash=...).
        #
        # LIVE-ITERATION NOTE: depth-1 sibling-fork persistence is the
        # one mechanically-tricky step in this test. The assertion below
        # is the checkpoint — if chain_yp is None when you run it,
        # block_1p did NOT persist as a retrievable parallel chain.
        # Before adjusting the test, re-read Node.add_block (the
        # create_chain fallback) and Chain.from_db(block_hash=...) to
        # confirm how a depth-1 sibling lands a ChainDAO row. Do not
        # weaken the cross-fork assertion to work around a setup issue.
        m.receive_block(block_1p.to_json())
        chain_yp = Chain.from_db(block_hash=block_1p.block_hash)
        assert chain_yp is not None, (
            'sibling fork block_1p did not persist as a retrievable '
            'chain; cross-fork test setup needs adjustment (see '
            'LIVE-ITERATION NOTE above)'
        )

        # Build block_2p extending block_1p whose coinbase REPLAYS T_cb.
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t_cb_replayed = Transaction.from_json(t_cb.to_json())
        assert t_cb_replayed.txid == t_cb.txid
        b_2p = Block()
        chain_yp.link_block(b_2p)
        b_2p.add_txn(t_cb_replayed, is_coinbase=True)
        b_2p.merkle_root = b_2p.get_merkle_root()
        b_2p.timestamp = now_iso()
        b_2p.mill()
        assert b_2p.block_hash is not None

        # The new duplicate-coinbase check must NOT reject this: T_cb is
        # on the canonical fork (block_1), not in block_2p's Y-fork
        # lineage (block_1p -> g1). get_transaction(t_cb.txid) on
        # chain_yp returns None, so no DuplicateCoinbaseError. (Other
        # validation passes too — block_reward is the constant REWARD,
        # which T_cb's outflow already pays.)
        try:
            chain_yp.validate_block_coinbase(b_2p)
        except DuplicateCoinbaseError:
            pytest.fail(
                'cross-fork coinbase replay was incorrectly rejected: '
                'T_cb is on the canonical fork, not in the Y-fork lineage'
            )
```

### Step 9: Verify the cross-fork test passes + the module counts update

```bash
uv run pytest tests/test_verification_audit.py::test_a4_c_cross_fork_coinbase_replay_accepted -v 2>&1 | tail -10
```

Expected: `1 passed`.

If it errors at the `chain_yp is not None` assertion, the sibling-fork persistence didn't land — follow the LIVE-ITERATION NOTE in the test. If it fails at `pytest.fail(...)`, the new check is genuinely over-rejecting cross-fork replay — that is a real bug in the Task 4 fix (the walk is not chain-scoped as intended); re-inspect `Chain.get_transaction` / `BlockDAO.get_transaction_in_chain` before proceeding.

Then re-check the whole audit module:

```bash
uv run pytest tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `3 passed, 4 xfailed` (A2.e + A4.c + the new cross-fork test pass; A1.f, A7.b, A7.e, A7.h xfail).

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `3 passed, 4 failed` (the 3 passing tests stay passing under --runxfail; the 4 xfail findings fail).

```bash
uv run ruff check tests/test_verification_audit.py
uv run ruff format --check tests/test_verification_audit.py
uv run mypy 2>&1 | tail -3
```

All exit 0. If `ruff format --check` reports a diff (likely on the new import line / test body), run `uv run ruff format tests/test_verification_audit.py`.

---

## Task 6: Update the audit doc to reflect A4.c closure

**Files:**
- Modify: `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` — three edits.

### Step 1: Remove A4.c from the Findings table

Find the row starting with `| A4.c | Medium |`. The exact text is:

```
| A4.c | Medium | MILLER-role coinbase txn replay creates duplicate `block_transactions` m2m row → `wallet_balance` inflated by REWARD per replay. | <remediation sketch text> | `test_a4_c_ii_coinbase_replay_inflates_balance` |
```

Delete the entire row.

### Step 2: Update §Adversary 4 → Attack c.ii outcome

Find the `**Outcome:** ACCEPTED at step 4` line in §Adversary 4 → Attack c.ii (around audit doc line 596). Replace the entire block starting with `**Outcome:** ACCEPTED at step 4` and continuing through `**Demonstration test:** test_a4_c_ii_coinbase_replay_inflates_balance in tests/test_verification_audit.py.` with:

```markdown
**Outcome:** REJECTED at the new chain-lineage check inside `Chain.validate_block_coinbase`. Before computing the reward, the method now derives the candidate's parent (`parent = Block.from_db(block.prev_hash)`) and calls `self.get_transaction(cb.txid, start_block=parent)`, walking the parent's lineage backward via `Block.from_db(prev_hash)` and the per-block recursive CTE in `BlockDAO.get_transaction_in_chain`. Starting from the parent (rather than `self.last_block`) keeps the candidate block itself out of the walk, so `Chain.validate()` full-chain revalidation is unaffected. If the txid is found in this chain's lineage, `DuplicateCoinbaseError(InvalidCoinbaseError)` is raised. Cross-fork legitimacy (Attack b's case) is preserved because the walk is chain-scoped. Fixed by the impl PR following from `docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md`.

**Result:** Validation correctly rejects (post-remediation). No finding.
```

### Step 3: Update the Executive summary count

Find the Executive summary at the top of the audit doc. The current state (post-A2.e closure on PR #87) reads something like:

> Six findings were originally confirmed (all Medium or Low; no Critical or High). One has since been remediated (A2.e); five remain open.

Update to:

> Six findings were originally confirmed (all Medium or Low; no Critical or High). Two have since been remediated (A2.e, A4.c); four remain open.

If there's a severity-breakdown line that says "0 Critical / 0 High / 1 Medium / 4 Low (post-A2.e)", update it to "0 Critical / 0 High / 0 Medium / 4 Low (post-A4.c)". (A4.c was the last Medium; after this PR no Mediums remain.)

### Step 4: Verify structural counts

```bash
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
grep -c '^\*\*Finding A' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
```

Expected: both = 4. (Was 5 post-A2.e; A4.c removal brings it to 4.)

---

## Task 7: Move A4.c in ROADMAP from open to closed

**Files:**
- Modify: `docs/superpowers/ROADMAP.md` — move A4.c entry; add closed entry.

### Step 1: Remove A4.c from the open "Audit remediation" list

Find the `## Audit remediation — verification pipeline findings (PR #84)` section. The current state (post-A2.e closure) lists items 1-5; A4.c is item 1 (next-priority Medium per the audit's Recommendations).

Remove item 1 (the A4.c bullet). Renumber items 2-5 to become items 1-4.

### Step 2: Add A4.c to "Closed items (historical reference)"

In the `## Closed items (historical reference)` section at the end, add this new line (use placeholders for the PR numbers — Task 8 fills them in):

```markdown
- ✅ **Audit finding A4.c — coinbase-txid replay inflates miller `wallet_balance`** — closed by docs PR [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (spec + plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). Added a chain-lineage uniqueness check on the candidate coinbase's `txid` inside `Chain.validate_block_coinbase`: it derives the candidate's parent (`parent = Block.from_db(block.prev_hash)`) and calls `self.get_transaction(cb.txid, start_block=parent)`, walking the parent's lineage backward — chain-scoped, so cross-fork replay stays legitimate per audit Attack b, and starting from the parent keeps the candidate out of the walk so `Chain.validate()` revalidation is unaffected. When the txid is found, a new `DuplicateCoinbaseError(InvalidCoinbaseError)` is raised. Test went from `@pytest.mark.xfail(strict=True)` to a real pass. Originated as finding A4.c (Medium) in the 2026-05-29 verification pipeline audit; closing this entry brings audit severity to 0 Critical / 0 High / 0 Medium / 4 Low.
```

### Step 3: Verify

```bash
grep -c '^## ' docs/superpowers/ROADMAP.md
grep -c '^- ✅' docs/superpowers/ROADMAP.md
grep 'A4.c' docs/superpowers/ROADMAP.md
```

Expected: `^## ` = 6 (Phase 6.7, Phase 7+ ×2, Audit remediation, Future audit, Closed items); `^- ✅` = 10 (was 9 post-A2.e closure + 1 new); `A4.c` appears in the Closed items section, not in the open Audit remediation list.

---

## Task 8: Pre-commit gates + commit + push + open impl PR

**Files:** all 6 modified files from Tasks 3-7 (`exceptions.py`, `chain.py`, `test_verification_audit.py`, audit doc, ROADMAP).

### Step 1: Full gate sweep

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest 2>&1 | tail -3
```

All exit 0. Pytest shows `239 passed, 4 xfailed, 1 skipped`.

### Step 2: --runxfail verification

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `3 passed, 4 failed`. A2.e + A4.c + the cross-fork non-regression test pass; remaining 4 audit findings (A1.f, A7.b, A7.e, A7.h) still demonstrate gaps.

### Step 3: DB check gate

```bash
TMPDB=$(mktemp --suffix=.db)
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db check
rm -f "${TMPDB}"
```

`db upgrade` reports OK; `db check` reports "No differences detected." No model changes in this PR.

### Step 4: Commit

```bash
git add src/cancelchain/exceptions.py src/cancelchain/chain.py tests/test_verification_audit.py docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
fix(a4c): reject same-chain coinbase replay in validate_block_coinbase

Closes audit finding A4.c (Medium): a MILLER-role adversary could
previously mine a block whose coinbase was a verbatim replay of any
prior block's coinbase transaction, appending a duplicate
block_transactions m2m row that inflated the original miller's
longest-chain wallet_balance by one REWARD per replay.

Adds a chain-lineage uniqueness check on the candidate coinbase's
txid inside Chain.validate_block_coinbase. The check derives the
candidate's parent (parent = Block.from_db(block.prev_hash)) and calls
self.get_transaction(cb.txid, start_block=parent), walking the
parent's lineage backward via Block.from_db(prev_hash) and the
per-block recursive CTE in BlockDAO.get_transaction_in_chain. Starting
from the parent (not self.last_block) keeps the candidate block out of
the walk, so Chain.validate() full-chain revalidation does not falsely
flag every coinbase. If the txid is found, a new
DuplicateCoinbaseError(InvalidCoinbaseError) is raised. The walk is
chain-scoped, so cross-fork legitimacy (audit Attack b's case) is
preserved.

The check fires before the existing reward check. Order is
behaviorally irrelevant (a duplicate cb would also pass the reward
check, since it was previously valid), but surfacing the more
fundamental issue first gives clearer error messages.

Test went from @pytest.mark.xfail(strict=True) on
test_a4_c_ii_coinbase_replay_inflates_balance to a real pass; full
suite is 239 passed, 4 xfailed, 1 skipped (A4.c un-xfailed +
a new cross-fork non-regression test). Audit doc Findings table
updated (4 findings remaining); ROADMAP A4.c entry moved from open
to closed. With A4.c closed, audit severity reaches
0 Critical / 0 High / 0 Medium / 4 Low.

Design: docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md
Plan: docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 5: Push

```bash
git push -u origin fix/a4c-coinbase-uniqueness
```

### Step 6: Open the impl PR

```bash
gh pr create --base main --title "fix(a4c): reject same-chain coinbase replay in validate_block_coinbase" --body "$(cat <<'EOF'
## Summary

Closes audit finding A4.c (Medium). \`Chain.validate_block_coinbase\` now rejects a block whose coinbase \`txid\` is already persisted in the chain's lineage.

A MILLER-role adversary can no longer mine a block whose coinbase is a verbatim replay of any prior block's coinbase to inflate the original miller's \`wallet_balance\`.

## Implementation notes

- **Chain-lineage check** via \`self.get_transaction(cb.txid, start_block=parent)\` where \`parent = Block.from_db(block.prev_hash)\`. Starting from the explicit parent (not the \`self.last_block\` default) keeps the candidate block out of the walk — important because \`Chain.validate()\` revalidation calls this with \`self.last_block\` pinned to the chain tip while validating interior blocks; a default-start walk would include the candidate and falsely flag every coinbase. The walk follows \`Block.from_db(prev_hash)\` and defers to \`BlockDAO.get_transaction_in_chain\` (the per-block recursive CTE \`_block_chain\`). Same chain-scoping pattern as the existing inflow uniqueness check in \`Chain.validate_txn_inflow\`. Genesis has no findable parent → check skipped.
- **New \`DuplicateCoinbaseError(InvalidCoinbaseError)\`** exception class, mirroring the existing \`InvalidCoinbaseErrorRewardError\` pattern. The xfail demonstration test asserts \`pytest.raises(InvalidCoinbaseError)\`, which matches the new subclass via inheritance — no test body change needed beyond removing the decorator.
- **Cross-fork legitimacy preserved.** Audit Attack b documented that cross-fork transaction replay (including coinbase) is structurally legitimate — each chain's per-block CTE keeps fork state independent. The new check doesn't change that: a coinbase that exists only on a stale fork is not found by a walk through the current chain's lineage.
- **Check fires before the reward check.** Order is behaviorally irrelevant (a duplicate cb would also pass the reward check, since it was previously valid), but the duplicate-coinbase error is more diagnostic.

## Documentation updates

- Audit doc Findings table: A4.c row removed; per-attack outcome in §Adversary 4 → Attack c.ii updated from "ACCEPTED at step 4" to "REJECTED at the new chain-lineage check ..." with a fix note; Executive summary updated to reflect 4 open findings + 2 closed.
- ROADMAP: A4.c moved from open "Audit remediation" list to "Closed items".

## Audit severity (post-fix)

| Severity | Open | Closed |
|---|---|---|
| Critical | 0 | 0 |
| High | 0 | 0 |
| Medium | 0 | 2 (A2.e, A4.c) |
| Low | 4 | 0 |

## Out of scope

- Cross-fork coinbase replay (Attack b's case) — structurally legitimate per the audit; the chain-lineage walk preserves it.
- Regular-transaction txid uniqueness beyond inflow consumption — already enforced via \`validate_txn_inflow\` + \`get_inflows_count\`.
- Reorg double-spend (A4.d note + A5.a/b cluster) — canonical PoW property; operator confirmation-depth guidance, not a validation-pipeline fix.

## Test plan

- [x] All 5 CI gates clean (ruff check + ruff format + pytest + mypy + db check).
- [x] \`uv run pytest 2>&1 | tail -3\` shows \`239 passed, 4 xfailed, 1 skipped\` (was \`237 passed, 5 xfailed, 1 skipped\` pre-fix; +1 A4.c un-xfailed, +1 new cross-fork non-regression test).
- [x] \`uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3\` shows \`3 passed, 4 failed\` (A2.e + A4.c + cross-fork test pass; remaining 4 findings still demonstrate gaps).
- [ ] CI green on 3.12 and 3.13.
- [ ] Docker builder build (\`docker build --target builder -t cc-a4c-final .\`) succeeds.

Design: \`docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md\`
Plan: \`docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Capture the PR number from the output URL.

### Step 7: Fill in the impl PR number in the ROADMAP

After `gh pr create` returns the PR URL, extract the PR number. Then update the ROADMAP placeholders in the just-committed file:

```bash
PR_IMPL_N=<N from gh pr create output>
PR_DOCS_N=<N from the merged docs PR — already in main's git log>
sed -i "s|#<N_impl>|#${PR_IMPL_N}|g; s|/pull/<N_impl>|/pull/${PR_IMPL_N}|g; s|#<N_docs>|#${PR_DOCS_N}|g; s|/pull/<N_docs>|/pull/${PR_DOCS_N}|g" docs/superpowers/ROADMAP.md
git diff docs/superpowers/ROADMAP.md
```

Verify the diff shows ONLY the placeholder substitution (no other changes).

Add as a NEW commit (per cancelchain memory: don't amend):

```bash
git add docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
fix(a4c): fill in PR numbers in ROADMAP A4.c closed entry

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push
```

### Step 8: Stop — controller handles wor + mwg

After the push, run `git log --oneline -3` and report.

---

## Task 9: Phase verification (acceptance)

After the impl PR merges to main.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

Expected: top commits include the impl PR squash + the docs PR squash.

- [ ] **Step 2: Source changes present**

```bash
grep -n 'class DuplicateCoinbaseError' src/cancelchain/exceptions.py
grep -n 'DuplicateCoinbaseError\|get_transaction(cb.txid)' src/cancelchain/chain.py
```

Expected: one `DuplicateCoinbaseError` class definition in `exceptions.py`; `DuplicateCoinbaseError` referenced in `chain.py` (import + raise); `get_transaction(cb.txid)` call in `chain.py`.

- [ ] **Step 3: xfail decorator removed**

```bash
grep -B 5 'def test_a4_c_ii_coinbase_replay_inflates_balance' tests/test_verification_audit.py | head -10
```

Expected: the 5 lines preceding the `def` are no longer `@pytest.mark.xfail(...)` content (should be the closing `"""` of the prior test's docstring + a blank line + the new `def`).

- [ ] **Step 4: pytest reports the new baseline**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: `239 passed, 4 xfailed, 1 skipped`.

- [ ] **Step 5: `--runxfail` confirms remaining findings still demonstrate gaps**

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `3 passed, 4 failed` (A2.e + A4.c + cross-fork test pass; the 4 remaining findings fail).

- [ ] **Step 6: Hard CI gates pass**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

All exit 0.

- [ ] **Step 7: Audit doc + ROADMAP reflect closure**

```bash
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
grep -c '^\*\*Finding A' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
grep 'A4.c' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md | head -2
grep 'A4.c' docs/superpowers/ROADMAP.md
```

Expected: Findings table = 4; Finding entries = 4; A4.c still appears in audit doc (in §Adversary 4 → Attack c.ii, now marked REJECTED); A4.c in ROADMAP appears in the `Closed items` section with PR links.

- [ ] **Step 8: Docker build smoke**

```bash
docker build --target builder -t cc-a4c-final .
```

Succeeds.

- [ ] **Step 9: Acceptance complete**

If Steps 1-8 all pass, A4.c remediation is done. With A4.c closed, the audit severity distribution is 0 Critical / 0 High / 0 Medium / 4 Low. Next-priority remediation per the audit's Recommendations is A7.b (alternate-genesis admission — Low, two-for-one with A7.j).

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 8) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id`. **Per `project_copilot_auto_rereview`, auto-rereview on cancelchain is inconsistent in practice — the controller asks the user to click "Re-request review" if the 10-min polling loop times out.**
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend — per cancelchain memory).

---

## Risks and watchpoints

### Risk: `DuplicateCoinbaseError` not imported into `chain.py`

Task 4 Step 1 checks the import surface. If `DuplicateCoinbaseError` is missing from the import block, Task 4 Step 3 adds it. If you forget the import, Python raises `NameError` at runtime — but ruff/mypy should catch it at Task 4 Step 4. Mitigation: Step 4's `mypy 2>&1 | tail -3` is the safety net.

### Risk: A test elsewhere in the suite incidentally constructs a duplicate coinbase

If `tests/test_chain.py`, `tests/test_block.py`, `tests/test_models.py`, `tests/test_miller.py`, or any other test in the suite happens to validate a block whose coinbase txid was already in the chain's lineage, that test would start failing under the new check. Mitigation: Task 4 Step 7 runs the full test suite; any regression surfaces here. If a regression appears, trace whether the test is constructing an INTENTIONAL duplicate (rare; would need a code change in the test) or whether the chain instance is misaligned (`self.block_hash != block.prev_hash`, indicating the new check is over-firing — see Spec Risk 2).

### Risk: `Chain.get_transaction` walk performance under deep-reorg or deep-fill_chain

For typical receive_block (single-block extension), the walk hits BlockDAO at the parent — one DB lookup. For `fill_chain` (batch extension), the walk may traverse multiple in-flight in-session blocks before hitting persistence; under `commit=False` (per the A2.e remediation), the walk does see flushed-but-uncommitted blocks via the same session. Cost is equivalent to the existing per-txn `get_transaction(outflow_txid, start_block=block)` calls in `validate_txn_inflow`. Mitigation: no new bench gate needed; the A2.e implementation didn't add a bench gate either and the bench remained ~0.25 ms/step.

### Risk: `Chain.validate()` revalidation false positive (resolved by explicit-parent start)

`Chain.validate()` revalidates every existing block by looping `self.blocks` through `validate_block` with `self.last_block` pinned to the chain tip. The original design (default `start_block=self.last_block`) would have walked from the tip — including the interior candidate block itself — and found each block's own coinbase, raising `DuplicateCoinbaseError` for every block and breaking `cancelchain validate`. **Resolved** by computing `parent = Block.from_db(block.prev_hash)` and passing `start_block=parent`: the walk searches only blocks strictly upstream of the candidate, regardless of what `self.last_block` is. The check no longer depends on the `self.block_hash == block.prev_hash` invariant at all. Mitigation that this stays fixed: `tests/test_chain.py` calls `chain.validate()` and has `test_validate_block_coinbase`; any reintroduction of the default-start bug fails those tests (Task 4 Step 7).

### Risk: the docs PR (Task 1) takes longer than expected to review/merge

The impl PR (Tasks 2-8) is blocked on the docs PR. If the docs PR sits unreviewed, the implementer can still start Task 2 (baseline + re-read — no edits) but should not push the impl branch until the docs PR merges, to avoid PR ordering confusion.
