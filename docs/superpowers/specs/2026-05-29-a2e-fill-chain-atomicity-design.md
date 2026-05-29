# A2.e remediation — `Node.fill_chain` atomicity — design spec

**Status:** Draft for review
**Date:** 2026-05-29
**Scope:** Remediate audit finding A2.e (Medium) by making `Node.fill_chain`'s apply loop atomic via deferred commits — a validation failure on any block rolls back every earlier block's persistence within the same `fill_chain` call. Closes A2.e; removes its xfail demonstration test (which becomes a real pass under `strict=True`); updates the audit doc + ROADMAP to reflect closure.

## Goal

Eliminate the partial-fork-prefix-adoption gap surfaced by audit finding A2.e: a hostile peer can no longer commit our node to an attacker-influenced chain head by serving a multi-block fork that ends in an intentionally-invalid tip. After this PR, `Node.fill_chain` either applies the full staged chain or applies none of it.

## Non-goals

- **Single-block receive path** (`Node.receive_block` → `Node.process_block` → `Node.add_block`). Today this path runs two commits per block (one from `Block.to_db()`, one from `Chain.to_db()` after `Chain.add_block`). If the `Block.to_db()` commit succeeds but `Chain.to_db()` then raises, the block row is already persisted and the existing `except SQLAlchemyError` handler can only roll back the in-flight (chain) transaction — not the already-committed block row. Making single-block receive truly atomic would require the same deferred-commits treatment as `fill_chain`; that's a defensible follow-up but out of scope for A2.e. This PR's default `commit=True` preserves the existing two-commit behavior (and its race window) exactly; only `fill_chain` (the new `commit=False` caller) gets batched into a single end-of-loop commit.
- **Orphan `ChainFill` rows on process crash** (audit's A5.c hygiene observation). The `finally`-block `chain_fill.delete()` handles graceful exceptions but not SIGKILL. A periodic-sweep job is a separate concern; not addressed here.
- **Headers-first / batched-blocks redesign** (the "validate-then-persist" Option C from brainstorming). Larger refactor not justified by A2.e alone; deferred to a future Phase if profiling motivates it.
- **`new_block_signal` listener semantics.** No listeners are registered today. The signal-deferral behavior introduced here (fires only for blocks that survived the batch's `db.session.commit()`, in apply order) is a defense-in-depth refinement, not a behavior change anyone observes today.
- **No spec changes to validation rules.** The validation rules inside `Chain.validate_block` are unchanged. This PR only changes how the apply loop reacts to a validation failure.

## Decisions taken during brainstorming

- **Deferred commits over savepoint or validate-then-persist.** The initial brainstorm picked savepoint (`db.session.begin_nested()`); Copilot review on PR #86 surfaced that SQLAlchemy 2.0's `Session.commit()` explicitly commits the *root* transaction unconditionally (verified in `Session.commit` docstring: "The outermost database transaction is committed unconditionally, automatically releasing any SAVEPOINTs in effect" — and in source: `trans.commit(_to_root=True)`). Because `Block.to_db()` and `Chain.to_db()` both call `db.session.commit()` internally, the first per-block commit inside `begin_nested()` would commit the root and release the savepoint, defeating the atomicity. Switched to deferred-commits: add a keyword-only `commit: bool = True` parameter to `BlockDAO.commit()`, `Block.to_db()`, `Chain.to_db()`, `Chain.add_block()`, `Node.add_block()`, and `Node.create_chain()` (the last to make the create-chain fallback respect `commit=False` end-to-end — without it, `Node.add_block`'s fallback would commit inside the apply loop). `Node.fill_chain` calls with `commit=False` per block and issues a single `db.session.commit()` after the loop succeeds (or `db.session.rollback()` on exception). Validate-then-persist remains out of scope (larger refactor: requires teaching `Chain.validate_block` to resolve `prev_block` from an in-memory candidate map before the DB).
- **Defer `new_block_signal` emission to after the post-loop commit.** Today no listeners exist, so the change is unobservable. But emitting signals during the loop and then rolling back leaves a brief "signal fired for a block that doesn't exist" gap for any future consumer — and the deferral costs one extra list traversal (cheap). Worth doing once, in this PR.
- **`Node.add_block`'s existing `except SQLAlchemyError` handler propagates `commit` through.** With `commit=False`, no per-block `db.session.commit()` is issued inside the try block (the DB still does reads — `Chain.from_db`, `Block.from_db`, the `validate_block` lookups — and the per-block writes are flushes, not commits). On `SQLAlchemyError`, the existing `rollback_session()` path rolls back to the (autobegun) root transaction, which is what we want mid-loop: the fill_chain outer `except` then catches the propagated error and triggers the explicit `db.session.rollback()` for the whole batch. The race-loss swallow at `node.py:191-193` (where a row "ended up persisted anyway" via concurrent write) is unchanged in semantics: if a concurrent writer persisted the same block while we were trying to, `Block.from_db` still returns the row, and we still swallow as today.
- **Single PR for spec + impl plan; second PR for implementation.** Mirrors the verification-audit precedent (PR #83 docs / PR #84 impl). Keeps each PR focused.

## Architecture

### Why SAVEPOINT doesn't work here

The original brainstorm picked SAVEPOINT (`db.session.begin_nested()`) on the assumption that `Session.commit()` inside `begin_nested()` would `RELEASE SAVEPOINT` while leaving the outer savepoint open. That assumption is incorrect in SQLAlchemy 2.0. The `Session.commit` docstring is explicit:

> "The outermost database transaction is committed unconditionally, automatically releasing any SAVEPOINTs in effect."

And in source (SQLAlchemy 2.0.50, `sqlalchemy/orm/session.py`): `trans.commit(_to_root=True)`. So the very first per-block `db.session.commit()` inside the nested context would commit the root and release all savepoints — defeating atomicity. By the time a later block's validation fails, there is no savepoint to roll back to.

The deferred-commits approach makes the inner persistence calls use `db.session.flush()` instead of `db.session.commit()`, leaving the autobegun root transaction open until `fill_chain` finishes the loop. A single explicit `db.session.commit()` after the loop persists all blocks atomically; an exception triggers `db.session.rollback()` which undoes every flushed block in the loop.

### The change shape

Six method signatures gain an optional `commit: bool = True` parameter (default preserves today's behavior; only `fill_chain` passes `commit=False`):

1. `BlockDAO.commit()` in `src/cancelchain/models.py` (line ~335 — inside `class BlockDAO`, the line near `def commit(self)` between the class definition at line 251 and the next class at line 466). Confirm with `grep -n -B 30 'def commit' src/cancelchain/models.py | grep -E 'class |def commit'` to verify which DAO each `def commit` belongs to.
2. `Block.to_db()` in `src/cancelchain/block.py` (line 342) — forwards to `BlockDAO.commit()`.
3. `Chain.to_db()` in `src/cancelchain/chain.py` (line 564) — has inline `db.session.commit()` at line 570; replaced with conditional commit.
4. `Chain.add_block()` in `src/cancelchain/chain.py` (line 153) — forwards to `Block.to_db()`.
5. `Node.add_block()` in `src/cancelchain/node.py` (line 181) — forwards to `chain.add_block()`, `self.create_chain()`, and `chain.to_db()`.
6. `Node.create_chain()` in `src/cancelchain/node.py` (line 196) — forwards to `chain.add_block()`. Without this, the `create_chain` fallback inside `Node.add_block` would call `chain.add_block(block)` with the default `commit=True`, committing inside the loop and defeating atomicity whenever a block lands on an ancestor that exists as a `Block` row but isn't currently a `Chain` tip.

Then `Node.fill_chain` calls `self.add_block(block, commit=False)` inside its apply loop, and commits once at the end:

```python
# Node.fill_chain apply loop — AFTER
progress_switch()
applied: list[Block] = []
try:
    for chain_fill_block in chain_fill.blocks:
        if chain_fill_block.block_json is None:
            continue
        block = Block.from_json(chain_fill_block.block_json)
        self.add_block(block, commit=False)
        applied.append(block)
        progress_next()
    db.session.commit()  # Atomic commit of all flushed blocks.
except Exception:
    db.session.rollback()  # Undo every flushed block.
    raise
# Post-commit — fire signals only for confirmed-persisted blocks, in apply order.
for block in applied:
    new_block_signal.send(self, block=block)
return True
```

The outer `try/except Exception` + `finally` block (lines 312-358) keep their current roles: log the exception, delete the `ChainFill` staging row. The inner `try/except` propagates failures to the outer handler; the explicit `db.session.rollback()` inside the inner `except` ensures the autobegun root transaction is reset before logging proceeds.

### `BlockDAO.commit(commit=False)` semantics

```python
# src/cancelchain/models.py — BlockDAO.commit (sketch)
def commit(self, *, commit: bool = True) -> None:
    db.session.add(self)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
```

When `commit=False`, the row is added to the autobegun root transaction and flushed (so subsequent reads within the session see it, and FK relationships resolve correctly for `Chain.to_db()`'s call to `dao.sync_longest_chain_blocks()`). The root transaction stays open. A later `db.session.commit()` commits all flushed-but-uncommitted rows together; a later `db.session.rollback()` discards them.

`Chain.to_db(commit=False)` follows the same pattern: the inline `db.session.commit()` at line 570 becomes conditional. The intermediate `db.session.flush()` at line 567 (needed for `self.cid = dao.id` and `dao.sync_longest_chain_blocks()`) is unchanged — that flush is already required for FK resolution and is harmless to call before another flush.

### Why deferring `new_block_signal` matters

Today `new_block_signal` has no registered listeners. The only `.send` callsites are `Node.process_block:177` (single-block path: `receive_block` → `process_block` → `add_block` → emit) and `Node.fill_chain:350`; no `.connect` callsites exist. But emitting signals inside the apply loop, then rolling back, creates a brief observable inconsistency for any future consumer: the signal fires for a block that isn't (or won't be) in the chain. Moving the emission to after the explicit `db.session.commit()` ensures signals only fire for blocks that actually committed, in apply order. Cost: one extra list traversal, length ≤ the apply loop's iteration count.

### Callers of `fill_chain` (unchanged)

Only two: `Miller.poll_latest_blocks` (`miller.py:108`, called when polling peers for a longer chain) and `cancelchain sync` (`command.py:379`). Both call `fill_chain` for its side effect (peer-block sync into BlockDAO/ChainDAO) and discard the return value; the bool is purely diagnostic. Neither passes `commit=False` to anything — they use the default `commit=True` behavior in their other DB operations. No caller-side changes needed.

### Other callers of `Block.to_db()` / `Chain.to_db()` / `Chain.add_block()` / `Node.add_block()` / `Node.create_chain()`

All existing callers omit the new parameter, getting the default `commit=True` behavior. The parameter is keyword-only (`*, commit: bool = True`) to prevent accidental positional misuse. No call site outside `fill_chain` (transitively) needs to change.

Confirmed by grep against current `main`:

- `Block.to_db()` callers: `Chain.add_block` (chain.py:155, the only caller — `fill_chain` reaches it transitively via `Node.add_block`).
- `Chain.to_db()` callers: `Node.add_block` (node.py:188, the only caller — same path).
- `Chain.add_block()` callers: `Node.create_chain` (node.py:200, via the create-chain fallback in `Node.add_block`), `Node.add_block` itself (node.py:185, the direct chain.add_block call), tests in `tests/test_chain.py` (regression coverage that defaults to commit=True).
- `Node.add_block()` callers: `Node.process_block` (node.py:176, single-block path — uses default commit=True), `Node.fill_chain` (node.py:349, this PR's case — switches to commit=False), `cancelchain validate`/sync CLI (`command.py:484`, uses default commit=True).
- `Node.create_chain()` callers: `Node.add_block` itself (node.py:187, the fallback path).
- `Node.process_block()` callers: `Node.receive_block` (node.py — the entry-point delegate; not directly affected by the refactor).

## Changes

### Files (in scope)

- **Modify:** `src/cancelchain/models.py` — `BlockDAO.commit()` (line ~335, inside `class BlockDAO` between line 251 and 466) gains a keyword-only `commit: bool = True` parameter. When `False`, replaces `db.session.commit()` with `db.session.flush()`. ~5 lines changed. Other DAOs (TransactionDAO at line 97, ChainDAO at line 793, etc.) are NOT modified.
- **Modify:** `src/cancelchain/block.py` — `Block.to_db()` (line 342) gains a keyword-only `commit: bool = True` parameter; forwards to `BlockDAO.commit()`. ~3 lines changed.
- **Modify:** `src/cancelchain/chain.py` — `Chain.to_db()` (line 564) gains a keyword-only `commit: bool = True` parameter; the inline `db.session.commit()` at line 570 becomes conditional. `Chain.add_block()` (line 153) gains the same parameter; forwards to `Block.to_db()`. ~6 lines changed.
- **Modify:** `src/cancelchain/node.py` — `Node.add_block()` (line 181) and `Node.create_chain()` (line 196) both gain a keyword-only `commit: bool = True` parameter. `Node.add_block` forwards to `chain.add_block()`, `self.create_chain()`, and `chain.to_db()`. `Node.create_chain` forwards to its internal `chain.add_block()` call. `Node.fill_chain` apply loop is refactored to call `self.add_block(block, commit=False)`, accumulate an `applied` list, commit once at the end / rollback on exception, and defer `new_block_signal.send` calls to after the commit. ~22 lines changed.
- **Modify:** `src/cancelchain/signals.py` — add a multi-line `#` comment above the `new_block` definition documenting its new "fires after fill_chain commits, in apply order" semantics. (Receive-path single-block emission unchanged.) ~4 lines added.
- **Modify:** `tests/test_verification_audit.py` — remove the `@pytest.mark.xfail(strict=True)` decorator on `test_a2_e_partial_chain_adoption_via_invalid_tip`. The test body is unchanged; it becomes a real pass.
- **Modify:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` —
  - Remove the A2.e row from the Findings table.
  - Update the per-attack outcome in §Adversary 2 → Attack e from "ACCEPTED partially" to "REJECTED (fixed by PR #N)" with a brief note pointing at this remediation.
  - Update the Executive summary's finding count from "6 findings (0 Critical, 0 High, 2 Medium, 4 Low)" to "5 findings (0 Critical, 0 High, 1 Medium, 4 Low)" (A4.c remains; A2.e closed).
- **Modify:** `docs/superpowers/ROADMAP.md` — move the A2.e entry from the open "Audit remediation — verification pipeline findings" list to the "Closed items (historical reference)" section, with PR link.

### Files (read but not modified)

- `src/cancelchain/database.py` — `db` instance; no changes.
- `src/cancelchain/miller.py` — `Miller.poll_latest_blocks` calls `fill_chain` but doesn't pass any `commit` parameter (gets default behavior on its own DB ops); no changes.
- `src/cancelchain/command.py` — `cancelchain sync` calls `fill_chain` similarly; no changes.
- `tests/conftest.py` — existing fixtures used by the A2.e demonstration test.

## Test plan

- **A2.e demonstration test** (`test_a2_e_partial_chain_adoption_via_invalid_tip`) goes from xfail to real pass after decorator removal. CI's `pytest` step verifies.
- **`pytest --runxfail tests/test_verification_audit.py`** still shows the remaining 5 xfails fail (sanity: no other findings were accidentally caught).
- **`uv run pytest` total**: was `236 passed, 6 xfailed, 1 skipped`; becomes `237 passed, 5 xfailed, 1 skipped`.
- **Regression coverage for the modified methods**: `Block.to_db`, `Chain.to_db`, `Chain.add_block`, `Node.add_block`, and `Node.create_chain` are exercised across `tests/test_block.py`, `tests/test_chain.py`, `tests/test_models.py`, `tests/test_command.py`, and `tests/test_miller.py`. All those tests use the default `commit=True` behavior; they must remain passing. There is no dedicated `tests/test_node.py` and no other test currently calls `Node.fill_chain` directly — the only direct `fill_chain` coverage is `tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip` (the demonstration test this PR converts from xfail to a real pass). The deferred-commits path is transparent for the default-commit case: every modified method preserves its current behavior when `commit=True`.
- **Manual smoke**: build the docker image and run `cancelchain init` inside it to confirm the modified DAO/to_db signatures import cleanly under the production Python config.

## Acceptance

- `src/cancelchain/node.py:344-352` (or equivalent line range after edit) calls `self.add_block(block, commit=False)` per iteration, commits once after the loop via `db.session.commit()`, rolls back on exception via `db.session.rollback()`, and defers `new_block_signal.send` calls to after the commit.
- `tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip` has no `@pytest.mark.xfail` decorator and passes.
- `uv run pytest 2>&1 | tail -3` shows `237 passed, 5 xfailed, 1 skipped`.
- `uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3` shows `5 failed` (the remaining audit findings still demonstrate gaps).
- `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, `uv run cancelchain db check` all exit 0.
- Audit doc's Findings table no longer lists A2.e; the per-attack trace in §Adversary 2 → Attack e records the fix and PR link; Executive summary's count is updated.
- ROADMAP's open "Audit remediation" entry no longer lists A2.e; "Closed items" gains an A2.e entry with the impl PR link.
- `docker build --target builder -t cc-a2e-final .` succeeds (smoke test).

## Risks

### Risk: a `to_db()` caller relies on the implicit commit for crash-safety

Today `Block.to_db()` and `Chain.to_db()` commit immediately, so callers can rely on "after this returns, the block is durable on disk." With the new `commit` parameter defaulting to `True`, that contract is preserved for all existing callers. The only `commit=False` call site is `Node.fill_chain`'s apply loop, which explicitly commits at the end. **Mitigation:** the implementation plan includes a grep step confirming no other callers pass `commit=False`; the keyword-only `*,` separator on the parameter prevents accidental positional misuse. The full test suite catches any caller that broke a flush-vs-commit assumption.

### Risk: `Chain.to_db()`'s `sync_longest_chain_blocks()` call assumes a committed state

`Chain.to_db()` runs `dao.sync_longest_chain_blocks()` after the existing `db.session.flush()` (chain.py:567) and before the (now-conditional) commit. If `sync_longest_chain_blocks` reads from the session and the read assumes the chain row is committed (not just flushed), behavior could differ when called with `commit=False`. **Mitigation:** flushed rows are visible to the same session's subsequent queries (that's the point of `flush()`); only cross-session reads need a commit. The impl plan includes a targeted re-read of `sync_longest_chain_blocks` to confirm it only touches the local session.

### Risk: `Node.add_block`'s `except SQLAlchemyError` path under `commit=False`

Today the handler runs `rollback_session()` (which is `db.session.rollback()`) and conditionally swallows the error if the block ended up persisted by another worker. With `commit=False`, `db.session.rollback()` discards all flushed-but-uncommitted blocks in the current `fill_chain` batch — including blocks that already passed validation in earlier iterations. The exception then re-raises (or is swallowed) per existing logic. **Mitigation:** this is the desired behavior — a SQLAlchemyError on block N means the whole batch should abort. The fill_chain outer `except Exception` handler's explicit `db.session.rollback()` at the end is a no-op when the transaction is already rolled back; harmless. The swallow path at lines 191-193 (race-loss case) still fires correctly for the single-block path with `commit=True`; for the batch path with `commit=False`, the in-flight block is gone but so is the whole batch — appropriate semantics for a multi-block rollback.

### Risk: future consumer of `new_block_signal` regresses on the deferred-emission contract

Today the signal has no listeners; future consumers might assume immediate emission. **Mitigation:** the multi-line `#` comment above `signals.new_block` makes the deferred-batch semantic explicit. Any future listener reads the comment before connecting. The impl PR's commit message also documents this.

### Risk: the demonstration test is testing the wrong thing

The xfail test's assertions check `result is False` AND no hostile block in `BlockDAO` AND `longest_chain.length` unchanged. After the fix, all three should hold: the explicit `db.session.rollback()` in `fill_chain`'s new `except` clause undoes all flushed blocks, the outer `try/except Exception` + `return False` fallback still triggers (the rollback re-raises into it). **Mitigation:** the impl plan includes a step to run the test in non-xfail mode (`pytest --runxfail`) against the fix; if the test passes, the gap is closed.

### Risk: single-block receive path regresses

`Node.receive_block` → `Node.add_block(block)` uses the default `commit=True`. Behavior is unchanged. **Mitigation:** the impl plan includes a regression-coverage step running the existing `tests/test_chain.py` and `tests/test_models.py` tests that exercise the single-block receive path.

## Open decisions

None at design time. Brainstorming + Copilot review resolved:

- **Initial brainstorm pick (savepoint wrap) was incorrect** based on a wrong assumption about SQLAlchemy 2.0's `Session.commit()` behavior inside `begin_nested()`. PR #86 round-2 review surfaced the bug. Switched to deferred-commits (Option B from brainstorm).
- Defer `new_block_signal` emission to post-commit (chosen — defense-in-depth).
- Keyword-only `commit` parameter on all modified methods (chosen — prevents accidental positional misuse).
- `Node.add_block`'s `except SQLAlchemyError` handler propagates `commit` through unchanged.
- Single-PR spec + impl-plan; second-PR implementation (mirrors audit precedent).

## What comes next

- **Impl PR.** Executes this design. Branch `fix/a2e-fill-chain-atomicity`. Single commit (or commit-per-section if review surfaces issues mid-PR). Removes the xfail decorator; updates audit doc + ROADMAP.
- **Next audit remediations.** A4.c is the natural follow-on (next-priority Medium per the audit's Recommendations). Same brainstorm-spec-plan-impl flow.
- **API auth audit.** Still deliberately deferred. Can be picked up at any time independently of further remediation PRs.
