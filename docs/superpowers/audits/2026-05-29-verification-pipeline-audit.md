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

**Capabilities:** Configured in our `CC_PEERS` list (presumed trusted-ish) but adversarial. Can send arbitrary HTTP requests to our `/api/block` and `/api/transaction` endpoints with valid peer credentials. Can craft blocks/txns with malformed content. Sees our public chain state.

**Validation pipeline summary.** Adversary 2 enters at two endpoints:

1. **Inbound gossip** — `BlockView.post` (`src/cancelchain/api.py:308`, gated to MILLER role via `miller_block_view` at `src/cancelchain/api.py:342,352-362`) calls `Node.receive_block` (`src/cancelchain/node.py:140`). That path runs (in order): `Block.from_json` (schema via `BlockModel.model_validate_json` — `src/cancelchain/block.py:354`) → URL `block_hash` ↔ body `block_hash` check (line 153) → duplicate-suppression via `Block.from_db` (line 155) → `block.validate()` (full Block-layer validation — `src/cancelchain/block.py:289`) → `MissingBlockError` if parent unknown (line 159-164) → `Node.process_block` → `Node.add_block` → `Chain.add_block` → `Chain.validate_block` (which re-runs `block.validate()` AND adds chain-context checks — `src/cancelchain/chain.py:170`).
2. **Backfill** — `Node.fill_chain` (`src/cancelchain/node.py:306`), invoked by `cancelchain sync` (`src/cancelchain/command.py:379`) and `Miller.poll_latest_blocks` (`src/cancelchain/miller.py:108`), walks backward from a peer's claimed tip via repeated `Node.request_block` (peer's `GET /api/block/<hash>`), stages each block as a `ChainFillBlock` row, then forward-applies them through `Node.add_block` in `ChainFillBlock.idx` order. `Block.validate()` is **not** run before staging — schema is run inside `request_block`→`Block.from_json`, but the full block validation runs only at apply time inside `Chain.validate_block`.

Cross-layer: `Chain.validate_block` invokes `block.validate()` as its first step (`src/cancelchain/chain.py:171`), so every Block-layer check (`validate_block_hash`, `validate_merkle_root`, per-txn timestamp window, `validate_coinbase` shape) is enforced before chain-context checks (`FutureBlockError`, `InvalidPreviousHashError`, `OutOfOrderBlockError`, `InvalidBlockIndexError`, `InvalidTargetError`, UTXO checks via `validate_block_txn`, reward check via `validate_block_coinbase`) run.

Block-layer `Block.validate_coinbase` (`src/cancelchain/block.py:274`) checks coinbase shape (presence, `validate_coinbase()`, schadenfreude/grace/mudita totals against extra outflows) but does **not** check that `cb.outflows[0].amount == REWARD` — that check lives in `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:283-285`). Both run on the receive path, so the split is harmless; the trace for attack a confirms.

#### Attack a: Submit a block that fails one of `Block.validate*` but `Chain.validate_block` doesn't catch

**Pre-state:** Local chain at height ≥ 1. Adversary builds a block whose `merkle_root` is correct relative to its `txns` but whose `block_hash` doesn't match `mill_hash(header)` (mutated after milling), OR a block whose coinbase reward is inflated (e.g., `cb.outflows[0].amount = REWARD + 1`), OR similar Block-only invariants.

**Attack:** POST the mutated block to `/api/block/<block_hash>` with MILLER-role peer credentials.

