# A4.c remediation — coinbase-txid uniqueness check — design spec

**Status:** Draft for review
**Date:** 2026-05-30
**Scope:** Specifies a remediation for audit finding A4.c (Medium): a MILLER-role adversary can mine a block whose coinbase is a verbatim replay of any prior block's coinbase transaction, appending a duplicate `block_transactions` m2m row that inflates the original miller's longest-chain `wallet_balance` by one REWARD per replay. The fix adds a chain-lineage uniqueness check on the candidate coinbase's `txid` inside `Chain.validate_block_coinbase`. **This spec ships in a docs-only PR with its implementation plan;** the actual code changes ride a separate follow-up impl PR. When that impl PR lands it closes A4.c, removes the xfail demonstration test (which becomes a real pass under `strict=True`), and updates the audit doc + ROADMAP to reflect closure.

## Goal

Reject same-chain coinbase-txid replay at `Chain.validate_block_coinbase`. After the follow-up impl PR lands, a block whose coinbase reuses a `txid` already present in the candidate block's chain lineage raises `DuplicateCoinbaseError` and is not persisted.

## Non-goals

- **Cross-fork coinbase legitimacy.** Audit Attack b established that cross-fork transaction replay (including coinbase) is structurally legitimate — each chain's per-block recursive CTE keeps fork state independent, and the same coinbase associated with two competing chains' blocks does not inflate balances on either chain (each chain's longest-chain query is scoped to its own lineage). The fix preserves this: the chain-lineage walk only inspects ancestors reachable from the candidate block's parent (`Block.from_db(block.prev_hash)`), so a coinbase that exists only on a stale fork is not found and not rejected.
- **Cross-fork double-spend / classic PoW reorg attack** (audit A4.d note + A5.a/b cluster). Canonical PoW property; mitigation lives off-chain in recipient confirmation-depth policy, not in the validator. Out of scope.
- **Regular-transaction txid uniqueness beyond inflow consumption.** Already enforced by `validate_txn_inflow` + `get_inflows_count`; A4.c is specifically about coinbase txns where no inflow check runs (`Block.regular_txns` excludes the coinbase, so `validate_block_txn` is never called on it).
- **`Block.regular_txns` positional-coinbase rule.** The audit notes the coinbase is identified by being the last txn (`self.txns[0:-1]` and `self.coinbase = self.last_txn`), not by an authoritative flag. Restructuring that is out of scope; A4.c's chain-lineage check works regardless of how the coinbase is identified.
- **Standalone coinbase replay via `/api/transaction`** (audit A4.c.i). Already rejected at the receive-transaction path by `RegularTransactionModel`'s `inflows: min_length=1` schema check. No code change needed.
- **`Block.to_dao()` / `Transaction.to_dao()` returning existing rows** (the root-cause mechanism A4.c exploits, also implicated in A2.e's signal-emission contract violation that PR #87 round-4 addressed). Changing that contract has cross-cutting blast radius beyond A4.c. The validation-level check this spec proposes is a sufficient remediation; a future PR could narrow the to_dao contract separately.

## Decisions taken during brainstorming

- **Chain-lineage check via `self.get_transaction(cb.txid)`** (Approach A) over DB-wide `TransactionDAO.get(cb.txid)` (Approach B) or protocol-level coinbase-author binding (Approach C). Approach A matches the existing chain-scoped validation pattern (`validate_txn_inflow` calls `self.get_transaction(outflow_txid, start_block=block)` for inflow uniqueness — analogous logic, slightly different scope). Approach B would reject cross-fork coinbase replay incorrectly, regressing Attack b's documented legitimacy. Approach C would require introducing a "block author" protocol field; out of scope for a Medium-severity accounting bug.
- **`self.get_transaction(cb.txid, start_block=parent)` with an explicit parent** (`parent = Block.from_db(block.prev_hash)`) over either `start_block=block` (would find the cb itself in `block.txns`) or the `self.last_block` default (correct only in the add-block path, but **wrong** during `Chain.validate()` revalidation, where `self.last_block` is the chain tip and `block` is an interior block — the walk would include `block` itself and falsely flag every coinbase). Computing the parent from `block.prev_hash` is correct in both contexts and skips cleanly for genesis (`Block.from_db` returns `None`). This was the key correction from PR #88 round-3 review.
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
        # A4.c: reject same-chain coinbase replay. Start the lookup from
        # the candidate block's PARENT, not self.last_block. During
        # Chain.validate() full-chain revalidation, self.last_block is the
        # chain tip while `block` is an interior block, so a default
        # start_block=self.last_block walk would include `block` itself
        # (and its descendants) and find the candidate's own coinbase —
        # falsely flagging every block. Searching the parent's lineage
        # instead finds the cb only if it was already persisted UPSTREAM
        # in THIS chain. Cross-fork replay (Attack b) stays legitimate
        # because the walk is chain-scoped via the per-block recursive CTE
        # in BlockDAO.get_transaction_in_chain. A genesis block has no
        # findable parent (Block.from_db returns None), so the check is
        # skipped — a genesis coinbase can't be a replay of anything.
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

Note: `validate_block` (the sole caller) already computes
`prev_block = Block.from_db(block.prev_hash)` for its own prev-hash /
ordering checks; `validate_block_coinbase` recomputes it independently
to keep its signature stable for any other caller. `Block.from_db` is an
indexed single-row lookup, so the duplicate read is negligible.

### The new exception

`src/cancelchain/exceptions.py`, added next to the existing `InvalidCoinbaseError` / `InvalidCoinbaseErrorRewardError`:

```python
class DuplicateCoinbaseError(InvalidCoinbaseError):
    pass
```

### Why the scoping is correct

Three properties make this clean:

1. **The lookup starts from the candidate block's parent (`Block.from_db(block.prev_hash)`), not `self.last_block`.** This is correct in both calling contexts:
   - **Add-block path** (`Chain.from_db(block_hash=block.prev_hash)` → `add_block` → `validate_block` → `validate_block_coinbase`): the candidate block isn't persisted yet; `parent` is the chain tip. Searching the parent's lineage searches everything before the candidate. The candidate's own (fresh) coinbase isn't in that lineage, so no false positive.
   - **Full-chain revalidation path** (`Chain.validate()` loops `self.blocks`, calling `validate_block(block)` with `self.last_block` pinned to the chain tip): `block` is an interior block, NOT the tip. Starting from `self.last_block` (the tip) would walk back through the whole chain *including `block` itself*, finding the candidate's own coinbase and falsely raising on every block. Starting from `block`'s parent searches only blocks strictly upstream of `block`, so a legitimately-unique coinbase is never flagged. This is the bug this design avoids by computing `parent` explicitly.
   - **Genesis:** `block.prev_hash` is `None` or `GENESIS_HASH`; `Block.from_db` returns `None`, so `parent is None` and the check is skipped — a genesis coinbase precedes nothing and can't be a replay.

2. **The walk is chain-scoped.** `Chain.get_transaction` follows `prev_hash` links via `Block.from_db(prev_hash)` (chain.py:302) and defers to `BlockDAO.get_transaction_in_chain` once it reaches a persisted ancestor (chain.py:306-308). `get_transaction_in_chain` uses the per-block recursive CTE `_block_chain` scoped to that ancestor's lineage. A coinbase that exists on a competing fork is never found by a walk through this chain's lineage. Cross-fork legitimacy preserved.

3. **The check fires before the reward check.** Order is behaviorally irrelevant (a duplicate cb would also pass the reward check, since it was previously valid), but the duplicate-coinbase error is more diagnostic than the reward error for the A4.c attack surface.

### Block-replay false positive (non-issue)

If the same legitimate block is received twice (e.g., network glitch), the second receive's coinbase txid would be in `BlockDAO` and the walk would find it. **But this case is already caught earlier** at `Node.process_block:175`: `if block.block_hash and Block.from_db(block.block_hash): return None`. Block-level duplicate suppression runs before `Chain.validate_block_coinbase`, so the false-positive scenario never reaches the new check.

## Changes

### Files (in scope)

- **Modify:** `src/cancelchain/chain.py` — `Chain.validate_block_coinbase` (line 278) gains a 3-line check + 9-line explanatory comment. ~12 lines added.
- **Modify:** `src/cancelchain/exceptions.py` — adds `class DuplicateCoinbaseError(InvalidCoinbaseError): pass` next to the existing `InvalidCoinbaseErrorRewardError`. ~3 lines added (with blank-line separator).
- **Modify:** `tests/test_verification_audit.py` — (1) removes the `@pytest.mark.xfail(strict=True)` decorator on `test_a4_c_ii_coinbase_replay_inflates_balance` (its body unchanged save a post-fix docstring refresh); (2) adds `test_a4_c_cross_fork_coinbase_replay_accepted`, a non-regression test that builds a competing sibling fork, replays a canonical-fork coinbase onto it, and asserts `validate_block_coinbase` does NOT raise `DuplicateCoinbaseError` (guarding the new check's primary edge case of over-rejecting cross-fork replay); (3) adds `Chain` and `DuplicateCoinbaseError` to the module imports.
- **Modify:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`:
  - Remove the A4.c row from the Findings table.
  - Update the per-attack outcome in §Adversary 4 → Attack c.ii from "ACCEPTED at step 4 ... Finding A4.c — Severity Medium: ..." to "REJECTED (fixed by impl PR following from `docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md`) ... Result: Validation correctly rejects (post-remediation). No finding."
  - Update the Executive summary's finding count from "Six findings were originally confirmed ... five remain open" (current state on `main`, post-A2.e closure) to "Six findings were originally confirmed ... two have since been remediated (A2.e, A4.c); four remain open." Severity breakdown becomes "0 Critical / 0 High / 0 Medium / 4 Low (post-A4.c)".
  - **Update the Recommendations section's A4.c entry** (§2 "A4.c (Medium) — coinbase-uniqueness check"). The audit's original remediation sketch (both there and in the Attack c.ii consequence paragraph) proposed `self.get_transaction(cb.txid, start_block=block)` paired with an m2m self-exclusion caveat (because starting from `block` itself would otherwise match the candidate's own coinbase). The implemented design is cleaner — `start_block=parent` (where `parent = Block.from_db(block.prev_hash)`) never includes the candidate, so no self-exclusion caveat is needed. Mark the recommendation as implemented (✅, with the impl PR link) and note the `start_block=parent` design so the historical guidance does not contradict the shipped code.
- **Modify:** `docs/superpowers/ROADMAP.md` — move the A4.c entry from the open "Audit remediation — verification pipeline findings" list to the "Closed items (historical reference)" section, with PR links.

### Files (read but not modified)

- `src/cancelchain/block.py` — `Block.coinbase` (positional `last_txn`), `Block.validate_coinbase`, `Block.regular_txns` — confirmed the existing positional-coinbase rule doesn't require change.
- `src/cancelchain/transaction.py` — `Transaction.to_dao` (line 257) — confirmed the existing-row return is the root-cause mechanism; the validation-level fix is sufficient without changing this contract.
- `src/cancelchain/models.py` — `BlockDAO.get_transaction_in_chain`, the per-block recursive CTE `_block_chain` — confirmed the walk is chain-scoped.
- `tests/conftest.py` — existing fixtures (`app`, `wallet`, `time_machine`) used by the A4.c demonstration test.

## Test plan

- **A4.c demonstration test** (`test_a4_c_ii_coinbase_replay_inflates_balance`) goes from xfail to real pass after decorator removal. CI's `pytest` step verifies.
- **`pytest --runxfail tests/test_verification_audit.py`** still shows the remaining 4 xfails fail (sanity: no other finding was accidentally caught by the new check).
- **`uv run pytest` total**: was `237 passed, 5 xfailed, 1 skipped` (post-A2.e); becomes `239 passed, 4 xfailed, 1 skipped` (A4.c un-xfailed +1, plus a new cross-fork non-regression test +1).
- **Regression coverage for the modified methods**: `Chain.validate_block_coinbase` is exercised across `tests/test_chain.py`, `tests/test_block.py`, `tests/test_miller.py`, and `tests/test_models.py` via normal block-add flows. All those tests construct fresh coinbase txns; none replay a prior coinbase, so none should regress under the new check.
- **Manual smoke**: `docker build --target builder -t cc-a4c-final .` to confirm the SQLAlchemy / Python imports load cleanly under the production Python config (no model changes, but worth confirming).

## Acceptance

- `src/cancelchain/chain.py:Chain.validate_block_coinbase` contains the `self.get_transaction(cb.txid)` check raising `DuplicateCoinbaseError`.
- `src/cancelchain/exceptions.py` defines `class DuplicateCoinbaseError(InvalidCoinbaseError)`.
- `tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance` has no `@pytest.mark.xfail` decorator and passes.
- `uv run pytest 2>&1 | tail -3` shows `239 passed, 4 xfailed, 1 skipped`.
- `uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3` shows `4 failed, 3 passed` (A2.e, A4.c, and the cross-fork non-regression test pass; remaining 4 findings still demonstrate gaps).
- `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, and the `cancelchain db check` gate all exit 0.
- Audit doc's Findings table no longer lists A4.c; the per-attack trace in §Adversary 4 → Attack c.ii records the fix and links to this spec; the Executive summary's count is updated to reflect 4 open + 2 closed.
- ROADMAP's open "Audit remediation" entry no longer lists A4.c; "Closed items" gains an A4.c entry with both the docs PR and impl PR links.
- `docker build --target builder -t cc-a4c-final .` succeeds.

## Risks

### Risk: `get_transaction` walk performance on long chains

For receive_block (single-block extension), the walk hits BlockDAO at the immediate parent — O(1) DB lookup + a recursive CTE join. For `fill_chain` (batch extension), the walk may traverse multiple in-flight blocks before hitting persistence. **Mitigation:** equivalent cost to the existing `validate_txn_inflow` call which already runs `self.get_transaction(outflow_txid, start_block=block)` per regular txn. Coinbase txns run the check exactly once per block (one coinbase per block), so the overhead per block is one additional walk — negligible vs. the existing per-txn walks. No new bench gate needed.

### Risk: caller context where `self.last_block` is not the candidate's parent

This was a real bug in the initial design (which used the `self.last_block` default) and is **resolved** by computing `parent = Block.from_db(block.prev_hash)` explicitly. The check no longer depends on `self.last_block` at all, so it is correct regardless of what chain the `validate_block_coinbase` instance represents:
- **`Chain.validate()` full-chain revalidation** pins `self.last_block` to the tip while validating interior blocks — the original default-`start_block` design would have falsely flagged every coinbase here, breaking `cancelchain validate`. The explicit-parent design searches only blocks strictly upstream of the candidate, so revalidation passes.
- **Add-block** (`Chain.from_db(block_hash=block.prev_hash)` → `validate_block`) — `self.last_block` happens to equal the parent here, but the explicit-parent lookup produces the same result, so the change is behavior-preserving for this path.

**Mitigation:** the impl plan's regression suite includes a full run of `tests/test_chain.py` / `tests/test_miller.py` (which exercise `Chain.validate` and multi-block add flows) plus the new cross-fork non-regression test; a `cancelchain validate`-style revalidation regression would surface as unexpected `DuplicateCoinbaseError` raises in those tests.

### Risk: the demonstration test asserts the wrong exception

The test asserts `pytest.raises(InvalidCoinbaseError)`. `DuplicateCoinbaseError(InvalidCoinbaseError)` matches via subclass — `pytest.raises` accepts. If a future refactor changes the exception hierarchy (e.g., reparents `DuplicateCoinbaseError` to a different root), the assertion may stop matching. **Mitigation:** the impl plan keeps the new exception's inheritance chain explicit in the diff; CI reruns the test on every PR, catching any hierarchy change.

### Risk: cross-fork coinbase replay is incorrectly rejected

The walk is chain-scoped (per Architecture point 2), so cross-fork replay should stay legitimate. This is the new check's primary edge case — over-rejecting the structurally-legitimate cross-fork replay documented in audit Attack b. **Mitigation (two layers):** (1) the new check reuses `self.get_transaction`, the *identical* chain-scoped method already used (and proven correct) by `Chain.validate_txn_inflow` for inflow uniqueness — the audit's Attack b analysis already established this method scopes to lineage via the per-block recursive CTE in `BlockDAO.get_transaction_in_chain`. (2) the impl plan adds an explicit non-regression test, `test_a4_c_cross_fork_coinbase_replay_accepted` (plan Task 5), that builds a competing sibling fork, replays a canonical-fork coinbase onto it, and asserts `validate_block_coinbase` does NOT raise `DuplicateCoinbaseError`. If a future refactor inadvertently makes `get_transaction_in_chain` DB-wide, that test fails and the regression surfaces before merge.

### Risk: the check fires on duplicate-receive-block scenarios

If a peer or sync race re-receives the same block, the duplicate-coinbase check would match. **Mitigation:** `Node.process_block:175` checks `Block.from_db(block.block_hash)` and returns None for duplicates before validation runs. The new check is never reached for a duplicate block.

## Open decisions

None at design time. Brainstorming resolved:

- Chain-lineage check (Approach A) over DB-wide or protocol-level alternatives.
- `self.get_transaction(cb.txid, start_block=parent)` with `parent = Block.from_db(block.prev_hash)` over the `self.last_block` default (which breaks `Chain.validate()` revalidation) or `start_block=block` (which finds the cb itself).
- New `DuplicateCoinbaseError(InvalidCoinbaseError)` subclass over reusing `InvalidCoinbaseError`.
- Check fires before the reward check (order is behaviorally irrelevant; diagnostic preference).
- Single-PR spec + impl-plan; second-PR implementation (mirrors PR #86/#87 precedent).

## What comes next

- **Impl PR.** Executes this design. Branch `fix/a4c-coinbase-uniqueness`. Touches `chain.py`, `exceptions.py`, `tests/test_verification_audit.py`, audit doc, ROADMAP. Removes the xfail decorator; updates closure markers.
- **Next audit remediations** (per current ROADMAP order after A2.e + A4.c close): A7.b (alternate-genesis admission — Low, two-for-one with A7.j), then A7.h (non-printable subject chars — Low), then A7.e (TXN_TIMEOUT operator inconsistency — Low), then A1.f (mempool replay of mined txids — Low). Same brainstorm-spec-plan-impl flow per remediation.
- **API auth audit.** Still deliberately deferred. Can be picked up at any time independently of further remediation PRs.
