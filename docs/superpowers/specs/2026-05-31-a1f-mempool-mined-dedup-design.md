# A1.f — Reject Already-Mined Txids from the Mempool Design

**Audit finding:** A1.f (Low) — *`Node.receive_transaction` admits already-mined txids into the pending pool.*
**Source audit:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`
**Significance:** the **last** open finding — closing it brings the verification-pipeline audit to **0 Critical / 0 High / 0 Medium / 0 Low**.

---

## Problem

`Node.receive_transaction` (`src/cancelchain/node.py`) parses a transaction,
checks the txid matches, runs structural `txn.validate()`, then adds it to the
pending pool and gossips it to peers. It never checks whether the txid is
**already mined** (already persisted in `TransactionDAO`). So any actor can
replay a previously-mined transaction's JSON back into the mempool, where it
lingers until `TXN_TIMEOUT` (4h) expiry.

The chain itself is unaffected — block assembly (`Miller.pending_chain_txns`)
filters out txids already on the chain — so this is **pure mempool noise**, not
a chain-correctness or value-conservation issue. But the pool can be inflated
with stale entries, increasing read/walk costs for `/api/transaction/pending`
and miller selection. Severity Low.

The gap is observable on any node where the replayed txn isn't already in its
own pending pool — e.g. a peer that learned the txn only via block gossip and
never had it pending (the usual post-mining state, since block assembly
removes mined txids from pending). Note the existing
`if txn not in self.pending_txns:` guard only skips *re-adding* an
already-pending txn — `receive_transaction` still calls `send_transaction`
afterward regardless, so even a same-node replay is re-gossiped. Placing the
new mined-check before that guard therefore stops both the pool re-entry and
the gossip amplification of replayed mined txns.

## Goal

Reject a transaction from mempool admission when its txid is already mined,
with a clear error, before it is added to the pending pool or gossiped.

## Approach

A single global lookup against `TransactionDAO` (the table of every mined txn;
`txid` is unique-indexed) in `receive_transaction`, raising a new
`DuplicateMinedTransactionError`. This is the audit's sketch.

**Scope: global, not lineage.** `TransactionDAO.get(txid)` is an O(1)
unique-indexed lookup. The alternative — a lineage-scoped check against the
longest chain (`Chain.get_transaction`) — walks blocks backward (the
recursive-walk pattern that is a documented performance bottleneck in this
project) and would run on every `receive_transaction`. The global lookup is
both cheaper and simpler. Its only trade-off: a txn mined solely on a
now-orphaned fork keeps its `TransactionDAO` row across reorgs, so it cannot
re-enter the mempool — but that is rare, recoverable (re-issue with a new
timestamp → new txid), and immaterial for a Low mempool-hygiene finding.

### Components

**1. `src/cancelchain/exceptions.py` — new exception**

```python
class DuplicateMinedTransactionError(InvalidTransactionError):
    pass
```

Placed among the other `InvalidTransactionError` subclasses. Subclassing
`InvalidTransactionError` means it surfaces as a 400 through the existing API
error path, consistent with the other `receive_transaction` rejections
(`InvalidTransactionIdError`, the `validate()` errors).

**2. `src/cancelchain/node.py` — the check**

Add `TransactionDAO` to the `from cancelchain.models import (...)` block and
`DuplicateMinedTransactionError` to the `from cancelchain.exceptions import (...)`
block. In `receive_transaction`, immediately after `txn.validate()` and before
the `if txn not in self.pending_txns:` guard:

```python
        txn.validate()
        if TransactionDAO.get(txn.txid) is not None:
            raise DuplicateMinedTransactionError()
        if txn not in self.pending_txns:
            ...
```

`TransactionDAO.get` returns the row for an already-persisted txid (or `None`);
`txid` is unique-constrained, so there is no `MultipleResultsFound` risk.

### Data flow

`parse → txid match → validate() → mined-check (new) → pending-add → gossip`.
Rejecting before the add-and-gossip step means a replayed mined txn neither
enters this node's pool nor propagates to peers.

### Placement rationale

After structural `validate()` (so a malformed replay still fails with its
specific structural error first), and before the pending-membership guard — the
mined-check is a property of the transaction, not of pending membership, so a
mined txn is rejected regardless of whether it currently sits in pending. This
is what makes the cross-node case (the test drains pending first) reject
correctly.

## Not a consensus change

`receive_transaction` is mempool admission, not block/chain validation. Block
assembly already filters mined txids, so chain validity is untouched. This is a
pure hygiene improvement; safe to tighten (no legacy chain, no deployed
installs).

## Testing

- **Acceptance:** un-xfail `test_a1_f_mined_txid_replay_into_pending` in
  `tests/test_verification_audit.py` — a mined txn replayed to
  `receive_transaction` (after draining pending) raises
  `InvalidTransactionError`. Remove its `@pytest.mark.xfail`, and tighten its
  assertion from `InvalidTransactionError` to the specific
  `DuplicateMinedTransactionError`.
- **Regression** (`tests/test_miller.py`): a mined txn replayed →
  `DuplicateMinedTransactionError`; a fresh (never-mined) txn → still admitted
  to pending (guards against over-rejection of legitimate new transactions).

## Documentation updates

- **Audit doc**: mark A1.f remediated (status lead-in matching the
  A4.c/A7.b/A7.h/A7.e convention; the Finding paragraph's gap description in
  past tense under a `✅ Remediated` banner; flip its sub-attack Outcome to
  RESOLVED), remove its row from the open findings table, and update the intro
  count and findings-table count to **all six remediated, none open**
  (`0 Critical / 0 High / 0 Medium / 0 Low`). Update the remediation-priority
  A1.f entry to a `✅ Implemented` status whose body describes the shipped
  global `TransactionDAO.get` check (not the original sketch). Add a brief
  closing line that the verification-pipeline audit is now fully remediated.
- **ROADMAP**: the "Audit remediation — verification pipeline findings" open
  section now has zero open items — replace the numbered list with a note that
  all six findings are remediated (see Closed items), and add the A1.f entry to
  the Closed items section (mirroring the A7.e entry), severity →
  **0 Critical / 0 High / 0 Medium / 0 Low**. Do not modify earlier closed-item
  historical tallies.

## Out of scope (non-goals)

- Lineage-scoped (canonical-chain-only) mined-checking — the orphan-fork
  re-entry edge is accepted (see Approach).
- Any change to `TXN_TIMEOUT` or the pending-pool expiry sweep.
- Peer-gossip-level dedup or rate-limiting of replays (a separate concern from
  admission).

## Acceptance criteria

1. `test_a1_f_mined_txid_replay_into_pending` passes with its `xfail` removed
   and the tightened `DuplicateMinedTransactionError` assertion.
2. The new `test_miller.py` regression passes (mined → rejected; fresh →
   admitted).
3. Full suite green (`COLUMNS=200 uv run pytest`), `ruff check`/`format` clean,
   `mypy` clean. No migration / schema change.
4. The audit reaches 0 open findings.
