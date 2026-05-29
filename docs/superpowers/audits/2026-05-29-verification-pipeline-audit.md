# Cancelchain verification pipeline threat-modeled audit

**Date:** 2026-05-29
**Methodology spec:** `docs/superpowers/specs/2026-05-29-verification-pipeline-audit-design.md`
**Demonstration tests:** `tests/test_verification_audit.py`

## Executive summary

[Placeholder — filled in by Task 10 after all per-adversary tasks complete.]

## Threat model

The audit considers 7 adversary categories. Each is defined by capabilities (what the adversary can do, including authentication state) and goals (what they would attempt). Capabilities are stated; the audit assumes authentication is correctly implemented (auth-layer flaws are out of scope per the spec's Non-goals — they get their own audit pass).

[The 7 adversary descriptions are restated below in Section 5 alongside their traces.]

## Methodology

For each attack attempt:

1. **Pre-state:** what's true about the chain when the attack begins.
2. **Attack:** the exact API call or gossip message the attacker sends.
3. **Trace:** which validation methods get called, in what order, what they check.
4. **Outcome:** REJECTED at step N (no finding) or ACCEPTED (gap — finding produced).
5. **Finding (if gap):** severity (Critical/High/Medium/Low) + one-line remediation sketch.
6. **Demonstration test (if gap):** a `@pytest.mark.xfail(strict=True)` test in `tests/test_verification_audit.py`.

Findings are ID'd as `A<N>.<letter>` where `N` is the adversary number (1-7) and `letter` is the attack within that adversary's enumeration. E.g., `A3.b` = adversary 3 (malicious miller), attack b.

## Findings table

[Placeholder — built by Task 10 as a cross-cutting summary of every finding produced by per-adversary tasks.]

| ID | Severity | Description | Remediation sketch | Test |
|---|---|---|---|---|

## Per-adversary traces

### Adversary 1: External attacker with valid TRANSACTOR role

**Capabilities:** Has a wallet address that matches a `CC_TRANSACTOR_ADDRESSES` regex. Can authenticate. Can submit transactions via the `/api/transaction` POST endpoint. Knows their own wallet's private key. Does NOT have MILLER privileges (can't submit blocks directly), can submit txns that millers may include.

**Validation pipeline summary.** Adversary 1 enters at `TxnView.post` (`src/cancelchain/api.py:366`), which calls `Node.receive_transaction` (`src/cancelchain/node.py:76`). Two validation layers run on the receive path:

1. **Schema + intrinsic checks (`Transaction.validate()` — `src/cancelchain/transaction.py:214`).** Pydantic `RegularTransactionModel` enforces shape (extra-fields forbidden, min/max in/outflow counts, formats); `validate_signature()` (line 208) verifies the wallet signature over `signing_data`; `validate_txid()` (line 204) recomputes the txid from `data_csv` and rejects mismatches.
2. **Pending-pool admission (`PendingTxnSet.add` — `src/cancelchain/transaction.py:380`).** Writes `PendingTxnDAO` and per-inflow `PendingIOflowDAO` rows. The `PendingTxnDAO.txid` column carries `unique=True` (`src/cancelchain/models.py:841`), so a same-txid duplicate already in pending is silently no-op'd at `Node.receive_transaction` line 90 via `if txn not in self.pending_txns`.

Chain-aware validation (`Chain.validate_block_txn` — `src/cancelchain/chain.py:200` — including double-spend, missing-outflow, address-mismatch, and balance-conservation checks) and block-level validation (`Block.validate_transaction` — `src/cancelchain/block.py:259` — including timestamp window) are **NOT** invoked on the receive-transaction path. They run only when a miller assembles a candidate block (`Miller.create_block` — `src/cancelchain/miller.py:82`) and when the resulting block is validated for chain admission (`Chain.add_block` → `Chain.validate_block` — `src/cancelchain/chain.py:153,170`).

This split means the pending pool is permissive: most chain-rule violations are caught at block-assembly time and the offending transactions are discarded (`Miller.create_block` lines 96-100) without ever entering the chain. The traces below confirm this for attacks a/b/d/e/g; the one residual gap is f (mined-txn replay into the pending pool).

#### Attack a: Double-spend their own outflow

**Pre-state:** Wallet W has a confirmed unspent outflow O at index i of mined transaction T_prior. Two distinct candidate spending transactions T_x and T_y both list `Inflow(outflow_txid=T_prior.txid, outflow_idx=i)`; their outflow destinations differ (so the txids differ).

