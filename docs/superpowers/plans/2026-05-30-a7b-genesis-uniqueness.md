# A7.b — Canonical-Genesis Uniqueness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject alternate-genesis blocks at validation time so they can't fragment the chain registry (and so a disjoint-genesis reorg, A7.j, becomes unreachable).

**Architecture:** Add a `Block.genesis_from_db()` domain helper (symmetric with `Block.from_db`, keyed on `idx == 0` to avoid a `chain.py → block.py` circular import). In `Chain.validate_block`, when the candidate is a genesis block, reject it with a new `DuplicateGenesisError(InvalidBlockError)` if a *different* genesis is already persisted. The `block_hash`-equality guard keeps the check idempotent (safe in `Chain.validate()` full-chain revalidation). No schema/migration change.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0 (`Mapped[]` DAOs), Pydantic v2 domain dataclasses, pytest + time-machine, uv.

**Spec:** `docs/superpowers/specs/2026-05-30-a7b-genesis-uniqueness-design.md`

---

## Prerequisites (read before starting)

- **Full-suite pytest needs `COLUMNS=200`**: a latent terminal-width bug in `tests/test_command.py::test_create_wallet` (unrelated to this work) fails on narrow terminals. Always run the full suite as `COLUMNS=200 uv run pytest`.
- `idx == 0 ⟺ genesis` is guaranteed by `validate_block`'s `idx == prev_index + 1` rule (only a genesis has `prev_index == -1`). That is why the helper keys on `idx`, not `prev_hash == GENESIS_HASH` — and why `block.py` must NOT import `GENESIS_HASH` from `chain.py` (it would create a circular import; `chain.py` already imports `Block` from `block.py`).
- `BlockDAO.get(idx=0)` already exists (`src/cancelchain/models.py:393-402`): `db.select(cls).filter_by(idx=idx)` → `scalar_one_or_none()`. Reuse it.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cancelchain/exceptions.py` | Exception hierarchy | Add `DuplicateGenesisError(InvalidBlockError)` |
| `src/cancelchain/block.py` | `Block` domain dataclass + DB round-trip | Add `Block.genesis_from_db()` classmethod |
| `src/cancelchain/chain.py` | Chain validation | Import `DuplicateGenesisError`; add genesis-uniqueness check in `validate_block` |
| `tests/test_block.py` | `Block` unit tests | Add `test_genesis_from_db` |
| `tests/test_verification_audit.py` | Audit demonstration/regression tests | Un-xfail `test_a7_b…`; add `test_a7_j…`; update module docstring; import `DuplicateGenesisError` |
| `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` | Audit record | Mark A7.b remediated, A7.j closed-via-A7.b; update counts |
| `docs/superpowers/ROADMAP.md` | Roadmap | Move A7.b to remediated; severity → 0/0/0/3 |

---

### Task 1: `Block.genesis_from_db()` helper

**Files:**
- Modify: `src/cancelchain/block.py` (after `from_db`, ends at line 389)
- Test: `tests/test_block.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_block.py` (the file already imports `Block`, `GENESIS_HASH`, and defines `TEST_TARGET = 'F' * 64`; `app`, `reward`, `wallet` are conftest fixtures). Mirror the persistence pattern in `test_db` (line 163):

```python
def test_genesis_from_db(app, reward, wallet):
    """Block.genesis_from_db() returns None until a genesis is persisted,
    then returns the persisted canonical genesis."""
    with app.app_context():
        assert Block.genesis_from_db() is None
        block = Block()
        block.link(0, GENESIS_HASH, TEST_TARGET)
        block.seal(wallet, reward)
        block.mill()
        block.validate()
        block.to_db()
        genesis = Block.genesis_from_db()
        assert genesis is not None
        assert genesis == block
        assert genesis.idx == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_block.py::test_genesis_from_db -v`
Expected: FAIL with `AttributeError: type object 'Block' has no attribute 'genesis_from_db'`.

- [ ] **Step 3: Implement the helper**

In `src/cancelchain/block.py`, immediately after the `from_db` classmethod (which ends at line 389), add:

```python
    @classmethod
    def genesis_from_db(cls) -> Self | None:
        dao = BlockDAO.get(idx=0)
        return cls.from_dao(dao) if dao else None