**Trace:**
1. `src/cancelchain/api.py:321` — `BlockView.post` calls `node.receive_block(request.data, block_hash=block_hash, ...)`.
2. `src/cancelchain/node.py:150` — `Block.from_json(block_str)` runs `BlockModel.model_validate_json`. Schema enforces `idx ≥ 0`, `target/prev_hash/merkle_root` as `MillHashType`, `proof_of_work ≥ 0`, `1 ≤ len(txns) ≤ 100`, `version == '1'`, AND `validate_difficulty` (`src/cancelchain/block.py:88-92`) requires `int(block_hash, 16) < int(target, 16)`. Pass for our mutated block (block_hash itself is still a valid hex hash).
3. `src/cancelchain/node.py:153-154` — URL `block_hash` ↔ body `block.block_hash` check. The adversary submits to the URL matching the body hash, so pass.
4. `src/cancelchain/node.py:155` — `Block.from_db(block.block_hash)` returns None (this hash is new), so no short-circuit.
5. `src/cancelchain/node.py:157` — `block.validate()` runs:
   - `BlockModel.model_validate(self.to_dict())` re-runs the schema (pass).
   - `self.validate_block_hash()` (`src/cancelchain/block.py:251-253`) — `block_hash != get_header_hash()` raises **`InvalidBlockHashError`** for the mutated-header variant.
   - `self.validate_merkle_root()` (`src/cancelchain/block.py:255-257`) — `merkle_root != get_merkle_root()` raises **`InvalidMerkleRootError`** for a mutated-merkle variant.
   - Per regular txn: `validate_transaction` raises **`FutureTransactionError`/`ExpiredTransactionError`/`OutOfOrderTransactionError`** (wrapped as `InvalidBlockError`).
   - `validate_coinbase` (`src/cancelchain/block.py:274-287`) raises **`MissingCoinbaseError`** or **`InvalidCoinbaseError`** on coinbase-shape violations.
6. If the block somehow survives step 5 (e.g., the only invariant violation is an inflated coinbase reward, which `Block.validate_coinbase` does **not** check), `Node.process_block` → `Chain.add_block` → `Chain.validate_block` runs at `src/cancelchain/chain.py:170`. It calls `block.validate()` AGAIN (line 171; same result for any cross-layer-shared check) then runs chain-context checks. `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:283-285`) raises **`InvalidCoinbaseErrorRewardError`** when `cb.outflows[0].amount != reward`.

**Outcome:** REJECTED at step 5 via `InvalidBlockHashError`/`InvalidMerkleRootError`/`InvalidBlockError`-wrapped txn errors, or at step 6 via `InvalidCoinbaseErrorRewardError` for the reward variant. Every Block-layer check that `Block.validate` aggregates is invoked in the receive path; the reward-amount check is the one structural Block-layer-vs-Chain-layer split, and `Chain.validate_block_coinbase` covers it.

**Result:** Validation correctly rejects. No cross-layer gap; `Chain.validate_block`'s first action is `block.validate()`, and the one Block-layer-omitted check (coinbase reward amount) is covered by the chain-level coinbase validator. No finding.

#### Attack b: Force expensive reorgs via alternate-chain blocks with adjusted timestamps

**Pre-state:** Local chain at height h. Adversary maintains a competing fork of similar length whose tip they have legitimately mined (real PoW).

**Attack:** Repeatedly POST blocks from the competing fork to `/api/block/<block_hash>`. Timestamps within each block are nudged to make the difficulty target retarget appear favorable — e.g., advance prev/start block timestamps to push `interval_delta` higher and lift `factor` toward 4.0 (lower difficulty) per `Chain.block_target` at `src/cancelchain/chain.py:121-136`.