**Attack:** POST T_x to `/api/transaction/<T_x.txid>` and POST T_y to `/api/transaction/<T_y.txid>`. The adversary hopes both land in the pending pool and that millers on different forks include them in different blocks.

**Trace:**
1. `src/cancelchain/api.py:379` — `TxnView.post` calls `node.receive_transaction(txid, request.data, ...)` for each.
2. `src/cancelchain/node.py:84` — `Transaction.from_json(...)` decodes via `TransactionModel.model_validate_json` (loose base model). Passes for both.
3. `src/cancelchain/node.py:87` — `if txid != txn.txid: raise InvalidTransactionIdError()`. URL txid matches body txid for both.
4. `src/cancelchain/node.py:89` — `txn.validate()` (`src/cancelchain/transaction.py:214`) runs `RegularTransactionModel.model_validate`, `validate_signature()`, `validate_txid()`. Both transactions are independently well-formed and pass.
5. `src/cancelchain/node.py:90` — `if txn not in self.pending_txns`. Distinct txids; both pass.
6. `src/cancelchain/transaction.py:380` — `PendingTxnSet.add` writes one `PendingTxnDAO` row plus one `PendingIOflowDAO` per inflow. No uniqueness constraint on `(outflow_txid, outflow_idx)` in `PendingIOflowDAO` (`src/cancelchain/models.py:890`). **Both transactions enter pending side-by-side.**
7. Miller assembly: `Miller.create_block` (`src/cancelchain/miller.py:82`) iterates `pending_chain_txns`. The first txn passes `Chain.validate_block_txn` (`src/cancelchain/chain.py:200`) and is added via `block.add_txn(txn)` (`src/cancelchain/miller.py:92`).
8. For the second txn, `Chain.validate_txn_inflow` calls `get_inflows_count(block, outflow_txid, outflow_idx)` (`src/cancelchain/chain.py:271`). The walk over `block.txns` (`src/cancelchain/chain.py:319-327`) sees the already-added first txn's reference; `num_inflows == 1` and `txn_in_block=False`, so the guard at `src/cancelchain/chain.py:274` raises `SpentTransactionError`. The miller catches it (`src/cancelchain/miller.py:96-98`) and discards the loser from pending (`src/cancelchain/miller.py:99-100`).
9. Even on different miners building competing blocks, once one block commits, the next call to `validate_txn_inflow` consults the persisted chain via `BlockDAO.inflows_in_chain_count` (`src/cancelchain/chain.py:332`) and again raises `SpentTransactionError`.

**Outcome:** REJECTED at step 8 via `SpentTransactionError` (and at step 9 across blocks).

**Result:** Validation correctly rejects at block assembly and chain-admission time. The pending pool permits both transactions to coexist (standard mempool model — Bitcoin behaves the same way), but no double-spend can be persisted to the chain. No finding.

#### Attack b: Inflate value

**Pre-state:** Wallet W has unspent outflows summing to S curmudgeons. The adversary crafts a transaction whose `inflows` reference outflows summing to S_in and whose `outflows` (amounts) sum to S_out, with `S_out > S_in` (or `S_out < S_in`).

**Attack:** POST the imbalanced transaction to `/api/transaction/<txid>`. Signature is valid (it's the adversary's own wallet).

**Trace:**
1. `src/cancelchain/api.py:379` → `node.receive_transaction`.
2. `src/cancelchain/node.py:89` — `txn.validate()` runs schema + signature + txid. None of `RegularTransactionModel`, `validate_signature`, or `validate_txid` examine cross-flow value conservation. **Imbalanced txn passes; enters pending.**
3. Miller assembly: `Chain.validate_block_txn` (`src/cancelchain/chain.py:200`) sums inflow amounts into `subject_amounts` and `other_amounts` (lines 207-217), then subtracts outflow amounts (lines 219-236). At `src/cancelchain/chain.py:237`: `if other_amounts != 0: raise ImbalancedTransactionError()`. At `src/cancelchain/chain.py:239-241`: `for _, amount in subject_amounts.items(): if amount != 0: raise ImbalancedTransactionError()`.
4. Miller catches, discards. The same path runs at chain admission time if the txn ever reaches a block (`Chain.add_block` → `validate_block` → `validate_block_txn` at `src/cancelchain/chain.py:196-197`).