```

`BlockDAO` is already imported in `block.py` (line 33). No new imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_block.py::test_genesis_from_db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cancelchain/block.py tests/test_block.py
git commit -m "feat(a7b): add Block.genesis_from_db() helper"
```

---

### Task 2: `DuplicateGenesisError` + the `validate_block` check (un-xfail A7.b)

**Files:**
- Modify: `src/cancelchain/exceptions.py` (InvalidBlockError subclasses, ~line 109)
- Modify: `src/cancelchain/chain.py` (exception import block lines 14-32; `validate_block` lines 171-198)
- Test (acceptance): `tests/test_verification_audit.py::test_a7_b_alternate_genesis_fragments_chain_registry` (already exists, currently `@pytest.mark.xfail(strict=True)`)

- [ ] **Step 1: Make the existing demonstrator the failing test (remove its xfail)**

In `tests/test_verification_audit.py`, delete the `@pytest.mark.xfail(...)` decorator block (lines 324-336) that sits directly above `def test_a7_b_alternate_genesis_fragments_chain_registry`. The decorator to remove is exactly:

```python
@pytest.mark.xfail(
    reason=(
        'Audit finding A7.b — severity Low — Chain.validate_block has no '
        '"is the canonical genesis already taken?" check, so any block '
        'with prev_hash=GENESIS_HASH, idx=0, target=MAX_TARGET is accepted '
        'as a fresh genesis. Each accepted alternate genesis creates a new '
        'ChainDAO row, fragmenting the chain registry into N parallel '
        'single-block chains and consuming DB rows without any operational '
        'recovery path. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
```

Leave the `def test_a7_b_alternate_genesis_fragments_chain_registry(...)` and its body unchanged (it already asserts `pytest.raises(InvalidBlockError)` and `_chain_count() == initial_chain_count`).

- [ ] **Step 2: Run it to verify it fails (gap still present)**

Run: `uv run pytest tests/test_verification_audit.py::test_a7_b_alternate_genesis_fragments_chain_registry -v`
Expected: FAIL — today `receive_block(g2)` does NOT raise, so `pytest.raises(InvalidBlockError)` fails (and/or `_chain_count()` becomes 2).

- [ ] **Step 3: Add the exception**

In `src/cancelchain/exceptions.py`, immediately after the `class InvalidBlockError(CCError): pass` block (line 109-110), add:

```python
class DuplicateGenesisError(InvalidBlockError):
    pass
```

- [ ] **Step 4: Wire the check into `validate_block`**

In `src/cancelchain/chain.py`, add `DuplicateGenesisError` to the alphabetically-sorted exception import block (lines 14-32) — insert it as the FIRST entry, before `EmptyChainError`:

```python
from cancelchain.exceptions import (
    DuplicateGenesisError,
    EmptyChainError,
    FutureBlockError,
    ...
```

Then in `validate_block` (line 171), insert the genesis-uniqueness check immediately after the `FutureBlockError` check (line 174) and before `prev_block = ...` (line 175):

```python
    def validate_block(self, block: Block) -> None:
        block.validate()
        if block.timestamp_dt is not None and block.timestamp_dt > now():
            raise FutureBlockError()
        if is_genesis_block(block):
            existing_genesis = Block.genesis_from_db()
            if (
                existing_genesis is not None
                and existing_genesis.block_hash != block.block_hash
            ):
                raise DuplicateGenesisError()
        prev_block = Block.from_db(block.prev_hash) if block.prev_hash else None
        ...  # rest unchanged
```

- [ ] **Step 5: Run the acceptance test to verify it passes**