**Trace:**
1. `Node.receive_block` runs (steps 1-5 of attack a). Each gossiped block must carry a real `block_hash < target` value AND `block_hash == mill_hash(header)`. If the adversary fakes either, step 5's `validate_block_hash` raises `InvalidBlockHashError`; the schema's `validate_difficulty` raises `MissedTarget`/`InvalidBlockError`.
2. `Chain.validate_block` (`src/cancelchain/chain.py:170`) computes the canonical target via `self.block_target(block=block)` (`src/cancelchain/chain.py:109`). The retarget formula clamps `factor = min(max(interval_delta/TARGET_INTERVAL_SECONDS, 0.25), 4.0)` (`src/cancelchain/chain.py:131-132`) — at most ×4 easier per `TARGET_INTERVAL = 2016` blocks. The result is also clamped to ≤ `MAX_TARGET` (line 134). At line 194: `if block.target != self.block_target(block=block): raise InvalidTargetError()` — the adversary cannot present a fake-easy target that diverges from this computation; if they do, `InvalidTargetError`.
3. `Chain.validate_block` also enforces `OutOfOrderBlockError` (`src/cancelchain/chain.py:181-183` — block.timestamp < prev.timestamp) and `FutureBlockError` (line 172-173 — block.timestamp > now()). Timestamp manipulation across the fork is bounded by these on a per-block basis.
4. Any block that passes all checks is, by definition, a legitimately mined alternate-chain block — the adversary paid the full PoW cost the network requires. Persistence is correct: it becomes a fork in `BlockDAO`; `longest_chain` selection picks the longer tip via `ChainDAO.longest`.

**Outcome:** REJECTED at step 1/2/3 for any block lacking real PoW or with a forged target. For legitimately-mined alternate-chain blocks: ACCEPTED, but this is consensus working as designed — adopting a longer competing fork is the chain's intended behavior, paid for in adversary PoW work.

**Result:** Per-block validation enforces structural PoW-and-target invariants; the difficulty retarget is bounded ×4/÷4 and clamped to `MAX_TARGET`. The chain-correctness invariant holds. Forced reorgs from a peer with real PoW are intended behavior, not a finding. The asymmetry is the standard PoW economic cost: the adversary pays as much as the honest network does. **No finding.** A peer-bandwidth DoS via many short reorgs is a known limit; mitigations (rate-limiting at the API layer, peer reputation) are out of scope for this audit (auth/transport).

#### Attack c: Inject malformed-but-deserializable JSON

**Pre-state:** None required.