**Outcome:** REJECTED at step 3 via `ImbalancedTransactionError` (regression-covered by `tests/test_chain.py::test_validate_block_txn` at line 454).

**Result:** Validation correctly rejects. No finding.

#### Attack c: Smuggle malformed payload past schema validation

**Pre-state:** None required.

**Attack:** POST a transaction with one or more deliberately malformed fields: oversized subject (>79 raw chars), non-base64 signature, non-mill-hash txid, extra unknown fields, address that doesn't match the public key, empty `outflows` list, empty `inflows` list (regular txn), an outflow with both `address` and `subject` set, etc.

**Trace:**
1. `src/cancelchain/api.py:379` → `node.receive_transaction`.
2. `src/cancelchain/node.py:84` — `Transaction.from_json` calls `TransactionModel.model_validate_json` (`src/cancelchain/transaction.py:299`). Wraps a `pydantic.ValidationError` in `InvalidTransactionError`. The base `TransactionModel` (`src/cancelchain/transaction.py:78`) declares `model_config = ConfigDict(extra='forbid')`, typed fields including `MillHashType` (txid), `AddressType` (address), `PublicKeyType` (public_key), `Base64Type | None` (signature), `TimestampType`, `Literal['1']` (version), nested `InflowModel`/`OutflowModel` with `min/max_length`, and an `@model_validator(mode='after') validate_pk_address` (line 94) that re-derives the address from the public key.
3. Even if `from_json` somehow leniently loads (it does not — `TransactionModel` is the same schema with min_length=0 for inflows), `Transaction.validate()` at `src/cancelchain/node.py:89` re-runs `RegularTransactionModel.model_validate` (`src/cancelchain/transaction.py:218-221`) which tightens `inflows` to `min_length=1`. Schema errors raise `InvalidTransactionError`.
4. Specific sub-attacks:
   - Oversized subject: `payload.validate_subject` (`src/cancelchain/payload.py:39`) checks `MIN_SUBJECT_LENGTH <= len(raw) <= MAX_SUBJECT_LENGTH` (79). Failure → `ValueError` → `InvalidTransactionError`.
   - Non-base64 signature: `Base64Type` AfterValidator `_check_base64` (`src/cancelchain/schema.py:117`) round-trips through `b64decode`/`b64encode`; mismatch → `ValueError`.
   - Non-mill-hash txid: `_check_mill_hash` (`src/cancelchain/schema.py:124`) enforces `validate_base64(s) and len(s) == 64`.
   - Extra unknown fields: `extra='forbid'` rejects.
   - Address ↔ public-key mismatch: `validate_pk_address` (`src/cancelchain/transaction.py:94`) raises `ValueError(ADDRESS_MISMATCH_MSG)`; regression-covered by `tests/test_transaction.py::test_txn_invalid_address`.
   - Empty `outflows`: `Field(min_length=1, ...)` rejects.
   - Empty `inflows` (regular txn): `RegularTransactionModel.inflows` `Field(min_length=1, ...)` rejects (in `Transaction.validate`).
   - Outflow with both `address` and `subject`: `OutflowModel.validate_destinations` (`src/cancelchain/payload.py:77-89`) raises `ValueError(INVALID_DESTINATION_MSG)`.

**Outcome:** REJECTED at step 2 or 3 via `InvalidTransactionError` (with Pydantic-formatted field-level messages).

**Result:** Schema layer is comprehensive. No finding.

#### Attack d: Exploit forgive/support asymmetry — forgive someone else's opposition

**Pre-state:** Wallet W_a previously created a subject-typed outflow O_subj opposing subject S (txn T_subj has `Outflow(amount=N, subject=S)` at index 0; `T_subj.address == W_a.address`). Wallet W_b (a different address) wants to forgive S without W_a's consent.

**Attack:** W_b constructs a transaction T_b with `Inflow(outflow_txid=T_subj.txid, outflow_idx=0)` and `Outflow(amount=N, forgive=S)`, signs it with W_b's private key (so `T_b.address == W_b.address`), and submits to `/api/transaction/<T_b.txid>`.

