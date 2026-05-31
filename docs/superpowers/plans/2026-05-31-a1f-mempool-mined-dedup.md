# A1.f — Reject Already-Mined Txids from the Mempool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject already-mined txids from `Node.receive_transaction` so replays can't inflate the mempool — closing the **last** open audit finding.

**Architecture:** One global `TransactionDAO.get(txn.txid)` lookup in `receive_transaction` (after `validate()`, before the pending-add), raising a new `DuplicateMinedTransactionError`. No schema change.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, pytest + time-machine, uv.

**Spec:** `docs/superpowers/specs/2026-05-31-a1f-mempool-mined-dedup-design.md`

---

## Prerequisites (read before starting)

- **Full-suite pytest needs `COLUMNS=200`** (latent unrelated `test_command.py::test_create_wallet` bug). Use `COLUMNS=200 uv run pytest` for full-suite runs.
- `TransactionDAO.get(txid)` is `db.select(cls).filter_by(txid=txid).scalar_one_or_none()`; `txid` is unique-indexed, so no `MultipleResultsFound` risk and the lookup is O(1).
- `test_a1_f_mined_txid_replay_into_pending` is `@pytest.mark.xfail(strict=True)`. The fix makes it pass, so its xfail MUST be removed in the **same commit** (strict xfail → xpass → CI failure). Remove it first (Step 1).
- `tests/test_node.py` does NOT exist. Node-level tests live in `tests/test_miller.py` (Miller extends Node).
- ruff isort uses `order-by-type` (constants, then classes, then functions).

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cancelchain/exceptions.py` | exception hierarchy | Add `DuplicateMinedTransactionError(InvalidTransactionError)` |
| `src/cancelchain/node.py` | mempool admission | Add the `TransactionDAO.get` check in `receive_transaction`; extend two imports |
| `tests/test_verification_audit.py` | audit regression | Un-xfail `test_a1_f…`; tighten its assertion |
| `tests/test_miller.py` | Node/Miller tests | Add a mined-replay regression test |
| `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` | audit record | Mark A1.f remediated; audit → fully closed |
| `docs/superpowers/ROADMAP.md` | roadmap | All findings remediated; A1.f → Closed |

---

### Task 1: `DuplicateMinedTransactionError` + the receive-path check (un-xfail A1.f)

**Files:**
- Modify: `src/cancelchain/exceptions.py`
- Modify: `src/cancelchain/node.py` (imports + `receive_transaction` ~lines 77-101)
- Modify: `tests/test_verification_audit.py` (remove A1.f xfail; tighten assertion)
- Modify: `tests/test_miller.py` (add regression)

- [ ] **Step 1: Un-xfail the acceptance demonstrator**

In `tests/test_verification_audit.py`, delete the `@pytest.mark.xfail(...)` decorator block directly above `def test_a1_f_mined_txid_replay_into_pending` (line 72). The decorator to remove is exactly:

```python
@pytest.mark.xfail(
    reason=(
        'Audit finding A1.f — severity Low — Node.receive_transaction '
        'does not reject txids that already exist in the persisted chain '
        '(TransactionDAO), so an adversary can replay any mined '
        'transaction back into the pending pool where it lives until '
        'TXN_TIMEOUT (4h). The chain is unaffected — block assembly '
        'filters mined txids out — but the pending pool can be inflated '
        'with stale entries. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
```

- [ ] **Step 2: Tighten the acceptance assertion + add imports**

In `tests/test_verification_audit.py`, add `DuplicateMinedTransactionError` to the `from cancelchain.exceptions import (...)` block (sorted — `DuplicateGenesisError` before `DuplicateMinedTransactionError`, since "G" < "M"):

```python
from cancelchain.exceptions import (
    DuplicateGenesisError,
    DuplicateMinedTransactionError,
    InvalidCoinbaseError,
    InvalidTransactionError,
    MismatchedCoinbaseError,
    MissingBlockError,
)
```

In `test_a1_f_mined_txid_replay_into_pending`, change the final assertion from the base class to the specific exception:
```python
        with pytest.raises(DuplicateMinedTransactionError):
            m.receive_transaction(t.txid, t.to_json())
```
Leave the rest of the test body unchanged.

- [ ] **Step 3: Add the regression test in test_miller.py**

`tests/test_miller.py` already imports `datetime`, `pytest`, `Inflow`, `Outflow`, `Transaction`, `Miller`, `now`. Its exceptions import is a single line: `from cancelchain.exceptions import InsufficientFundsError`. Adding a second name exceeds 80 chars, so rewrite it to the parenthesized multi-line form (alphabetical: D before I):
```python
from cancelchain.exceptions import (
    DuplicateMinedTransactionError,
    InsufficientFundsError,
)
```
Then append:

```python
def test_mined_txn_replay_rejected(app, time_machine, wallet):
    """A1.f: a fresh txn is admitted to pending, but replaying it after
    it is mined raises DuplicateMinedTransactionError (and is not re-added)."""
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        assert cb0 is not None
        cb0_amount = next(iter(cb0.outflows)).amount
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        # A fresh (never-mined) txn is admitted to pending.
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        m.receive_transaction(t.txid, t.to_json())
        assert t in m.pending_txns
        # Mine it, then drain pending (cross-node replay scenario).
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        b1 = m.create_block()
        m.mill_block(b1)
        for ptxn in list(m.pending_txns):
            m.pending_txns.discard(ptxn)
        assert len(m.pending_txns) == 0
        # Replaying the now-mined txn is rejected and not re-added.
        with pytest.raises(DuplicateMinedTransactionError):
            m.receive_transaction(t.txid, t.to_json())
        assert len(m.pending_txns) == 0
```

- [ ] **Step 4: Run to verify failure**

Run:
```bash
uv run pytest tests/test_verification_audit.py::test_a1_f_mined_txid_replay_into_pending tests/test_miller.py::test_mined_txn_replay_rejected -v
```
Expected: FAIL. Both modules error at collection (`ImportError: cannot import name 'DuplicateMinedTransactionError'`) until Step 5 defines it. (That collection error IS the red signal.)

- [ ] **Step 5: Add the exception**

In `src/cancelchain/exceptions.py`, immediately after the `class InvalidTransactionError(CCError):\n    pass` block, add:

```python
class DuplicateMinedTransactionError(InvalidTransactionError):
    pass
```

- [ ] **Step 6: Add the check in receive_transaction**

In `src/cancelchain/node.py`:

(a) Add `TransactionDAO` to the `from cancelchain.models import (...)` block (order-by-type: classes then functions →):
```python
from cancelchain.models import (
    ChainDAO,
    ChainFill,
    ChainFillBlock,
    TransactionDAO,
    rollback_session,
)
```

(b) Add `DuplicateMinedTransactionError` to the `from cancelchain.exceptions import (...)` block (alphabetical, first):
```python
from cancelchain.exceptions import (
    DuplicateMinedTransactionError,
    InvalidBlockError,
    InvalidBlockHashError,
    InvalidTransactionIdError,
    MissingBlockError,
)
```

(c) In `receive_transaction`, insert the check between `txn.validate()` and the `if txn not in self.pending_txns:` guard. The method currently reads:
```python
        if txid != txn.txid:
            raise InvalidTransactionIdError()
        txn.validate()
        if txn not in self.pending_txns:
```
Change to:
```python
        if txid != txn.txid:
            raise InvalidTransactionIdError()
        txn.validate()
        if TransactionDAO.get(txn.txid) is not None:
            raise DuplicateMinedTransactionError()
        if txn not in self.pending_txns:
```

- [ ] **Step 7: Run to verify pass**

Run:
```bash
uv run pytest tests/test_verification_audit.py::test_a1_f_mined_txid_replay_into_pending tests/test_miller.py::test_mined_txn_replay_rejected -v
```
Expected: both PASS.

- [ ] **Step 8: Regression check (normal admission still works)**

Run:
```bash
COLUMNS=200 uv run pytest tests/test_miller.py tests/test_api.py tests/test_chain.py -q
```
Expected: PASS — existing tests that submit fresh (never-mined) txns via `receive_transaction` (`test_miller.py::test_duplicate_transaction`, `test_expired_transaction`, etc.) still admit them. (A fresh txn's txid isn't in `TransactionDAO`, so the new check is a no-op for it.)

- [ ] **Step 9: Lint/type, then commit**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean (run `ruff check --fix` if it flags import sorting). Then:
```bash
git add src/cancelchain/exceptions.py src/cancelchain/node.py tests/test_verification_audit.py tests/test_miller.py
git commit -m "fix(a1f): reject already-mined txids from the mempool"
```

---

### Task 2: Docs — audit fully closed + ROADMAP

**Files:**
- Modify: `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`
- Modify: `docs/superpowers/ROADMAP.md`

> All Find/Replace edits below are **substring replacements** (use the Edit tool): match the quoted "Find" and replace only that span, preserving other text on the line.

- [ ] **Step 1: Update the audit doc**

(a) Intro count (line 9) — Find exactly:
```
Six findings were originally confirmed (all Medium or Low; no Critical or High). Five have since been remediated (A2.e, A4.c, A7.b, A7.h, A7.e); one remains open (A1.f). Each open finding is paired with a `@pytest.mark.xfail(strict=True)` demonstration in `tests/test_verification_audit.py`.
```
Replace with:
```
Six findings were originally confirmed (all Medium or Low; no Critical or High). **All six have since been remediated** (A2.e, A4.c, A7.b, A7.h, A7.e, A1.f); none remain open. Each was paired with a `@pytest.mark.xfail(strict=True)` demonstration in `tests/test_verification_audit.py`, now converted to a passing regression test.
```

(b) Findings-table count + the table itself (lines 38-42) — replace the count line AND the now-single-row table with a closure note. Find exactly:
```
1 open finding: 0 Critical / 0 High / 0 Medium / 1 Low (post-A7.e). Sorted by severity (highest first), then by ID within each severity.

| ID | Severity | Description | Remediation sketch | Test |
|---|---|---|---|---|
| A1.f | Low | `Node.receive_transaction` does not check whether a candidate txn's txid is already in `TransactionDAO`, so any actor can replay mined txids back into the pending pool, where each entry lives for `TXN_TIMEOUT = 4h` until expiry. The chain itself is unaffected (block-assembly filters mined txids out), but the pool can be inflated, increasing read/walk costs for `/api/transaction/pending` and `Miller.pending_chain_txns`. | In `Node.receive_transaction` (`src/cancelchain/node.py:76`), before `self.pending_txns.add(txn)`, look up `TransactionDAO.get(txn.txid)`; raise a new `DuplicateMinedTransactionError(InvalidTransactionError)` on hit so the rejection is observable as a 400 to the submitter. | `test_a1_f_mined_txid_replay_into_pending` |
```
Replace with:
```
0 open findings: 0 Critical / 0 High / 0 Medium / 0 Low (post-A1.f). **The verification-pipeline audit is fully remediated** — all six findings (A2.e, A4.c, A7.b, A7.h, A7.e, A1.f) are closed. See the Recommendations section for the as-implemented details of each fix and the per-adversary traces below for the original analysis.
```

(c) A1.f sub-attack Outcome (line 179) — Find exactly:
```
**Outcome:** ACCEPTED at step 6 (no rejection occurred; gap exists). T is not re-included in a block (step 7), so the chain stays correct, but the pending pool now carries a stale duplicate.
```
Replace with:
```
**Outcome:** RESOLVED (post-remediation). `Node.receive_transaction` now rejects T at admission with `DuplicateMinedTransactionError` (a `TransactionDAO.get(T.txid)` hit), so the replay never enters the pending pool. (Pre-remediation, the replay was ACCEPTED into pending — the chain stayed correct since block assembly never re-included T, but the pool carried a stale duplicate until expiry.)
```

(d) Finding A1.f paragraph (line 181) — Find exactly:
```
**Finding A1.f — Severity Low:** `Node.receive_transaction` does not check whether a candidate txn's txid is already present in the persisted chain (`TransactionDAO`), so an adversary can replay any number of mined transactions back into the pending pool, where each entry lives for `TXN_TIMEOUT = 4h` (`src/cancelchain/block.py:50`) until expiry. The chain itself is not affected — block assembly filters mined txids out — but the pending pool can be inflated to its in-memory and DB capacity with already-mined entries, increasing the cost of `/api/transaction/pending` reads and (more importantly) extending the per-miller pending-pool walk at `Miller.pending_chain_txns`. A coordinated replay across many mined txids amounts to a low-amplification DoS on memory and miller wall-clock time.
```
Replace with:
```
✅ **Remediated.** **Finding A1.f — Severity Low** (pre-remediation behavior described below): `Node.receive_transaction` did not check whether a candidate txn's txid was already present in the persisted chain (`TransactionDAO`), so an adversary could replay any number of mined transactions back into the pending pool, where each entry lived for `TXN_TIMEOUT = 4h` until expiry. The chain itself was not affected — block assembly filters mined txids out — but the pending pool could be inflated to its in-memory and DB capacity with already-mined entries, increasing the cost of `/api/transaction/pending` reads and (more importantly) extending the per-miller pending-pool walk at `Miller.pending_chain_txns`. A coordinated replay across many mined txids amounted to a low-amplification DoS on memory and miller wall-clock time. Remediated: `Node.receive_transaction` now performs a single O(1) `TransactionDAO.get(txn.txid)` lookup after `txn.validate()` and raises `DuplicateMinedTransactionError(InvalidTransactionError)` when the txid is already mined, before the pending-add and gossip. Regression: `test_a1_f_mined_txid_replay_into_pending` plus `test_mined_txn_replay_rejected` in `tests/test_miller.py`.
```

(e) Remediation-priority section (heading line 1172, body line 1174). The body must be REWRITTEN (not just prefixed) — it currently describes the original sketch including the lineage alternative that was NOT taken.

Change the heading — Find exactly:
```
### 6. A1.f (Low) — mined-txid check in `Node.receive_transaction`
```
Replace with:
```
### 6. A1.f (Low) — ✅ Implemented — mined-txid check in `Node.receive_transaction`
```

Replace the body paragraph — Find exactly:
```
The fix lives at `src/cancelchain/node.py:76-96`. Before `self.pending_txns.add(txn)` at line 92, look up `TransactionDAO.get(txn.txid)` (or equivalently `Chain.get_transaction` against the longest chain) and raise a new `DuplicateMinedTransactionError(InvalidTransactionError)` defined in `src/cancelchain/exceptions.py` when the lookup returns a hit. The check belongs on the receive path (not at block-assembly time, where `Miller.pending_chain_txns` already filters mined txids implicitly) so that the rejection is observable to the submitter as a 400 response and never enters the pool. Blast radius: closes the pending-pool inflation DoS surface; per-receive cost is one indexed lookup on `TransactionDAO.txid`. Acceptance signal: `test_a1_f_mined_txid_replay_into_pending` flips from xfail to pass.
```
Replace with:
```
✅ **Implemented.** `Node.receive_transaction` now performs a single global `TransactionDAO.get(txn.txid)` lookup immediately after `txn.validate()` and before the pending-pool add, raising a new `DuplicateMinedTransactionError(InvalidTransactionError)` (`src/cancelchain/exceptions.py`) when the txid is already mined. The check is global (any persisted txn) rather than lineage-scoped: a single indexed lookup on `TransactionDAO.txid` (O(1)), avoiding the backward chain walk `Chain.get_transaction` would require on every receive. The rejection is observable to the submitter as a 400, and the replay never enters the pool or gossips to peers. The check belongs on the receive path (not at block-assembly time, where `Miller.pending_chain_txns` already filters mined txids). Blast radius: closes the pending-pool inflation DoS surface; mempool admission only — no consensus change. Acceptance signal: `test_a1_f_mined_txid_replay_into_pending` is now a passing regression test (xfail removed, asserting the specific `DuplicateMinedTransactionError`), plus `test_mined_txn_replay_rejected`.
```

- [ ] **Step 2: Update the ROADMAP**

(a) Replace the open-findings block (lines 48-52: the count prose, the "Pick from this list" line, and the numbered A1.f item) with an all-remediated note. Find exactly:
```
One open finding from the 2026-05-29 verification pipeline audit (A2.e, A4.c, A7.b, A7.h, and A7.e are closed; see Closed items). It has a demonstration `@pytest.mark.xfail(strict=True)` test in `tests/test_verification_audit.py`; remediation removes the xfail and the test becomes a real pass (strict mode forces this).

Pick from this list in priority order (recommended ordering per the audit's Recommendations section — based on tractability + blast-radius alignment, not strict severity).

1. **A1.f — Low — Mempool admits already-mined txids.** `Node.receive_transaction` does not reject txids that exist in `TransactionDAO`, so an adversary can replay any mined transaction into the pending pool where it lingers until 4h `TXN_TIMEOUT` expiry. Chain is unaffected (block-assembly filters); pure mempool noise. Remediation: chain-side `TransactionDAO` lookup in `Node.receive_transaction`. Test: `test_a1_f_mined_txid_replay_into_pending`.
```
Replace with:
```
**All six findings from the 2026-05-29 verification pipeline audit are remediated** (A2.e, A4.c, A7.b, A7.h, A7.e, A1.f — see Closed items). The audit is fully closed at 0 Critical / 0 High / 0 Medium / 0 Low; every `@pytest.mark.xfail(strict=True)` demonstration is now a passing regression test in `tests/test_verification_audit.py`.
```
(Leave the subsequent "**Not on this list…**" subsection and "Originating audit" pointer unchanged.)

(b) At the END of the "## Closed items (historical reference)" section (after the A7.e bullet), add:
```
- ✅ **Audit finding A1.f — mempool admits already-mined txids** — closed by docs PR [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (design+plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). `Node.receive_transaction` now performs a global `TransactionDAO.get(txn.txid)` lookup after `txn.validate()` and raises `DuplicateMinedTransactionError(InvalidTransactionError)` when the txid is already mined — before the pending-add/gossip, so replays never enter the pool. Global indexed lookup (O(1)) rather than a lineage chain-walk. Mempool admission only; no consensus change, no schema change. **This was the last open audit finding — the verification-pipeline audit is now fully remediated: 0 Critical / 0 High / 0 Medium / 0 Low.**
```
The `#<N_docs>` / `#<N_impl>` placeholders are filled when the PRs open. **Do NOT modify the historical severity tallies in earlier closed-item bullets** (A4.c "4 Low", A7.b "3 Low", A7.h "2 Low", A7.e "1 Low").

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a1f): mark A1.f remediated; verification-pipeline audit fully closed"
```

---

### Task 3: Final gates

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `COLUMNS=200 uv run pytest`
Expected: **256 passed, 0 xfailed, 1 skipped** (baseline 254 passed / 1 xfailed / 1 skipped; this PR adds `test_mined_txn_replay_rejected` as passing and moves `test_a1_f…` from xfailed to passed). **Zero xfailed means the whole audit is remediated.** If the baseline differs, the invariant is: +2 net new passing tests, and the last xfail (A1.f) is gone.

- [ ] **Step 2: xfail cross-check**

Run: `uv run pytest --runxfail tests/test_verification_audit.py -q`
Expected: all audit tests pass (no failures) — there are no remaining open-finding xfails.

- [ ] **Step 3: Lint + types**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean. No schema change ⇒ no migration / `db check` impact.

- [ ] **Step 4: Confirm no migration drift**

Run: `git status --porcelain src/cancelchain/migrations/`
Expected: empty.

---

## Notes for the implementer

- The check is global (`TransactionDAO.get(txn.txid)`), deliberately NOT lineage-scoped — do not introduce a `Chain.get_transaction` chain-walk (perf bottleneck).
- Placement is after `txn.validate()` and before the `if txn not in self.pending_txns:` guard — a mined txn is rejected regardless of pending membership (the cross-node case).
- This is mempool admission, not consensus — block assembly already filters mined txids, so there is no chain-validity change.
- This is a fix PR: no adjacent refactors. The pre-existing `test_create_wallet` terminal-width bug stays out of scope.