**Attack:** POST a block JSON with deliberately malformed fields — variant attempts include: (i) negative `idx`, (ii) `prev_hash` claiming to be a legitimate ancestor (collision attempt), (iii) `target` set to a value above `MAX_TARGET` (or below the chain's expected target), (iv) `block_hash` not below `target`, (v) extra unknown top-level fields, (vi) zero-length `txns` list, (vii) `version != '1'`.

**Trace:**
1. `src/cancelchain/node.py:150` — `Block.from_json` calls `BlockModel.model_validate_json` (`src/cancelchain/block.py:354-361`). `BlockModel` (`src/cancelchain/block.py:72-92`) declares `model_config = ConfigDict(extra='forbid')` and typed fields:
   - **Negative `idx`:** `Field(ge=0)` rejects → `InvalidBlockError`.
   - **`prev_hash` collision:** SHA-256 collision-resistance — the adversary cannot fabricate a value that resolves to a legitimate ancestor's hash. If they pick an existing legitimate `prev_hash` to chain off, that's a normal extension; if it's bogus, `Block.from_db(prev_hash)` returns None at `src/cancelchain/node.py:160-164` → `MissingBlockError`.
   - **`target` above `MAX_TARGET` or otherwise wrong:** `MillHashType` (`src/cancelchain/schema.py:124-128`) requires `validate_base64(s) and len(s) == 64` only — format check, not value check. Schema passes. But `Chain.validate_block` at line 194 (`if block.target != self.block_target(block=block)`) raises **`InvalidTargetError`** when the claimed target diverges from the chain's computed target. `Chain.block_target` clamps to `MAX_TARGET` at line 134-135.
   - **`block_hash` not below `target`:** `BlockModel.validate_difficulty` (`src/cancelchain/block.py:88-92`) raises **`ValueError(MISSED_TARGET_MSG)`** (wrapped as `InvalidBlockError`).
   - **Extra unknown fields:** `extra='forbid'` rejects → `InvalidBlockError`.
   - **Zero-length `txns`:** `Field(min_length=1, max_length=MAX_TRANSACTIONS)` rejects → `InvalidBlockError`.
   - **`version != '1'`:** `Literal['1']` rejects → `InvalidBlockError`.
2. The `JSONDecodeError` branch (`src/cancelchain/block.py:359-360`) wraps malformed-JSON into `InvalidBlockError`.

**Outcome:** REJECTED at step 1 via `InvalidBlockError` (Pydantic-formatted field messages) or `MissingBlockError` for the collision-attempt variant (when the fabricated prev_hash points nowhere).

**Result:** Schema layer is comprehensive; structural PoW invariants (`block_hash < target`) are enforced at schema time; chain-context target-correctness is enforced at `Chain.validate_block`. No finding.

#### Attack d: Manipulate the ChainFill staging table

**Pre-state:** Adversary is in our peers list. We're behind their chain tip (`Block.from_db(last_block.block_hash) is None`).

**Attack:** Adversary's `GET /api/block` (called by `Node.request_latest_blocks` at `src/cancelchain/node.py:228-229`) returns a chain tip whose parent walk eventually resolves invalid blocks. The attacker hopes that (i) blocks land in `chain_fill_block` rows unvalidated, (ii) a crash between staging and apply leaves poisoned staging rows that get later applied, or (iii) the staging table itself can be inflated for DB-bloat DoS.

**Trace:**
1. `Node.fill_chain` (`src/cancelchain/node.py:306`) creates a `ChainFill` row (line 315-316), then writes the adversary's claimed `last_block` to `ChainFillBlock` (line 317-322). **`Block.validate()` is not invoked here** — only the schema check inside `Block.from_json` from the `request_block` path (the `last_block` came from `request_latest_blocks` which calls `Block.from_json(r.text)` at line 230, running `BlockModel.model_validate_json`).
2. The walk loop (line 325-343) repeatedly calls `Node.request_block(prev_hash)` (line 333), which itself iterates through all `self.peers` (line 204) and accepts a 200 from any of them. So a hostile peer can serve ancestor blocks; each one passes only schema validation in `Block.from_json` before being staged.
3. After the walk, the apply loop (line 345-351) iterates `chain_fill.blocks` ordered by `ChainFillBlock.idx` ascending (per relationship `order_by='ChainFillBlock.idx'` at `src/cancelchain/models.py:922`). For each block: `Block.from_json(chain_fill_block.block_json)` → `self.add_block(block)` → `Chain.add_block` → `Chain.validate_block` (full validation). **Apply-time validation is comprehensive.**
4. On any apply failure, the `except Exception as e` at line 353 logs the error. The `finally` block at line 355-357 deletes the `ChainFill` row; `cascade='delete, delete-orphan'` (`src/cancelchain/models.py:923`) cascades the deletion to all `ChainFillBlock` rows.
5. **Staging-table inflation:** the `ChainFill` row is created in a single `fill_chain` call; the `finally` always cleans up. The only way to leave a stale `ChainFill` is a process crash between staging and finally — a true crash, not a normal exception. SQLite (the dev DB) commits per row, so partial staging can survive a kill. But the apply phase starts from `chain_fill.blocks` of an actively-tracked `ChainFill` instance; orphan `ChainFill` rows from prior crashed runs are never re-applied. They just consume disk until a manual cleanup.

**Outcome:** REJECTED in the operational sense — staged blocks that fail apply-time validation never enter `BlockDAO`, and the staging row is cleaned up in `finally`. The "stage without validation" behavior is by design (Bitcoin's `headers-first` sync follows the same pattern), and apply-time validation catches everything `Chain.validate_block` covers.

**Result:** Staging-table manipulation alone does not bypass any validation. The latent crash-bloat (orphan `ChainFill` rows from a killed sync) is an operational concern unrelated to consensus correctness. No finding for attack d in isolation — but the partial-adoption gap surfaced by attack e below is the real exploit pathway through `fill_chain`.

#### Attack e: Chain whose tip is longer but whose intermediate blocks fail validation

**Pre-state:** Local chain at height h. Adversary presents a chain tip claiming height h+N where N ≥ 2. The first N-1 blocks (when walked backward) are legitimately constructible (valid PoW, valid txns, valid targets) — for example, blocks the adversary mined on an isolated fork. The Nth block (the claimed tip) is invalid in a way that `Chain.validate_block` catches but `Block.validate()` does not — e.g., `block.idx` is wrong (skipped index), `block.target` is wrong, prev_hash mismatch with the chain context.

**Attack:** Adversary's `GET /api/block` returns the invalid tip. We invoke `Node.fill_chain(invalid_tip)` (via `cancelchain sync` or miller poll). The walk-back stages all N blocks; the forward apply commits blocks 1..N-1 to `BlockDAO`, then fails at the tip.

**Trace:**
1. `Node.fill_chain` (`src/cancelchain/node.py:306`) walks backward via `request_block`, staging each ancestor to `ChainFillBlock`. The walk terminates at the first ancestor already in `BlockDAO` (line 330).
2. The forward apply loop (line 345-351) iterates `chain_fill.blocks` ordered by `idx` ascending. For each block: `self.add_block(block)` (line 349) → `Chain.add_block` (`src/cancelchain/chain.py:153`):
   ```
   def add_block(self, block: Block) -> None:
       self.validate_block(block)
       block.to_db()
       self.block_hash = block.block_hash
   ```
   Then `chain.to_db()` in `Node.add_block` (`src/cancelchain/node.py:188`) commits the `ChainDAO` row pointing at the new tip.
3. Blocks 1..N-1 pass `Chain.validate_block` (they're legitimately constructed) and each gets persisted via `block.to_db()` and `chain.to_db()`. After block N-1 applies, `ChainDAO` has a row with tip = block N-1's hash, length = h + (N-1).
4. Block N (the invalid tip) fails `Chain.validate_block` — e.g., raises **`InvalidBlockIndexError`** when its idx skips ahead, or **`InvalidTargetError`** when its target diverges from the canonical computation. The exception propagates from `chain.add_block` → `Node.add_block` (which only catches `SQLAlchemyError`, not `InvalidBlockError` at `src/cancelchain/node.py:189`) → `fill_chain`'s `except Exception` at line 353.
5. `fill_chain` logs the exception and the `finally` at line 355-357 deletes the `ChainFill` row. **Blocks 1..N-1 are not rolled back** — they remain in `BlockDAO`, and the `ChainDAO` row advanced to N-1's hash remains.
6. Subsequent `Node.longest_chain` reads return this adversary-prefix chain as the new longest chain (assuming h + N-1 > our prior tip's length).

**Outcome:** ACCEPTED partially — blocks 1..N-1 enter `BlockDAO` and `ChainDAO` is advanced to the N-1 tip, even though the adversary's claimed tip N is rejected. The adversary can force partial adoption of a fork prefix by appending any cheap-to-construct invalid tip.

**Finding A2.e — Severity Medium:** `Node.fill_chain`'s apply loop (`src/cancelchain/node.py:345-351`) is non-atomic with respect to per-block validation failures. When the last block of a staged chain fails `Chain.validate_block`, all earlier blocks that passed validation remain persisted in `BlockDAO` and advance `ChainDAO`'s tip. A hostile peer can therefore commit our node to a fork prefix it controls by serving a cheap-to-construct invalid tip — the prefix blocks themselves are legitimately mined (they pass PoW + chain validation), so chain-correctness invariants hold, but the node's operational chain head adopts the adversary's fork rather than waiting for confirmation that the full claimed chain is valid. Until a longer canonical-chain sync arrives from another peer, the node operates on an attacker-influenced chain head. This is not chain-correctness existential (each persisted block is valid in isolation) but is a real availability/consensus-gravity gap: it lowers the cost for an adversary to influence which fork the network majority adopts during transient peer connectivity.

**Remediation sketch:** Wrap the apply loop at `src/cancelchain/node.py:345-351` in a savepoint / nested transaction. On any `InvalidBlockError` raised by `self.add_block(block)`, roll back every block-add performed in this `fill_chain` call (and the `ChainDAO` tip advances), then return False. Concretely: open a `db.session.begin_nested()` around the loop, commit on success, roll back inside the existing `except Exception` handler. An alternative (more compatible with SQLite's lock model) is a two-phase validate-then-persist: iterate `chain_fill.blocks` once in a read-only pass calling `Chain.validate_block` against an in-memory Chain (no `block.to_db()`), and only if all pass, run a second pass that persists each. The "validate everything before persisting anything" framing maps cleanly onto Bitcoin Core's `headers-first → blocks-batched` model and avoids the savepoint complexity at the cost of one extra validation walk.

**Demonstration test:** `test_a2_e_partial_chain_adoption_via_invalid_tip` in `tests/test_verification_audit.py`.

#### Attack f: Probe validation order — fail at a deep check to see if earlier persistence side-effects leak

**Pre-state:** Local chain at height ≥ 1. Adversary constructs a block that passes every check up to some late stage (e.g., passes schema + `block.validate()` + `FutureBlockError` + `InvalidPreviousHashError` + `OutOfOrderBlockError` + `InvalidBlockIndexError` + `InvalidTargetError`) but fails in `validate_block_txn` (e.g., `SpentTransactionError` for a regular txn) or `validate_block_coinbase` (e.g., `InvalidCoinbaseErrorRewardError`).

**Attack:** POST the crafted block to `/api/block/<block_hash>`. The adversary hopes that some earlier per-txn check or coinbase preparation step has written state to the DB before the deep-check exception fires.

**Trace:**
1. `Node.receive_block` (`src/cancelchain/node.py:140`) runs `Block.from_json` (schema), then `block.validate()` (pure — no DB writes; all hash recomputation and per-txn shape checks are in-memory).
2. `Node.process_block` → `Node.add_block` (`src/cancelchain/node.py:181-194`) → `Chain.add_block`:
   ```
   def add_block(self, block: Block) -> None:
       self.validate_block(block)
       block.to_db()
       self.block_hash = block.block_hash
   ```
3. `Chain.validate_block` (`src/cancelchain/chain.py:170-198`) runs entirely before `block.to_db()` at line 155. Every check inside `validate_block` — including the per-txn `validate_block_txn` loop (line 196-197) and the coinbase reward check at line 198 — is read-only against `BlockDAO`/`TransactionDAO` (`get_transaction`, `get_inflows_count`, `block_target`). No writes occur during validation.
4. `block.to_db()` (`src/cancelchain/block.py:342-343`) only runs after `validate_block` returns successfully — `self.to_dao().commit()` writes a `BlockDAO` + all `TransactionDAO`/`InflowDAO`/`OutflowDAO` rows in one commit. If `commit()` itself raises (e.g., a SQLAlchemy integrity error), `Node.add_block` catches `SQLAlchemyError` at line 189 and calls `rollback_session()` (line 190).
5. `chain.to_db()` (`src/cancelchain/chain.py:564-570`) similarly runs after `chain.add_block` succeeds; it commits the updated `ChainDAO` tip in one transaction.
6. Receive-block-path side effects — `Block.from_db(block.block_hash)` lookup at `src/cancelchain/node.py:155` is a read; no write side effect from receive-time. Even the duplicate-suppression short-circuit (line 156) returns `None` without touching state.

**Outcome:** REJECTED with no persistence leak. Every chain-context check inside `Chain.validate_block` is read-only; persistence only begins after `validate_block` returns. The only persistence ordering risk is between `block.to_db()` and `chain.to_db()` (the block commits before the chain tip is updated), but this is per-block-atomic via the catch-and-rollback at `src/cancelchain/node.py:189-193`. (The cross-block partial-adoption issue from attack e is the multi-block version of this concern; the single-block path is clean.)

**Result:** Validation order is correct — validate-then-persist, no early writes. No finding for single-block receive.

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
