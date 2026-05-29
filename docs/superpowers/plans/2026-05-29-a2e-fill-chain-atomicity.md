# A2.e — `Node.fill_chain` atomicity remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Node.fill_chain`'s apply loop atomic — a validation failure on any block rolls back every earlier block's persistence within the same `fill_chain` call. Closes audit finding A2.e; the demonstration test transitions from `@pytest.mark.xfail(strict=True)` to a real pass.

**Architecture:** Deferred-commits approach. Add a keyword-only `commit: bool = True` parameter to `BlockDAO.commit()`, `Block.to_db()`, `Chain.to_db()`, `Chain.add_block()`, `Node.add_block()`, and `Node.create_chain()`. (`Node.create_chain` must be in the chain because `Node.add_block` falls back to it whenever the block's prev_hash exists as a Block row but isn't currently a Chain tip — without threading `commit` through, the fallback path would commit inside `fill_chain`'s loop and defeat atomicity.) When `commit=False`, the session is flushed (not committed) so flushed rows stay in the autobegun root transaction. `Node.fill_chain` passes `commit=False` per block, then issues a single `db.session.commit()` after the loop succeeds (or `db.session.rollback()` on exception). `new_block_signal.send` is deferred to a second loop after the explicit commit so signals fire only for confirmed-persisted blocks.

**Note on initial design:** The brainstorm originally picked a SAVEPOINT wrap (`db.session.begin_nested()`), but PR #86 round-2 Copilot review correctly surfaced that SQLAlchemy 2.0's `Session.commit()` "commits the outermost database transaction unconditionally, automatically releasing any SAVEPOINTs in effect" (per the docstring; verified in source as `trans.commit(_to_root=True)`). So the per-block `db.session.commit()` inside `Block.to_db()` / `Chain.to_db()` would commit the root and release the savepoint on the first iteration, defeating atomicity. The deferred-commits approach avoids the issue by making `commit()` calls conditional.

**Tech Stack:** Python 3.12 + SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1 + SQLite (test) / production-DB. The demonstration test uses `pytest` + `time_machine` + existing `tests/conftest.py` fixtures.

The companion design spec is `docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md`.

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Verification audit merged. Verify with `git log --oneline -3 main` showing `e163adf audit(verification): threat-modeled audit findings + demonstration tests (#84)` near the top.
- ROADMAP cleanup merged. Verify with `git log --oneline -2 main` showing `eb360f7 docs(roadmap): close verification audit, add 6 remediation items + auth audit (#85)` as the head.
- The branch `docs/a2e-fill-chain-atomicity` exists locally with one commit:
  - `aee422d docs(a2e): add fill_chain atomicity remediation design spec`
  This plan adds a second commit on that branch (the plan file itself) and ships both as the docs PR.
- CI hard-gates (per `.github/workflows/tests.yml`): `ruff check`, `ruff format --check`, `pytest`, `mypy`, and `cancelchain db upgrade` + `cancelchain db check`.
- Test baseline: **236 passed, 6 xfailed, 1 skipped** (post-audit). After this PR, expect **237 passed, 5 xfailed, 1 skipped** (A2.e moves from xfail to pass).
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent. Auto-rereview on cancelchain is inconsistent in practice (per `project_copilot_auto_rereview`) — the controller asks the user to click "Re-request review" if the polling loop times out.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-29-a2e-fill-chain-atomicity.md` (this file) + spec already on branch |
| 2 | impl PR | branch off main; verify baseline; targeted re-read |
| 3 | impl PR | `src/cancelchain/models.py` — `BlockDAO.commit(*, commit: bool = True)` |
| 3 | impl PR | `src/cancelchain/block.py` — `Block.to_db(*, commit: bool = True)` |
| 3 | impl PR | `src/cancelchain/chain.py` — `Chain.to_db(*, commit: bool = True)` and `Chain.add_block(*, commit: bool = True)` |
| 3 | impl PR | `src/cancelchain/node.py` — `Node.add_block(*, commit: bool = True)` and `Node.fill_chain` apply-loop refactor |
| 4 | impl PR | `src/cancelchain/signals.py` — multi-line `#` comment documenting deferred-batch semantics |
| 5 | impl PR | `tests/test_verification_audit.py` — remove xfail decorator |
| 6 | impl PR | `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` — close A2.e in 3 spots |
| 7 | impl PR | `docs/superpowers/ROADMAP.md` — move A2.e to closed |
| 8 | impl PR | run gates + single commit + push + open PR |
| 9 | acceptance | none (verification only) |

The impl PR is a single commit (the change is one logical unit — fix + housekeeping). No new files; all 5 modified files are existing.

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** The design spec is committed on `docs/a2e-fill-chain-atomicity` (`aee422d`). This task adds the implementation plan as a second commit and ships both as one docs PR.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/a2e-fill-chain-atomicity`; spec file is tracked; commit count above main is `1`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-29-a2e-fill-chain-atomicity.md
git status docs/superpowers/plans/
```

Expected: file exists; shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-29-a2e-fill-chain-atomicity.md
git commit -m "$(cat <<'EOF'
docs(a2e): add fill_chain atomicity remediation implementation plan

Plan executes the A2.e remediation design from
2026-05-29-a2e-fill-chain-atomicity-design.md. Single impl PR
making Node.fill_chain's apply loop atomic via deferred commits,
deferring new_block_signal emission to post-commit, removing the
xfail decorator on the demonstration test, and updating the audit
doc + ROADMAP to reflect A2.e closure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/a2e-fill-chain-atomicity
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/a2e-fill-chain-atomicity --title "docs(a2e): fill_chain atomicity remediation design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the A2.e remediation design spec (\`docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md\`).
- Adds the A2.e remediation implementation plan (\`docs/superpowers/plans/2026-05-29-a2e-fill-chain-atomicity.md\`).
- No code changes.

Remediates audit finding A2.e (Medium): make \`Node.fill_chain\`'s apply loop atomic via deferred commits (each per-block persistence flushes instead of committing; a single \`db.session.commit()\` after the loop persists all blocks atomically). The hostile-peer partial-fork-prefix adoption attack documented in the audit's per-adversary Section 5.2 (Adversary 2, Attack e) is closed by atomic apply.

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

**Files:** No edits. Branch off main; verify baseline gates pass; read the to_db() / Node.add_block surface to confirm the spec's "no changes needed" claim.

### Step 1: Branch off main + baseline gates

After the docs PR merges:

```bash
git checkout main && git pull --ff-only
git checkout -b fix/a2e-fill-chain-atomicity
git log --oneline -1
```

Expected: top commit is the docs PR squash (e.g., `<sha> docs(a2e): fill_chain atomicity remediation design + plan (#<N>)`).

Confirm baseline gates are green BEFORE any edit:

```bash
uv run mypy
uv run ruff check src tests
uv run pytest 2>&1 | tail -3
```

Expected: mypy clean; ruff clean; pytest `236 passed, 6 xfailed, 1 skipped`.

If baseline gates aren't clean, STOP and report BLOCKED — don't proceed.

### Step 2: Confirm the A2.e demonstration test is currently xfail

```bash
uv run pytest tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip 2>&1 | tail -5
```

Expected: `1 xfailed`.

```bash
uv run pytest --runxfail tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip 2>&1 | tail -10
```

Expected: `1 failed` — the test genuinely demonstrates the gap today.

### Step 3: Targeted re-read of `to_db()` / DAO commit / `Node.add_block`

Read these files with the deferred-commits-compatibility lens.

```bash
grep -n -B 2 -A 5 'def to_db' src/cancelchain/block.py src/cancelchain/chain.py
grep -n -B 2 -A 5 'def add_block' src/cancelchain/chain.py src/cancelchain/node.py
grep -n -B 2 -A 5 'def commit' src/cancelchain/models.py
grep -n 'sync_longest_chain_blocks' src/cancelchain/models.py src/cancelchain/chain.py
```

Confirm:
- `Block.to_db()` (block.py:342) calls `self.to_dao().commit()` → `BlockDAO.commit()` which lives inside `class BlockDAO` (defined at `models.py:251`). The actual `def commit(self)` is around `models.py:335`. Use `grep -n -B 30 'def commit' src/cancelchain/models.py | grep -E 'class |def commit'` to map each `def commit` to its enclosing class — only modify the one inside `BlockDAO`. **Do NOT modify TransactionDAO.commit (line ~97) or ChainDAO.commit (line ~793) or any other DAO commit.** `BlockDAO.commit` does `db.session.add(self); db.session.commit()`. Will be modified to accept a keyword-only `commit: bool = True` parameter.
- `Chain.to_db()` (chain.py:564) does `db.session.add(dao); db.session.flush(); self.cid = dao.id; dao.sync_longest_chain_blocks(); db.session.commit()`. The flush is required before `sync_longest_chain_blocks()` so the dao gets an ID. Will be modified to make the trailing `db.session.commit()` conditional.
- `Chain.add_block()` (chain.py:153) does `self.validate_block(block); block.to_db(); self.block_hash = block.block_hash`. Will be modified to pass `commit` through to `block.to_db()`.
- `Node.add_block()` (node.py:181-194) catches `SQLAlchemyError` and calls `rollback_session()`. Has the create_chain fallback at line 187 (`chain = self.create_chain(block=block)`). Will be modified to pass `commit` through to `chain.add_block()`, `self.create_chain()`, and `chain.to_db()`.
- `Node.create_chain()` (node.py:196-201) calls `chain.add_block(block)` internally. Will be modified to accept and forward `commit`. Without this, the create_chain fallback path would commit inside the loop even when `fill_chain` requests `commit=False`.
- `dao.sync_longest_chain_blocks()` — confirm it only reads/writes session-local state (uses `db.session.execute(...)` queries that see uncommitted-but-flushed rows). If it makes a cross-session assumption, behavior could differ under `commit=False`. **This is the spec's Risk 2.**

If any of these don't hold (e.g., `sync_longest_chain_blocks` opens a fresh connection or reads from a different session), STOP and report DONE_WITH_CONCERNS noting the discrepancy — the design may need revision.

---

## Task 3: Apply the deferred-commits refactor

**Files:**
- Modify: `src/cancelchain/models.py` — `BlockDAO.commit()`.
- Modify: `src/cancelchain/block.py` — `Block.to_db()`.
- Modify: `src/cancelchain/chain.py` — `Chain.to_db()` and `Chain.add_block()`.
- Modify: `src/cancelchain/node.py` — `Node.add_block()` and `Node.fill_chain` apply loop.

All `commit` parameters are keyword-only (`*, commit: bool = True`) so existing callers are unchanged.

### Step 1: Verify `db` is imported in `node.py`

```bash
grep -n 'from cancelchain.database\|import db' src/cancelchain/node.py | head -5
```

The new `fill_chain` body calls `db.session.commit()` / `db.session.rollback()` directly. If `db` is not imported at module level, add it in Step 6:

```python
from cancelchain.database import db
```

### Step 2: Modify `BlockDAO.commit()` in `models.py`

**Critical:** there are 8 `def commit(self) -> None:` methods in `models.py`, one per DAO. They are at lines 97 (TransactionDAO), 335 (BlockDAO), 793 (ChainDAO), 854 (PendingTxnDAO), 909 (PendingIOflowDAO), 929 (ChainFill), 955 (ChainFillBlock), and 987 (ApiToken). **Only modify the one inside `class BlockDAO` (around line 335 — between the BlockDAO class definition at line 251 and the next class LongestChainBlockDAO at line 466).**

Confirm with:

```bash
grep -n -B 30 'def commit' src/cancelchain/models.py | grep -E 'class |def commit' | head -20
```

The output associates each `def commit` with its enclosing class. Pick the one preceded by `class BlockDAO`.

Find (inside `class BlockDAO`):

```python
    def commit(self) -> None:
        db.session.add(self)
        db.session.commit()
```

Replace with:

```python
    def commit(self, *, commit: bool = True) -> None:
        db.session.add(self)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
```

Leave the other 7 DAO `commit()` methods unchanged. The deferred-commits path only flows through `BlockDAO.commit()` from `fill_chain`.

### Step 3: Modify `Block.to_db()` in `block.py`

Find (around `block.py:342`):

```python
    def to_db(self) -> None:
        self.to_dao().commit()
```

Replace with:

```python
    def to_db(self, *, commit: bool = True) -> None:
        self.to_dao().commit(commit=commit)
```

### Step 4: Modify `Chain.to_db()` and `Chain.add_block()` in `chain.py`

Find `Chain.to_db()` (around `chain.py:564`):

```python
    def to_db(self) -> None:
        dao = self.to_dao(create=True)
        db.session.add(dao)
        db.session.flush()
        self.cid = dao.id
        dao.sync_longest_chain_blocks()
        db.session.commit()
```

Replace with:

```python
    def to_db(self, *, commit: bool = True) -> None:
        dao = self.to_dao(create=True)
        db.session.add(dao)
        db.session.flush()
        self.cid = dao.id
        dao.sync_longest_chain_blocks()
        if commit:
            db.session.commit()
```

Then find `Chain.add_block()` (around `chain.py:153`):

```python
    def add_block(self, block: Block) -> None:
        self.validate_block(block)
        block.to_db()
        self.block_hash = block.block_hash
```

Replace with:

```python
    def add_block(self, block: Block, *, commit: bool = True) -> None:
        self.validate_block(block)
        block.to_db(commit=commit)
        self.block_hash = block.block_hash
```

### Step 5: Modify `Node.add_block()` AND `Node.create_chain()` in `node.py`

These two methods must change together — `Node.add_block` calls `self.create_chain(...)` as a fallback when no Chain currently has the block's prev_hash as its tip, and `create_chain` internally calls `chain.add_block(...)`. If only `Node.add_block` is updated, the fallback path still commits inside the loop and breaks atomicity (Copilot's PR #86 round-3 finding).

Find `Node.add_block` (around `node.py:181-194`):

```python
    def add_block(self, block: Block) -> Block | None:
        try:
            chain = Chain.from_db(block_hash=block.prev_hash)
            if chain:
                chain.add_block(block)
            else:
                chain = self.create_chain(block=block)
            chain.to_db()
        except SQLAlchemyError:
            rollback_session()
            if not (block.block_hash and Block.from_db(block.block_hash)):
                raise
            block = None  # type: ignore[assignment]
        return block
```

Replace with:

```python
    def add_block(self, block: Block, *, commit: bool = True) -> Block | None:
        try:
            chain = Chain.from_db(block_hash=block.prev_hash)
            if chain:
                chain.add_block(block, commit=commit)
            else:
                chain = self.create_chain(block=block, commit=commit)
            chain.to_db(commit=commit)
        except SQLAlchemyError:
            rollback_session()
            if not (block.block_hash and Block.from_db(block.block_hash)):
                raise
            block = None  # type: ignore[assignment]
        return block
```

Then find `Node.create_chain` (around `node.py:196-201`):

```python
    def create_chain(self, block: Block | None = None) -> Chain:
        block_hash = block.prev_hash if block is not None else None
        chain = Chain(block_hash=block_hash)
        if block is not None:
            chain.add_block(block)
        return chain
```

Replace with:

```python
    def create_chain(
        self, block: Block | None = None, *, commit: bool = True
    ) -> Chain:
        block_hash = block.prev_hash if block is not None else None
        chain = Chain(block_hash=block_hash)
        if block is not None:
            chain.add_block(block, commit=commit)
        return chain
```

This ensures the create-chain fallback path respects `commit=False` end-to-end: `fill_chain` → `Node.add_block(commit=False)` → `Node.create_chain(commit=False)` → `chain.add_block(commit=False)` → `Block.to_db(commit=False)` → `BlockDAO.commit(commit=False)` → `db.session.flush()`. No early commit anywhere along the path.

### Step 6: Refactor `Node.fill_chain` apply loop

Locate the apply loop (around `node.py:344-352` — verify with `grep -n 'progress_switch' src/cancelchain/node.py`):

```python
            progress_switch()
            for chain_fill_block in chain_fill.blocks:
                if chain_fill_block.block_json is None:
                    continue
                block = Block.from_json(chain_fill_block.block_json)
                self.add_block(block)
                new_block_signal.send(self, block=block)
                progress_next()
            return True
```

Replace with:

```python
            progress_switch()
            # Atomic apply: pass commit=False to each per-block add_block so
            # rows are flushed (not committed) into the autobegun root
            # transaction. A single db.session.commit() after the loop
            # persists all blocks atomically; db.session.rollback() on
            # exception undoes every flushed block. Closes audit finding
            # A2.e (hostile-peer partial-fork-prefix adoption).
            applied: list[Block] = []
            try:
                for chain_fill_block in chain_fill.blocks:
                    if chain_fill_block.block_json is None:
                        continue
                    block = Block.from_json(chain_fill_block.block_json)
                    self.add_block(block, commit=False)
                    applied.append(block)
                    progress_next()
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            # Post-commit — fire signals only for confirmed-persisted
            # blocks, in apply order.
            for block in applied:
                new_block_signal.send(self, block=block)
            return True
```

If `db` is not imported at module level (per Step 1 check), add at the top of `src/cancelchain/node.py` in the cancelchain imports block:

```python
from cancelchain.database import db
```

### Step 7: Confirm syntax + types

```bash
uv run ruff check src/cancelchain/node.py src/cancelchain/chain.py src/cancelchain/block.py src/cancelchain/models.py
uv run ruff format --check src/cancelchain/node.py src/cancelchain/chain.py src/cancelchain/block.py src/cancelchain/models.py
uv run mypy 2>&1 | tail -5
```

All three exit 0. If `ruff format --check` reports a diff, run `uv run ruff format` on the affected files.

### Step 8: Run the A2.e demonstration test under `--runxfail` to verify the fix

The test still carries `@pytest.mark.xfail(strict=True)`. Under `--runxfail`, xfail is ignored — so a passing test means the fix works.

```bash
uv run pytest --runxfail tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip 2>&1 | tail -10
```

Expected: `1 passed`. (Was `1 failed` in Task 2 Step 2.)

If the test still fails, the fix didn't take. Re-check the `commit=False` propagation: each level (`Node.add_block` → `chain.add_block` → `block.to_db` → `BlockDAO.commit`) must forward the parameter. Also verify `Chain.to_db(commit=False)` correctly skips the `db.session.commit()` call.

### Step 9: Run the test under the normal pytest invocation

With the xfail decorator still in place but the fix applied, strict-mode kicks in:

```bash
uv run pytest tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip 2>&1 | tail -10
```

Expected: `1 failed — [XPASS(strict)]`. That's the signal that says "this xfail no longer demonstrates a gap; remove the decorator." Task 4 does the removal.

### Step 10: Regression check — full test suite

```bash
uv run pytest 2>&1 | tail -3
```

Expected: still `236 passed, 6 xfailed, 1 skipped` (the xfail decorator on A2.e is still in place at this point; we'll remove it in Task 4). All single-block paths should continue passing since they use the default `commit=True`. If any test that exercises `Chain.add_block` / `Node.add_block` / `Block.to_db` regresses, the deferred-commits parameter wasn't propagated correctly — re-check Steps 2-5.

---

## Task 4: Remove the xfail decorator + verify test passes

**Files:**
- Modify: `tests/test_verification_audit.py` — remove the `@pytest.mark.xfail(strict=True, ...)` decorator on `test_a2_e_partial_chain_adoption_via_invalid_tip`.

### Step 1: Locate the decorator

```bash
grep -n -B 1 'test_a2_e_partial_chain_adoption' tests/test_verification_audit.py | head -5
```

Find the decorator. It looks like:

```python
@pytest.mark.xfail(
    reason=(
        'Audit finding A2.e — severity Medium — Node.fill_chain '
        ...
    ),
    strict=True,
)
def test_a2_e_partial_chain_adoption_via_invalid_tip(
    app, time_machine, wallet
) -> None:
```

### Step 2: Remove the decorator block (preserve the test body)

Delete the entire `@pytest.mark.xfail(...)` decorator block (the `@` line through the closing `)`). Leave the `def test_a2_e_partial_chain_adoption_via_invalid_tip(...)` definition untouched.

### Step 3: Verify the test passes as a real test

```bash
uv run pytest tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip -v 2>&1 | tail -10
```

Expected: `1 passed`. (Not xfailed; not failed.)

### Step 4: Verify the full audit test module

```bash
uv run pytest tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `1 passed, 5 xfailed`. (One more pass, one fewer xfail than before.)

### Step 5: Verify the demonstration tests still demonstrate their gaps for the remaining 5 findings

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -10
```

Expected: `1 passed, 5 failed`. The passing test is the A2.e fix; the 5 failing tests are the remaining demonstration tests for the other audit findings (A1.f, A4.c, A7.b, A7.e, A7.h).

If any test other than A2.e unexpectedly passes under `--runxfail`, that's a false-positive finding — but should not happen under this task (we only modified `Node.fill_chain`, which other tests don't exercise).

---

## Task 5: Document `new_block_signal`'s deferred-emission semantics

**Files:**
- Modify: `src/cancelchain/signals.py` — add a multi-line `#` comment above the `new_block` signal definition.

### Step 1: Open the file

```bash
cat src/cancelchain/signals.py
```

Current contents:

```python
from __future__ import annotations

from blinker import Namespace

_signals = Namespace()

txn_failed = _signals.signal('transaction-failed')
new_block = _signals.signal('new-block')
http_post = _signals.signal('http-post')
```

### Step 2: Add the comment

Insert a 4-line `#` comment above the `new_block` line:

```python
# Fires for each newly-persisted block. From Node.process_block (the
# single-block delegate of receive_block): fires immediately after the
# per-block commit. From Node.fill_chain: fires only after the batch's
# db.session.commit() succeeds, in apply order — never for blocks that
# were rolled back by a later validation failure.
new_block = _signals.signal('new-block')
```

### Step 3: Verify

```bash
uv run ruff check src/cancelchain/signals.py
uv run ruff format --check src/cancelchain/signals.py
```

Both exit 0.

---

## Task 6: Update the audit doc to reflect A2.e closure

**Files:**
- Modify: `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` — three edits.

### Step 1: Remove A2.e from the Findings table

Find the Findings table row for A2.e. It looks like:

```
| A2.e | Medium | `Node.fill_chain`'s apply loop commits each block individually; invalid tip leaves prefix blocks persisted and advances `ChainDAO`'s tip into a hostile peer's fork. | <remediation sketch> | `test_a2_e_partial_chain_adoption_via_invalid_tip` |
```

Delete this entire row. Adjust the count of findings noted in any surrounding text from "6 findings" to "5 findings".

### Step 2: Update §Adversary 2 → Attack e outcome

Find the line that reads `**Outcome:** ACCEPTED partially — ...` in the Adversary 2 section. Replace with:

```markdown
**Outcome:** REJECTED — `Node.fill_chain`'s apply loop now calls `self.add_block(block, commit=False)` per iteration and issues a single `db.session.commit()` after the loop (rollback on exception). A validation failure on any block rolls back every earlier block's persistence within the same `fill_chain` call. Fixed by the impl PR following from `docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md`.

**Result:** Validation correctly rejects (post-remediation). No finding.
```

Delete the old Finding A2.e block (the `**Finding A2.e — Severity Medium:** ...` paragraph + `**Remediation sketch:** ...` + `**Demonstration test:** ...` lines).

### Step 3: Update the Executive summary count

Find the Executive summary at the top of the audit doc. Update the finding count from:

```
Six findings were confirmed, all Medium or Low; no Critical or High findings were produced.
```

to:

```
Six findings were originally confirmed (all Medium or Low; no Critical or High). One has since been remediated (A2.e); five remain open.
```

Similarly update the severity-breakdown count from "0 Critical / 0 High / 2 Medium / 4 Low" to "0 Critical / 0 High / 1 Medium / 4 Low (post-A2.e)".

### Step 4: Verify the structural counts

```bash
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
grep -c '^\*\*Finding A' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
```

Expected: `^| A[1-7]\.` = 5; `^\*\*Finding A` = 5. (Was 6 + 6 before.)

---

## Task 7: Move A2.e in ROADMAP from open to closed

**Files:**
- Modify: `docs/superpowers/ROADMAP.md` — move A2.e entry; add closed entry.

### Step 1: Remove A2.e from the open "Audit remediation" list

Find the `## Audit remediation — verification pipeline findings (PR #84)` section. Remove item 1 (the A2.e bullet). Renumber items 2-6 to become items 1-5.

### Step 2: Add A2.e to "Closed items (historical reference)"

At the end of the existing `## Closed items (historical reference)` list, add (replace `#<N>` with the actual impl PR number once you know it; for now use `#<N>` as a placeholder and update post-PR-open):

```markdown
- ✅ **Audit finding A2.e — `Node.fill_chain` partial fork-prefix adoption** — closed by docs PR [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (spec + plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). Made `Node.fill_chain`'s apply loop atomic via deferred commits: added a keyword-only `commit: bool = True` parameter to `BlockDAO.commit()` / `Block.to_db()` / `Chain.to_db()` / `Chain.add_block()` / `Node.add_block()`; `fill_chain` passes `commit=False` per block and commits once at the end (rollback on exception). A validation failure on any block rolls back every earlier block's persistence within the same call. Test went from `@pytest.mark.xfail(strict=True)` to a real pass. Originated as finding A2.e (Medium) in the 2026-05-29 verification pipeline audit.
```

Leave the `#<N_docs>` and `#<N_impl>` placeholders for now; Task 8 fills them in after the PR is opened.

### Step 3: Verify

```bash
grep -c '^## ' docs/superpowers/ROADMAP.md
grep -c '^- ✅' docs/superpowers/ROADMAP.md
```

Expected: `^## ` = 6 (Phase 6.7, Phase 7+ ×2, Audit remediation, Future audit, Closed items); `^- ✅` = 9 (was 8 + 1 new).

---

## Task 8: Pre-commit gates + commit + push + open impl PR

**Files:** all 8 modified files from Tasks 3-7 (`models.py`, `block.py`, `chain.py`, `node.py`, `signals.py`, `test_verification_audit.py`, audit doc, ROADMAP).

### Step 1: Full gate sweep

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest 2>&1 | tail -3
```

All exit 0. Pytest shows `237 passed, 5 xfailed, 1 skipped`.

### Step 2: Re-run `--runxfail` on the audit test module

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `1 passed, 5 failed`. The 5 failures correspond to the remaining audit findings (A1.f, A4.c, A7.b, A7.e, A7.h).

### Step 3: Cancelchain DB check gate

```bash
TMPDB=$(mktemp --suffix=.db)
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db check
rm -f "${TMPDB}"
```

`db upgrade` reports "OK" or similar; `db check` reports "No differences detected." (No model changes in this PR.)

### Step 4: Commit

```bash
git add src/cancelchain/models.py src/cancelchain/block.py src/cancelchain/chain.py src/cancelchain/node.py src/cancelchain/signals.py tests/test_verification_audit.py docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
fix(a2e): make Node.fill_chain atomic via deferred-commits

Closes audit finding A2.e (Medium): a validation failure on any block
during fill_chain's apply loop now rolls back every earlier block's
persistence within the same call. A hostile peer can no longer force
partial adoption of a fork prefix by serving a cheap-to-construct
invalid tip.

Adds a keyword-only `commit: bool = True` parameter to
BlockDAO.commit(), Block.to_db(), Chain.to_db(), Chain.add_block(),
Node.add_block(), and Node.create_chain(). When commit=False,
db.session.commit() is replaced with db.session.flush() so rows stay
in the autobegun root transaction. Node.fill_chain passes commit=False
per block and issues a single db.session.commit() after the loop
succeeds (or db.session.rollback() on exception).

Node.create_chain is part of the chain because Node.add_block falls
back to it when the block's prev_hash exists as a Block row but isn't
currently a Chain tip; without threading commit through, the fallback
path would commit inside the loop.

Why not SAVEPOINT (db.session.begin_nested())? SQLAlchemy 2.0's
Session.commit() commits the outermost transaction unconditionally,
automatically releasing any SAVEPOINTs in effect (per its docstring;
trans.commit(_to_root=True) in source). The per-block db.session.commit()
inside Block.to_db() / Chain.to_db() would commit the root and release
the savepoint on the first iteration, defeating atomicity.

new_block_signal.send is deferred to a second loop after the explicit
commit so signals fire only for confirmed-persisted blocks. signals.py
adds a multi-line comment documenting the deferred-batch semantics for
any future consumer.

Test went from @pytest.mark.xfail(strict=True) on
test_a2_e_partial_chain_adoption_via_invalid_tip to a real pass; full
suite is 237 passed, 5 xfailed, 1 skipped. Audit doc Findings table
updated (5 findings remaining); ROADMAP A2.e entry moved from open
to closed.

Design: docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md
Plan: docs/superpowers/plans/2026-05-29-a2e-fill-chain-atomicity.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 5: Push

```bash
git push -u origin fix/a2e-fill-chain-atomicity
```

### Step 6: Open the impl PR

```bash
gh pr create --base main --title "fix(a2e): make Node.fill_chain atomic via deferred-commits" --body "$(cat <<'EOF'
## Summary

Closes audit finding A2.e (Medium). \`Node.fill_chain\`'s apply loop is now atomic: a validation failure on any block rolls back every earlier block's persistence within the same \`fill_chain\` call.

A hostile peer can no longer force partial adoption of a fork prefix by serving a cheap-to-construct invalid tip.

## Implementation notes

- **Deferred-commits approach.** Add a keyword-only \`commit: bool = True\` parameter to \`BlockDAO.commit()\`, \`Block.to_db()\`, \`Chain.to_db()\`, \`Chain.add_block()\`, \`Node.add_block()\`, and \`Node.create_chain()\` (the last to make the create-chain fallback respect \`commit=False\` end-to-end). When \`commit=False\`, \`db.session.commit()\` is replaced with \`db.session.flush()\`. \`Node.fill_chain\` passes \`commit=False\` per block and issues a single \`db.session.commit()\` after the loop succeeds (or \`db.session.rollback()\` on exception).
- **Why not SAVEPOINT?** SQLAlchemy 2.0's \`Session.commit()\` commits the outermost transaction unconditionally, automatically releasing any SAVEPOINTs in effect (per docstring; \`trans.commit(_to_root=True)\` in source). The per-block commits inside \`to_db()\` would commit the root and release the savepoint on the first iteration. Surfaced by Copilot review on the docs PR.
- **Backward compatible.** All existing callers omit the new parameter and get the default \`commit=True\` behavior. The keyword-only \`*,\` separator prevents accidental positional misuse.
- **\`new_block_signal\` emission deferred to post-commit.** Defense-in-depth: no listeners exist today, but the deferred-batch semantic is the correct default for any future consumer. \`signals.py\` gets a multi-line comment documenting the contract.
- **Demonstration test transition:** \`tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip\` was \`@pytest.mark.xfail(strict=True)\`; the decorator is removed and the test becomes a real pass.

## Documentation updates

- Audit doc Findings table: A2.e row removed; per-attack outcome in §Adversary 2 → Attack e updated from \"ACCEPTED partially\" to \"REJECTED\" with a fix note; Executive summary updated to reflect 5 open findings + 1 closed.
- ROADMAP: A2.e moved from open \"Audit remediation\" list to \"Closed items\".

## Out of scope

- Single-block receive path (already atomic).
- Orphan ChainFill rows on process crash (audit A5.c hygiene observation; separate concern).
- Headers-first / batched-blocks redesign (not motivated by A2.e alone).

## Test plan

- [x] All 5 CI gates clean (ruff check + ruff format + pytest + mypy + db check).
- [x] \`uv run pytest 2>&1 | tail -3\` shows \`237 passed, 5 xfailed, 1 skipped\` (was \`236 passed, 6 xfailed, 1 skipped\` pre-fix).
- [x] \`uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3\` shows \`1 passed, 5 failed\` (A2.e passes; other findings still demonstrate gaps).
- [ ] CI green on 3.12 and 3.13.
- [ ] Docker builder build (\`docker build --target builder -t cc-a2e-final .\`) succeeds.

Design: \`docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md\`
Plan: \`docs/superpowers/plans/2026-05-29-a2e-fill-chain-atomicity.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 7: Note the PR number + update ROADMAP placeholders

After `gh pr create` returns the PR URL, extract the PR number. Then update the ROADMAP placeholders in the just-committed file:

```bash
PR_IMPL_N=<N from gh pr create output>
PR_DOCS_N=<N from the merged docs PR — already in main's git log>
sed -i "s/#<N_impl>/#${PR_IMPL_N}/g; s/<N_impl>/${PR_IMPL_N}/g; s/#<N_docs>/#${PR_DOCS_N}/g; s/<N_docs>/${PR_DOCS_N}/g" docs/superpowers/ROADMAP.md
```

Note: `sed -i` is a destructive edit; verify with `git diff docs/superpowers/ROADMAP.md` before staging.

### Step 8: Amend or add follow-up commit

If the PR-number placeholders were the only change, amend the existing commit:

```bash
git add docs/superpowers/ROADMAP.md
git commit --amend --no-edit
git push --force-with-lease origin fix/a2e-fill-chain-atomicity
```

(Per CLAUDE.md: never amend except for trivial follow-up touch-ups before the first review lands. The PR-number fill-in falls in that category. If Copilot has already reviewed the original push, do NOT amend — add a follow-up commit instead.)

### Step 9: Stop — controller handles wor + mwg + sync

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
grep -n 'commit=False' src/cancelchain/node.py
grep -n 'commit: bool = True' src/cancelchain/models.py src/cancelchain/block.py src/cancelchain/chain.py src/cancelchain/node.py
grep -n 'Fires for each newly-persisted block' src/cancelchain/signals.py
```

Expected: at least one `commit=False` match in node.py (the fill_chain apply loop and the inner forwarding calls in `Node.add_block`/`Node.create_chain`); 6 `commit: bool = True` matches (one per modified method — `BlockDAO.commit`, `Block.to_db`, `Chain.to_db`, `Chain.add_block`, `Node.add_block`, `Node.create_chain`); one signal comment match.

- [ ] **Step 3: xfail decorator removed**

```bash
grep -B 5 'test_a2_e_partial_chain_adoption_via_invalid_tip' tests/test_verification_audit.py | head -10
```

Expected: the 5 lines preceding the `def` are no longer `@pytest.mark.xfail(...)` content.

- [ ] **Step 4: pytest reports the new baseline**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: `237 passed, 5 xfailed, 1 skipped`.

- [ ] **Step 5: `--runxfail` confirms remaining findings still demonstrate gaps**

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -5
```

Expected: `1 passed, 5 failed`.

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
grep -c 'A2.e' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
grep 'A2.e' docs/superpowers/ROADMAP.md
```

Expected: Findings table = 5; Finding entries = 5; A2.e still appears in audit doc (in §Adversary 2 → Attack e, now marked REJECTED); A2.e in ROADMAP appears in the `Closed items` section with PR links.

- [ ] **Step 8: Docker build smoke**

```bash
docker build --target builder -t cc-a2e-final .
```

Succeeds.

- [ ] **Step 9: Acceptance complete**

If Steps 1-8 all pass, A2.e remediation is done. A4.c (next-priority Medium per audit Recommendations) is the natural follow-on.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 8) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id`. **Per `project_copilot_auto_rereview`, auto-rereview on cancelchain is inconsistent in practice — the controller asks the user to click "Re-request review" if the 10-min polling loop times out.**
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend — per CLAUDE.md).

---

## Risks and watchpoints

### Risk: `db` import missing from `node.py`

Task 3 Steps 1 + 6 ensure `from cancelchain.database import db` is present in node.py's imports. If absent, the explicit `db.session.commit()` / `db.session.rollback()` calls in fill_chain raise `NameError` at runtime. Mitigation: Task 3 Step 7 (ruff + mypy) catches missing imports before pytest runs.

### Risk: `commit` parameter not propagated through all 6 method signatures

Task 3 modifies 6 methods (BlockDAO.commit, Block.to_db, Chain.to_db, Chain.add_block, Node.add_block, Node.create_chain). Each must forward `commit=commit` to the next layer. If any layer drops the parameter, `fill_chain`'s `commit=False` reaches that layer as the default `commit=True`, defeating atomicity. Mitigation: Task 3 Step 8 (`pytest --runxfail tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip`) verifies end-to-end; if the test still fails, trace which layer dropped the parameter.

### Risk: `Chain.to_db()`'s `sync_longest_chain_blocks()` regresses under `commit=False`

Task 2 Step 3's targeted re-read confirms whether `sync_longest_chain_blocks` only touches session-local state (visible after flush) or assumes a fully-committed state (would require a commit). If the latter, the deferred-commits approach breaks chain materialization. Recovery: either inline the relevant bits of `sync_longest_chain_blocks` into a flush-safe variant, or fall back to inlining persistence in `fill_chain` (bypass `Chain.to_db()` entirely). The xfail demonstration test exercises a 3-block hostile fork, so any materialization bug surfaces quickly.

### Risk: editing the wrong `def commit()` in `models.py`

There are 8 `def commit(self) -> None:` methods in `models.py`, one per DAO. Task 3 Step 2 must edit ONLY the one inside `class BlockDAO` (around line 335). The other 7 (TransactionDAO at line 97, ChainDAO at line 793, etc.) must stay unchanged. Mitigation: Step 2 includes a `grep -n -B 30 'def commit' src/cancelchain/models.py | grep -E 'class |def commit'` command that maps each `def commit` to its enclosing class — the implementer reads this output before making the edit.

### Risk: the docs PR (Task 1) takes longer than expected to review/merge

The impl PR (Tasks 2-8) is blocked on the docs PR. If the docs PR sits unreviewed, the implementer can still start Task 2 (baseline + re-read — no edits) but should not push the impl branch until the docs PR merges, to avoid PR ordering confusion.

### Risk: future consumer of `new_block_signal` regresses on the deferred-emission contract

Today no listeners exist. The multi-line comment in `signals.py` is the contract. If a future PR connects a listener and assumes immediate emission, the deferred-batch semantic surprises them. Mitigation: the comment exists; the commit message documents the contract; reviewers of any future `.connect` PR will read both.
