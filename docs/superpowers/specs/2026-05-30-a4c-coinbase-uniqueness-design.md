# A4.c remediation — coinbase-txid uniqueness check — design spec

**Status:** Draft for review
**Date:** 2026-05-30
**Scope:** Specifies a remediation for audit finding A4.c (Medium): a MILLER-role adversary can mine a block whose coinbase is a verbatim replay of any prior block's coinbase transaction, appending a duplicate `block_transactions` m2m row that inflates the original miller's longest-chain `wallet_balance` by one REWARD per replay. The fix adds a chain-lineage uniqueness check on the candidate coinbase's `txid` inside `Chain.validate_block_coinbase`. **This spec ships in a docs-only PR with its implementation plan;** the actual code changes ride a separate follow-up impl PR. When that impl PR lands it closes A4.c, removes the xfail demonstration test (which becomes a real pass under `strict=True`), and updates the audit doc + ROADMAP to reflect closure.

## Goal

Reject same-chain coinbase-txid replay at `Chain.validate_block_coinbase`. After the follow-up impl PR lands, a block whose coinbase reuses a `txid` already present in the candidate block's chain lineage raises `DuplicateCoinbaseError` and is not persisted.

## Non-goals

- **Cross-fork coinbase legitimacy.** Audit Attack b established that cross-fork transaction replay (including coinbase) is structurally legitimate — each chain's per-block recursive CTE keeps fork state independent, and the same coinbase associated with two competing chains' blocks does not inflate balances on either chain (each chain's longest-chain query is scoped to its own lineage). The fix preserves this: the chain-lineage walk only inspects ancestors reachable from `self.last_block`, so a coinbase that exists only on a stale fork is not found and not rejected.
- **Cross-fork double-spend / classic PoW reorg attack** (audit A4.d note + A5.a/b cluster). Canonical PoW property; mitigation lives off-chain in recipient confirmation-depth policy, not in the validator. Out of scope.
- **Regular-transaction txid uniqueness beyond inflow consumption.** Already enforced by `validate_txn_inflow` + `get_inflows_count`; A4.c is specifically about coinbase txns where no inflow check runs (`Block.regular_txns` excludes the coinbase, so `validate_block_txn` is never called on it).
- **`Block.regular_txns` positional-coinbase rule.** The audit notes the coinbase is identified by being the last txn (`self.txns[0:-1]` and `self.coinbase = self.last_txn`), not by an authoritative flag. Restructuring that is out of scope; A4.c's chain-lineage check works regardless of how the coinbase is identified.
- **Standalone coinbase replay via `/api/transaction`** (audit A4.c.i). Already rejected at the receive-transaction path by `RegularTransactionModel`'s `inflows: min_length=1` schema check. No code change needed.
- **`Block.to_dao()` / `Transaction.to_dao()` returning existing rows** (the root-cause mechanism A4.c exploits, also implicated in A2.e's signal-emission contract violation that PR #87 round-4 addressed). Changing that contract has cross-cutting blast radius beyond A4.c. The validation-level check this spec proposes is a sufficient remediation; a future PR could narrow the to_dao contract separately.

## Decisions taken during brainstorming

- **Chain-lineage check via `self.get_transaction(cb.txid)`** (Approach A) over DB-wide `TransactionDAO.get(cb.txid)` (Approach B) or protocol-level coinbase-author binding (Approach C). Approach A matches the existing chain-scoped validation pattern (`validate_txn_inflow` calls `self.get_transaction(outflow_txid, start_block=block)` for inflow uniqueness — analogous logic, slightly different scope). Approach B would reject cross-fork coinbase replay incorrectly, regressing Attack b's documented legitimacy. Approach C would require introducing a "block author" protocol field; out of scope for a Medium-severity accounting bug.
- **`self.get_transaction(cb.txid)` without `start_block`** (defaults to `self.last_block`, the candidate block's parent) over `start_block=block` (would falsely find the cb itself in `block.txns`). For any chain instance whose `block_hash` equals the candidate's `prev_hash` (the standard path via `Chain.from_db(block_hash=block.prev_hash)`), `self.last_block = Block.from_db(self.block_hash)` returns the parent — exactly the right starting point.
- **New `DuplicateCoinbaseError(InvalidCoinbaseError)` exception class.** Mirrors the existing `InvalidCoinbaseErrorRewardError(InvalidCoinbaseError)` pattern for greppable failure-mode reporting. The xfail test asserts `pytest.raises(InvalidCoinbaseError)`, which matches the new subclass via inheritance — no test body change needed.
- **Check fires before the reward check.** A duplicate coinbase by definition once had the right reward (it was previously valid), so the reward check would also pass. Behaviorally order-independent, but surfacing the more fundamental issue first (the coinbase isn't ours) gives clearer error messages.
- **Single PR for spec + impl plan; second PR for implementation.** Mirrors the PR #86 + #87 precedent.

## Architecture

### The change site

`src/cancelchain/chain.py`, `Chain.validate_block_coinbase` (lines 278-285). Current:

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

After:

```python
def validate_block_coinbase(self, block: Block) -> None:
    block.validate_coinbase()
    reward = self.block_reward(block)
    cb = block.coinbase
    if cb is not None:
        # A4.c: reject same-chain coinbase replay. self.get_transaction
        # defaults start_block to self.last_block (the candidate block's
        # parent), walking the parent's lineage backward. The candidate
        # block itself is never inspected, so the cb is found only if
        # it's already persisted somewhere upstream in THIS chain. Cross-
        # fork replay (Attack b's case) stays legitimate because the walk
        # is chain-scoped via Block.from_db(prev_hash) traversal and the
        # underlying per-block recursive CTE in get_transaction_in_chain.
        if self.get_transaction(cb.txid) is not None:
            raise DuplicateCoinbaseError()
        outflow = cb.get_outflow(0)
        if outflow is not None and outflow.amount != reward:
            raise InvalidCoinbaseErrorRewardError()
```

### The new exception

`src/cancelchain/exceptions.py`, added next to the existing `InvalidCoinbaseError` / `InvalidCoinbaseErrorRewardError`:

```python
class DuplicateCoinbaseError(InvalidCoinbaseError):
    pass
```

### Why the scoping is correct

Three properties make this clean:

1. **`self.get_transaction(cb.txid)` defaults `start_block` to `self.last_block`** (chain.py:297). For any chain instance whose `block_hash` is the candidate block's `prev_hash` (the standard case via `Chain.from_db(block_hash=block.prev_hash)`), `self.last_block` returns the parent. The walk inspects ancestor blocks only, never the candidate. No false positive against the cb being validated.

2. **The walk is chain-scoped.** `Chain.get_transaction` follows `prev_hash` links via `Block.from_db(prev_hash)` (chain.py:302) and defers to `BlockDAO.get_transaction_in_chain` once it reaches a persisted ancestor (chain.py:306-308). `get_transaction_in_chain` uses the per-block recursive CTE `_block_chain` scoped to that ancestor's lineage. A coinbase that exists on a competing fork is never found by a walk through this chain's lineage. Cross-fork legitimacy preserved.

3. **The check fires before the reward check.** Order is behaviorally irrelevant (a duplicate cb would also pass the reward check, since it was previously valid), but the duplicate-coinbase error is more diagnostic than the reward error for the A4.c attack surface.

### Block-replay false positive (non-issue)

If the same legitimate block is received twice (e.g., network glitch), the second receive's coinbase txid would be in `BlockDAO` and the walk would find it. **But this case is already caught earlier** at `Node.process_block:175`: `if block.block_hash and Block.from_db(block.block_hash): return None`. Block-level duplicate suppression runs before `Chain.validate_block_coinbase`, so the false-positive scenario never reaches the new check.

## Changes

### Files (in scope)

- **Modify:** `src/cancelchain/chain.py` — `Chain.validate_block_coinbase` (line 278) gains a 3-line check + 9-line explanatory comment. ~12 lines added.
- **Modify:** `src/cancelchain/exceptions.py` — adds `class DuplicateCoinbaseError(InvalidCoinbaseError): pass` next to the existing `InvalidCoinbaseErrorRewardError`. ~3 lines added (with blank-line separator).
- **Modify:** `tests/test_verification_audit.py` — removes the `@pytest.mark.xfail(strict=True)` decorator on `test_a4_c_ii_coinbase_replay_inflates_balance`. Test body unchanged.
- **Modify:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`:
  - Remove the A4.c row from the Findings table.
  - Update the per-attack outcome in §Adversary 4 → Attack c.ii from "ACCEPTED at step 4 ... Finding A4.c — Severity Medium: ..." to "REJECTED (fixed by impl PR following from `docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md`) ... Result: Validation correctly rejects (post-remediation). No finding."
  - Update the Executive summary's finding count from "Six findings were originally confirmed ... five remain open" (current state on `main`, post-A2.e closure) to "Six findings were originally confirmed ... two have since been remediated (A2.e, A4.c); four remain open." Severity breakdown becomes "0 Critical / 0 High / 0 Medium / 4 Low (post-A4.c)".
- **Modify:** `docs/superpowers/ROADMAP.md` — move the A4.c entry from the open "Audit remediation — verification pipeline findings" list to the "Closed items (historical reference)" section, with PR links.

### Files (read but not modified)

- `src/cancelchain/block.py` — `Block.coinbase` (positional `last_txn`), `Block.validate_coinbase`, `Block.regular_txns` — confirmed the existing positional-coinbase rule doesn't require change.
- `src/cancelchain/transaction.py` — `Transaction.to_dao` (line 257) — confirmed the existing-row return is the root-cause mechanism; the validation-level fix is sufficient without changing this contract.
- `src/cancelchain/models.py` — `BlockDAO.get_transaction_in_chain`, the per-block recursive CTE `_block_chain` — confirmed the walk is chain-scoped.
- `tests/conftest.py` — existing fixtures (`app`, `wallet`, `time_machine`) used by the A4.c demonstration test.

## Test plan

- **A4.c demonstration test** (`test_a4_c_ii_coinbase_replay_inflates_balance`) goes from xfail to real pass after decorator removal. CI's `pytest` step verifies.
- **`pytest --runxfail tests/test_verification_audit.py`** still shows the remaining 4 xfails fail (sanity: no other finding was accidentally caught by the new check).
- **`uv run pytest` total**: was `237 passed, 5 xfailed, 1 skipped` (post-A2.e); becomes `238 passed, 4 xfailed, 1 skipped`.
- **Regression coverage for the modified methods**: `Chain.validate_block_coinbase` is exercised across `tests/test_chain.py`, `tests/test_block.py`, `tests/test_miller.py`, and `tests/test_models.py` via normal block-add flows. All those tests construct fresh coinbase txns; none replay a prior coinbase, so none should regress under the new check.
- **Manual smoke**: `docker build --target builder -t cc-a4c-final .` to confirm the SQLAlchemy / Python imports load cleanly under the production Python config (no model changes, but worth confirming).

## Acceptance

- `src/cancelchain/chain.py:Chain.validate_block_coinbase` contains the `self.get_transaction(cb.txid)` check raising `DuplicateCoinbaseError`.
- `src/cancelchain/exceptions.py` defines `class DuplicateCoinbaseError(InvalidCoinbaseError)`.
- `tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance` has no `@pytest.mark.xfail` decorator and passes.
- `uv run pytest 2>&1 | tail -3` shows `238 passed, 4 xfailed, 1 skipped`.
- `uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3` shows `4 failed, 2 passed` (A2.e and A4.c pass; remaining 4 findings still demonstrate gaps).
- `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, and the `cancelchain db check` gate all exit 0.
- Audit doc's Findings table no longer lists A4.c; the per-attack trace in §Adversary 4 → Attack c.ii records the fix and links to this spec; the Executive summary's count is updated to reflect 4 open + 2 closed.
- ROADMAP's open "Audit remediation" entry no longer lists A4.c; "Closed items" gains an A4.c entry with both the docs PR and impl PR links.
- `docker build --target builder -t cc-a4c-final .` succeeds.

## Risks

### Risk: `get_transaction` walk performance on long chains

For receive_block (single-block extension), the walk hits BlockDAO at the immediate parent — O(1) DB lookup + a recursive CTE join. For `fill_chain` (batch extension), the walk may traverse multiple in-flight blocks before hitting persistence. **Mitigation:** equivalent cost to the existing `validate_txn_inflow` call which already runs `self.get_transaction(outflow_txid, start_block=block)` per regular txn. Coinbase txns run the check exactly once per block (one coinbase per block), so the overhead per block is one additional walk — negligible vs. the existing per-txn walks. No new bench gate needed.

### Risk: a future caller invokes `Chain.validate_block_coinbase` with `self.block_hash != block.prev_hash`

The check assumes `self.last_block` resolves to the candidate's parent. If a caller constructs a `Chain` instance whose `block_hash` differs from the candidate's `prev_hash`, the walk would start from the wrong block. **Mitigation:** all current callers (`Chain.validate_block` at chain.py:198) flow through `Chain.from_db(block_hash=block.prev_hash)` → `chain.add_block(block)` → `chain.validate_block(block)` → `validate_block_coinbase(block)`. The `self.block_hash = block.prev_hash` invariant holds end-to-end. If a future refactor breaks that invariant, the impl plan's regression test suite catches it (existing tests construct chain instances and validate; misaligned starting points would surface as unexpected `DuplicateCoinbaseError` raises).

### Risk: the demonstration test asserts the wrong exception

The test asserts `pytest.raises(InvalidCoinbaseError)`. `DuplicateCoinbaseError(InvalidCoinbaseError)` matches via subclass — `pytest.raises` accepts. If a future refactor changes the exception hierarchy (e.g., reparents `DuplicateCoinbaseError` to a different root), the assertion may stop matching. **Mitigation:** the impl plan keeps the new exception's inheritance chain explicit in the diff; CI reruns the test on every PR, catching any hierarchy change.

### Risk: cross-fork coinbase replay is incorrectly rejected

The walk is chain-scoped (per Architecture point 2), so cross-fork replay should stay legitimate. **Mitigation:** the impl plan includes a manual review step against `Chain.get_transaction` + `BlockDAO.get_transaction_in_chain` to confirm the per-block CTE scoping is preserved. If a future refactor inadvertently makes `get_transaction_in_chain` DB-wide, this check would over-reject and the audit's Attack b would become a finding. The impl plan tests for the cross-fork case explicitly via a new test (deferred to Task 4 — see plan).

### Risk: the check fires on duplicate-receive-block scenarios

If a peer or sync race re-receives the same block, the duplicate-coinbase check would match. **Mitigation:** `Node.process_block:175` checks `Block.from_db(block.block_hash)` and returns None for duplicates before validation runs. The new check is never reached for a duplicate block.

## Open decisions

None at design time. Brainstorming resolved:

- Chain-lineage check (Approach A) over DB-wide or protocol-level alternatives.
- `self.get_transaction(cb.txid)` (default `start_block=self.last_block`) over `start_block=block`.
- New `DuplicateCoinbaseError(InvalidCoinbaseError)` subclass over reusing `InvalidCoinbaseError`.
- Check fires before the reward check (order is behaviorally irrelevant; diagnostic preference).
- Single-PR spec + impl-plan; second-PR implementation (mirrors PR #86/#87 precedent).

## What comes next

- **Impl PR.** Executes this design. Branch `fix/a4c-coinbase-uniqueness`. Touches `chain.py`, `exceptions.py`, `tests/test_verification_audit.py`, audit doc, ROADMAP. Removes the xfail decorator; updates closure markers.
- **Next audit remediations** (per current ROADMAP order after A2.e + A4.c close): A7.b (alternate-genesis admission — Low, two-for-one with A7.j), then A7.h (non-printable subject chars — Low), then A7.e (TXN_TIMEOUT operator inconsistency — Low), then A1.f (mempool replay of mined txids — Low). Same brainstorm-spec-plan-impl flow per remediation.
- **API auth audit.** Still deliberately deferred. Can be picked up at any time independently of further remediation PRs.