Run: `uv run pytest tests/test_verification_audit.py::test_a7_b_alternate_genesis_fragments_chain_registry -v`
Expected: PASS — `receive_block(g2)` now raises `DuplicateGenesisError` (a subclass of `InvalidBlockError`) and the `ChainDAO` count stays 1.

- [ ] **Step 6: Verify no other genesis flow regressed**

Run: `COLUMNS=200 uv run pytest tests/test_chain.py tests/test_block.py tests/test_models.py tests/test_node.py -q`
Expected: PASS (the canonical first-genesis flow, milling, and full-chain `Chain.validate()` revalidation are unaffected — the check is idempotent for the single persisted genesis).

- [ ] **Step 7: Commit**

```bash
git add src/cancelchain/exceptions.py src/cancelchain/chain.py tests/test_verification_audit.py
git commit -m "fix(a7b): reject alternate-genesis blocks in Chain.validate_block"
```

---

### Task 3: A7.j disjoint-reorg regression test

**Files:**
- Modify: `tests/test_verification_audit.py` (add import; add test after `test_a7_b…` ends, ~line 408, before the A7.e xfail block)

- [ ] **Step 1: Import `DuplicateGenesisError`**

In `tests/test_verification_audit.py`, add `DuplicateGenesisError` to the `cancelchain.exceptions` import block (currently imports `InvalidBlockError, InvalidCoinbaseError, InvalidTransactionError, MismatchedCoinbaseError`), keeping it alphabetically sorted:

```python
from cancelchain.exceptions import (
    DuplicateGenesisError,
    InvalidBlockError,
    InvalidCoinbaseError,
    InvalidTransactionError,
    MismatchedCoinbaseError,
)
```

- [ ] **Step 2: Write the regression test**

Insert directly after the end of `test_a7_b_alternate_genesis_fragments_chain_registry` (its last line is `assert _chain_count() == initial_chain_count`), before the next `@pytest.mark.xfail(` block for A7.e. This is a plain (non-xfail) regression test. `Block`, `GENESIS_HASH`, `REWARD`, `Miller`, `ChainDAO`, `db`, `now`, `TEST_TARGET`, and `datetime` are already imported/defined in the file.

