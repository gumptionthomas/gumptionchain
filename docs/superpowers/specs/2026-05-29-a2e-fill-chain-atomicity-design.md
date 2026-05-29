# A2.e remediation — `Node.fill_chain` atomicity — design spec

**Status:** Draft for review
**Date:** 2026-05-29
**Scope:** Remediate audit finding A2.e (Medium) by wrapping `Node.fill_chain`'s apply loop in a SQLAlchemy SAVEPOINT (`db.session.begin_nested()`) so a validation failure on any block rolls back every earlier block's persistence within the same `fill_chain` call. Closes A2.e; removes its xfail demonstration test (which becomes a real pass under `strict=True`); updates the audit doc + ROADMAP to reflect closure.

## Goal

Eliminate the partial-fork-prefix-adoption gap surfaced by audit finding A2.e: a hostile peer can no longer commit our node to an attacker-influenced chain head by serving a multi-block fork that ends in an intentionally-invalid tip. After this PR, `Node.fill_chain` either applies the full staged chain or applies none of it.

## Non-goals

- **Single-block receive path** (`Node.receive_block` → `Node.add_block`). Already atomic — one commit per receive; the existing `except SQLAlchemyError` handler rolls it back. No change.
- **Orphan `ChainFill` rows on process crash** (audit's A5.c hygiene observation). The `finally`-block `chain_fill.delete()` handles graceful exceptions but not SIGKILL. A periodic-sweep job is a separate concern; not addressed here.
- **Headers-first / batched-blocks redesign** (the "validate-then-persist" Option C from brainstorming). Larger refactor not justified by A2.e alone; deferred to a future Phase if profiling motivates it.
- **`new_block_signal` listener semantics.** No listeners are registered today. The signal-deferral behavior introduced here (fires only for blocks that survived the savepoint commit, in apply order) is a defense-in-depth refinement, not a behavior change anyone observes today.
- **No spec changes to validation rules.** The validation rules inside `Chain.validate_block` are unchanged. This PR only changes how the apply loop reacts to a validation failure.

## Decisions taken during brainstorming

- **Savepoint wrap (`db.session.begin_nested()`) over deferred-commits or validate-then-persist.** Smallest code footprint (~10 lines in `node.py` only); no signature changes to `Block.to_db()` / `Chain.to_db()`; SQLAlchemy 2.0 + SQLite both support SAVEPOINT natively. Deferred-commits required changing `to_db()` signatures across multiple files; validate-then-persist required teaching `Chain.validate_block` to resolve `prev_block` from an in-memory candidate map before the DB, which is invasive.
- **Defer `new_block_signal` emission to after the savepoint commits.** Today no listeners exist, so the change is unobservable. But emitting signals during the savepoint then rolling back leaves a brief "signal fired for a block that doesn't exist" gap for any future consumer — and the deferral costs one extra list traversal (cheap). Worth doing once, in this PR.
- **No changes to `Node.add_block`'s existing `except SQLAlchemyError` handler.** Inside a savepoint, `rollback_session()` rolls back to the savepoint (not the outer transaction); the row that "ended up persisted anyway" race-loss swallow at `node.py:191-193` becomes dead under savepoint, but harmlessly so — `Block.from_db` returns None after the savepoint rollback, so `Node.add_block` re-raises and the outer savepoint catches it.
- **Single PR for spec + impl plan; second PR for implementation.** Mirrors the verification-audit precedent (PR #83 docs / PR #84 impl). Keeps each PR focused.

## Architecture

### The change site

`src/cancelchain/node.py`, `Node.fill_chain` apply loop (lines 344-352):

```python
# BEFORE
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

```python
# AFTER
progress_switch()
applied: list[Block] = []
with db.session.begin_nested():
    for chain_fill_block in chain_fill.blocks:
        if chain_fill_block.block_json is None:
            continue
        block = Block.from_json(chain_fill_block.block_json)
        self.add_block(block)
        applied.append(block)
        progress_next()
# Savepoint committed — fire signals only for confirmed-persisted blocks.
for block in applied:
    new_block_signal.send(self, block=block)
return True
```

The outer `try/except Exception` + `finally` block (lines 312-358) keep their current roles unchanged: log the exception, delete the `ChainFill` staging row. When `begin_nested()` rolls back on exception, the exception re-propagates to the outer `except`, which logs it; the function falls through to `return False`.

### Why savepoint semantics are correct here

Three SQLAlchemy 2.0 behaviors make this work without further code changes:

1. **`Session.commit()` inside `begin_nested()` releases the inner savepoint, not the outer transaction.** `Block.to_db()` (`block.py:342-343`) calls `db.session.commit()`; `Chain.to_db()` (`chain.py:564-570`) does too. Inside the nested context, those calls translate to `RELEASE SAVEPOINT` operations against the inner savepoint — the outer savepoint remains open and accumulates all the released writes.
2. **Exiting `begin_nested()` via exception triggers `ROLLBACK TO SAVEPOINT`.** Every "released" inner savepoint inside is undone, because SQLite rolls back all uncommitted-to-outer changes when the outer SAVEPOINT is rolled back.
3. **`db.session.rollback()` inside an active savepoint rolls back to the savepoint, not the outer transaction.** `Node.add_block`'s `except SQLAlchemyError: rollback_session()` (lines 189-190) therefore behaves correctly inside the savepoint: SQLAlchemyError rolls back to the savepoint, the row that would have been retried is truly gone, `Block.from_db` returns None, the swallow path at lines 191-193 doesn't trigger, and the exception re-raises to the savepoint context manager.

### Why deferring signals matters

Today `new_block_signal` has no registered listeners (the only `.send` callsites are `Node.receive_block:177` and `Node.fill_chain:350`; no `.connect` callsites exist). But emitting signals inside the savepoint, then rolling back, creates a brief observable inconsistency for any future consumer: the signal fires for a block that isn't (or won't be) in the chain. Moving the emission outside the savepoint ensures signals only fire for blocks that survived the savepoint commit, in apply order. Cost: one extra list traversal, length ≤ the apply loop's iteration count.

### Callers of `fill_chain` (unchanged)

Only two: `Miller.poll_latest_blocks` (`miller.py:108`, called when polling peers for a longer chain) and `cancelchain sync` (`command.py:379`). Both treat `fill_chain`'s return value as a boolean for retry logic. Neither holds a savepoint open before calling — confirmed by grep. The savepoint added inside `fill_chain` is therefore a top-level savepoint (one level deep), not a nested-nested case.

## Changes

### Files (in scope)

- **Modify:** `src/cancelchain/node.py` — wrap apply loop in `with db.session.begin_nested():`; collect applied blocks into a list; defer `new_block_signal.send` calls to after the savepoint. ~10 lines changed.
- **Modify:** `src/cancelchain/signals.py` — add a one-line docstring comment to `new_block` documenting its new "fires after fill_chain savepoint commit, in apply order" semantics. (Receive-path single-block emission unchanged.)
- **Modify:** `tests/test_verification_audit.py` — remove the `@pytest.mark.xfail(strict=True)` decorator on `test_a2_e_partial_chain_adoption_via_invalid_tip`. The test body is unchanged; it becomes a real pass.
- **Modify:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` —
  - Remove the A2.e row from the Findings table.
  - Update the per-attack outcome in §Adversary 2 → Attack e from "ACCEPTED partially" to "REJECTED (fixed by PR #N)" with a brief note pointing at this remediation.
  - Update the Executive summary's finding count from "6 findings (0 Critical, 0 High, 2 Medium, 4 Low)" to "5 findings (0 Critical, 0 High, 1 Medium, 4 Low)" (A4.c remains; A2.e closed).
- **Modify:** `docs/superpowers/ROADMAP.md` — move the A2.e entry from the open "Audit remediation — verification pipeline findings" list to the "Closed items (historical reference)" section, with PR link.

### Files (read but not modified)

- `src/cancelchain/block.py`, `src/cancelchain/chain.py` — the `to_db()` definitions; reviewed to confirm `db.session.commit()` placement is compatible with savepoint nesting.
- `src/cancelchain/models.py` — `rollback_session()` definition; reviewed to confirm `db.session.rollback()` inside a savepoint rolls back to the savepoint.
- `src/cancelchain/database.py` — `db` instance; no changes.
- `tests/conftest.py` — existing fixtures used by the A2.e demonstration test.

## Test plan

- **A2.e demonstration test** (`test_a2_e_partial_chain_adoption_via_invalid_tip`) goes from xfail to real pass after decorator removal. CI's `pytest` step verifies.
- **`pytest --runxfail tests/test_verification_audit.py`** still shows the remaining 5 xfails fail (sanity: no other findings were accidentally caught).
- **`uv run pytest` total**: was `236 passed, 6 xfailed, 1 skipped`; becomes `237 passed, 5 xfailed, 1 skipped`.
- **Regression coverage for `fill_chain` happy path**: existing test suite includes `fill_chain` exercises (notably the multi-node sync paths in `tests/test_node.py` / `tests/test_chain.py`). All must remain passing. The savepoint is transparent for happy-path: each block's commit becomes a savepoint release within the outer savepoint, then the outer savepoint commits at `__exit__`.
- **Manual smoke**: build the docker image and run `cancelchain init` inside it to confirm the SQLAlchemy savepoint code paths import cleanly under the production Python config.

## Acceptance

- `src/cancelchain/node.py:344-352` (or equivalent line range after edit) wraps the apply loop in `with db.session.begin_nested():` and defers `new_block_signal.send` calls to after the savepoint.
- `tests/test_verification_audit.py::test_a2_e_partial_chain_adoption_via_invalid_tip` has no `@pytest.mark.xfail` decorator and passes.
- `uv run pytest 2>&1 | tail -3` shows `237 passed, 5 xfailed, 1 skipped`.
- `uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3` shows `5 failed` (the remaining audit findings still demonstrate gaps).
- `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, `uv run cancelchain db check` all exit 0.
- Audit doc's Findings table no longer lists A2.e; the per-attack trace in §Adversary 2 → Attack e records the fix and PR link; Executive summary's count is updated.
- ROADMAP's open "Audit remediation" entry no longer lists A2.e; "Closed items" gains an A2.e entry with the impl PR link.
- `docker build --target builder -t cc-a2e-final .` succeeds (smoke test).

## Risks

### Risk: hidden assumption about session state across `Block.to_db()` / `Chain.to_db()`

Both methods call `db.session.commit()` explicitly. Inside `begin_nested()`, those become SAVEPOINT releases. If either method also depends on `db.session.is_active` being False / a fresh transaction being autobegun afterward (e.g., a follow-on flush that relies on a clean session state), behavior could differ inside vs outside a savepoint. **Mitigation:** the implementation plan includes a targeted re-read of `to_db()` and immediate callers to confirm no post-commit session-state probe; the full test suite run on the impl branch will catch any regression.

### Risk: SAVEPOINT performance under deep-reorg fallback

A savepoint per `fill_chain` call adds bookkeeping to SQLite's transaction log. For typical fill_chain calls (small N, peer-catchup), overhead is negligible. For catastrophic deep-reorg fallback (rare), the savepoint contains the entire reorg's writes — possibly millions of bytes. **Mitigation:** the alternative is the A2.e bug. SQLite's SAVEPOINT implementation is well-optimized for journal-based transactions; the same operations that today are flushed per-block will be flushed per-block under the savepoint with negligible extra cost. No new bench gate needed.

### Risk: future consumer of `new_block_signal` regresses on the deferred-emission contract

Today the signal has no listeners; future consumers might assume immediate emission. **Mitigation:** the one-line docstring on `signals.new_block` makes the deferred-batch semantic explicit. Any future listener reads the docstring before connecting. The impl PR's commit message also documents this.

### Risk: savepoint nesting if a caller already holds an outer savepoint

None today (confirmed by grep: only `Miller.poll_latest_blocks` and `cancelchain sync` call `fill_chain`; neither uses savepoints). **Mitigation:** if a future caller wraps `fill_chain` in its own savepoint, this code becomes a nested-nested savepoint — SQLAlchemy + SQLite both support this, but the semantics are subtler (outer rollback discards inner work; inner commit doesn't propagate). The risk is theoretical until someone introduces a wrapping savepoint. Document the assumption in a code comment alongside `with db.session.begin_nested():` so a future caller is forewarned.

### Risk: the demonstration test is testing the wrong thing

The xfail test's assertions check `result is False` AND no hostile block in `BlockDAO` AND `longest_chain.length` unchanged. After the fix, all three should hold. **Mitigation:** the impl plan includes a step to manually run the test in non-xfail mode against the fix and inspect output. If `result is False` doesn't hold despite the savepoint rollback, the test's structure may need adjustment (currently it expects the existing outer `try/except Exception` + `return False` fallback to still trigger, which it does — the savepoint exception re-propagates).

## Open decisions

None at design time. Brainstorming resolved:

- Savepoint wrap over deferred-commits / validate-then-persist (chosen).
- Defer `new_block_signal` emission to post-savepoint (chosen — defense-in-depth).
- No changes to `Node.add_block`'s `SQLAlchemyError` handler (correct under savepoint as-is).
- Single-PR spec + impl-plan; second-PR implementation (mirrors audit precedent).

## What comes next

- **Impl PR.** Executes this design. Branch `fix/a2e-fill-chain-atomicity`. Single commit (or commit-per-section if review surfaces issues mid-PR). Removes the xfail decorator; updates audit doc + ROADMAP.
- **Next audit remediations.** A4.c is the natural follow-on (next-priority Medium per the audit's Recommendations). Same brainstorm-spec-plan-impl flow.
- **API auth audit.** Still deliberately deferred. Can be picked up at any time independently of further remediation PRs.