**Trace:**
1. `src/cancelchain/api.py:379` → `node.receive_transaction`.
2. `src/cancelchain/node.py:89` — `txn.validate()` runs schema + signature + txid against T_b's own fields. Pass — T_b is self-consistent (W_b really did sign it).
3. T_b enters pending.
4. Miller assembly: `Chain.validate_txn_inflow` (`src/cancelchain/chain.py:243`) resolves `i.outflow_txid` to `T_subj` via `get_transaction` (line 254). The outflow O_subj has `subject=S` set, `address=None` (subject outflows have no address per `OutflowModel.validate_destinations`).
5. `src/cancelchain/chain.py:260-261` — `if ioflow.forgive is not None or ioflow.support is not None`. O_subj's `forgive` and `support` are None (only `subject` is set), so this guard does NOT trigger.
6. `src/cancelchain/chain.py:263-267` — `address = ioflow.address if ioflow.address else ioflow_txn.address`. `ioflow.address` is None, so the fallback is `T_subj.address == W_a.address`.
7. `src/cancelchain/chain.py:268-269` — `if address != txn.address: raise InflowOutflowAddressMismatchError()`. `address == W_a.address`, `txn.address == W_b.address`; they differ → **`InflowOutflowAddressMismatchError`**.

**Sub-attack: W_a tries to forgive a subject S' for which they hold no subject UTXO.** They craft a transaction with inflows referencing their own unspent outflows summing to N (address-typed, since those are what they have) and an outflow `Outflow(amount=N, forgive=S')`. Trace:
- Schema and per-inflow checks pass; address matches at line 268-269 (their own outflows).
- `Chain.validate_block_txn` (`src/cancelchain/chain.py:200`) sums inflows into `other_amounts` (no subject inflow, so `subject_amounts` stays empty).
- At outflow processing (`src/cancelchain/chain.py:220-222`): `if o.forgive: subject_amounts[o.forgive] = subject_amounts.get(o.forgive, 0) - o.amount = 0 - N = -N`.
- At `src/cancelchain/chain.py:239-241`: `if amount != 0: raise ImbalancedTransactionError()`.

**Outcome:** REJECTED at step 7 via `InflowOutflowAddressMismatchError` for the cross-wallet forgive attack; REJECTED via `ImbalancedTransactionError` for the same-wallet forgive-without-subject-inflow variant. Regression-covered by `tests/test_chain.py::test_validate_io_address_mismatch` (line 633) and `tests/test_miller.py::test_subject_forgive_txns` (forgive-with-correct-subject-inflow positive case at line 190-195).

**Result:** Validation correctly rejects. The forgive mechanic is structurally enforced by the address-fallback rule and the subject-bucket balance rule. No finding.

#### Attack e: Submit a transaction with `inflow` referencing an invalid outflow

**Pre-state:** Adversary identifies a candidate outflow they wish to fraudulently consume — one of: (1) no such txid exists, (2) outflow belongs to a different address, (3) outflow was already spent.

**Attack:** POST a transaction whose `inflow.outflow_txid` and `inflow.outflow_idx` point at the chosen outflow.

**Trace:**
1. `src/cancelchain/node.py:89` — schema + signature passes (the txn is self-consistent).
2. Enters pending. (`PendingTxnSet.add` at `src/cancelchain/transaction.py:380` walks the inflows defensively at lines 406-424; if `TransactionDAO.get(outflow_txid)` returns None or the index is out of range, it skips spend-tracking on that inflow without raising — the pending row is written regardless.)
3. Miller assembly: `Chain.validate_txn_inflow` (`src/cancelchain/chain.py:243`):
   - **Sub-attack e.1 (no such outflow):** `ioflow_txn = self.get_transaction(i.outflow_txid, start_block=block)` returns None → `ioflow = None` → `src/cancelchain/chain.py:257-258` raises **`MissingInflowOutflowError`**. Regression-covered by `tests/test_chain.py::test_validate_txn_inflow` lines 473-474, 495-496.
   - **Sub-attack e.2 (outflow belongs to a different address):** Address resolution at line 263-267 returns the legitimate owner's address; `address != txn.address` → **`InflowOutflowAddressMismatchError`** at line 269. Regression-covered by `tests/test_chain.py::test_validate_io_address_mismatch`.
   - **Sub-attack e.3 (already spent):** `get_inflows_count` returns ≥ 1; line 274 raises **`SpentTransactionError`**. Regression-covered by `tests/test_chain.py::test_validate_txn_inflow` lines 519-520.

**Outcome:** REJECTED at step 3 via the appropriate exception for each variant.

**Result:** Validation correctly rejects. No finding.

#### Attack f: Replay a previously-mined transaction (same txid) into the pending pool

**Pre-state:** Transaction T was mined into block B at chain height h. T is in `TransactionDAO`, indexed in the block's `block_transactions` relation. T is no longer in `PendingTxnDAO` (either it was never there, e.g. on a peer that received T only via block gossip, or it was added to pending and the miller subsequently mined it without re-using the pending entry).

**Attack:** POST the byte-identical JSON of T to `/api/transaction/<T.txid>`. The adversary controls the signature (it's their own wallet), but does not need to — they're replaying T verbatim.

**Trace:**
1. `src/cancelchain/api.py:379` → `node.receive_transaction(T.txid, T.to_json(), ...)`.
2. `src/cancelchain/node.py:84` — `Transaction.from_json(T_json)` decodes the original txn. Pass.
3. `src/cancelchain/node.py:87` — txid matches.
4. `src/cancelchain/node.py:89` — `txn.validate()`. The schema, signature, and txid are still all correct (T hasn't changed). Pass.
5. `src/cancelchain/node.py:90` — `if txn not in self.pending_txns`. `PendingTxnSet.__contains__` (`src/cancelchain/transaction.py:367-370`) checks `PendingTxnDAO.get(txn.txid) is not None`. T is NOT in `PendingTxnDAO` (it's only in `TransactionDAO`), so the check returns False; the `if not in` body runs.
6. `src/cancelchain/node.py:92` — `self.pending_txns.add(txn)` succeeds: `PendingTxnDAO.txid`'s `unique=True` constraint (`src/cancelchain/models.py:841`) only protects against duplicate rows in *the same table*, not against collisions with `TransactionDAO.txid`. **A row corresponding to the already-mined T is written into pending.**
7. At the next miller assembly, `Miller.pending_chain_txns` (`src/cancelchain/miller.py:68-80`) filters T out via `not chain.get_transaction(txn.txid)` — so T is correctly not re-included in another block. **But T sits in pending until 4-hour `TXN_TIMEOUT` expiry** (`Node.discard_expired_pending_txns` at `src/cancelchain/node.py:102`).
8. The receive call returns 201/202 (depending on `API_ASYNC_PROCESSING`), so the adversary can also probe pending state via `GET /api/transaction/pending` (`src/cancelchain/api.py:587`) to confirm their entry landed.

**Outcome:** ACCEPTED at step 6 (no rejection occurred; gap exists). T is not re-included in a block (step 7), so the chain stays correct, but the pending pool now carries a stale duplicate.

**Finding A1.f — Severity Low:** `Node.receive_transaction` does not check whether a candidate txn's txid is already present in the persisted chain (`TransactionDAO`), so an adversary can replay any number of mined transactions back into the pending pool, where each entry lives for `TXN_TIMEOUT = 4h` (`src/cancelchain/block.py:50`) until expiry. The chain itself is not affected — block assembly filters mined txids out — but the pending pool can be inflated to its in-memory and DB capacity with already-mined entries, increasing the cost of `/api/transaction/pending` reads and (more importantly) extending the per-miller pending-pool walk at `Miller.pending_chain_txns`. A coordinated replay across many mined txids amounts to a low-amplification DoS on memory and miller wall-clock time.

**Remediation sketch:** In `Node.receive_transaction` (`src/cancelchain/node.py:76`), before calling `self.pending_txns.add(txn)`, look up `TransactionDAO.get(txn.txid)` (or equivalently consult the longest chain via `Chain.get_transaction`) and raise a dedicated exception (e.g. a new `DuplicateMinedTransactionError(InvalidTransactionError)` in `src/cancelchain/exceptions.py`) when the lookup returns a hit. The check belongs on the receive path (not at block-assembly time, where it already exists implicitly via `pending_chain_txns`) so that the rejection is observable to the submitter as a 400 response and never enters the pool to begin with.

**Demonstration test:** `test_a1_f_mined_txid_replay_into_pending` in `tests/test_verification_audit.py`.

#### Attack g: Submit a transaction with a future or past timestamp outside the acceptable window

**Pre-state:** Wallet W has spendable balance. Adversary crafts a valid spending transaction T and sets its `timestamp` to either (i) a time well in the future or (ii) a time more than `TXN_TIMEOUT = 4h` (`src/cancelchain/block.py:50`) in the past.

**Attack:** POST T to `/api/transaction/<T.txid>`.

**Trace:**
1. `src/cancelchain/node.py:89` — `txn.validate()`. The timestamp field is checked only by `TimestampType` AfterValidator `_check_timestamp` (`src/cancelchain/schema.py:131-135`), which delegates to `validate_timestamp` (`src/cancelchain/schema.py:83`). That call asks only "does `iso_2_dt(s)` succeed?" — i.e., is the string a parseable ISO timestamp? **No window check.** Pass.
2. T enters pending.
3. Miller assembly: `Miller.pending_chain_txns` (`src/cancelchain/miller.py:68-80`) filters `txn.timestamp_dt > expired_dt` where `expired_dt = now() - TXN_TIMEOUT`. This excludes past-window txns from miller selection, **but does NOT exclude future-window txns**.
4. For future-window T that survives step 3: `Chain.validate_block_txn` (`src/cancelchain/chain.py:200`) does not examine timestamps. T is added to the candidate block.
5. `chain.seal_block(block, milling_wallet)` (`src/cancelchain/miller.py:101`) — sets `block.timestamp = now_iso()` (`src/cancelchain/block.py:229`). Block.timestamp is now < T.timestamp.
6. `m.mill_block(b)` (`src/cancelchain/miller.py:112`) computes proof-of-work. Then `self.receive_block(block.to_json())` (`src/cancelchain/miller.py:137`) → `Node.receive_block` → `block.validate()` (`src/cancelchain/node.py:157`) → `Block.validate` (`src/cancelchain/block.py:289`) → `Block.validate_transaction(txn, ...)` (`src/cancelchain/block.py:259`).
7. `src/cancelchain/block.py:266-268` — `if self.timestamp_dt and txn_ts_dt is not None: if txn_ts_dt > self.timestamp_dt: raise FutureTransactionError()`. T.timestamp is in the future relative to block.timestamp → **`FutureTransactionError`** (wrapped as `InvalidBlockError({f'Transaction {T.txid}': ...})` at `src/cancelchain/block.py:300-301`). Regression-covered by `tests/test_chain.py::test_validate_block_txn` line 440-441 (`match='FutureTransactionError'`).
8. For past-window T that survives step 3 (i.e. older than the 4h cutoff but still selected because step 3 actually does filter past-window): in practice step 3 catches these. If a past txn does reach `Block.validate_transaction`, the check at `src/cancelchain/block.py:269-270` raises `ExpiredTransactionError`.

**Outcome:** REJECTED at step 7 via `FutureTransactionError` for future-window; REJECTED at step 3 (filtered out of miller selection) or step 8 via `ExpiredTransactionError` for past-window. No malformed-timestamp block ever lands in `BlockDAO`.

**Result:** The chain-correctness invariant holds, but at the cost of wasted miller proof-of-work for the future-window case — Adversary 1 can repeatedly submit future-timestamped txns and force the miller to compute and discard a block per submission until pending expiry. This is a known weakness pattern across UTXO chains (Bitcoin's `nLockTime` and `MedianTimePast` rules predate it for a reason) but does not rise to a chain-correctness finding under this audit's severity rubric — the block is rejected before persistence. **No finding** for Adversary 1; the pending-pool laxness pattern (also seen in attack a and f) is cross-cutting and will be summarized in the audit's cross-cutting observations section after later adversaries are traced.

### Adversary 2: Hostile peer over gossip

[Placeholder — filled in by Task 4.]

### Adversary 3: Malicious miller (MILLER role)

[Placeholder — filled in by Task 5.]

### Adversary 4: Replay attacker

[Placeholder — filled in by Task 6.]

### Adversary 5: Reorg attacker

[Placeholder — filled in by Task 7.]

### Adversary 6: Race / concurrency attacker

[Placeholder — filled in by Task 8.]

### Adversary 7: Genesis / edge-case attacker

[Placeholder — filled in by Task 9.]

## Cross-cutting observations

[Placeholder — filled in by Task 10. Patterns that span multiple adversaries: validation order inconsistencies between API entry and gossip receive; recurring near-misses that suggest a structural issue; etc.]

## Recommendations

[Placeholder — filled in by Task 10. Prioritized remediation ordering, dependencies between findings, suggestion of severity grouping into remediation PRs.]