```python
def test_a7_j_disjoint_genesis_reorg_rejected(
    app, time_machine, wallet, miller_2_wallet
) -> None:
    """A7.j: a longer fork rooted at an alternate genesis cannot displace
    the canonical chain — its root genesis is rejected at admission.

    A7.j (disjoint-ancestor reorg) has no standalone finding: the
    catastrophic-rebuild branch is correct PoW longest-chain behavior. The
    gap is the alternate-genesis admission (A7.b). This test proves closing
    A7.b closes A7.j: even a LONGER fork (g2 + child b2, length 2 vs the
    canonical length 1) cannot win, because its root genesis g2 is rejected,
    making b2 unrootable. The reorg never completes.
    """
    with app.app_context():

        def _chain_count() -> int:
            return len(db.session.execute(db.select(ChainDAO)).scalars().all())

        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        # Canonical genesis g1 paying `wallet`.
        m1 = Miller(milling_wallet=wallet)
        g1 = m1.create_block()
        m1.mill_block(g1)
        assert g1.block_hash is not None
        assert g1.idx == 0
        canonical_chain = ChainDAO.longest()
        assert canonical_chain is not None
        canonical_tip = canonical_chain.block.block_hash
        assert canonical_tip == g1.block_hash
        assert _chain_count() == 1

        # Build a LONGER disjoint fork rooted at an alternate genesis.
        when_dt += datetime.timedelta(minutes=5)
        time_machine.move_to(when_dt)
        g2 = Block()
        g2.link(0, GENESIS_HASH, TEST_TARGET)
        g2.seal(miller_2_wallet, REWARD)
        g2.mill()
        assert g2.block_hash is not None
        assert g2.block_hash != g1.block_hash
        assert g2.idx == 0
        # Child b2 chains off g2 — fork length 2 > canonical length 1.
        when_dt += datetime.timedelta(minutes=5)
        time_machine.move_to(when_dt)
        b2 = Block()
        b2.link(1, g2.block_hash, TEST_TARGET)
        b2.seal(miller_2_wallet, REWARD)
        b2.mill()
        assert b2.idx == 1
        assert b2.prev_hash == g2.block_hash

        # The fork's root g2 is rejected at admission, so the whole longer
        # fork is unrootable and the reorg can never trigger.
        with pytest.raises(DuplicateGenesisError):
            m1.receive_block(g2.to_json())

        # Canonical chain unchanged; registry still single.
        post_chain = ChainDAO.longest()
        assert post_chain is not None
        assert post_chain.block.block_hash == canonical_tip
        assert _chain_count() == 1
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `uv run pytest tests/test_verification_audit.py::test_a7_j_disjoint_genesis_reorg_rejected -v`
Expected: PASS.

- [ ] **Step 4: Run the whole audit module**

Run: `uv run pytest tests/test_verification_audit.py -q`
Expected: A7.b and A7.j are among the passing tests; the remaining open findings (A1.f, A7.e, A7.h) still xfail. Confirm **0 failures and 0 unexpectedly-passing xfails** (exactly 3 xfailed remain).

- [ ] **Step 5: Commit**

```bash
git add tests/test_verification_audit.py
git commit -m "test(a7b): add A7.j disjoint-genesis reorg regression test"
```

---

### Task 4: Docs — audit, ROADMAP, test module docstring

**Files:**
- Modify: `tests/test_verification_audit.py` (module docstring)
- Modify: `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`
- Modify: `docs/superpowers/ROADMAP.md`

- [ ] **Step 1: Update the test module docstring**

In `tests/test_verification_audit.py`, the module docstring lists remediated findings as "(e.g. A2.e, A4.c)". Update that parenthetical to include A7.b:

Find: `pass as plain regression tests guarding the fix (e.g. A2.e, A4.c).`
Replace: `pass as plain regression tests guarding the fix (e.g. A2.e, A4.c, A7.b).`

- [ ] **Step 2: Update the audit doc**

In `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`:

(a) Intro count (line 9):
Find: `Two have since been remediated (A2.e, A4.c); four remain open.`
Replace: `Three have since been remediated (A2.e, A4.c, A7.b); three remain open (A7.h, A7.e, A1.f).`

(b) Findings-table count line (line 38):
Find: `4 open findings: 0 Critical / 0 High / 0 Medium / 4 Low (post-A4.c).`
Replace: `3 open findings: 0 Critical / 0 High / 0 Medium / 3 Low (post-A7.b).`

(c) A7.b findings-table row (line 43): prefix the description with a remediation marker, mirroring how remediated findings are flagged elsewhere in the table. Change the leading `| A7.b | Low |` cell text to begin with `✅ Remediated (PR #<N_impl>). ` and flip the substance to past tense ("accepted … " → "previously accepted; now rejected via a canonical-genesis check raising `DuplicateGenesisError`"). Keep the row's test reference.

(d) A7.b deep-dive section (the `**Outcome:**` / `**Finding A7.b…**` / `**Remediation sketch:**` block around lines 885-895): add a `✅ **Remediated.**` lead-in to the Finding paragraph and append a sentence describing the shipped fix: `Chain.validate_block` now calls `Block.genesis_from_db()` and raises `DuplicateGenesisError(InvalidBlockError)` when a different genesis is already persisted; `Block.genesis_from_db()` keys on `idx == 0` to avoid a `chain.py → block.py` circular import. Flip the `**Outcome:**` line for sub-attack b.ii from `ACCEPTED` to `REJECTED (post-remediation)`.

(e) A7.j cross-link section (around lines 1072-1089): update the `**No new finding for j.**` paragraph to note A7.j's entry path is now closed: append `Closed via the A7.b remediation (PR #<N_impl>): the alternate-genesis root is rejected at admission, so a disjoint-ancestor reorg can no longer be mounted. Regression: test_a7_j_disjoint_genesis_reorg_rejected.`

(f) Remediation-priority section "### 3. A7.b (Low)" (around line 1163): add a `✅ **Implemented.**` lead-in and replace the "Acceptance signal: … flips from xfail to pass" sentence with `Acceptance signal: test_a7_b_alternate_genesis_fragments_chain_registry is now a passing regression test (xfail removed); test_a7_j_disjoint_genesis_reorg_rejected proves the A7.j reorg path is closed.`

- [ ] **Step 3: Update the ROADMAP**

In `docs/superpowers/ROADMAP.md`:

(a) Remove the A7.b bullet from the open-findings list (line 52, the `1. **A7.b — Low — Alternate-genesis…**` item) and renumber the remaining open items (A7.h, A7.e, A1.f) accordingly.

(b) Update the open-findings severity line wherever it states the current tally (it reads `0 Critical / 0 High / 0 Medium / 4 Low` post-A4.c) to `0 Critical / 0 High / 0 Medium / 3 Low`.

(c) Add a remediated entry for A7.b in the same style as the A4.c entry (the `✅ **Audit finding A4.c …**` line), linking the docs PR (this branch's PR) and the impl PR placeholder:

```markdown
- ✅ **Audit finding A7.b — alternate-genesis admission fragments the chain registry** — closed by docs PR [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (design+plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). `Chain.validate_block` now rejects a block claiming genesis when a different genesis is already persisted, raising `DuplicateGenesisError` (via a `Block.genesis_from_db()` helper keyed on `idx == 0`). This also closes A7.j (disjoint-ancestor reorg), whose only entry path is alternate-genesis admission. No schema change. Brings audit severity to 0 Critical / 0 High / 0 Medium / 3 Low.
```

> The `#<N_docs>` / `#<N_impl>` placeholders are filled in once the PRs are opened (docs PR number when committing on this branch; impl PR number after it is opened), mirroring the A4.c closeout.

- [ ] **Step 4: Commit**

```bash
git add tests/test_verification_audit.py docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a7b): mark A7.b remediated, A7.j closed-via-A7.b; update counts"
```

---

### Task 5: Final gates

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `COLUMNS=200 uv run pytest`
Expected: **244 passed, 3 xfailed, 1 skipped** (the post-A4.c baseline was 241 passed / 4 xfailed / 1 skipped; this PR adds `test_genesis_from_db` and `test_a7_j…` as passing, and moves `test_a7_b…` from xfailed to passed). No unexpectedly-passing xfails. If the baseline differs, the invariant that must hold is: +2 net new passing tests, and A7.b moved from xfailed to passed (xfailed count drops by exactly 1).

- [ ] **Step 2: xfail audit cross-check**

Run: `uv run pytest --runxfail tests/test_verification_audit.py -q`
Expected: the three still-open findings surface their real failure modes; A2.e/A4.c/A7.b/A7.j pass.

- [ ] **Step 3: Lint + format + types**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean. (No schema change ⇒ no migration / `db check` impact.)

- [ ] **Step 4: Confirm no migration drift (sanity)**

Run: `git status --porcelain src/cancelchain/migrations/`
Expected: empty — this change adds no migration.

---

## Notes for the implementer

- **Do not** import `GENESIS_HASH` into `block.py` (circular import). The helper keys on `idx == 0`, which is equivalent to genesis by the `idx == prev_index + 1` invariant.
- **Idempotency is load-bearing.** The `existing_genesis.block_hash != block.block_hash` guard is what keeps `Chain.validate()` full-chain revalidation green (revalidating the canonical genesis compares it against itself). Do not simplify it to a bare "a genesis already exists → raise".
- Keep the A7.j test self-contained (submit `g2` directly); do not wire a peer `fill_chain` walk — the point is that g2's rejection makes the longer fork unrootable.
- This is a fix PR: no adjacent refactors. The pre-existing `test_create_wallet` terminal-width bug is out of scope (separate PR).
