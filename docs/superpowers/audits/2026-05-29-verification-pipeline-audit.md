# Cancelchain verification pipeline threat-modeled audit

**Date:** 2026-05-29
**Methodology spec:** `docs/superpowers/specs/2026-05-29-verification-pipeline-audit-design.md`
**Demonstration tests:** `tests/test_verification_audit.py`

## Executive summary

This audit traced 42 attack attempts across 7 adversary categories through cancelchain's verification pipeline (`Node.receive_transaction`, `Node.receive_block`, `Block.validate`, `Chain.validate_block`, `Chain.validate_block_txn`, `Chain.validate_block_coinbase`, and the surrounding fill / reorg / pending-pool machinery). Six findings were originally confirmed (all Medium or Low; no Critical or High). One has since been remediated (A2.e); five remain open. Each open finding is paired with a `@pytest.mark.xfail(strict=True)` demonstration in `tests/test_verification_audit.py`.

The headline conclusion is that **the chain-correctness invariant is structurally well-defended**. The PoW core (Adversary 3) and the schema layer (Adversary 1 attack c, Adversary 7 attacks a/c/d/f/g) produced zero findings. The per-block recursive CTE on `prev_id` (`BlockDAO._block_chain`) correctly scopes value-conservation checks to each candidate block's lineage, so competing forks can coexist in `BlockDAO` without their UTXO checks interfering — that invariant absorbed every reorg / cross-fork attack in Adversaries 4-5 without surfacing a validation gap. Phase 6.5's documented cross-worker stale-cache risk for `_is_longest` was confirmed bounded to the read/UX layer (Adversary 5 attack d): block-validation paths never consult the materialization, so the stale cache cannot escalate to validation correctness.

The findings concentrate in two structurally related pockets. First, **operational state management around `Node.fill_chain` / `ChainFill`** (A2.e Medium): the apply loop commits each block individually, so a hostile peer can force partial adoption of a fork prefix by appending a cheap-to-construct invalid tip. Second, **accounting-side replays that do not violate value conservation** (A4.c Medium, A1.f Low): a malicious miller can replay another miller's coinbase txn to inflate the original miller's wallet-balance reads, and any transactor can replay a mined txid back into the pending pool until 4h `TXN_TIMEOUT` expiry. Three further Low findings on Adversary 7 surfaced conceptual gaps (missing checks) rather than off-by-one errors: alternate-genesis fragments the chain registry (A7.b), `TXN_TIMEOUT` uses three different comparison operators across three call sites (A7.e), and subjects accept arbitrary UTF-8 codepoints including control characters and bidi overrides (A7.h).

Cross-cutting patterns and prioritized remediation are detailed in the Cross-cutting observations and Recommendations sections below. The reorg-double-spend cluster (A4.d note + A5.a + A5.b) is a canonical PoW property, not a validation-pipeline gap — its mitigation belongs in operator-facing confirmation-depth guidance, not in `validate_*` code. Auth-layer correctness (challenge cipher, JWT issuance, role regex matching) is out of scope per the audit spec's Non-goals; it will get its own audit pass.

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

5 open findings: 0 Critical / 0 High / 1 Medium / 4 Low (post-A2.e). Sorted by severity (highest first), then by ID within each severity.

| ID | Severity | Description | Remediation sketch | Test |
|---|---|---|---|---|
| A4.c | Medium | A MILLER-role adversary mines a block whose coinbase is a verbatim replay of any prior block's coinbase txn. `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278-285`) enforces only the canonical REWARD and S/G/M shape; no check rejects a coinbase whose txid is already on the lineage. The duplicate `block_transactions` m2m row makes the join in `BlockDAO.longest_chain_transactions_q` produce two rows for the replayed coinbase, inflating the original miller's `ChainDAO.wallet_balance` by one REWARD per replay. The inflated balance is not directly spendable (`InflowDAO`'s `(txid, idx)` unique constraint blocks double-consumption) but the accounting layer reports a violation of the no-double-counting invariant. | Add a `self.get_transaction(cb.txid, start_block=block)` check in `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278`), analogous to the inflow-uniqueness check in `validate_txn_inflow`; raise a new `DuplicateCoinbaseError(InvalidCoinbaseError)` when the lineage-scoped lookup returns non-None. | `test_a4_c_ii_coinbase_replay_inflates_balance` |
| A1.f | Low | `Node.receive_transaction` does not check whether a candidate txn's txid is already in `TransactionDAO`, so any actor can replay mined txids back into the pending pool, where each entry lives for `TXN_TIMEOUT = 4h` until expiry. The chain itself is unaffected (block-assembly filters mined txids out), but the pool can be inflated, increasing read/walk costs for `/api/transaction/pending` and `Miller.pending_chain_txns`. | In `Node.receive_transaction` (`src/cancelchain/node.py:76`), before `self.pending_txns.add(txn)`, look up `TransactionDAO.get(txn.txid)`; raise a new `DuplicateMinedTransactionError(InvalidTransactionError)` on hit so the rejection is observable as a 400 to the submitter. | `test_a1_f_mined_txid_replay_into_pending` |
| A7.b | Low | `Chain.validate_block` accepts any block whose `prev_hash == GENESIS_HASH`, `idx == 0`, `target == MAX_TARGET`, regardless of whether a different genesis is already persisted. Each accepted alternate-genesis spawns a fresh `ChainDAO` row, fragmenting the chain registry into parallel single-block chains. `ChainDAO.longest()` still picks the canonical winner by deterministic tiebreaker (chain-correctness preserved), but the DB accumulates unrooted rows with no recovery path. Also unlocks A7.j (disjoint-ancestor reorg). | In `Chain.validate_block` (`src/cancelchain/chain.py:170`), after the `is_genesis_block(block)` branch passes, query `BlockDAO` for any existing block with `idx == 0` and `prev_hash == GENESIS_HASH` whose `block_hash` differs from the candidate; raise a new `DuplicateGenesisError(InvalidBlockError)` if found. Closes A7.j's only entry path. | `test_a7_b_alternate_genesis_fragments_chain_registry` |
| A7.e | Low | Three call sites apply `TXN_TIMEOUT` with three different comparison operators: `Block.validate_transaction` uses strict `<` (`src/cancelchain/block.py:269`), `Miller.pending_chain_txns` uses strict `>` (`src/cancelchain/miller.py:74`), `Node.discard_expired_pending_txns` uses `<=` (`src/cancelchain/node.py:105`). A txn whose timestamp is exactly `now - TXN_TIMEOUT` is "non-expired" per the block validator but "expired" per pool maintenance / miller selection. No correctness invariant is violated today (miller exclusion catches it first), but the inconsistency is a refactor foot-gun. | Pick one canonical comparison and apply consistently across all three sites. Recommended: open boundary (`<` for "expired"); change `discard_expired_pending_txns` to `<` and `Miller.pending_chain_txns` to `>=`. Document the semantics in a `TXN_TIMEOUT` docstring. | `test_a7_e_txn_timeout_boundary_inconsistency` |
| A7.h | Low | `validate_subject` / `validate_raw_subject` (`src/cancelchain/payload.py:39-55`) enforce length (`1 <= len <= 79`) and canonical encoding round-trip, but accept any UTF-8 codepoint — including null bytes, C0/C1 control chars (BEL, ESC, LF, DEL), RTL override, zero-width joiners, and zero-width spaces. Subjects propagate to `BalanceView` HTML, CLI `subject` outputs, and `wallet_leaderboard` JSON; any consumer that doesn't strip control chars will render deceptively. | Add a content-class check in `validate_raw_subject` after the length check: reject Unicode categories `Cc` (control), `Cf` (format — bidi + zero-width), `Cn` (unassigned), `Cs` (surrogates) via `unicodedata.category(c)`. Apply symmetrically in `validate_subject` after `decode_subject`. | `test_a7_h_non_printable_subject_accepted` |

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

**Outcome:** REJECTED — `Node.fill_chain`'s apply loop now calls `self.add_block(block, commit=False)` per iteration and issues a single `db.session.commit()` after the loop (or `db.session.rollback()` on exception). A validation failure on any block rolls back every earlier block's persistence within the same `fill_chain` call. Fixed by the impl PR following from `docs/superpowers/specs/2026-05-29-a2e-fill-chain-atomicity-design.md`.

**Result:** Validation correctly rejects (post-remediation). No finding.

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

**Capabilities:** Solves and submits blocks. Authenticated as MILLER. Controls the coinbase address. Can choose which pending transactions to include. Can manipulate block timestamps and `proof_of_work`.

**Validation pipeline summary.** Adversary 3 enters at `BlockView.post` (`src/cancelchain/api.py:308`, gated to MILLER role via `miller_block_view` at `src/cancelchain/api.py:342,354,360`), which calls `Node.receive_block` (`src/cancelchain/node.py:140`). The receive path runs (in order): `Block.from_json` (schema via `BlockModel.model_validate_json` — `src/cancelchain/block.py:354`) → URL `block_hash` ↔ body `block_hash` check (line 153) → duplicate-suppression via `Block.from_db` (line 155) → `block.validate()` (`src/cancelchain/block.py:289`, the full Block-layer aggregator: schema re-check, `validate_block_hash`, `validate_merkle_root`, per-txn `validate_transaction`, `validate_coinbase` shape) → parent-presence check raising `MissingBlockError` (lines 158-164) → `Node.process_block` → `Node.add_block` → `Chain.add_block` → `Chain.validate_block` (`src/cancelchain/chain.py:170`).

`Chain.validate_block`'s first action is `block.validate()` (line 171); it then layers chain-context checks: `FutureBlockError` (line 172-173), `InvalidPreviousHashError` (line 175-176, 185-186), `OutOfOrderBlockError` (line 177-183), `InvalidBlockIndexError` (line 192-193), `InvalidTargetError` (line 194-195), per-regular-txn `validate_block_txn` (line 196-197, runs the UTXO conservation rules — `MissingInflowOutflowError` / `InflowOutflowAddressMismatchError` / `SpentTransactionError` / `ImbalancedTransactionError`), and `validate_block_coinbase` (line 198), which catches the one Block-layer-omitted coinbase invariant: `outflows[0].amount != REWARD` raises `InvalidCoinbaseErrorRewardError` (`src/cancelchain/chain.py:283-285`).

Crucially for this adversary: `Miller.create_block` (`src/cancelchain/miller.py:82`) is **internal-honest-miller optimization** — it pre-validates pending txns via `Chain.validate_block_txn` and drops failures so an honest miller doesn't waste PoW. A malicious miller bypasses it entirely by hand-crafting a `Block(...)` instance and POSTing the milled result to `/api/block/<hash>`. Only the receive-path validation defends the chain; the traces below evaluate that path against each attack.

#### Attack a: Include an invalid transaction in their block

**Pre-state:** Local chain at height ≥ 1. Adversary holds the MILLER role and a coinbase-funded wallet on the chain. Adversary constructs a "regular" transaction T_bad that fails one of the chain-rule checks: e.g., an inflow referencing a non-existent outflow (`MissingInflowOutflowError`), an inflow whose owning address differs from `txn.address` (`InflowOutflowAddressMismatchError`), an inflow referencing an outflow already consumed in the chain (`SpentTransactionError`), or unbalanced inflows/outflows (`ImbalancedTransactionError`). The schema and signature on T_bad are correct (adversary signs with their own wallet); only chain-context rules fail.

**Attack:** Adversary builds a `Block` directly — not via `Miller.create_block`, which would have caught T_bad at line 91 and dropped it — adds T_bad via `block.add_txn(t_bad)` (which calls `Block.validate_transaction` at `src/cancelchain/block.py:259`, exercising only the Block-layer per-txn checks: schema, signature, txid, timestamp window, order). Adversary then links to the local chain tip, seals (`block.seal(wallet, REWARD)` adds a correct coinbase, computes merkle root, sets timestamp), and mills until PoW is found. POST the milled block to `/api/block/<block_hash>`.

**Trace:**
1. `src/cancelchain/api.py:321` — `BlockView.post` calls `node.receive_block(request.data, block_hash=block_hash, ...)`.
2. `src/cancelchain/node.py:150` — `Block.from_json` runs `BlockModel.model_validate_json`. Pass (block is structurally well-formed and adversary's PoW is real).
3. `src/cancelchain/node.py:153-154` — URL hash matches body hash.
4. `src/cancelchain/node.py:155` — block not in `BlockDAO`.
5. `src/cancelchain/node.py:157` — `block.validate()` runs schema re-check + `validate_block_hash` + `validate_merkle_root` + per-regular-txn `validate_transaction` (Block-layer only: T_bad's signature and txid are correct, so this passes) + `validate_coinbase` (the adversary's coinbase shape matches block-level S/G/M totals). Pass.
6. `src/cancelchain/node.py:158-164` — parent block resolves; not genesis. Pass.
7. `src/cancelchain/node.py:166` — `process_block` → `add_block` → `Chain.add_block` (`src/cancelchain/chain.py:153`) → `Chain.validate_block` (line 170).
8. `Chain.validate_block` calls `block.validate()` again (line 171, pass), then chain-context checks. At line 196-197: `for txn in block.regular_txns: self.validate_block_txn(block, txn)`.
9. `Chain.validate_block_txn` (line 200) invokes `validate_txn_inflow` (line 243), which raises the appropriate exception per the inflow variant:
   - **a.i (no such outflow):** `get_transaction(i.outflow_txid, start_block=block)` returns None → **`MissingInflowOutflowError`** at line 257-258.
   - **a.ii (address mismatch):** address resolution at lines 263-267 returns the legitimate owner; `address != txn.address` → **`InflowOutflowAddressMismatchError`** at line 269.
   - **a.iii (double-spend against the persisted chain):** `get_inflows_count` returns ≥ 1 → **`SpentTransactionError`** at line 274.
   - **a.iv (imbalanced):** after `validate_txn_inflow` resolves all inflows, line 237 raises **`ImbalancedTransactionError`** when `other_amounts != 0`, or line 240 when a per-subject bucket fails to balance.
10. `Chain.validate_block` propagates `InvalidBlockError` (wrapping the per-txn error) before `block.to_db()` runs — `Chain.add_block` line 154 calls `validate_block` first, line 155 only persists on success.

**Outcome:** REJECTED at step 9 via the appropriate `InvalidTransactionError` subclass (wrapped as `InvalidBlockError({f'Transaction {txid}': ...})` at `src/cancelchain/block.py:300-301`). No block-side-effect: persistence at `Chain.add_block` line 155 runs only after `validate_block` returns successfully (the same validate-then-persist ordering surfaced by A2.f).

**Result:** Validation correctly rejects. The Miller-internal `create_block` pre-validation (`src/cancelchain/miller.py:91`) is purely an honest-miller PoW-economy optimization; the receive path's `Chain.validate_block_txn` invocation enforces the same rules against every block regardless of who signed it. Regression-covered by `tests/test_chain.py::test_validate_block_txn` (line 416, `ImbalancedTransactionError` at line 454) and `tests/test_chain.py::test_validate_txn_inflow` (line 458, all three inflow variants). No finding.

#### Attack b: Claim excess coinbase reward (output > REWARD)

**Pre-state:** Local chain at height ≥ 1 with a known `REWARD = 100 * CURMUDGEON_PER_GRUMBLE = 10000` (`src/cancelchain/chain.py:42`). Adversary intends to mint more than the protocol-defined block reward to their own address.

**Attack:** Adversary bypasses `Block.seal`/`Block.add_coinbase` (which call `Transaction.coinbase(wallet, reward=REWARD, ...)` — a wrapper that hard-codes the canonical reward at `src/cancelchain/block.py:208-211`) and instead hand-builds the coinbase transaction directly: a `Transaction` with no inflows and one outflow `Outflow(amount=REWARD + N, address=adversary_address)` (or two outflows: `[Outflow(amount=REWARD, address=adv), Outflow(amount=N, address=adv)]` to try to pass `outflows[0].amount == REWARD` while still inflating the total). The coinbase is sealed, signed, and added to the block via `block.add_txn(cb, is_coinbase=True)` (which only invokes `cb.validate_coinbase()` — schema-only; see `src/cancelchain/block.py:202-206`). Adversary then sets `merkle_root`, `timestamp`, mills until PoW lands, and POSTs.

**Trace:**
1. `src/cancelchain/node.py:150-157` — schema + `block.validate()` runs `validate_coinbase` (`src/cancelchain/block.py:274-287`). This checks coinbase **shape**, not the reward amount: line 286-287 compares `cb.outflows[1:].amount` against the block-level S/G/M totals (`comps`). For the **outflow[0]-inflated** variant (`outflows = [Outflow(REWARD + N, adv)]`, no S/G/M), `comps = []` and `outflows[1:] = []` — they match; pass. For the **multi-outflow** variant (`outflows = [REWARD, N]`, S/G/M = 0), `comps = []` and `outflows[1:] = [N]` — mismatch → **`InvalidCoinbaseError`** raised at line 287, wrapped as `InvalidBlockError` (regression-covered by `tests/test_chain.py::test_validate_block_coinbase` at line 573).
2. For the **outflow[0]-inflated** variant that survives step 1: `Node.process_block` → `Chain.add_block` → `Chain.validate_block` → `validate_block_coinbase` at `src/cancelchain/chain.py:278-285`:
   ```
   def validate_block_coinbase(self, block: Block) -> None:
       block.validate_coinbase()
       reward = self.block_reward(block)
       cb = block.coinbase
       if cb is not None:
           outflow = cb.get_outflow(0)
           if outflow is not None and outflow.amount != reward:
               raise InvalidCoinbaseErrorRewardError()
   ```
   `outflow.amount = REWARD + N != REWARD` → **`InvalidCoinbaseErrorRewardError`** (subclass of `InvalidCoinbaseError`, subclass of `InvalidTransactionError`). Regression-covered by `tests/test_chain.py::test_validate_block_coinbase` at line 546.
3. The schema layer also constrains the attack surface: `CoinbaseTransactionModel` (`src/cancelchain/transaction.py:107-109`) declares `inflows: min_length=0, max_length=0` (so no inflows can be smuggled into the coinbase) and `outflows: min_length=1, max_length=4` (capping the outflow count at REWARD + S + G + M). `OutflowModel` (`src/cancelchain/payload.py:68-89`) requires `amount >= 1` and exactly one destination flag; the adversary cannot route a single outflow's amount across multiple destinations to confuse `outflows[0]`.

**Outcome:** REJECTED at step 1 (`InvalidCoinbaseError`) for any multi-outflow inflation variant, or at step 2 (`InvalidCoinbaseErrorRewardError`) for the `outflows[0].amount > REWARD` variant.

**Result:** Validation correctly rejects. The Block-layer check (`Block.validate_coinbase`) enforces the structural mapping between the block's regular-txn S/G/M totals and the coinbase outflows[1:]; the Chain-layer check (`Chain.validate_block_coinbase`) closes the one remaining gap — the canonical REWARD amount on `outflows[0]`. Both run on the receive path. No finding.

#### Attack c: Censor specific subjects (refuse to include txns matching a pattern)

**Pre-state:** Adversary holds the MILLER role. Pending pool contains transactions whose `outflows[0].subject` (or `forgive`/`support`) matches some pattern the adversary wishes to suppress — e.g., support for a particular subject, or forgive-txns targeting a subject the adversary opposes.

**Attack:** Adversary's `Miller.pending_chain_txns` iteration (`src/cancelchain/miller.py:68-80`) selects only txns the adversary chooses, skipping pattern-matched ones. The selection logic is entirely under the adversary's control; the resulting block contains a strict subset of the legitimately-mineable pending transactions.

**Trace:** The receive-path validation surface (`Block.validate`, `Chain.validate_block`, `Chain.validate_block_txn`, `Chain.validate_block_coinbase`) examines only the transactions present **in the submitted block** plus their relationship to the persisted chain. No method inspects which pending transactions were available at the time of block construction; no method compares the included-transaction set to the pending pool. `Chain.block_target` (`src/cancelchain/chain.py:109`) and `Chain.block_reward` (line 140) are functions of chain height alone — neither penalizes a miller for sparse blocks. `Block.validate_transactions` does not require any minimum diversity, fairness, or anti-censorship inclusion property; the only minimum on `txns` is the schema's `min_length=1` (`src/cancelchain/block.py:84`), which is satisfied by the coinbase alone.

**Outcome:** ACCEPTED — the censoring block is structurally valid and persists to the chain. But this is **by design**: censorship resistance is not an invariant the cancelchain protocol enforces.

**Result:** **No finding by design.** Inclusion fairness is fundamentally incompatible with permissionless miner choice — a miner must be free to construct blocks from whichever subset of the pending pool they wish, including the empty subset (coinbase-only). Any mechanism that forced inclusion (e.g., "must include all eligible pending txns of age ≥ N") would require ordering across the entire network's pending pool (no peer agrees on the same pool), penalize honest miners whose `pending_txns_gen` poll missed a txn by milliseconds, and create denial-of-service vectors against miller bandwidth. Censorship resistance in PoW chains is an economic property, not a structural one: a censored transaction is mineable by any honest miner; persistent censorship requires a sustained majority-hashrate adversary, which is the 51% problem and out of scope for this audit. No remediation possible at the validation-pipeline layer.

#### Attack d: Embed contradictory inflows/outflows in their block (intra-block double-spend)

**Pre-state:** Local chain at height ≥ 1. Adversary holds an unspent outflow O at index `idx` of mined transaction T_prior with amount A. They craft two distinct regular transactions T_x and T_y, each carrying `Inflow(outflow_txid=T_prior.txid, outflow_idx=idx)` and each spending A to different destinations (so the txids differ). Both are correctly signed and self-consistent.

**Attack:** Adversary builds a block containing **both** T_x and T_y as regular transactions (plus a correct coinbase). Seals, mills, POSTs.

**Trace:**
1. `src/cancelchain/node.py:150-157` — schema and Block-layer per-txn checks pass: T_x and T_y are independently well-formed, signed, and txid-consistent. `validate_merkle_root` matches.
2. `Chain.add_block` → `Chain.validate_block` → line 196-197: `for txn in block.regular_txns: self.validate_block_txn(block, txn)`.
3. For T_x (first in `block.regular_txns`): `validate_block_txn` calls `validate_txn_inflow(block, T_x, T_x.inflow_0)` (`src/cancelchain/chain.py:243`). Inside, `get_inflows_count(block, T_prior.txid, idx)` (line 271-273) walks `block.txns` (`src/cancelchain/chain.py:319-327`): the in-memory walk iterates every transaction's every inflow. **The block being validated is not yet in `BlockDAO`** (`Chain.add_block` line 154 runs `validate_block` before `block.to_db()` at line 155), so the `while block is not None and BlockDAO.get(block.block_hash) is None` loop visits this block's txns first. The walk counts T_x's inflow (i=1) AND T_y's inflow (i=2) against `(T_prior.txid, idx)`. Result: `num_inflows = 2`.
4. `src/cancelchain/chain.py:274` — `if num_inflows > 1 or (num_inflows > 0 and not txn_in_block): raise SpentTransactionError()`. `num_inflows > 1` is True → **`SpentTransactionError`** (wrapped as `InvalidBlockError({f'Transaction {T_x.txid}': ...})` at `src/cancelchain/block.py:300-301` — actually at `Chain.validate_block`'s call site; the wrap happens implicitly when `InvalidTransactionError` propagates out of `validate_block_txn`).
5. The single-transaction variant — T_x has TWO inflows both referencing `(T_prior.txid, idx)` — is caught the same way: `get_inflows_count` returns 2 from T_x's own inflows, and step 4 raises `SpentTransactionError`. Regression-covered by `tests/test_chain.py::test_validate_txn_inflow` line 519-520 for the cross-block spent case; the intra-block walk is the natural extension of the same code path.

**Outcome:** REJECTED at step 4 via `SpentTransactionError`. The in-memory `block.txns` walk at `src/cancelchain/chain.py:319-327` is what defends the intra-block double-spend case; without it, the persisted-chain check at line 332 would miss the duplicate because neither T_x nor T_y has been written to `BlockDAO` yet.

**Result:** Validation correctly rejects. The structural property — that `get_inflows_count` examines the candidate block's in-memory txns before descending to the persisted chain — is what closes the intra-block double-spend gap. No finding.

#### Attack e: Manipulate timestamps to push the difficulty target up or down beyond the ±4× clamp

**Pre-state:** Local chain at height h ≥ 1, near a difficulty-retarget boundary (i.e., next block's idx is divisible by `TARGET_INTERVAL = 2016`). Adversary intends to mine the retarget-boundary block with a manipulated timestamp so the resulting target is easier than the protocol intends.

**Attack:** Adversary considers two manipulation axes: (i) set the new block's own `timestamp` to inflate the time-elapsed signal feeding `block_target`'s retarget formula; (ii) submit a block whose `target` field claims an easier-than-canonical value, hoping the chain accepts it.

**Trace:**
1. `src/cancelchain/node.py:150` — schema `BlockModel` runs `validate_difficulty` (`src/cancelchain/block.py:88-92`): `int(block_hash, 16) < int(target, 16)`. Pass — adversary's mined `block_hash` is below the (potentially fake-easy) `target`.
2. `src/cancelchain/node.py:157` — `block.validate()` re-runs schema and `validate_block_hash` / `validate_merkle_root` / per-txn checks. Pass (block is internally consistent).
3. `Chain.validate_block` (`src/cancelchain/chain.py:170`):
   - Line 172-173: `FutureBlockError` if `block.timestamp_dt > now()`. Caps forward timestamp manipulation at wall-clock-now.
   - Line 177-183: `OutOfOrderBlockError` if `block.timestamp_dt < prev_block.timestamp_dt`. Forbids backward manipulation past the previous block.
   - **Line 194-195: `if block.target != self.block_target(block=block): raise InvalidTargetError()`.** The chain recomputes the canonical target via `Chain.block_target(block=block)` (`src/cancelchain/chain.py:109-138`) and rejects any divergent claim. Variant (ii) — a fabricated easier target — is REJECTED here.
4. For variant (i) — manipulating the new block's `timestamp` to influence the retarget at the NEXT epoch boundary (since the current-block target is computed from PRIOR blocks' timestamps, immutable at this point) — the adversary's only knob is the timestamp of the block they're currently mining. That timestamp will become the `prev_block.timestamp_dt` consumed by the next epoch boundary's retarget. But:
   - The clamp at `src/cancelchain/chain.py:131-132` (`factor = min(max(factor, 0.25), 4.0)`) bounds the retarget multiplier at ±4× per `TARGET_INTERVAL = 2016` blocks **regardless** of how extreme `interval_delta` is.
   - The forward bound (`FutureBlockError`, ≤ now()) caps the inflated-elapsed-time signal at the wall clock; the adversary can't claim more time than has actually passed.
   - The backward bound (`OutOfOrderBlockError`, ≥ prev) prevents shrinking `interval_delta` below zero.
   - The result clamp at `src/cancelchain/chain.py:134-135` further caps `new_target ≤ MAX_TARGET`, so the retarget cannot make difficulty trivially easy even if the factor saturates.

**Outcome:** REJECTED at step 3 via `InvalidTargetError` for the fabricated-target variant. For the timestamp-manipulation variant, the ±4× clamp + `FutureBlockError` + `OutOfOrderBlockError` + `MAX_TARGET` cap together bound the manipulation strictly within the protocol-defined window — the adversary cannot push past the clamp by construction.

**Result:** The structural clamp on `factor` (`src/cancelchain/chain.py:131-132`) is itself the defense; the adversary cannot break a deterministic clamp computed by the validator. The audit's specific question — "push the difficulty target up or down beyond the ±4× clamp" — has no mechanism within the validation pipeline that would permit it. The wider question of "should the ±4× clamp be tighter?" (Bitcoin Core's `nPowTargetSpacing` and median-time-past rules predate this for a reason) is a protocol-design question outside this audit's scope. No finding.

#### Attack f: Submit a block with a valid proof_of_work but an invalid merkle root

**Pre-state:** Local chain at height ≥ 1. Adversary mines a block honestly (real PoW, real txns) but, before POSTing, mutates the `merkle_root` field to a value that does **not** match the merkle root of the included `txns` — hoping the receive path accepts the block while disagreeing about which transactions it commits to.

**Attack:** Variant f.i (header doesn't match body): mine with `merkle_root = X`, then mutate the in-memory block to `merkle_root = Y` (Y ≠ X) and submit. Variant f.ii (mutate txns after mining): mine with txns = [A, B, C] and `merkle_root = merkle(A, B, C)`, then swap to txns = [A, B, D] before submission while keeping the original `merkle_root`.

**Trace:**
1. `src/cancelchain/node.py:150` — `Block.from_json` runs `BlockModel.model_validate_json` (`src/cancelchain/block.py:354-361`). `BlockModel.validate_difficulty` (line 88-92) checks `int(block_hash, 16) < int(target, 16)`. Pass — `block_hash` is a real-mined value below target.
2. `src/cancelchain/node.py:157` — `block.validate()` (`src/cancelchain/block.py:289-306`) runs:
   - `BlockModel.model_validate` (re-check). Pass.
   - **`self.validate_block_hash()` (line 251-253):** `block_hash != get_header_hash()`. The `unproven_header` (line 146-157) concatenates `idx,timestamp,prev_hash,target,merkle_root,version,proof_of_work` — `merkle_root` is part of the PoW preimage. Variant f.i: adversary mined with `merkle_root=X` so `block_hash = mill_hash(header_with_X)`, but submitted `merkle_root=Y`. `get_header_hash()` recomputes from the submitted `merkle_root=Y` → mismatch → **`InvalidBlockHashError`**.
   - **`self.validate_merkle_root()` (line 255-257):** `merkle_root != get_merkle_root()`. `get_merkle_root()` rebuilds the merkle tree from the submitted `txns` list (line 173-178). Variant f.ii: adversary's submitted `merkle_root` commits to [A, B, C] but `block.txns = [A, B, D]` — recompute over [A, B, D] yields a different root → **`InvalidMerkleRootError`** (subclass of `InvalidBlockError`).
   - Variant f.i also hits `InvalidMerkleRootError` if Y happens to be a valid but unrelated value: the recompute yields the correct-for-included-txns root, which won't equal Y. The first of `validate_block_hash` / `validate_merkle_root` to fire wins (they run sequentially at lines 294-295).
3. Even if an adversary somehow constructed a block that survived both block-hash and merkle-root checks, the schema's `validate_difficulty` already requires `block_hash < target` against the **submitted** `block_hash`, so the submitted hash can't be replaced with an unmined fabrication without losing the PoW.

**Outcome:** REJECTED at step 2 via `InvalidBlockHashError` (variant f.i; the header doesn't commit to the submitted merkle_root) and/or `InvalidMerkleRootError` (variant f.ii; the merkle_root doesn't commit to the submitted txns). The two checks are independently sufficient; the structural property is that PoW commits to merkle_root and merkle_root commits to txns, so any tampering at either layer is detectable by re-computation.

**Result:** Validation correctly rejects. The header-hash-commits-to-merkle-root invariant is enforced by `validate_block_hash`; the merkle-root-commits-to-txns invariant is enforced by `validate_merkle_root`. Both are invoked on the receive path before any persistence. Regression-covered by `tests/test_block.py::test_invalid_transaction` lines 122-123 (`InvalidBlockError, match='block_hash'`) and lines 136-141 (`InvalidBlockError, match='merkle_root'`). No finding.

#### Attack g: Submit a block at the wrong difficulty for the current chain height

**Pre-state:** Local chain at height h ≥ 1. Adversary intends to submit a block whose `target` field claims a value other than the canonical target the chain would compute for the block's claimed `idx`. Variants: (g.i) `target` is the previous block's target but the block is at a retarget boundary where it should have changed; (g.ii) `target` claims `MAX_TARGET` (max-easy) at any idx; (g.iii) `target` claims a value harder than canonical, hoping to displace the canonical chain by pretending more work.

**Attack:** Adversary builds a block with a fabricated `target` field, mines until `block_hash < target` (the schema's `validate_difficulty` check), seals, and POSTs.

**Trace:**
1. `src/cancelchain/node.py:150` — `BlockModel.validate_difficulty` (`src/cancelchain/block.py:88-92`) only checks the **internal** consistency of the submitted block: `block_hash < target`. It does not compare `target` against the chain's canonical computation. Pass.
2. `src/cancelchain/node.py:157` — `block.validate()` does not check `target` against the chain either; the Block layer has no chain context to compute the canonical target from. Pass.
3. `src/cancelchain/node.py:158-164` — parent-presence check. The parent is in `BlockDAO` (adversary chains off the real tip). Pass.
4. `Chain.add_block` → `Chain.validate_block` (`src/cancelchain/chain.py:170`):
   - Line 172-173: `FutureBlockError` — not triggered if adversary timestamps honestly.
   - Line 175-183: prev_block lookup + `OutOfOrderBlockError` — pass.
   - Line 185-186: `InvalidPreviousHashError` — pass.
   - Line 192-193: `InvalidBlockIndexError` — pass (adversary claims correct idx, since they want their block to extend the chain).
   - **Line 194-195: `if block.target != self.block_target(block=block): raise InvalidTargetError()`.** `Chain.block_target(block=block)` recomputes the canonical target for the block's idx using `prev_target` (or the retarget formula at idx % TARGET_INTERVAL == 0). The adversary's fabricated `target` diverges from this canonical value → **`InvalidTargetError`** (subclass of `InvalidBlockError`).
5. Regression-covered by `tests/test_chain.py::test_block_target` line 184 (`pytest.raises(InvalidTargetError)`).

**Outcome:** REJECTED at step 4 via `InvalidTargetError`. The schema-layer check (`validate_difficulty`) covers the structural PoW invariant (`block_hash < target`); the chain-layer check (`Chain.validate_block` line 194-195) closes the chain-context gap by comparing the claimed target against the deterministic canonical computation.

**Result:** Validation correctly rejects. The split is structurally sound — Block layer cannot compute the canonical target without chain context, so the check lives at the Chain layer; both layers run on the receive path. No finding.

### Adversary 4: Replay attacker

**Capabilities:** Has seen previously-broadcast transactions (they're public). Has not necessarily solved any block. Has whatever roles are useful for resubmission (often TRANSACTOR is enough; attack c.ii additionally requires MILLER).

**Validation pipeline summary.** Replay attacks enter at one of two endpoints. For transaction replay (attacks a, c.i): `TxnView.post` (`src/cancelchain/api.py:366`) calls `Node.receive_transaction` (`src/cancelchain/node.py:76`). The receive path runs `Transaction.from_json` → URL/body txid check → `txn.validate()` (schema + signature + txid; `RegularTransactionModel` requires `min_length=1` inflows) → pending-pool admission. There is **no** check against `TransactionDAO` for already-mined txids — gap A1.f. For block-embedded replay (attacks b, c.ii, d): `BlockView.post` (`src/cancelchain/api.py:308`) calls `Node.receive_block` → `block.validate()` → `Chain.add_block` → `Chain.validate_block`. The chain-context txid/inflow checks (`Chain.get_transaction`, `Chain.get_inflows_count` — `src/cancelchain/chain.py:294,312`) walk the **candidate block's lineage** (recursive `BlockDAO._block_chain` CTE on `prev_id`), not the cross-chain DB-wide transaction set. This is by design (forks must remain independently validatable) but creates the cross-fork-replay surface examined below.

Two cross-cutting DB facts shape the traces:

1. `TransactionDAO.txid` has `unique=True` (`src/cancelchain/models.py:59`), and `Transaction.to_dao()` returns the **existing** row when the txid is already persisted (`src/cancelchain/transaction.py:257`). Cross-fork replay of the same txid therefore never raises a DB integrity error — it just appends a new `block_transactions` m2m row.
2. `Block.regular_txns` is positional: `self.txns[0:-1]` (`src/cancelchain/block.py:117`); the coinbase is whichever txn is last in the list. No rule binds the coinbase's signing address to "the miller's wallet" — a miller can put any well-formed coinbase-shaped transaction in the last slot, including another miller's coinbase from the chain's history.

#### Attack a: Resubmit a confirmed transaction into the pending pool

**Pre-state:** Transaction T was mined into block B at chain height h. T is in `TransactionDAO` (persistent). The replay attacker observed T on the network (or fetched it via `GET /api/transaction/<txid>`) and re-broadcasts it.

**Attack:** POST T's exact JSON to `/api/transaction/<T.txid>`. The replay-attacker frame is broader than A1.f's same-node-after-drain case: consider an honest peer node N that has T in its persisted chain (received via block gossip) but never had T in its pending pool, then the attacker resubmits T to N's API.

**Trace:**
1. `src/cancelchain/api.py:379` → `Node.receive_transaction` (`src/cancelchain/node.py:76`).
2. `src/cancelchain/node.py:84,87,89` — `Transaction.from_json`, URL/body txid check, `txn.validate()`. T's bytes are unchanged from when it was originally mined; schema/signature/txid all pass.
3. `src/cancelchain/node.py:90` — `if txn not in self.pending_txns`. `PendingTxnSet.__contains__` (`src/cancelchain/transaction.py:367-370`) checks `PendingTxnDAO.get(txn.txid) is not None`. On the peer that received T only via block gossip, T was never in pending; on the same node that mined T, the miller drained pending after sealing. Either way: not in pending. Check returns False; the "add to pending" branch runs.
4. `src/cancelchain/node.py:92` — `self.pending_txns.add(txn)`. `PendingTxnDAO.txid`'s `unique=True` (`src/cancelchain/models.py:841`) protects only against duplicate **pending** rows, not against collisions with the persisted `TransactionDAO.txid` unique constraint. No cross-table check exists. T enters pending.

**Outcome:** ACCEPTED at step 4. **Same gap as A1.f (see Adversary 1, Attack f).** Adversary 4's replay-attacker frame confirms the cross-node case A1.f's demonstration test already simulates (by draining pending before the replay); it does not surface a distinct angle. The validation gap is identical: `Node.receive_transaction` consults the pending pool (via `PendingTxnSet.__contains__`) but never consults `TransactionDAO` for mined-txid membership before admitting to pending.

**Result:** Related to A1.f; no new finding. The remediation sketch in A1.f (`TransactionDAO.get(txn.txid)` lookup before `pending_txns.add`) closes both the same-node and cross-node frames in one fix.

#### Attack b: Resubmit the same transaction into a competing chain fork

**Pre-state:** Two competing chains exist with a shared ancestor block_0. Chain X has tip block_X with regular transaction T persisted; T spends outflow O = (T_prior.txid, 0) where T_prior is a coinbase on the shared ancestor block_0. Chain Y is a parallel fork branching from block_0; Y has its own tip block_Y at the same or different height as X. T is **not** in Y's lineage. The adversary is a miller (or persuades a miller) on chain Y who wishes to include T verbatim in a new Y block.

**Attack:** Adversary constructs block_Y_new extending block_Y with `txns = [T, coinbase_Y_new]` (T as the only regular txn, plus a new coinbase paying themselves). Mills and POSTs to `/api/block/<block_Y_new.block_hash>`.

**Trace:**
1. `src/cancelchain/node.py:150-157` — `Block.from_json` + `block.validate()`. T's schema/signature/txid are unchanged from when it was mined on X; `validate_transaction` (block-layer) passes. The new coinbase is well-formed. `validate_merkle_root` / `validate_block_hash` pass (block was milled honestly with these inputs).
2. `Chain.add_block` → `Chain.validate_block` (`src/cancelchain/chain.py:170`). Chain-context checks against Y's lineage:
   - `FutureBlockError` / `OutOfOrderBlockError` / `InvalidPreviousHashError` / `InvalidBlockIndexError` / `InvalidTargetError` — all pass (block_Y_new is honestly constructed).
3. Line 196-197: `for txn in block.regular_txns: self.validate_block_txn(block, txn)` runs for T.
4. `Chain.validate_block_txn` (`src/cancelchain/chain.py:200`) → `validate_txn_inflow(block_Y_new, T, T.inflows[0])`:
   - Line 254: `ioflow_txn = self.get_transaction(T_prior.txid, start_block=block_Y_new)`. `Chain.get_transaction` walks `block_Y_new`'s ancestry via `Block.from_db(prev_hash)` (`src/cancelchain/chain.py:298-310`); T_prior is in the shared ancestor block_0, so the walk finds it. Pass.
   - Line 263-269: address resolution — T's `address` is whichever wallet originally signed T; T_prior's outflow O has the same address (T was a valid spend of O on chain X, so addresses already aligned). Pass.
   - Line 271-273: `get_inflows_count(block_Y_new, T_prior.txid, 0)`. `Chain.get_inflows_count` (`src/cancelchain/chain.py:312-333`) walks block_Y_new's ancestry via `Block.from_db(prev_hash)`, then defers to `BlockDAO.inflows_in_chain_count` (`src/cancelchain/models.py:362-371`) which uses the **per-block recursive CTE `_block_chain`** scoped to block_Y_new's lineage. T's inflow row exists (it was created when T was first persisted on X), but the join sees only inflows whose owning transaction is in block_Y_new's m2m lineage — i.e., chain Y's blocks. T is NOT yet in Y, so `num_inflows == 0`. Pass.
5. `Chain.validate_block_coinbase` (line 198) passes for coinbase_Y_new.
6. `Chain.add_block` line 155: `block.to_db()`. `Block.to_dao()` builds a `BlockDAO` with `transaction_daos=[txn.to_dao() for txn in self.txns]`. For T: `Transaction.to_dao()` (`src/cancelchain/transaction.py:257`) does `TransactionDAO.get(txid) or TransactionDAO(...)` — the existing row is returned. The `BlockDAO.__init__` then appends this existing TransactionDAO to `self.transactions`, which writes a new `block_transactions` m2m row (block_Y_new ↔ T). **No `IntegrityError` on `TransactionDAO.txid`'s `unique=True`.**

**Outcome:** ACCEPTED at step 6 — block_Y_new is persisted, and T is now associated via the m2m with both block_X (its original home on chain X) and block_Y_new. Per-chain validation continues to give correct results: on chain X, T's outputs land on X's lineage; on chain Y, T's outputs land on Y's lineage. The recipient is the same on both chains (T's outflow addresses haven't changed); no value is redirected.

**Result:** Acceptance is structurally correct — T was already a valid spend of O, and Y's chain-scoped validation never sees X's T-inflow row. The cross-fork double-persistence is harmless on its own: each chain's longest-chain query is scoped to that chain's lineage via the recursive CTE / `LongestChainBlockDAO` materialization, so wallet balances on chain X are unaffected by Y's T-association and vice versa. The pure-replay variant of Attack b transfers no value to the adversary (T's outputs go to the original recipient on whichever chain wins). **No finding.** The economically interesting variant — using a **different** txid that consumes the same O — is Attack d's classic PoW reorg double-spend; the txid-replay variant here just confirms the chain-scoping invariant holds.

#### Attack c: Replay a coinbase transaction

The coinbase is a special-shape transaction (no inflows, ≤4 outflows, signed by whoever generated it). Two attack surfaces:

##### Attack c.i: Standalone coinbase replay via /api/transaction

**Pre-state:** Coinbase transaction T_cb from a previously-mined block on the chain. The adversary has its bytes (public).

**Attack:** POST T_cb to `/api/transaction/<T_cb.txid>` as if it were a regular transaction.

**Trace:**
1. `src/cancelchain/api.py:379` → `Node.receive_transaction` (`src/cancelchain/node.py:76`).
2. `src/cancelchain/node.py:84` — `Transaction.from_json` uses base `TransactionModel` (`src/cancelchain/transaction.py:78`), which allows `inflows: min_length=0`. T_cb parses successfully.
3. `src/cancelchain/node.py:87,89` — URL/body txid check passes; `txn.validate()` runs `RegularTransactionModel.model_validate` (`src/cancelchain/transaction.py:218-221`), which tightens `inflows` to **`min_length=1`** (`src/cancelchain/transaction.py:102-104`). T_cb has zero inflows → Pydantic validation error → `InvalidTransactionError`.

**Outcome:** REJECTED at step 3 via `InvalidTransactionError` (Pydantic message: `inflows: List should have at least 1 item`).

**Result:** Validation correctly rejects. The `RegularTransactionModel` / `CoinbaseTransactionModel` schema split (`src/cancelchain/transaction.py:101-109`) is exactly the defense against this attack — coinbase-shaped transactions cannot enter via the regular-transaction endpoint. No finding.

##### Attack c.ii: Embed another miller's coinbase transaction in your own block

**Pre-state:** Local chain has a block B_orig at index h whose coinbase is T_cb (paying miller M_orig the canonical REWARD; T_cb is in `TransactionDAO` and m2m'd with B_orig). The adversary holds the MILLER role with their own wallet M_adv. They want to mine a new block B_adv extending the chain's tip while re-using T_cb verbatim as B_adv's coinbase.

**Attack:** Adversary constructs `B_adv` with `txns = [T_cb]` (just the replayed coinbase, no regular txns). They `link` B_adv to the current tip, set `merkle_root` and `timestamp` manually (bypassing `Block.seal`, which would call `Block.add_coinbase` and overwrite with a fresh M_adv-paying coinbase), mill until PoW lands, and POST to `/api/block/<B_adv.block_hash>` under MILLER credentials.

**Trace:**
1. `src/cancelchain/node.py:150-157` — `Block.from_json` + `block.validate()`:
   - `BlockModel.validate_difficulty` (`src/cancelchain/block.py:88-92`) — `block_hash < target` checked. Pass (adversary mined honestly).
   - `validate_block_hash` / `validate_merkle_root` — pass (header and merkle commit to the submitted `[T_cb]`).
   - `for txn in self.regular_txns: validate_transaction(...)` — `regular_txns = self.txns[0:-1] = []` (the only txn is the coinbase; nothing to iterate). Pass.
   - `validate_coinbase()` (`src/cancelchain/block.py:274-287`):
     - `cb = self.coinbase = self.last_txn = T_cb`. Present. Pass.
     - `cb.validate_coinbase()` runs `CoinbaseTransactionModel.model_validate` + signature + txid. T_cb's signature is M_orig's (still valid against T_cb's stored public_key); txid still matches `mill_hash(data_csv)`. Pass.
     - `comps = []` (block has no S/G/M because no regular_txns); `[o.amount for o in cb.outflows[1:]] = []` (T_cb has one outflow). `[] == []` → pass.
2. `src/cancelchain/node.py:158-164` — parent in `BlockDAO`. Pass.
3. `Chain.add_block` → `Chain.validate_block` (`src/cancelchain/chain.py:170`):
   - Lines 172-195: timestamp / prev_hash / idx / target checks — all pass.
   - Line 196-197: `for txn in block.regular_txns: validate_block_txn(...)` — `regular_txns == []`. Loop body skipped. **No chain-context check runs on T_cb.**
   - Line 198: `validate_block_coinbase(block)` (`src/cancelchain/chain.py:278-285`) — `block.validate_coinbase()` re-runs (pass, same as step 1); `outflow.amount = REWARD == reward`. Pass.
4. Line 155: `block.to_db()`. `Block.to_dao()` builds `transaction_daos = [T_cb.to_dao()]`. `Transaction.to_dao()` (`src/cancelchain/transaction.py:257`): `TransactionDAO.get(T_cb.txid)` returns the existing row → returned without modification (no inflow/outflow re-insert, no `IntegrityError` on `TransactionDAO.txid`'s `unique=True`). The new `BlockDAO` is committed with `transactions = [existing_T_cb_dao]`, which appends a **new** `block_transactions` m2m row associating B_adv with T_cb.
5. Chain state after persistence: T_cb is m2m'd with both B_orig (on the chain) and B_adv (the new tip). Both blocks are in the longest chain.

**Outcome:** ACCEPTED at step 4 — B_adv is persisted with T_cb as its coinbase, and a duplicate m2m row associates T_cb with B_adv.

**Consequence — longest-chain balance inflation for M_orig.** `BlockDAO.longest_chain_transactions_q` (`src/cancelchain/models.py:415-428`) joins `TransactionDAO` to `LongestChainBlockDAO`-filtered blocks via the m2m. T_cb is associated with **two** longest-chain blocks (B_orig and B_adv), so the join produces two `TransactionDAO` rows for T_cb. `longest_chain_outflows_q` (line 431-446) joins this 2-row T_cb subquery with `OutflowDAO`, producing **two rows of T_cb's REWARD outflow**. `ChainDAO.wallet_balance` (`src/cancelchain/models.py:558-567`) sums `OutflowDAO.amount` over a subquery that includes both rows — the M_orig wallet's reported balance is inflated by one extra REWARD per coinbase-replay. The InflowDAO `unique=True` on `(txid, idx)` still prevents M_orig from constructing two distinct spending inflows of the same (T_cb.txid, 0) — so the inflated balance is not directly spendable — but the chain's accounting query layer reports the wrong number.

The schema layer permits this surface because (i) `Block.regular_txns` identifies the coinbase positionally (last txn) rather than by an authoritative "is this txn a coinbase?" flag, and (ii) `Chain.validate_block_coinbase` enforces only the REWARD amount and S/G/M shape — never that the coinbase txid is fresh / not previously persisted on this chain. A `Chain.get_transaction(cb.txid, start_block=block)` check in `validate_block_coinbase` (analogous to the inflow-uniqueness check in `validate_txn_inflow`) would close the gap.

**Finding A4.c — Severity Medium:** A MILLER-role adversary can mine a block whose coinbase is a verbatim replay of any prior block's coinbase transaction. `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278-285`) enforces only the canonical REWARD amount and the schadenfreude/grace/mudita shape match against `block.regular_txns`; no check rejects a coinbase whose `txid` is already persisted in the chain's lineage. Because `Transaction.to_dao()` returns the existing `TransactionDAO` row for any already-persisted txid (`src/cancelchain/transaction.py:257`), the second persistence path appends a new `block_transactions` m2m row associating the replayed coinbase with the adversary's new block. The original coinbase recipient (M_orig) now has T_cb m2m'd with two longest-chain blocks; `BlockDAO.longest_chain_transactions_q`'s join produces two rows for T_cb, propagating into `longest_chain_outflows_q` and `ChainDAO.wallet_balance`'s sum — M_orig's reported balance inflates by one REWARD per replay. The inflated balance is not directly spendable (the `InflowDAO` `UniqueConstraint('txid', 'idx')` at `src/cancelchain/models.py:208` prevents the same outflow from being consumed twice), but the chain's accounting-query layer reports values that violate the no-double-counting invariant a UTXO model is meant to guarantee. The attack provides no direct value to the adversary (the inflated balance belongs to the original miller), but it remains a chain-integrity violation — and a malicious miller could collude with M_orig (or be M_orig themselves) to inflate M_orig's apparent balance without honest matching reward.

**Remediation sketch:** In `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278`), before/after the existing reward check, look up `self.get_transaction(cb.txid, start_block=block)`; if the lookup returns non-None (and the matched txn isn't this candidate block's own coinbase — i.e., its m2m doesn't include `block`), raise a new exception (e.g. `DuplicateCoinbaseError(InvalidCoinbaseError)` in `src/cancelchain/exceptions.py`). The same scope check that defends inflow uniqueness (`get_inflows_count`) naturally extends to coinbase-txid uniqueness; both walk the candidate's lineage rather than the global `TransactionDAO`, preserving fork-replay legitimacy (Attack b) while rejecting same-chain coinbase replay. An equally good alternative is to require the coinbase's `address` to equal a freshly-derived "this miller's wallet" address — but cancelchain has no protocol-layer notion of "the miller", so the txid-uniqueness check is the more conservative fix.

**Demonstration test:** `test_a4_c_ii_coinbase_replay_inflates_balance` in `tests/test_verification_audit.py`.

#### Attack d: Reorg double-spend across chains

**Pre-state:** Chain X is currently longest; block_X1 (extending the shared ancestor block_0) contains regular transaction T1 with `Inflow(outflow_txid=T_prior.txid, outflow_idx=0)` consuming outflow O (a coinbase outflow on block_0 paying the adversary's wallet W_adv). T1's outflow pays address B (a third-party recipient, e.g. a merchant who provided off-chain goods in exchange for T1's payment). The adversary secretly builds a competing chain Y also extending block_0; on Y they craft T2, a **different-txid** transaction whose inflow also consumes (T_prior.txid, 0) but whose outflow pays W_adv themselves (so the adversary recovers the funds while keeping B's off-chain goods). Y accumulates more PoW than X.

**Attack:** Adversary mines Y to the point that `Y.length > X.length`, gossips Y's blocks to honest peers. Each Y block is validated as it arrives.

**Trace:**
1. As each Y-extending block arrives via `BlockView.post` → `Node.receive_block` → `Chain.add_block` → `Chain.validate_block`. Block-layer and chain-context checks (timestamp / target / merkle / coinbase) all pass — Y was honestly mined.
2. The Y block containing T2 reaches `Chain.validate_block_txn(block_Y_n, T2)` → `validate_txn_inflow(block_Y_n, T2, T2.inflows[0])` (`src/cancelchain/chain.py:243`):
   - Line 254: `get_transaction(T_prior.txid, start_block=block_Y_n)` walks block_Y_n's ancestry via `Block.from_db(prev_hash)` (`src/cancelchain/chain.py:298-310`). T_prior is on the shared ancestor block_0 — found. Pass.
   - Line 263-269: address match — O's owner is W_adv; T2.address is W_adv. Pass.
   - Line 271-273: `get_inflows_count(block_Y_n, T_prior.txid, 0)`. The walk uses `Block.from_db(prev_hash)` traversal + `BlockDAO.inflows_in_chain_count` (`src/cancelchain/models.py:362-371`), which scopes its join to block_Y_n's per-block recursive CTE `_block_chain`. T1 (on chain X) is **not** in Y's lineage. The CTE walks from block_Y_n.prev → ... → block_0; T1's inflow row exists in `InflowDAO` but its owning transaction T1 is only m2m'd with chain X's blocks, so the join produces zero matches. `num_inflows == 0`. Pass.
3. Y's blocks finish applying. `ChainDAO.longest()` selects Y (`ChainDAO.chains` orders by `BlockDAO.idx.desc()` — `src/cancelchain/models.py:820-828`). `LongestChainBlockDAO` is rebuilt to reflect Y's lineage (`Phase 6` materialization).
4. Post-reorg `wallet_balance(B)`: queries `longest_chain_outflows_q`, which joins via `LongestChainBlockDAO`-filtered blocks. B's T1-outflow exists in `OutflowDAO` but T1 is only m2m'd with block_X1 (not in longest). Join produces zero rows for B's T1-outflow. **B's balance reads zero — no funds received** (which is honest: T1 isn't on the canonical chain).
5. Post-reorg `wallet_balance(W_adv)`: T2 is m2m'd with a Y block (in longest). T2's outflow to W_adv shows as unspent. W_adv's balance includes the recovered value.

**Outcome:** ACCEPTED — Y is now the longest chain, T2 is canonical, T1 is on a stale fork. The adversary spent O to themselves via T2; B's expectation of T1's payment is reverted by reorg.

**Result:** The validation pipeline behaves correctly per its design: each chain is independently consistent under per-chain UTXO rules, and the canonical-chain selection picks the longer-PoW tip. Value conservation **per chain** holds — chain X observes T1 consuming O and outputting to B; chain Y observes T2 consuming O and outputting to W_adv; both internally balance. The cross-chain "double-spend" is not a validation failure but the canonical Proof-of-Work assumption: any chain with sufficient majority hashrate over a sufficient depth can rewrite history. Bitcoin handles this with the "wait for N confirmations" mitigation pattern (off-chain, applied by recipients), not via a protocol-level rule rejecting reorgs that overwrite spent outputs. Cancelchain inherits the same property; no validation-pipeline rule could reject Y without breaking the PoW fork-resolution invariant.

The chain-scoping invariant (per-block recursive CTE on `prev_id`) is what defends value conservation within any single chain — `get_inflows_count` correctly counts T1's inflow against X's lineage and T2's against Y's, never confusing them. The cross-chain conflict surfaces only after the consensus layer (longest-chain selection) has chosen one fork; from that point on, only the canonical chain's UTXO state is queried.

**No finding.** The reorg double-spend is a PoW economic-security property, not a validation-pipeline gap. Mitigation lives off-chain (recipient confirmation-depth policy), not in the validator. The audit's recommendation set will note this as a documented limitation that warrants explicit confirmation-depth guidance in operator documentation — but no validation-pipeline remediation is possible without changing the consensus model.

### Adversary 5: Reorg attacker

**Capabilities:** Causes chain reorganizations either via hash power (controls or rents enough mining capacity) or via timing manipulation (gets blocks accepted before the network has propagated competing blocks).

**Validation pipeline summary.** Reorgs surface across three subsystems in cancelchain:

1. **Per-block validation.** Each block in a competing fork is validated on receive — schema, `block.validate()`, `Chain.validate_block` (chain-context checks against the candidate's lineage via the per-block recursive CTE `BlockDAO._block_chain`, not via the materialized `LongestChainBlockDAO` table). The candidate-lineage scoping is the structural invariant that lets two competing chains share a `BlockDAO` table without their per-block UTXO checks confusing each other (see Adversary 4 attack d's trace for the value-conservation walkthrough).
2. **Canonical-chain selection.** `ChainDAO.longest()` (`src/cancelchain/models.py:830-832`) returns the row whose linked block has the highest `idx`, with `(timestamp, block_hash)` tiebreakers (`src/cancelchain/models.py:820-828`). Whichever fork's tip is at the highest idx becomes longest; reorg "happens" at the moment a competing fork's tip overtakes ours. There's no per-call mutation — it's whichever tip exists in the DB right now.
3. **Materialization maintenance.** `ChainDAO.sync_longest_chain_blocks` (`src/cancelchain/models.py:656-743`) rebuilds the `LongestChainBlockDAO` flat table to reflect the new longest chain. Phase 6.6's smart-reorg algorithm walks the new tip back via `BlockDAO.prev` until it finds a common ancestor in the materialization, then truncates above the ancestor and inserts the diverging suffix — `O(reorg depth)` rather than `O(chain length)`. The catastrophic "no common ancestor before genesis" path deletes and rebuilds. Both bump `ChainDAO._chain_generation`, invalidating in-process `_is_longest` caches (Phase 6.5).

Crucially for the attacks below, **the block-validation path does not consult the materialized `LongestChainBlockDAO` table or the `_is_longest` cache.** Validation walks the candidate block's own ancestry via `BlockDAO._block_chain` (`src/cancelchain/models.py:307-316`), which is always correct relative to any block (it's a recursive CTE on `prev_id` from the candidate's row). The materialized table and the cache feed only **read-side queries** (`wallet_balance`, `wallet_leaderboard`, `unspent_outflows`, etc.) — they're a Phase 6 perf optimization for these reads, not a validator component.

#### Attack a: Invalidate previously-confirmed transactions via stale-branch displacement

**Pre-state:** Chain X is longest; block_X1 (height h) contains regular transaction T spending an outflow O from a coinbase on the shared ancestor block_0. T's outflow pays a recipient address B. The adversary mines a competing chain Y also extending block_0; once `Y.length > X.length`, Y becomes longest.

**Attack:** Same as Adversary 4's attack d (`A4.d`) at the consensus layer. The adversary's frame here is: once T is no longer in the active chain, is T's input outflow O available to re-spend on Y? And — distinct from A4.d — does anything in cancelchain attempt to "clean up" T's persisted state on the reorg (and if so, can that cleanup leave the DB inconsistent)?

**Trace:**
1. Y's blocks arrive via `BlockView.post` → `Node.receive_block` → `Chain.add_block` → `Chain.validate_block`. Each block's chain-context checks (`get_transaction`, `get_inflows_count`) are scoped to Y's own lineage via `BlockDAO._block_chain` (`src/cancelchain/chain.py:294-310, 312-333`). T's `InflowDAO` row exists in the DB but its owning transaction T is only m2m'd with chain X's blocks, so the lineage-scoped join produces zero matches — Y's blocks pass validation. (Same walkthrough as A4.d step 2.)
2. Y's tip overtakes X. `ChainDAO.longest()` switches to Y on its next call; `sync_longest_chain_blocks` runs during `Chain.to_db()` (`src/cancelchain/chain.py:564-570, models.py:656-743`). The smart-reorg walk collects Y's tip-back chain, finds the common ancestor at block_0's materialized position, truncates X's lineage above that position, and inserts Y's diverging suffix. `_chain_generation` is bumped, invalidating any held `_is_longest` cache.
3. **State left in the DB after reorg:**
   - **`BlockDAO`:** block_X1 remains. There is no codepath anywhere that deletes a non-canonical block; once persisted, a block stays in `BlockDAO` regardless of which chain is currently longest.
   - **`TransactionDAO`:** T remains. `TransactionDAO.txid` is `unique=True` (`src/cancelchain/models.py:59`); the row is shared across any block that m2m's it.
   - **`block_transactions` m2m:** the (block_X1, T) row remains. m2m rows are only inserted, never deleted.
   - **`InflowDAO` and `OutflowDAO`:** T's inflow row (consuming O) and outflow row (paying B) remain. No deletion path.
   - **`ChainDAO`:** chain X's row still exists, still has `block_hash = block_X1.block_hash`. The reorg doesn't delete the loser's `ChainDAO` row — `Chain.to_db` creates new chain rows; `ChainDAO.longest()` just selects whichever tip is highest.
   - **`LongestChainBlockDAO`:** rebuilt to reflect Y. block_X1 is no longer in this table.
   - **`PendingTxnDAO`:** if T was originally pending on this node before being mined into block_X1, that pending row was deleted by the miller after sealing. The reorg does NOT re-inject T into pending (no Bitcoin-style mempool reorg recovery exists — see `Node` for the absence of any such codepath).
4. **Can O be re-spent on Y?** Yes. The adversary (who owns O) constructs T' with `Inflow(outflow_txid=O.txid, outflow_idx=O.idx)` and any new outflow shape they choose. T' has a different txid than T (different outflow destinations). On Y's lineage, `get_inflows_count(block_Y_n, O.txid, O.idx)` walks Y's blocks; T's InflowDAO row exists in the DB but T is m2m'd only with block_X1, which isn't in Y's lineage. The lineage-scoped count returns 0. T' passes validation. (Identical mechanism to A4.d's T2.)

**Outcome:** RELATED to A4.d. The "displacement" frame produces the same validation-pipeline result as A4.d's "different-txid double-spend" frame: the per-block recursive CTE correctly scopes the inflow uniqueness check to the candidate block's lineage, and the loser-fork's T-on-X is correctly invisible to Y's lineage walk. The DB-state inventory in step 3 confirms that nothing in cancelchain attempts post-reorg cleanup of stale-branch transaction state — and that's the right call, because (i) the stale block_X1 may rejoin the canonical chain via a later reorg back to X, and (ii) every per-chain query is lineage-scoped so the stale rows are inert relative to any "is this txn on the canonical chain?" question.

**No new finding.** The validation-correctness analysis matches A4.d's note: the reorg double-spend is the canonical PoW property, not a validation gap. The mitigation (off-chain recipient confirmation-depth policy for B) is flagged in A4.d's note and will be carried forward to Task 10's Recommendations.

#### Attack b: Double-spend across the reorg boundary

**Pre-state:** Same as Attack a — chain X has T spending O to recipient B; adversary secretly mines chain Y with T' spending O to themselves. B observes T on chain X, ships off-chain goods, then watches the reorg switch the chain to Y.

**Attack:** The attacker times the reveal of Y so B has already shipped goods based on T's appearance on chain X. Once Y overtakes X, T is on the stale branch, T' is canonical, and the adversary has both the goods and the funds.

**Trace:** Identical to Attack a's trace through validation. The audit's question for Attack b is narrower: does the validation pipeline have any mechanism that could detect "the outflow this txn consumes was just consumed on a sibling branch within the last N blocks"? In other words: does any check look outside the candidate block's lineage to catch cross-fork double-spends at validation time?

The relevant lineage-scoping reads are:
- `Chain.get_transaction(txid, start_block=block)` at `src/cancelchain/chain.py:294-310` — walks `block.from_db(prev_hash)` backwards through the candidate's ancestry.
- `Chain.get_inflows_count(start_block, outflow_txid, outflow_idx)` at `src/cancelchain/chain.py:312-333` — walks `block.from_db(prev_hash)` then defers to `BlockDAO.inflows_in_chain_count` (`src/cancelchain/models.py:362-371`), which uses the per-block recursive CTE `_block_chain` scoped to the start block.

Both are scoped to the **candidate block's lineage**. By design, neither can see a sibling fork's inflow-consumption record. The chain-scoping invariant is what lets X and Y coexist independently in `BlockDAO`; relaxing it to a "global TransactionDAO-wide uniqueness" check would (i) break legitimate cross-fork same-txid replay (A4.b, which is structurally valid), and (ii) require defining "recent" in a network-agnostic way.

**Outcome:** RELATED to A4.d. No validation-pipeline mechanism could catch the cross-fork double-spend without breaking the per-chain UTXO invariant that lets forks coexist. Detection is fundamentally a consensus-layer event (after the longest chain has been chosen, the loser's spends are no longer canonical), not a validation-time event.

**No new finding.** The mitigation is the same as Attack a and A4.d: off-chain recipient confirmation-depth policy. This is the standard PoW reorg-double-spend property; Bitcoin and every UTXO chain inherit the same shape. Task 10's Recommendations section will collect the confirmation-depth guidance once across A4.d, A5.a, and A5.b.

#### Attack c: Exploit the gap between `ChainFill` staging and apply

**Pre-state:** Adversary is a peer in our `CC_PEERS` list. We're behind their advertised tip (`Block.from_db(tip.block_hash) is None`). `Node.fill_chain(tip)` runs.

**Attack:** The reorg-attacker frame for `fill_chain` overlaps heavily with Adversary 2 attack e. A2.e established the primary gap: the apply loop at `src/cancelchain/node.py:345-351` commits each block individually, so an invalid tip leaves earlier blocks persisted and `ChainDAO`'s tip advanced. The reorg-attacker angle here asks three further questions:

1. **What if the process dies between `ChainFill` row insert and `_apply_chain_fill_blocks`?** Are the staged rows resumable, or are they orphaned?
2. **What if a second `fill_chain` call arrives while the first is mid-apply?** (Also Adversary 6 race territory; the consistency angle is the reorg-attacker's interest.)
3. **What about the smart-reorg deep-fallback path inside `sync_longest_chain_blocks`** — when it falls back to the catastrophic "no common ancestor" branch, is that atomic?

**Trace — question 1 (orphan `ChainFill` rows after crash):**
1. `fill_chain` creates a `ChainFill` row and commits (`src/cancelchain/node.py:315-316`). The row is persistent immediately — SQLite commits per `.commit()` call.
2. Each `ChainFillBlock(...).commit()` (lines 317-322, 338-343) writes one row + commit. Per-block-staged rows are persistent immediately.
3. The `finally` block (lines 355-357) calls `chain_fill.delete()` — `cascade='delete, delete-orphan'` (`src/cancelchain/models.py:923`) cascades to `ChainFillBlock` rows.
4. **The finally only fires on normal Python exception unwind.** If the process receives `SIGKILL`, `SIGTERM` without grace shutdown, or the host is power-cycled mid-sync, the finally never runs and the `ChainFill` + its `ChainFillBlock` rows persist forever.
5. **No recovery on startup.** A grep over `src/cancelchain/` shows the only mentions of `ChainFill` are in `node.py:311-357` (the `fill_chain` creator/deleter) and `models.py:914-957` (the table definitions). There is no startup scan, periodic cleanup, or CLI command that finds and removes orphaned rows.

**Trace — question 2 (concurrent `fill_chain` calls):** Each call creates its own `ChainFill` row (autoincrement PK), so they don't collide on the staging table. They WOULD race on `Node.add_block` for the chain head — covered by Adversary 6.

**Trace — question 3 (smart-reorg catastrophic-fallback atomicity):**
1. `sync_longest_chain_blocks` runs inside `Chain.to_db()` (`src/cancelchain/chain.py:564-570`), which calls `dao.sync_longest_chain_blocks()` then `db.session.commit()` line 570.
2. The catastrophic branch (`src/cancelchain/models.py:714-727`) executes `db.session.execute(db.delete(LongestChainBlockDAO))` then bulk-inserts new rows. Both operations are within the same SQLAlchemy session/transaction; if the bulk insert fails partway, the outer `Chain.to_db` doesn't catch — the exception propagates up, the session is implicitly rolled back when the request unwinds (Flask-SQLAlchemy's `app.teardown_appcontext` calls `db.session.remove()`).
3. Atomicity is determined by the session's transaction boundary. The DELETE-then-INSERT pair is atomic in the sense that nothing between them commits independently; either both apply at the `db.session.commit()` line 570 boundary, or neither does (on rollback). The risk would be a partial commit if the session was flushed before that final commit — but SQLAlchemy autoflushes on query, not on individual `db.session.add`, so the DELETE + INSERTs queue up and flush together.

**Outcome:** Question 1 surfaces a distinct gap from A2.e (orphan staging rows, not partial chain adoption). Questions 2 and 3 do not surface validation-correctness gaps.

For Question 1, the consequence severity is bounded:
- **Chain correctness:** unaffected. `ChainFill` is staging-only — `BlockDAO`, `ChainDAO`, and `LongestChainBlockDAO` are written by `Node.add_block` / `Chain.to_db`, not by `ChainFill`. Orphaned `ChainFill` + `ChainFillBlock` rows have no read path that pulls them into validation or canonical chain selection.
- **DB bloat:** unbounded over time if a hostile peer repeatedly triggers kills mid-sync. Each orphan ChainFill carries `O(reorg depth)` ChainFillBlock rows with `Text` `block_json` payloads — non-trivial bytes per stage. But triggering process kills externally requires more capability than this audit's adversary model assumes (auth/transport vectors out of scope; Adversary 5 only has hashpower + timing manipulation).
- **No accidental re-apply:** the apply loop only iterates `chain_fill.blocks` of the actively-tracked instance (line 345); orphan rows from prior crashed runs are not discovered or applied.

Severity: **Low** under the audit rubric — pure operational hygiene, no chain-correctness or value-conservation consequence, requires an external process-kill trigger to weaponize for DoS-via-disk-bloat.

**No new finding — RELATED to A2.e.** The validation-pipeline gap in `fill_chain` is A2.e (partial chain adoption). The orphan-staging-row consequence of crash mid-sync is operational rather than validation, and the severity (Low, requires external kill capability) plus its tangential relationship to consensus correctness places it below the bar for a separate `A5.c` finding. Task 10's Recommendations will note it as an operational follow-up (a startup-time `DELETE FROM chain_fill` sweep is a one-liner remediation that doesn't need its own finding). Questions 2 and 3 produce no findings.

#### Attack d: `_is_longest` cache misbehavior

**Pre-state:** Multi-worker Gunicorn deployment (a target operational shape the project hasn't committed to — see CLAUDE.md "No 'production' yet" — but the audit's threat model includes it because the Phase 6.5 design spec explicitly flagged the cross-worker stale-cache risk). Worker A and Worker B each hold their own in-process `ChainDAO._chain_generation` counter and their own `ChainDAO` instances with cached `_is_longest` tuples. Both share the same DB.

**Attack:** Worker A processes a reorg (gossip-received block, sync, or local mining): `sync_longest_chain_blocks` rebuilds `LongestChainBlockDAO` to reflect the new chain, bumps Worker A's `_chain_generation`. Worker B is mid-request, holding a `ChainDAO` instance for what WAS the longest chain pre-reorg, with `_is_longest_cache = (B_old_gen, True)`. B's `_chain_generation` was never bumped — the bump is process-local — so B's next `_is_longest()` call returns the cached `True` from a generation that B still considers current.

**Trace:**
1. **Single-worker (test fixture, single-process dev) case.** `ChainDAO._chain_generation` is a `ClassVar[int]` on the SQLAlchemy class. Any `sync_longest_chain_blocks` or `_rebuild_longest_chain_blocks` bumps it (`src/cancelchain/models.py:726, 743, 768`). All in-process `ChainDAO` instances re-check the class-level counter on `_is_longest()` calls (`src/cancelchain/models.py:646-653`), so the bump correctly invalidates every held cache. **Confirmed correct.** Regression-covered by `tests/test_models.py::test_is_longest_cache_invalidated_by_bump` (line 459) and `test_is_longest_cache_hit_avoids_query` (line 439).
2. **Multi-worker Gunicorn case.** Worker A's bump never reaches Worker B's process. B's `_chain_generation` remains at its old value; B's `_is_longest_cache[0] == B._chain_generation` so the cache returns True. **Stale True is observable in Worker B for the lifetime of B's held `ChainDAO` instance** — typically a single Flask request, since Flask-SQLAlchemy's session scoping rebinds instances per-request.
3. **What does Worker B see?** When B accesses `chain.blocks`, `chain.outflows`, etc. on the stale instance, the True branch routes through `BlockDAO.longest_chain_blocks_q()` (`src/cancelchain/models.py:399-413`), which queries `LongestChainBlockDAO`. **But that table is shared across workers via the DB** — it now contains Worker A's reorged chain. So B's stale-True chain instance reads from a materialization that reflects the new chain, NOT the chain B's instance represents.
4. **Validation-layer consequence.** The block-validation path does NOT depend on `_is_longest` cache or `LongestChainBlockDAO`:
   - `Chain.validate_block` (`src/cancelchain/chain.py:170`) uses `Block.from_db(prev_hash)` and `self.block_target(block=block)` — both go through `ChainDAO.get_block` → `BlockDAO.get_block_in_chain` (per-block recursive CTE on `prev_id`), not the materialized table.
   - `Chain.validate_block_txn` (`src/cancelchain/chain.py:200`) uses `get_transaction(start_block=block)` and `get_inflows_count(start_block=block)` — both walk `Block.from_db(prev_hash)` then defer to `BlockDAO.inflows_in_chain_count` (per-block recursive CTE).
   - `Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278`) uses `block.validate_coinbase()` + the canonical reward computation — no chain-query reads.
   None of these route through the `ChainDAO` properties that consult `_is_longest`.
5. **Read-layer consequence (out of audit scope).** The stale True read DOES affect `wallet_balance`, `unspent_outflows`, `unforgiven_outflows`, `wallet_leaderboard`, `subject_balance`, `subject_support`. In Worker B, calling `chain.balance(addr)` on the stale instance returns the wrong wallet's balance (chain A's UTXO state under chain B's instance's logical view). **This is the Phase 6.5 documented risk.**
6. **Transaction-construction edge.** `Chain.create_transfer` / `create_subject` / `create_forgive` / `create_support` (`src/cancelchain/chain.py:409, 430, 468, 489`) call `self.unspent_outflows` / `self.unforgiven_address_outflows`, which call `self.to_dao().unspent_outflows(address)` → `ChainDAO.unspent_outflows` → routes through `_is_longest`. A stale True in Worker B during txn construction could select UTXOs from a chain other than the one the caller logically intended. **But the resulting txn is then re-validated on receive (the canonical chain's per-block lineage check), so a "bad" txn from B's stale view is REJECTED at validation time — `SpentTransactionError` if the picked outflow has already been consumed on the canonical chain, `MissingInflowOutflowError` if it doesn't exist there. The sender wastes work, but the chain doesn't admit an invalid txn.**

**Outcome:** No validation-layer consequence in the multi-worker case. The Phase 6.5 risk is real for read-layer correctness (`wallet_balance` returning the wrong number to a UI) and for transaction-construction efficiency (sender constructs a doomed txn), but the chain-correctness invariant is preserved because validation paths don't consult `_is_longest` or the materialized table; they walk per-block CTEs that are always correct relative to the candidate block.

**No new finding.** Cross-worker stale-cache risk is exactly the one already documented in the Phase 6.5 design spec's Risks section (`docs/superpowers/specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md` Risks). The validation-layer audit confirms the risk doesn't escalate beyond the read-layer/UX scope already documented there: **no Worker B can be tricked into accepting an invalid block** because block validation doesn't go through the cache; the worst the stale cache can do is return wrong reads or pick wrong UTXOs for a doomed-to-be-rejected outbound txn. No demonstration test is added — the multi-process scenario would require `@pytest.mark.multi` deselection-by-default and a process-spawning fixture, and the absence of validation-layer consequence means even a passing test would be demonstrating "reads stale data on a held instance" rather than a security gap.

### Adversary 6: Race / concurrency attacker

**Capabilities:** Coordinates the timing of multiple submissions to exploit windows between validation and persistence.

**Validation pipeline summary.** Concurrency in cancelchain has three relevant axes: (i) multi-worker Gunicorn (each worker has its own DB session and connection); (ii) Celery `tasks.post_process` callbacks reentering `/api/<...>/process` (`src/cancelchain/api.py:308,366`); (iii) multi-process milling via `multiprocessing.Pool` (`Miller.mill_block(mp=True)`). The relevant validate→commit windows are:

1. **`Node.receive_transaction`** (`src/cancelchain/node.py:76`) — runs `txn.validate()` (pure / in-memory) then `pending_txns.add(txn)` which writes a `PendingTxnDAO` row + per-inflow `PendingIOflowDAO` rows. `PendingTxnDAO.txid` has `unique=True` (`src/cancelchain/models.py:841`); the `SQLAlchemyError` catch at `src/cancelchain/node.py:93-96` rolls back the session and re-checks `txn not in self.pending_txns`, so a same-txid race resolves cleanly to "already in pending."
2. **`Node.receive_block`** (`src/cancelchain/node.py:140`) — runs `Block.from_db(block_hash)` short-circuit (line 155), then `block.validate()` (pure), then `process_block` → `add_block` → `Chain.add_block` → `validate_block` then `block.to_db()` (`src/cancelchain/chain.py:153-156`). `BlockDAO.block_hash` has `unique=True` (`src/cancelchain/models.py:257-259`); the `SQLAlchemyError` catch at `src/cancelchain/node.py:189-193` rolls back and re-checks `Block.from_db(block.block_hash)`, treating a same-hash race as already-handled.
3. **`Chain.add_block`** (`src/cancelchain/chain.py:153`) — validate-then-persist; NO lock between them. The validate path reads `Block.from_db(prev_hash)`, walks ancestry via the per-block recursive CTE on `prev_id`, and consults `BlockDAO.inflows_in_chain_count` (lineage-scoped). Persistence is one `db.session.commit()` per block (via `block.to_dao().commit()` at `src/cancelchain/models.py:335-337` and `chain.to_dao().to_db()` at `src/cancelchain/chain.py:564-570`).
4. **`Miller.create_block`** (`src/cancelchain/miller.py:82`) — iterates `pending_chain_txns(chain)`, calls `Chain.validate_block_txn` against the in-memory `block.txns`, and discards failures via `pending_txns.discard(...)`. The pending pool is read with no claim/lock — `pending_chain_txns` is a stateless filter over `PendingTxnSet.__iter__`.

Three structural defenses backstop the validate→commit gaps:

- **DB unique constraints on `block_hash` and `pending_txn.txid`** convert any duplicate-row commit race into a single `IntegrityError`, caught and resolved by the rollback handlers in `Node.add_block` and `Node.receive_transaction`.
- **The per-block recursive CTE `BlockDAO._block_chain`** scopes every `Chain.validate_block_txn` / `Chain.get_transaction` / `Chain.get_inflows_count` read to the candidate block's own lineage. Two concurrent blocks at the same height built off the same parent become sibling forks; neither sees the other in its lineage walk, so both can validly persist.
- **Deterministic longest-chain tiebreakers in `ChainDAO.chains()`** (`src/cancelchain/models.py:820-828`: `ORDER BY BlockDAO.idx DESC, BlockDAO.timestamp, BlockDAO.block_hash`) make canonical selection independent of arrival order across all workers and sessions.

Crucially, the InflowDAO `UniqueConstraint('txid', 'idx')` (`src/cancelchain/models.py:207-210`) is on the consuming transaction's own `(txid, idx)` — i.e., (this txn's txid, this inflow's position within this txn) — NOT on `(outflow_txid, outflow_idx)`. Distinct transactions consuming the same outflow therefore do NOT collide at the DB layer; uniqueness of outflow consumption is enforced **per chain lineage** by `Chain.get_inflows_count` (`src/cancelchain/chain.py:312-333`), never globally. This is structurally correct for the fork-coexistence invariant Adversary 4d / 5a rely on, and it shapes the conclusion of attack a below.

#### Attack a: TOCTOU on conflicting transactions

**Pre-state:** Wallet W has an unspent coinbase outflow O at index 0 of mined transaction T_prior. Two distinct regular transactions T_x and T_y both list `Inflow(outflow_txid=T_prior.txid, outflow_idx=0)`; they spend O to different destinations (so the txids differ). Adversary submits both within milliseconds of each other.

**Attack:** POST T_x to `/api/transaction/<T_x.txid>` and POST T_y to `/api/transaction/<T_y.txid>` simultaneously. The adversary hopes that (i) both land in pending, (ii) a miller (or two competing millers) includes both in a block before the chain-context double-spend check can catch them, OR (iii) the validate→commit window on a single block lets a second conflicting block commit before its validation can see the first.

**Trace:**
1. Both `TxnView.post` calls reach `Node.receive_transaction`. Each runs `txn.validate()` (schema + signature + txid — neither examines the chain). Each calls `pending_txns.add(txn)` — distinct txids, neither already in pending, so both succeed. **PendingTxnDAO has no unique on `(outflow_txid, outflow_idx)`** (the PendingIOflowDAO row is just a spend-tracking hint, written without uniqueness — `src/cancelchain/models.py:890-911`). Adversary 1 attack a's trace already established this as "by design" (Bitcoin mempool semantics); the audit's question here is whether the race opens new persistence-layer surface.
2. **Single-block case (miller assembles a block containing both):** `Miller.create_block` iterates `pending_chain_txns`. Adds T_x via `block.add_txn(t_x)` (after `Chain.validate_block_txn(block, t_x, txn_in_block=False)` passes — block is empty). For T_y: `Chain.validate_block_txn` calls `get_inflows_count(block, T_prior.txid, 0)`. The in-memory walk at `src/cancelchain/chain.py:319-327` traverses `block.txns` and counts T_x's inflow referencing `(T_prior.txid, 0)` — `num_inflows = 1`, `txn_in_block = False`, so line 274 raises `SpentTransactionError`. T_y is discarded. **The block-assembly check closes the single-block TOCTOU.**
3. **Cross-block sibling case (two competing blocks B_x and B_y, each containing one of the conflicting txns, both extending the same parent P):** Both blocks are independently valid: each contains only one of (T_x, T_y); each block's `validate_block_txn` walks its own block and sees a single inflow against O. The chain-context `get_inflows_count` reads `BlockDAO.inflows_in_chain_count` scoped to the candidate block's lineage via the per-block recursive CTE (`src/cancelchain/models.py:362-371`). Until either block is committed, neither lineage shows a prior consumption of O. Both validate successfully.
4. **Commit race in step 3.** Worker A commits B_x: `block.to_dao().commit()` writes B_x's BlockDAO row + T_x's TransactionDAO/InflowDAO/OutflowDAO rows + the `block_transactions` m2m. Worker B commits B_y: same flow for T_y. **`BlockDAO.block_hash` `unique=True` is irrelevant** — B_x and B_y have different hashes. **InflowDAO `UniqueConstraint('txid', 'idx')`** — T_x's InflowDAO has `(T_x.txid, 0)`; T_y's has `(T_y.txid, 0)`. Different txids, no collision. Both commits succeed.
5. **Post-commit state.** Two `InflowDAO` rows exist both referencing `(outflow_txid=T_prior.txid, outflow_idx=0)`. B_x is m2m'd with T_x; B_y is m2m'd with T_y. B_x and B_y are sibling blocks under the same parent P. `ChainDAO.longest()`'s deterministic tiebreaker (`BlockDAO.idx.desc(), BlockDAO.timestamp, BlockDAO.block_hash` at `src/cancelchain/models.py:820-828`) picks one as canonical; the other becomes a stale fork. `LongestChainBlockDAO` reflects only the canonical fork; `wallet_balance` and `unspent_outflows` queries scope through it, so the stale fork's InflowDAO is invisible to canonical-chain reads.
6. **Could the loser later "win"?** Only via a deeper reorg — Adversary 4d / 5a territory. From the moment one fork becomes canonical, the other's consumption of O is inert.

**Outcome:** REJECTED at step 2 (single-block intra-block double-spend) via `SpentTransactionError`. ACCEPTED at step 4 (cross-block sibling fork) — but the outcome is structurally identical to Adversary 4 attack d (same-outflow different-txid replay across forks), which is the canonical PoW consensus property, not a validation gap.

**Result:** RELATED to A4.d. The validate→commit gap in `Chain.add_block` is structural — there is no lock between `validate_block` returning and `block.to_db()` committing — but the per-block recursive CTE means each candidate block's validation is correct relative to ITS OWN lineage at commit time, regardless of what concurrent commits write to sibling lineages. The DB-level defense is **not** an `InflowDAO` uniqueness on `(outflow_txid, outflow_idx)` (which would actually break fork coexistence — see A4.b/A5.b notes) but the lineage-scoped CTE invariant + the deterministic longest-chain selection. The cross-fork-double-spend is consensus-by-design (mitigation = recipient confirmation-depth policy, carried forward from A4.d / A5.a). **No new finding.**

#### Attack b: Pending pool race

**Pre-state:** Pending pool contains a single valid regular transaction T (with `Inflow(outflow_txid=T_prior.txid, outflow_idx=0)` consuming a coinbase outflow O). Two miller processes M_1 and M_2 are both polling the pool to build their next blocks. Both target the same chain tip P.

**Attack:** M_1 and M_2 invoke `Miller.create_block` concurrently. The hope: both iterate `pending_chain_txns(chain)` and both pull T; both produce a block including T; both mill PoW; both POST to `/api/block/<...>`. The race exploits the absence of any atomic "claim" / dequeue on T as M_1 reads it.

**Trace:**
1. `Miller.create_block` (`src/cancelchain/miller.py:82`) → `pending_chain_txns(chain)` (line 89) iterates `PendingTxnSet` (`src/cancelchain/transaction.py:372-375`) which queries `PendingTxnDAO.json_datas()` — a stateless SELECT, no row locking, no state mutation on the pool entry. **Both M_1 and M_2 receive T.** This is the structural answer to the spec's "does the pool guard against simultaneous reads?": no, the pool is read-only; there is no per-txn "claimed by miller X" state.
2. Each miller validates T via `chain.validate_block_txn(block, t, txn_in_block=False)` against its own freshly-assembled `block` and the same parent P. Pass for both. Both `block.add_txn(t)`. Both seal a coinbase (each paying their respective milling wallet) and mill PoW.
3. Both POST. The receive-block path runs `Block.from_db(block_hash)` short-circuit (different block_hashes for B_1 and B_2 since coinbases differ → distinct merkle roots → distinct headers). Both pass `block.validate()`. Both reach `Chain.add_block` against parent P. Both pass chain-context validation (each block's lineage walk doesn't see the other).
4. `block.to_db()` for B_1: writes B_1's BlockDAO, T's TransactionDAO (if not already present), T's InflowDAO `(T.txid, 0)`, T's OutflowDAO rows, the (B_1, T) m2m, plus the new coinbase's rows. Commits cleanly.
5. `block.to_db()` for B_2: `T.to_dao()` (`src/cancelchain/transaction.py:257`) returns the now-persisted TransactionDAO row for T — `TransactionDAO.get(txid)` hit, so no new InflowDAO/OutflowDAO rows are minted for T. The new (B_2, T) m2m row is written; the new coinbase's rows are written. B_2's BlockDAO.block_hash is unique (different coinbase → different merkle → different hash). Commits cleanly.
6. **Possible TOCTOU collision in step 5 if both reach `T.to_dao()` before either has committed.** Both compute `TransactionDAO.get(T.txid)` → returns None (T not persisted yet in either's snapshot). Both build new TransactionDAO instances with `txid=T.txid`. Both attempt to insert. `TransactionDAO.txid` `unique=True` (`src/cancelchain/models.py:59`) → the second commit raises `IntegrityError`. `Node.add_block` catches `SQLAlchemyError` (`src/cancelchain/node.py:189-193`), `rollback_session()`, then re-checks `Block.from_db(block.block_hash)`. The losing worker's B_2 was NEVER persisted (the rollback unwound it along with the conflicting TransactionDAO insert); `Block.from_db(B_2.block_hash)` returns None, so the `raise` at line 192 re-raises the SQLAlchemyError. The Flask handler (`api.exception_response` at `src/cancelchain/api.py:161-163`) logs and returns 500 to the losing miller. **The chain stays consistent — only one of B_1/B_2 is persisted.**
7. **Sibling-fork outcome if step 5 succeeds for both:** B_1 and B_2 are sibling blocks at the same height. Same outcome as Attack a step 5: deterministic longest-chain tiebreaker picks one canonical; the other is inert via lineage-scoping. T m2m'd with both — same as A4.b's "cross-fork same-txid replay" — harmless because each fork's lineage walk sees T spent only once.

**Outcome:** ACCEPTED in the sibling-fork shape (consensus-by-design, RELATED to A4.b / A4.d). The losing-commit-race shape (step 6) is REJECTED at the DB-uniqueness layer; the loser surfaces as 500 to the miller, which is operationally noisy but not a chain-correctness gap — the canonical chain advances by exactly one block.

**Result:** RELATED to A1.a (pending pool admission is mempool-permissive by design), A4.b (cross-fork same-txid replay is structurally legitimate), and A4.d (sibling-fork outcomes resolve via PoW consensus). The "no atomic dequeue from pending" property is structurally Bitcoin-mempool semantics; adding a per-txn claim/lock would not improve chain correctness (the DB constraints + CTE scoping already enforce it) and would introduce its own liveness risks (claimed-then-crashed millers would orphan claims). **No new finding.**

#### Attack c: Block-submission race at same height

**Pre-state:** Local chain at height N with tip P. Two independently-milled valid blocks B_x and B_y both extend P (idx = N+1, prev_hash = P.block_hash). Both have valid PoW and valid coinbases paying different miller wallets; both pass `block.validate()` and `Chain.validate_block` in isolation. Both arrive at the same node within the validate→commit window.

**Attack:** Two `/api/block/<...>` POSTs interleave. The adversary's specific concern: is chain selection deterministic and idempotent across the resulting two-tip state?

**Trace:**
1. Worker A: `Node.receive_block(B_x.to_json())`. `Block.from_db(B_x.block_hash)` returns None (first arrival). `block.validate()` pass. `process_block` → `add_block` → `Chain.from_db(P.block_hash)` returns the chain pointing at P. `chain.add_block(B_x)` validates against P's lineage → pass. `block.to_db()` → BlockDAO for B_x + transactions + m2m. Commit.
2. `chain.to_db()` (`src/cancelchain/chain.py:564-570`): `Chain.to_dao(create=True)` — `ChainDAO.get(block_hash=B_x.block_hash)` returns None, `ChainDAO.get(id=self.cid)` returns the existing P-pointing row, calls `set_block_hash(B_x.block_hash)` (line 558) — and **`ChainDAO.block_hash` is `unique=True` (`src/cancelchain/models.py:497-499`)**, so if Worker B has already committed B_y on the SAME `ChainDAO` row, `set_block_hash(B_x.block_hash)` succeeds but creates a `ChainDAO` row swap. The implementation catches `SQLAlchemyError` here (line 558) and re-fetches `ChainDAO.get(block_hash=B_x.block_hash)` (line 559) — defensive. Then `sync_longest_chain_blocks` runs, `db.session.commit()`.
3. Worker B running B_y concurrently: identical path. Both blocks land in `BlockDAO` as siblings of P. Two `ChainDAO` rows may end up: one per tip block_hash. `ChainDAO.longest()` runs `ORDER BY BlockDAO.idx.desc(), BlockDAO.timestamp, BlockDAO.block_hash` and picks the tip with the highest idx (tie: N+1 each), then earliest timestamp, then lexicographically-smallest hash. **Selection is fully deterministic.** Same query result on every worker, every retry.
4. **Idempotency:** if a third request POSTs B_x again, `Block.from_db(B_x.block_hash)` returns the persisted row → `Node.receive_block` short-circuits at line 155-156 returning None. The status code goes to 200 (already-known). No double-persistence.
5. **Materialization-rebuild race during step 2/3 — concurrent `sync_longest_chain_blocks`.** Each call begins with `_is_longest()` (`src/cancelchain/models.py:681`) which itself runs `ChainDAO.longest()` against the CURRENT DB state. The smart-reorg walk then collects diverging blocks and updates the materialization. Two concurrent workers can both decide "I'm longest" (if their reads see snapshots before the other's commit) and both attempt to mutate `LongestChainBlockDAO`. `LongestChainBlockDAO.position` has `unique=True` (`src/cancelchain/models.py:483`), so a concurrent INSERT at the same position raises `IntegrityError` on the second commit. The catch path — `Chain.to_db` does NOT wrap `sync_longest_chain_blocks` in its own try/except; the error propagates to `Node.add_block`'s `SQLAlchemyError` catch, which rolls back the session AND the just-committed `block.to_db()` is ALREADY committed (it had its own `db.session.commit()` inside `block.to_dao().commit()` at `src/cancelchain/models.py:335-337` BEFORE `chain.to_db` runs). The losing worker therefore observes: B_y persisted, but the ChainDAO row + materialization update for B_y rolled back. A subsequent `ChainDAO.longest()` call on either worker still returns the canonical tip (whichever ChainDAO row survives) — and `_is_longest` cache is bumped only on successful rebuilds (`_bump_generation` at line 743 runs only on the success path, after the DB ops complete).
6. **Phase 6.5 known risk (Adversary 5d).** Even when sync_longest_chain_blocks commits successfully, the `_chain_generation` ClassVar bump is process-local — Worker A's bump doesn't reach Worker B's process. Worker B's held `ChainDAO` instances with cached `_is_longest=True` continue returning the cached value until the instance is rebound by Flask-SQLAlchemy's per-request scope. Adversary 5d's trace established this affects READ-layer accuracy (`wallet_balance` returning stale results) but NOT validation correctness — block validation walks per-block recursive CTEs and does not consult `_is_longest` or `LongestChainBlockDAO`.

**Outcome:** ACCEPTED — both sibling blocks persist deterministically; chain selection is correct and idempotent. The materialization-rebuild race surfaces a noisy 500 to one worker (if their sync_longest_chain_blocks commit collides on `LongestChainBlockDAO.position`) but converges to consistency on the next request: `ChainDAO.longest()` is recomputed on each call from DB state. No persistent invariant violation.

**Result:** RELATED to A5.d (cross-worker stale `_is_longest` cache). The block-submission race itself is consensus-correct: deterministic tiebreakers + per-block lineage scoping mean both sibling blocks coexist and one is unambiguously canonical. The operational concern is the noisy 500 from the materialization-rebuild collision (and the brief stale-cache window), which the Phase 6.5 spec already documented. **No new finding.** Severity if it were a finding: Low (operational noise, no chain-correctness consequence, no value-conservation break).

#### Attack d: ChainFill race

**Pre-state:** Two `Node.fill_chain(tip)` calls execute concurrently — e.g., the `cancelchain sync` CLI started in two terminals against the same node, OR a miller `poll_latest_blocks` running while a sync command is in flight. Both target tips that share ancestor blocks not yet in our `BlockDAO`.

**Attack:** Both fill_chains stage overlapping ancestry into the `chain_fill` / `chain_fill_block` tables, then both run their apply loops. The hope: (i) staging-table collision corrupts the resumable-walk state, (ii) the apply loops both call `Node.add_block` on the same block_hash and one crashes mid-commit leaving partial state, or (iii) the per-block commits A2.e flagged compound with concurrent applies to expand the partial-adoption window.

**Trace:**
1. Each `fill_chain` call creates its OWN `ChainFill` row (`src/cancelchain/node.py:315-316`) — autoincrement PK; no collision possible. Each stages its blocks into ChainFillBlock rows owned by that ChainFill via the `chain_fill_id` FK. `ChainFillBlock` has NO `UniqueConstraint('block_hash')` (`src/cancelchain/models.py:938-957`), so two concurrent stagers writing the same hash into different ChainFill collections do not collide. The staging tables are isolated per call.
2. The walk-back phase calls `Block.from_db(prev_hash)` (line 330) and `request_block(prev_hash)` (line 333) — both stateless reads. Concurrent walks may double-fetch the same ancestor from a peer (wasted bandwidth) but cannot corrupt state. Each walk terminates at the first ancestor present in `BlockDAO` — which may be different for the two walks if one walk's apply phase has already started committing blocks ahead of the other's walk. That's fine: each walk is consistent against its own snapshot.
3. The apply phase iterates `chain_fill.blocks` ordered by `ChainFillBlock.idx` ascending (relationship `order_by='ChainFillBlock.idx'` at `src/cancelchain/models.py:920-924`). Each iteration calls `Block.from_json(...)` then `self.add_block(block)`. **`Node.add_block` does NOT short-circuit on `Block.from_db(block.block_hash)`** before invoking `chain.add_block`; that early-return only lives in `Node.receive_block` (`src/cancelchain/node.py:155-156`) and `Node.process_block` (line 174-175). So in the apply loop, `chain.add_block(block)` always runs:
   - `validate_block(block)` re-validates an already-persisted block. Pass (it was previously persisted, so it's valid against its lineage).
   - `block.to_db()` → `BlockDAO.get(block.block_hash)` returns the existing row → no new insert. The existing-row path skips the relationship rebuild (`__init__` doesn't run; `to_dao()` returns the existing instance directly). `commit()` is effectively a no-op (`db.session.add(existing_instance)` is idempotent; commit flushes nothing).
   - `chain.to_db()` updates ChainDAO + sync_longest_chain_blocks — idempotent against the same tip.
   So duplicate apply is **idempotent at the BlockDAO/TransactionDAO level**, just wasteful — re-validating an already-persisted block re-walks its lineage via the recursive CTE.
4. **A2.e compounding.** If apply_1 commits B1, B2 and then B3 fails validation, the A2.e partial-adoption gap persists B1, B2. If apply_2 races and ALSO commits B1, B2, the second apply's commit_2 path is idempotent for B1, B2 (existing rows), then sees B3 as legitimately missing from BlockDAO and either succeeds (if apply_2 staged a valid B3') or fails the same way. The race compounds neither A2.e's blast radius nor introduces new state.
5. **Concurrent `chain.to_db()` materialization rebuild** — same as Attack c step 5. `LongestChainBlockDAO.position` unique catches simultaneous "I'm longest" rebuilds; loser rolls back its ChainDAO update, BlockDAO commits already landed independently (each block has its own `block.to_dao().commit()`), state converges.

**Outcome:** REJECTED on every distinct angle. ChainFill staging is isolated per call (autoincrement PK + no block_hash uniqueness); apply is idempotent at BlockDAO level; A2.e's per-block-commit gap is not amplified by concurrent applies; chain.to_db materialization race converges via DB-level position uniqueness + per-request `ChainDAO.longest()` reads.

**Result:** RELATED to A2.e (the underlying per-block-commit gap, already flagged) and A5.c (orphan ChainFill rows from killed processes — noted as operational in A5's Recommendations). The concurrent-call angle does not surface a distinct gap: the staging tables are per-call by autoincrement PK, the apply loop is idempotent against already-persisted blocks, and the materialization-rebuild race is the same Attack c shape. **No new finding.**

**Adversary 6 summary.** Four attack angles traced; zero new findings. The chain-correctness invariant survives every race window examined because three structural defenses backstop the validate→commit gaps: (1) `unique=True` on `block_hash`, `pending_txn.txid`, `transaction.txid`, and `longest_chain_block.position` converts duplicate-row races into single-winner IntegrityError-catching paths in `Node.add_block` / `Node.receive_transaction`; (2) the per-block recursive CTE `BlockDAO._block_chain` makes every `Chain.validate_block_txn` walk correct relative to ITS OWN candidate's lineage, so concurrent sibling-fork commits don't corrupt each other's validation; (3) the deterministic `ChainDAO.chains()` tiebreaker (`idx.desc(), timestamp, block_hash`) makes canonical selection arrival-order-independent. The operational concerns surfaced — noisy 500s from materialization-rebuild collisions during Attack c, stale `_is_longest` cache during multi-worker reorgs (Adversary 5d), wasted miller PoW on race-loser blocks (Attack b) — are all already flagged in prior findings or the Phase 6.5 spec's Risks; no demonstration tests are added for Adversary 6.

### Adversary 7: Genesis / edge-case attacker

**Capabilities:** Anything legitimate. Targets the special-case code paths that are likely under-tested.

**Validation pipeline summary.** Adversary 7's attacks probe boundary conditions and special-case branches scattered across the schema layer, `Block.validate` (`src/cancelchain/block.py:289`), and `Chain.validate_block` (`src/cancelchain/chain.py:170`). Three concrete code paths concentrate the boundary surface:

1. **Schema-layer length bounds.** `BlockModel.txns` is `Field(min_length=1, max_length=MAX_TRANSACTIONS=100)` (`src/cancelchain/block.py:82-85`). `RegularTransactionModel.inflows`/`outflows` are `min_length=1, max_length=MAX_FLOWS=50` (`src/cancelchain/transaction.py:101-104, 89-91`). `CoinbaseTransactionModel.inflows` is `min_length=0, max_length=0` (must be exactly empty); `outflows` is `min_length=1, max_length=4` (`src/cancelchain/transaction.py:107-109`). Subject content lives behind a `Subject = Annotated[str, AfterValidator(_check_subject)]` (`src/cancelchain/payload.py:65`) which delegates to `validate_subject` (`src/cancelchain/payload.py:39-46`) — `1 <= len(decode_subject(s)) <= 79`. Pydantic comparisons are inclusive on both ends.
2. **Genesis-specific code paths.** `is_genesis_block(block)` returns `block.prev_hash == GENESIS_HASH` (`src/cancelchain/chain.py:48-49`), where `GENESIS_HASH = mill_hash_str('GENESIS')` is a fixed sentinel hash (no `BlockDAO` row resolves to it). Eight call sites condition on this: `Chain.block_chain` (lines 89, 91), `Chain.link_block` (line 146-148), `Chain.validate_block` (lines 175, 185), `Node.receive_block` (line 162), `Node.fill_peer` (line 262), `Node.fill_chain` (line 327). Each treats genesis as the "no parent expected" case — the parent-presence check at `Chain.validate_block` line 175-176 explicitly tolerates `prev_block is None` when `is_genesis_block(block)`. No code checks "is this *the* canonical genesis"; any block carrying `prev_hash=GENESIS_HASH` qualifies.
3. **Timestamp boundaries.** Three call sites apply `TXN_TIMEOUT = timedelta(hours=4)` (`src/cancelchain/block.py:50`) with **inconsistent comparison operators**: `Block.validate_transaction` (line 269) uses strict `<` (`txn_ts < block_ts - TXN_TIMEOUT` → Expired), `Miller.pending_chain_txns` (`src/cancelchain/miller.py:74-76`) uses strict `>` (`txn_ts > now - TXN_TIMEOUT` → include), `Node.discard_expired_pending_txns` (`src/cancelchain/node.py:105`) uses `<=` (`txn_ts <= now - TXN_TIMEOUT` → discard). A txn whose timestamp is *exactly* `TXN_TIMEOUT` old is non-expired per Block but excluded from miller selection and discarded from pending.

`validate_hash_diff` (`src/cancelchain/block.py:54-55`) uses strict `int(block_hash, 16) < int(target, 16)`; equality is rejected, matching Bitcoin's PoW semantics. `BlockModel.validate_difficulty` (`src/cancelchain/block.py:88-92`) wraps the same check.

The traces below test each boundary and special-case path against the attack list.

#### Attack a: Empty block (no transactions)

**Pre-state:** Local chain at height ≥ 0. Adversary constructs a Block with `txns=[]` (no coinbase, no regular txns) and mills until PoW.

**Attack:** POST the empty block to `/api/block/<block_hash>`.

**Trace:**
1. `src/cancelchain/node.py:150` — `Block.from_json` calls `BlockModel.model_validate_json` (`src/cancelchain/block.py:354-361`). `BlockModel.txns` is `Field(min_length=1, max_length=MAX_TRANSACTIONS)` (`src/cancelchain/block.py:82-85`). Zero-length `txns` → **`InvalidBlockError`** (Pydantic: `List should have at least 1 item`).
2. Even if the adversary somehow constructs a Block in-memory and tries to use `block.seal(wallet, reward)`, that path calls `add_coinbase` which appends the coinbase to `self.txns` — so a sealed block always carries at least the coinbase. `block.txns == []` is only reachable by bypassing the seal API entirely, and the schema layer rejects it on submit.
3. `build_merkle_tree` (`src/cancelchain/block.py:173-178`) on empty `txns` produces an `InmemoryTree()` with no entries. `tree.root` is None (pymerkle convention for empty trees); `get_merkle_root` returns None (line 180-182). `validate_merkle_root` (line 255-257) compares `self.merkle_root != self.get_merkle_root()`. If the adversary set `merkle_root=None` AND somehow bypassed schema with `txns=[]`, the equality would hold — but `merkle_root` itself is `MillHashType` (`src/cancelchain/block.py:81`), required non-None at the schema layer, so any submitted block has a non-None `merkle_root` and the comparison against None fails.

**Outcome:** REJECTED at step 1 via `InvalidBlockError` (schema `min_length=1` on `txns`). The empty-merkle-tree behavior at `get_merkle_root` is also defensive (returns None instead of crashing), so the validation chain stays well-defined even on a bypass.

**Result:** Validation correctly rejects. The "coinbase-only" block (one txn — the coinbase) is the structural minimum; this is enforced at the schema layer and is what Adversary 3's censor-attack relies on as the lower bound. No finding.

#### Attack b: First block of the chain (genesis)

**Pre-state:** Empty `BlockDAO` (no blocks persisted yet). Adversary intends to submit the chain's first-ever block, which by definition has no parent.

**Attack:** POST a block with `prev_hash = GENESIS_HASH`, `idx = 0`, a single coinbase txn, `target = MAX_TARGET`. Sub-attack b.ii: a *second* legitimate-looking genesis block (different timestamp / different miller wallet) is also submitted to fragment the chain registry.

**Trace:**
1. `Node.receive_block` (`src/cancelchain/node.py:140`) → `Block.from_json` schema pass. `Block.from_db(block.block_hash)` returns None — short-circuit doesn't fire.
2. `block.validate()` (`src/cancelchain/block.py:289`) runs `BlockModel.model_validate` (schema), `validate_block_hash`, `validate_merkle_root`, per-txn `validate_transaction` (no regulars; `regular_txns == []`), `validate_coinbase`. All pass for a well-formed genesis.
3. `src/cancelchain/node.py:158-164` — parent-presence check: `prev_hash = GENESIS_HASH`, `Block.from_db(GENESIS_HASH)` returns None (no row at that hash), but `is_genesis_block(block)` is True, so the `MissingBlockError` branch is skipped.
4. `Node.process_block` → `Node.add_block` (`src/cancelchain/node.py:181-194`): `Chain.from_db(block_hash=block.prev_hash=GENESIS_HASH)` returns None (no `ChainDAO` row at `GENESIS_HASH`). Falls through to `self.create_chain(block=block)` (line 187) which builds a fresh `Chain(block_hash=GENESIS_HASH)` and calls `chain.add_block(block)`.
5. `Chain.add_block` → `Chain.validate_block` (`src/cancelchain/chain.py:170`):
   - Line 174: `prev_block = Block.from_db(GENESIS_HASH) = None`.
   - Line 175-176: `is_genesis_block(block)` True → no `InvalidPreviousHashError`.
   - Line 177-183: `prev_block is None`, the `OutOfOrderBlockError` check requires `prev_block.timestamp_dt`, so the AND condition is False — skipped.
   - Line 185-186: `block.prev_hash != prev_hash` where `prev_hash = None` (since `prev_block is None`), so `GENESIS_HASH != None` is True, but `is_genesis_block(block)` is True → skipped.
   - Line 187-191: `prev_index = -1` (prev_block None / idx None branch); line 192 requires `block.idx == 0`. Pass.
   - Line 194-195: `block.target == self.block_target(block=block)`. `Chain.block_target` (`src/cancelchain/chain.py:109-138`) with `index == 0` returns `MAX_TARGET` (line 114-115). Pass.
   - Per-txn loop (line 196-197): empty `regular_txns`; skipped.
   - `validate_block_coinbase` (line 198): coinbase pays `REWARD`, S/G/M comps empty match. Pass.
6. `block.to_db()` writes the BlockDAO + coinbase TransactionDAO/OutflowDAO. `chain.to_db()` writes a `ChainDAO(block_hash=block_genesis.block_hash)`. Genesis is now persisted.

**Sub-attack b.ii — alternate genesis fragmenting the chain registry:** The adversary mills a *second* block with `prev_hash = GENESIS_HASH`, `idx = 0`, different timestamp / coinbase address. Trace:
- All of steps 1-5 pass identically — the schema and `Chain.validate_block` paths only require `prev_hash == GENESIS_HASH` and `idx == 0` and `target == MAX_TARGET`. There is no global "is the canonical genesis already taken?" check anywhere; the closest is the duplicate-hash short-circuit at `Node.receive_block` line 155, which only fires for *byte-identical* blocks.
- Step 4: `Chain.from_db(block_hash=GENESIS_HASH)` still returns None (no `ChainDAO` was ever bound to `GENESIS_HASH` — the original genesis's `ChainDAO` row binds to `block_genesis.block_hash`, not the sentinel `GENESIS_HASH`). So `create_chain(block=block_genesis_2)` builds a fresh `Chain` instance and calls `chain.add_block(block_genesis_2)`.
- A second `ChainDAO` row is committed at `chain.to_db()` (`src/cancelchain/chain.py:564-570`): `ChainDAO.get(block_hash=block_genesis_2.block_hash)` returns None, `ChainDAO.get(id=self.cid)` returns None (cid is None for a fresh Chain), `dao = ChainDAO(block_hash=block_genesis_2.block_hash)`. Two `ChainDAO` rows now exist, each pointing at one of the two genesis blocks.
- `ChainDAO.longest()` picks one via `ORDER BY BlockDAO.idx DESC, BlockDAO.timestamp ASC, BlockDAO.block_hash ASC` (`src/cancelchain/models.py:820-828`). Both have `idx=0`; the canonical winner is the earlier-timestamped one, with hash as tiebreaker. The other becomes a stale 1-block chain that consumes DB rows indefinitely.

**Outcome:** ACCEPTED — the chain registry permits unlimited genesis-tagged blocks; each creates a new sibling chain. The validation pipeline accepts arbitrarily many "genesis" blocks because `is_genesis_block(block)` is just a `prev_hash == GENESIS_HASH` flag, not an "is the canonical genesis" predicate.

**Finding A7.b — Severity Low:** `Chain.validate_block` (`src/cancelchain/chain.py:170-198`) accepts any block whose `prev_hash == GENESIS_HASH`, `idx == 0`, and `target == MAX_TARGET`, regardless of whether a different genesis block is already persisted. Each accepted alternate-genesis creates a fresh `ChainDAO` row (via `Node.add_block`'s `create_chain` fallback at `src/cancelchain/node.py:187`), fragmenting the chain registry into N parallel single-block chains. `ChainDAO.longest()` still picks the canonical genesis via deterministic tiebreaker, so chain-correctness is preserved — but the DB accumulates one unrooted `ChainDAO` (and one `BlockDAO`/`TransactionDAO`/`OutflowDAO`) row per submission. A MILLER-role adversary can therefore inflate the chain registry with cheap-to-mill alternate genesis blocks (production `MAX_TARGET = 6 leading hex zeros` is still tractable for any modest hashrate). The attack does not break value conservation (each genesis pays `REWARD` to its own miller, and only the canonical genesis's `LongestChainBlockDAO` rows feed into `wallet_balance` reads), but it is a DB-bloat / inventory-pollution gap with no operational recovery path.

**Remediation sketch:** In `Chain.validate_block` (`src/cancelchain/chain.py:170`), after the `is_genesis_block(block)` branch passes the parent / idx / target checks, look up whether a genesis block is already persisted: `existing_genesis = db.session.execute(db.select(BlockDAO).where(BlockDAO.idx == 0, BlockDAO.prev_hash == GENESIS_HASH)).scalar_one_or_none()`. If `existing_genesis is not None` and `existing_genesis.block_hash != block.block_hash`, raise a new `DuplicateGenesisError(InvalidBlockError)` exception. This enforces canonical-genesis uniqueness at the validation layer without changing the `is_genesis_block` predicate or breaking the legitimate first-genesis flow. An equally good alternative is to make `GENESIS_HASH` resolve to a hardcoded canonical block (committed as part of `db.create_all()` / migrations); but that requires migration work and changes the bootstrap flow, so the validate-time uniqueness check is the more conservative fix.

**Demonstration test:** `test_a7_b_alternate_genesis_fragments_chain_registry` in `tests/test_verification_audit.py`.

#### Attack c: Block transaction-count boundaries (0, 100, 101)

**Pre-state:** Local chain at height ≥ 0. Adversary intends to test the exact boundaries of `BlockModel.txns: Field(min_length=1, max_length=MAX_TRANSACTIONS=100)` (`src/cancelchain/block.py:82-85`).

**Attack inputs:**
- **c.i (0 txns):** Reduces to Attack a — empty block, REJECTED at schema.
- **c.ii (exactly 100 txns: 99 regulars + 1 coinbase):** The upper-inclusive boundary. `Miller.create_block`'s `if i >= MAX_TRANSACTIONS - 1: break` (`src/cancelchain/miller.py:94`) caps `i` at 99 regular txns; the subsequent `chain.seal_block(block, ...)` appends one coinbase via `Block.add_coinbase` (`src/cancelchain/block.py:213-214`), making `len(block.txns) == 100`.
- **c.iii (exactly 101 txns: 100 regulars + 1 coinbase):** Schema upper bound + 1. Only constructible by bypassing `Miller.create_block` (hand-built Block + manual `add_txn` calls).

**Trace:**
1. **c.i:** schema rejects (see Attack a).
2. **c.ii:** `BlockModel.model_validate` accepts `len(txns) == 100` (the bound is `max_length=100`, inclusive). `validate_merkle_root` recomputes over all 100 entries via `build_merkle_tree` (pymerkle handles N=100 without special-case branches). `for txn in self.regular_txns` iterates the first 99 (`self.txns[0:-1]`). `validate_coinbase` operates on `self.txns[-1]`. Pass. Block persists.
3. **c.iii:** schema rejects with `List should have at most 100 items` (regression-covered by `tests/test_block.py::test_too_many_txns`).

**Outcome:** REJECTED at step 1/3 for c.i/c.iii; ACCEPTED at step 2 for c.ii. The boundaries are **inclusive on both ends** (1 ≤ N ≤ 100). The off-by-one risk would be a `max_length=MAX_TRANSACTIONS - 1` typo elsewhere, but the schema is consistent with the miller's `MAX_TRANSACTIONS - 1` regular-txn cap (which leaves one slot for the coinbase, yielding `MAX_TRANSACTIONS` total).

**Result:** Boundaries are correct and the documentation comment in `src/cancelchain/miller.py:94` matches the schema. No finding.

#### Attack d: Subject-length boundaries (0, 1, 79, 80 chars)

**Pre-state:** Adversary constructs an outflow with `subject = encode_subject(raw)` where `raw` is each boundary length.

**Attack inputs:**
- **d.i (0 chars):** `raw = ''`, `encode_subject('') = ''`. Tests the lower boundary.
- **d.ii (1 char):** `raw = 'a'`. Tests the inclusive lower bound.
- **d.iii (79 chars):** `raw = 'a' * 79`. Tests the inclusive upper bound.
- **d.iv (80 chars):** `raw = 'a' * 80`. Tests the off-by-one upper.

**Trace:** `validate_subject` (`src/cancelchain/payload.py:39-46`) decodes via `decode_subject` then asserts `MIN_SUBJECT_LENGTH (=1) <= len(raw_subject) <= MAX_SUBJECT_LENGTH (=79)` and round-trips through `encode_subject` to confirm canonical-form. Live probe results:
- 0 chars: `False` (rejected — len < 1).
- 1 char: `True` (accepted — inclusive lower).
- 79 chars: `True` (accepted — inclusive upper).
- 80 chars: `False` (rejected — len > 79).

The Pydantic-level enforcement runs via `Subject = Annotated[str, AfterValidator(_check_subject)]` (`src/cancelchain/payload.py:65`) wherever subject/forgive/support fields appear on `OutflowModel`. Out-of-bounds values raise `ValueError(f'Invalid subject: ...')` → wrapped as `InvalidTransactionError` at the schema layer.

**Outcome:** REJECTED at the schema layer for 0-char and 80-char inputs; ACCEPTED for 1- and 79-char. **Boundaries are inclusive on both ends, matching the documented intent "1-79 chars".** No off-by-one.

**Result:** Subject-length boundaries are correctly enforced via `MIN_SUBJECT_LENGTH <= len <= MAX_SUBJECT_LENGTH`. Regression-covered by `tests/test_payload.py::test_validate_subject` (positive case) and the boundary check is naturally enforced through Pydantic's `_check_subject` AfterValidator. No finding.

#### Attack e: Just-expired transaction at exact TXN_TIMEOUT boundary

**Pre-state:** Local chain at height ≥ 1. Adversary constructs a transaction T with `timestamp = now - TXN_TIMEOUT` (i.e., *exactly* 4 hours old per `src/cancelchain/block.py:50`). T is otherwise valid.

**Attack:** POST T to `/api/transaction/<T.txid>`, then attempt to mine it into a block whose timestamp is exactly `T.timestamp + TXN_TIMEOUT`.

**Trace:** Three call sites apply `TXN_TIMEOUT` with **different comparison operators**:

1. **`Block.validate_transaction` (`src/cancelchain/block.py:266-270`):**
   ```
   if txn_ts_dt < self.timestamp_dt - TXN_TIMEOUT:
       raise ExpiredTransactionError()
   ```
   Strict `<`. At `txn_ts == block_ts - TXN_TIMEOUT` exactly: condition is False → **NOT expired** → block accepts the txn.
2. **`Miller.pending_chain_txns` (`src/cancelchain/miller.py:71-76`):**
   ```
   expired_dt = now() - TXN_TIMEOUT
   if (txn.timestamp_dt is not None
       and txn.timestamp_dt > expired_dt
       and not chain.get_transaction(txn.txid)):
       yield txn
   ```
   Strict `>`. At `txn_ts == expired_dt` exactly: condition is False → **NOT yielded** → miller skips the txn.
3. **`Node.discard_expired_pending_txns` (`src/cancelchain/node.py:102-106`):**
   ```
   expired_dt = now() - TXN_TIMEOUT
   if txn.timestamp_dt <= expired_dt:
       self.pending_txns.discard(txn)
   ```
   `<=`. At `txn_ts == expired_dt` exactly: condition is True → **discarded**.

So an at-the-boundary txn:
- Is accepted into pending via `Node.receive_transaction` (no timestamp check there — schema only verifies `iso_2_dt` parses).
- Is discarded by the next `discard_expired_pending_txns` sweep.
- Is excluded from `Miller.create_block`'s pending-pool walk if it survives the sweep.
- Would be accepted by `Block.validate_transaction` if a miller hand-built a block including it.

The asymmetry means the adversary cannot mine a just-expired txn through the standard miller path (steps 2-3 conspire to remove it), but a hand-crafted block submitted to `/api/block` would have the just-expired txn accepted by the block-layer validation.

**Outcome:** REJECTED operationally (the txn gets discarded from pending before any miller picks it up) but ACCEPTED structurally (block-layer validation considers it non-expired). The boundary inconsistency is observable: the same txn-timestamp is "alive" per Block layer and "dead" per Node/Miller layers.

**Finding A7.e — Severity Low:** Three call sites apply `TXN_TIMEOUT` with three different comparison operators around the boundary value: `Block.validate_transaction` uses strict `<` (`src/cancelchain/block.py:269`), `Miller.pending_chain_txns` uses strict `>` (`src/cancelchain/miller.py:74`), and `Node.discard_expired_pending_txns` uses `<=` (`src/cancelchain/node.py:105`). A txn whose `timestamp` is *exactly* `now - TXN_TIMEOUT` is therefore "non-expired" per the block validator but "expired" per pending-pool maintenance and miller selection. No chain-correctness invariant is violated (the txn would be REJECTED via the miller's exclusion before reaching a block), but the inconsistency is a latent foot-gun: a future refactor that swaps the miller's `>` for `>=` (or removes `discard_expired_pending_txns`'s `<=` branch) would let the txn drift to a state where the block layer accepts what the miller silently rejected, complicating debugging of "why didn't this txn get mined." The spec intent ("`TXN_TIMEOUT` window") is ambiguous about whether the boundary is open or closed; pick one and apply consistently.

**Remediation sketch:** Pick a canonical comparison and apply across all three sites. Recommended: `<` (open boundary; "strictly older than `TXN_TIMEOUT`" = expired). Concretely, change `Node.discard_expired_pending_txns` line 105 from `<=` to `<`, and change `Miller.pending_chain_txns` line 74 from `>` to `>=`. After the change, all three sites agree that `txn_ts == now - TXN_TIMEOUT` is "alive". Add a docstring on `TXN_TIMEOUT` clarifying the semantics. The fix has no observable behavior change for txns that aren't exactly at the boundary (~negligible in practice but defensive against the refactor risk).

**Demonstration test:** `test_a7_e_txn_timeout_boundary_inconsistency` in `tests/test_verification_audit.py`.

#### Attack f: Transaction with empty inflow list

**Pre-state:** Adversary constructs a transaction with `inflows=[]` and tests both the regular-txn path (`Node.receive_transaction`) and the coinbase path (`Block.add_txn(is_coinbase=True)`).

**Attack:**
- **f.i (regular path):** POST a txn with `inflows=[]` to `/api/transaction/<txid>`.
- **f.ii (smuggle as coinbase):** Hand-build a Block and call `add_txn(empty_inflow_txn, is_coinbase=True)`.

**Trace:**
1. **f.i:** `Node.receive_transaction` → `Transaction.from_json` uses base `TransactionModel` (`src/cancelchain/transaction.py:78`), which allows `inflows: min_length=0, max_length=50` (`src/cancelchain/transaction.py:86-88`). Parses successfully. Then `Node.receive_transaction` line 89 calls `txn.validate()` which routes to `RegularTransactionModel.model_validate` (line 215-216 + 218-221). `RegularTransactionModel.inflows: min_length=1, max_length=50` (`src/cancelchain/transaction.py:102-104`). Empty inflows → Pydantic error → **`InvalidTransactionError`** (`inflows: List should have at least 1 item`).
2. **f.ii:** `Block.add_txn(txn, is_coinbase=True)` (`src/cancelchain/block.py:199-206`) calls `txn.validate_coinbase()` which routes to `CoinbaseTransactionModel.model_validate`. `CoinbaseTransactionModel.inflows: min_length=0, max_length=0` (`src/cancelchain/transaction.py:108`). Empty inflows are *required* for a coinbase, so pass — but the schema also enforces `outflows: min_length=1, max_length=4`, `txid`/`signature` shape, etc. The Block-level `validate_coinbase` (`src/cancelchain/block.py:274-287`) additionally checks the S/G/M shape matches. So coinbase shape is structurally enforced via the dedicated schema split.

**Outcome:** REJECTED at step 1 for f.i (`InvalidTransactionError`). f.ii is the *correct* coinbase shape; the dual-schema (`RegularTransactionModel` / `CoinbaseTransactionModel`) is the structural defense — a txn with empty inflows is *only* valid as a coinbase, and the coinbase enters the block exclusively via the `is_coinbase=True` path on `Block.add_txn`.

**Result:** Validation correctly rejects empty-inflow regular txns at the schema layer and structurally rejects empty-inflow non-coinbase entry. The schema split is the right mechanism. Regression-covered indirectly by `tests/test_transaction.py` (regular vs coinbase schema separation). No finding.

#### Attack g: proof_of_work boundary (0, target-1, target, target+1)

**Pre-state:** Adversary constructs a block with various `proof_of_work` values and measures whether validation accepts each.

**Attack inputs:**
- **g.i (`proof_of_work=0`):** The schema `Field(ge=0)` lower bound. Whether this lands a valid hash is probabilistic — production `MAX_TARGET = 6 leading hex zeros` makes pow=0 essentially never satisfy `mill_hash(header) < target`; the easy-mill `MAX_TARGET = F * 64` makes any pow satisfy.
- **g.ii (hash exactly equal to target):** `validate_hash_diff` (`src/cancelchain/block.py:54-55`) uses strict `int(hash, 16) < int(target, 16)`. Adversary tries to force `hash == target`.
- **g.iii (hash 1 below target):** The just-accepted upper-edge case.

**Trace:**
1. `BlockModel.proof_of_work: Field(ge=0)` (`src/cancelchain/block.py:80`) — the lower bound is `0`, inclusive. `proof_of_work = 0` is accepted at the schema layer.
2. `BlockModel.validate_difficulty` (`src/cancelchain/block.py:88-92`) raises `ValueError('Missed target')` when `int(block_hash, 16) >= int(target, 16)`. Equality is rejected (strict `<` semantics). The check operates on the submitted `block_hash` value, which the schema enforces is a valid `MillHashType` (64-char base64).
3. `Block.validate_block_hash` (`src/cancelchain/block.py:251-253`) recomputes `mill_hash(header)` and checks `block_hash != get_header_hash()`. The header includes `proof_of_work` (`src/cancelchain/block.py:146-157, 167`), so a mismatched pow that's still below target wouldn't yield the submitted hash → `InvalidBlockHashError`.
4. `Block.validate_proof_of_work(pow)` (`src/cancelchain/block.py:169-171`): returns `validate_hash_diff(mill_hash_str(potential_header), self.target)`. This is what `Block.solve` consults; if pow doesn't produce hash < target, `solve` raises `InvalidProofError`.

So the boundary semantics are:
- `pow = 0`: structurally legal; whether it produces a valid PoW depends on `mill_hash(...)` output for the specific header. Production MAX_TARGET makes it cryptographically infeasible; test MAX_TARGET makes it trivially feasible.
- `hash == target`: REJECTED (strict `<`).
- `hash < target` (any amount): ACCEPTED.

**Outcome:** REJECTED at step 2 for any submitted `block_hash >= target` (including equality). The boundary is **exclusive at the upper end** (`hash < target`), matching Bitcoin's PoW semantics. `pow = 0` itself is not directly an attack — what matters is whether the resulting hash is below target, which the schema enforces against the submitted values.

**Result:** PoW boundary is correctly enforced. The strict `<` matches Bitcoin convention; equality would let an adversary submit a "trivially-accepted" hash without doing the work. No finding.

#### Attack h: Non-printable / control-char subject

**Pre-state:** Adversary constructs an outflow with `subject = encode_subject(raw)` where `raw` is a 1-79-char string of non-printable bytes: null bytes (`\x00`), control characters (`\x07` BEL, `\x1b` ESC, `\x0a` LF, `\x7f` DEL), RTL override (`‮`), zero-width joiners (`‍`).

**Attack:** POST a transaction whose `outflows` includes `Outflow(amount=N, subject=encode_subject('\x00'))` (or similar). The adversary's goal is not direct value theft but downstream rendering exploits: terminal control-char injection in CLI tools that print subjects, RTL spoofing in web UIs, log-injection via newlines / null bytes.

**Trace:**
1. `Node.receive_transaction` → `Transaction.from_json` → `TransactionModel.model_validate_json`. `OutflowModel.subject` is `Subject | None` where `Subject = Annotated[str, AfterValidator(_check_subject)]` (`src/cancelchain/payload.py:65`).
2. `_check_subject(s)` (`src/cancelchain/payload.py:58-62`) calls `validate_subject(s)` (line 39-46). `validate_subject` does `decode_subject(s)` and checks **only the length** (`MIN_SUBJECT_LENGTH <= len(raw) <= MAX_SUBJECT_LENGTH`) plus the round-trip canonical-form invariant (`encode_subject(raw_subject) == subject`).
3. **No content validation.** Live probe (run during this audit):
   - `validate_raw_subject('\x00')` → `True` (null byte 1-char raw accepted).
   - `validate_raw_subject('\x07')` (BEL), `'\x1b'` (ESC), `'\x0a'` (LF), `'\x7f'` (DEL) → all `True`.
   - `validate_raw_subject('‮')` (RTL override) → `True`.
   - `validate_raw_subject('‍')` (zero-width joiner) → `True`.
   - `validate_raw_subject('😀')` (emoji) → `True`.
   - Their `encode_subject` round-trips all pass `validate_subject`.
4. The transaction enters pending, mines into a block, and persists with the non-printable subject intact in `OutflowDAO.subject` (`src/cancelchain/models.py`).

**Outcome:** ACCEPTED. The validation pipeline enforces length but not character-class restrictions on subjects.

**Finding A7.h — Severity Low:** `validate_subject` (`src/cancelchain/payload.py:39-46`) and `validate_raw_subject` (line 49-55) enforce only length bounds (`1 <= len <= 79`) and canonical base64-url encoding round-trip; they accept any UTF-8 codepoint including null bytes (`\x00`), C0/C1 control characters (`\x07` BEL, `\x1b` ESC, `\x0a` LF, `\x7f` DEL), bidirectional override (`‮` RLO), zero-width joiners (`‍`), and zero-width spaces. Subjects flow through `OutflowDAO.subject` into multiple read paths: `BalanceView` rendering (`src/cancelchain/api.py`), CLI `subject` commands (`src/cancelchain/command.py`), `wallet_leaderboard` JSON responses. A subject like `f'spoofed{chr(0x1b)}[31mRED'` or `'‮redips'` would render correctly per byte but display deceptively in any terminal/HTML consumer that doesn't strip control characters. No value-conservation invariant is violated (the malicious subject is still distinct from the spoofed one at the byte level), but the chain commits to a string the application layer is unlikely to handle safely.

**Remediation sketch:** Add a content-class check in `validate_raw_subject` (`src/cancelchain/payload.py:49-55`) after the length check: e.g., `if not all(_is_safe_codepoint(c) for c in raw_subject): return False`. The safe-codepoint predicate should reject Unicode general categories `Cc` (control), `Cf` (format — includes bidi overrides and zero-width chars), `Cn` (unassigned), and `Cs` (surrogates). The `unicodedata` stdlib gives `unicodedata.category(c)` for this. The check needs to be applied symmetrically in both `validate_raw_subject` (raw form, used by CLI input) and inside `validate_subject` after `decode_subject` (encoded form, used by API/JSON input). Adding the check tightens the surface without breaking any reasonable real-world subject (which would be human-readable text); existing tests use ASCII strings like `'failing tests'` / `'bugs'` / `'vogons'`, all of which pass the category check.

**Demonstration test:** `test_a7_h_non_printable_subject_accepted` in `tests/test_verification_audit.py`.

#### Attack i: Chain with one block (no parent to validate)

**Pre-state:** Local chain has exactly one block — the genesis. Adversary's interest: does `Chain.validate()` handle the no-parent-walk case correctly?

**Attack:** Invoke `chain.validate()` on a 1-block chain to probe the walk's edge case.

**Trace:**
1. `Chain.validate` (`src/cancelchain/chain.py:158-168`): `for block in self.blocks: validate_block(block)`. `self.blocks` is `Chain.block_chain(block_hash=self.block_hash)` (`src/cancelchain/chain.py:59-60`).
2. `Chain.block_chain` (`src/cancelchain/chain.py:77-93`): yields the tip block, then walks `prev_block = Block.from_db(block.prev_hash) if block.prev_hash else None`. For the genesis, `prev_hash = GENESIS_HASH`, `Block.from_db(GENESIS_HASH)` returns None (sentinel doesn't resolve).
3. Line 89: `if prev_block is None and not is_genesis_block(block): raise MissingPreviousBlockError()`. For genesis, `is_genesis_block(block)` is True, so the raise is skipped. Pass.
4. Line 91: `if is_genesis_block(block) and prev_block is not None: raise InvalidBlockError()`. `prev_block is None`, so the raise is skipped. Pass.
5. Line 93: `block = prev_block = None`. Loop exits cleanly.
6. `validate_block(genesis)` runs once (covered by Attack b's trace). Pass.

**Outcome:** REJECTED nothing — single-block chain validates correctly. The `is_genesis_block` check in `block_chain` is exactly the special-case path that lets the walk terminate without raising `MissingPreviousBlockError`.

**Result:** The walk's genesis-termination logic is correct. No finding. (The related risk — that an alternate-genesis block could spawn a parallel chain — is captured under A7.b.)

#### Attack j: Reorg with zero common ancestor (disjoint chains)

**Pre-state:** Local chain X exists with N blocks rooted at genesis G_x. Adversary delivers chain Y of N+1 blocks rooted at a *different* genesis G_y (constructed via the alternate-genesis path from A7.b, plus N additional blocks chaining off G_y honestly milled by the adversary).

**Attack:** Submit Y's blocks via `/api/block/<...>` to the node holding X. The hope: Y becomes longer than X and triggers a reorg to a chain whose entire ancestry is disjoint from X.

**Trace:**
1. Y's G_y arrives: validated as Attack b — accepted via the `is_genesis_block` path; a new `ChainDAO` row is committed at G_y's `block_hash`.
2. Y's subsequent blocks Y_1, Y_2, ..., Y_N arrive: each is validated via `Chain.validate_block`. Their `prev_hash` chain walks through G_y (in `BlockDAO`), so the parent-presence check passes. Per-block UTXO checks (`validate_block_txn`) walk Y's lineage via the per-block recursive CTE `BlockDAO._block_chain` (`src/cancelchain/models.py:307-316`) — which is scoped to Y's `prev_id` ancestry. The CTE never traverses X. Pass.
3. After Y_N applies, `ChainDAO.longest()` selects Y (idx=N for Y vs N-1 for X). `ChainDAO.sync_longest_chain_blocks` (`src/cancelchain/models.py:656-743`) runs to materialize Y into `LongestChainBlockDAO`. The smart-reorg walk attempts to find a common ancestor of Y_N in the materialization: walks `Y_N.prev → Y_N-1.prev → ... → G_y.prev_id`. None of these resolve to a `BlockDAO` row that's also in the current `LongestChainBlockDAO` (which still holds X's lineage from G_x).
4. The smart-reorg falls through to the **catastrophic rebuild branch** (`src/cancelchain/models.py:714-727`): `db.session.execute(db.delete(LongestChainBlockDAO))` then bulk-inserts Y's lineage. Both ops run in the same session/transaction; atomicity is preserved at the commit boundary (covered by A5.c).
5. Post-reorg, `LongestChainBlockDAO` reflects Y. `wallet_balance` reads against Y's lineage. X's chain remains in `BlockDAO` + `ChainDAO` (no codepath deletes non-canonical blocks; A5.a's DB-state inventory). The disjoint chains coexist; Y is canonical.

**Outcome:** ACCEPTED — disjoint-ancestor reorg works correctly via the catastrophic-rebuild branch. Y becomes canonical; X is preserved as a stale chain.

**Result:** The smart-reorg's catastrophic branch is designed for exactly this case (rebuild on no-common-ancestor). The chain-correctness invariant holds: each chain's per-block CTE walks ITS OWN lineage, so the UTXO checks on Y's blocks never confuse X's spends with Y's. Value conservation on Y is independent of X's state.

**The interesting cross-link is with A7.b.** Attack j cannot be mounted *without* first succeeding at Attack b's alternate-genesis admission. The catastrophic-rebuild branch is the validation pipeline's correct response to a successful A7.b attack: once a hostile fork rooted at an alternate genesis grows longer than the canonical chain, the materialization correctly switches to it. The chain-correctness consequence — Y becomes canonical — is by design under PoW longest-chain selection; the *gap* is the alternate-genesis admission itself (A7.b), not the reorg behavior here.

**No new finding for j.** The catastrophic-rebuild branch behaves correctly. The attack is **RELATED to A7.b**: closing A7.b (rejecting alternate genesis blocks) closes A7.j's only entry path, because a chain Y can only diverge from X all the way back to genesis if Y has a genesis block of its own, which A7.b's remediation would reject.

## Cross-cutting observations

These patterns surfaced across multiple adversary traces. Each captures something the flat findings list above does not: a structural property of the pipeline that explains *why* findings cluster where they do, and that informs how future audits and remediations should be scoped.

### 1. Validation reads and "longest chain" reads are architecturally decoupled

Per-block validation (`Chain.validate_block`, `Chain.validate_block_txn`, `Chain.validate_block_coinbase`) consults `BlockDAO._block_chain` — a per-block recursive CTE on `prev_id` that walks the *candidate block's own lineage*. Read-side queries (`wallet_balance`, `wallet_leaderboard`, `unspent_outflows`, `unforgiven_outflows`) consult the materialized `LongestChainBlockDAO` table and the in-process `_is_longest` cache.

Evidenced by: Adversary 4 attack d's trace through `get_inflows_count`, Adversary 5 attack d's enumeration of all `validate_*` reads, Adversary 5 attack a's DB-state inventory, Adversary 7 attack j's catastrophic-rebuild walkthrough.

The implication is large: Phase 6.5's documented cross-worker stale-cache risk for `_is_longest` cannot escalate to validation correctness in a multi-worker deploy, because no `validate_*` code path consults the cache. The worst case is wrong reads or wrong UTXO selection during transaction construction (which the validation layer then rejects). Future audits of the read/perf layer can be scoped independently of validation; the two surfaces share only the underlying `BlockDAO` rows.

### 2. Receive-transaction admits to mempool with intrinsic checks only

`Node.receive_transaction` runs schema + signature + txid + same-txid-in-pending checks; it does NOT run chain-rule checks (no double-spend lookup, no inflow-existence lookup, no value-conservation check, no timestamp-window check, no mined-already lookup). Chain-rule violations are caught at block assembly via `Miller.create_block` (which drops failures via the `txn_failed` signal) and at admission via `Chain.validate_block_txn`.

Evidenced by: Adversary 1 attacks a, b, d, e, f, g (every one passes through pending and is caught later); Adversary 4 attack a's reduction to A1.f's frame.

This is the standard Bitcoin-style mempool model — the pending pool is a candidate queue, not authoritative state. A1.f is the most visible consequence: mined txids re-admitted to pending until 4h expiry. The architecture is intentional and well-justified (deep checks at receive would push DB load into the public submit endpoint), but it means the pool can carry "noise" that doesn't compromise the chain. A1.f's specific gap (re-admission of mined txids) is worth closing because the check is cheap and the noise is unbounded; broader mempool sanitization (rejecting at receive on every chain-rule violation) is out of scope.

### 3. InflowDAO unique constraint scopes by consuming txn, not by consumed outflow

`InflowDAO.__table_args__` carries `UniqueConstraint('txid', 'idx')` (`src/cancelchain/models.py:208`) — but `(txid, idx)` here refers to the consuming transaction's own `(txid, idx)`, NOT to `(outflow_txid, outflow_idx)`. So InflowDAO does not enforce DB-level uniqueness of outflow consumption. That's the job of `Chain.get_inflows_count`, which is lineage-scoped.

Evidenced by: Adversary 4 attack d (the cross-fork double-spend trace explains why this scoping is the correct design), Adversary 5 attacks a and b (every "double-spend across fork" attack relies on this scoping for the per-chain UTXO model to work).

The implication: a single outflow CAN be consumed once per fork without DB error; cross-fork double-spend is permitted at the validation layer and resolved by longest-chain selection. This is the canonical UTXO-on-PoW design (Bitcoin behaves identically). The architectural alternative — "globally unique outflow consumption across all forks" — would break legitimate fork-replay (Adversary 4 attack b) and require defining "recent" in a network-agnostic way. The audit confirms the current scoping is sound; surfacing it here as a design note so future reviewers don't mistake it for a missing check.

### 4. Block-assembly is the comprehensive defense, not receive-transaction

Across multiple adversaries (A1, A3, A4, A6), the architectural pattern repeats: shallow checks at receive (schema + intrinsic), deep checks at validate_block (chain context). A1.f and A4.c are both consequences of this: the offending content was admitted at the shallow tier and counted somewhere (pending pool, `block_transactions` m2m) before the deep tier had a chance to filter it.

Evidenced by: A1.f (txid re-admitted to pending), A4.c (coinbase replayed via m2m duplicate row), Adversary 3's validation-pipeline summary (the receive path's three layers), Adversary 6's three-defense backstop pattern (`unique=True` on `BlockDAO.block_hash` + `PendingTxnDAO.txid` + catch-and-recheck on SQLAlchemyError).

This is a sound multi-layer defense (it keeps the receive endpoint fast and pushes expensive checks into milling, where economic incentives align with thorough validation), but its "deep checks happen late" property is exactly what makes A1.f and A4.c possible. Both findings are remediated by pulling one deep check earlier — A1.f to receive_transaction, A4.c into `validate_block_coinbase`. Future audits scanning for similar patterns should look at every "this txn / block was already counted before the deep check fired" frame; A4.c suggests there may be more accounting-layer (read-side) replays worth probing once the read layer's own audit happens.

### 5. Difficulty retargeting is structurally tamper-resistant

Adversary 3 attack e probed timestamp manipulation past the ±4× clamp. The clamp is computed BY the validator (`Chain.block_target` at `src/cancelchain/chain.py:109-138`), not BY the miller — so the miller cannot push it past its bounds regardless of what they put in the timestamp. The `MAX_TARGET` cap (`src/cancelchain/chain.py:43`) also blocks the miller from claiming an arbitrarily easy difficulty.

Evidenced by: Adversary 3 attack e (timestamp manipulation), attack g (wrong-difficulty block), the difficulty-retarget code at `Chain.block_target`. Zero findings on Adversary 3.

This is a sound design pattern worth recording. The validator computes the consensus-critical value from chain state; the miller's block-level inputs (their claimed timestamp, their claimed target) are checked against the validator's computation. Any future change to difficulty retargeting (e.g., a different retarget interval or a different clamp ratio) should preserve this property: derive the canonical value from chain state in the validator and reject any miller-supplied value that disagrees.

### 6. Boundary-condition sweep found conceptual gaps, not off-by-ones

Adversary 7 explicitly probed every documented numeric boundary in the validation pipeline: subject length (1-79 inclusive), MAX_TRANSACTIONS (1-100 inclusive), PoW hash strictly less than target. All three are correctly enforced; no off-by-ones surfaced.

Evidenced by: Adversary 7 attacks a, c, d, g — all "no finding" via inclusive/exclusive boundary matching the documented intent.

The three Low findings from Adversary 7 (A7.b, A7.e, A7.h) are all **conceptual gaps** — missing checks rather than incorrect ones. A7.b: no canonical-genesis check exists. A7.e: three different operators used at three sites (no single site is wrong, but the inconsistency is a refactor hazard). A7.h: a check that exists (length) and a check that doesn't exist (character class). Future boundary-sweep audits should pair "test every documented boundary" with "for each invariant the docs claim, prove the check that enforces it exists" — A7.h would have surfaced earlier under that frame.

### 7. The validate-then-persist ordering is consistently observed at the single-block level — and consistently broken at the multi-block level

Adversary 2 attack f explicitly probed for early-write leaks in single-block receive. None found: every chain-context check in `Chain.validate_block` is read-only against the DB, and `block.to_db()` only fires after `validate_block` returns successfully (`Chain.add_block` at `src/cancelchain/chain.py:153-156`). The `SQLAlchemyError` catch at `src/cancelchain/node.py:189-193` provides per-block atomicity.

Evidenced by: A2.f (no finding, clean ordering), Adversary 3's persistence trace, Adversary 6's three-defense backstop.

A2.e is the multi-block analog: `Node.fill_chain`'s apply loop validates and commits each block individually with no enclosing transaction. The single-block invariant (validate-then-persist) holds, but the multi-block aggregate doesn't — an invalid block N can leave blocks 1..N-1 persisted. This is a structural mismatch between two layers: the per-block layer guarantees per-block atomicity, but the multi-block syncing layer doesn't extend that guarantee to the batch. A2.e's remediation (wrap in `db.session.begin_nested()`, or do a two-phase validate-then-persist) restores the symmetry. Any future "operation that applies N blocks atomically" should explicitly state which layer's atomicity boundary it inherits.

## Recommendations

The six findings are ordered below by remediation tractability and blast-radius alignment, not strictly by severity. Each item below maps cleanly to a single small PR; the cross-finding effects (e.g., A7.b closing A7.j) are noted in-line. A standalone follow-up PR is suggested for each item rather than batching, because per-finding test coverage already exists in `tests/test_verification_audit.py` and converting each xfail to a passing test is the cleanest acceptance signal.

### 1. A2.e (Medium) — atomic apply loop in `Node.fill_chain`

The fix lives at `src/cancelchain/node.py:345-351`. Two implementation paths are viable: wrap the apply loop in `db.session.begin_nested()` and roll back inside the existing `except Exception` handler at line 353, or refactor to a two-phase validate-then-persist (one read-only pass calling `Chain.validate_block` against an in-memory `Chain` instance with no `block.to_db()`, then a second pass that persists each block only if all passed validation). The second path is more compatible with SQLite's lock model and maps onto Bitcoin Core's `headers-first → blocks-batched` pattern, at the cost of one extra validation walk; pick based on perf measurement of `cancelchain sync` against realistic peers. Blast radius: closes the partial-chain-adoption attack from any hostile peer in `CC_PEERS`; reduces operator exposure during transient peer connectivity. Acceptance signal: `test_a2_e_partial_chain_adoption_via_invalid_tip` flips from xfail to pass.

### 2. A4.c (Medium) — coinbase-uniqueness check in `Chain.validate_block_coinbase`

The fix lives at `src/cancelchain/chain.py:278-285`. Add a `self.get_transaction(cb.txid, start_block=block)` lookup; if non-None (and the matched txn isn't this candidate block's own coinbase — i.e., the matched txn's `block_transactions` m2m doesn't include `block`), raise a new `DuplicateCoinbaseError(InvalidCoinbaseError)` defined in `src/cancelchain/exceptions.py`. The lookup is lineage-scoped (it walks the candidate's ancestry, not the global `TransactionDAO`), so it correctly preserves legitimate cross-fork coinbase replay (the A4.b case). Blast radius: closes the wallet-balance inflation surface for any MILLER-role adversary; restores the no-double-counting invariant for coinbase outflows. Acceptance signal: `test_a4_c_ii_coinbase_replay_inflates_balance` flips from xfail to pass.

### 3. A7.b (Low) — canonical-genesis check in `Chain.validate_block`

The fix lives at `src/cancelchain/chain.py:170-198`. After the `is_genesis_block(block)` branch passes the existing parent / idx / target checks, query `BlockDAO` for any existing block with `idx == 0` and `prev_hash == GENESIS_HASH` whose `block_hash` differs from the candidate; raise a new `DuplicateGenesisError(InvalidBlockError)` if found. Blast radius: closes the chain-registry fragmentation surface from any MILLER-role adversary AND closes A7.j's only entry path (disjoint-ancestor reorg can only occur if an alternate genesis was first admitted). Acceptance signal: `test_a7_b_alternate_genesis_fragments_chain_registry` flips from xfail to pass. Two-for-one fix.

### 4. A7.h (Low) — content-class check in `validate_raw_subject`

The fix lives at `src/cancelchain/payload.py:49-55` (with symmetric application at lines 39-46 after `decode_subject`). After the existing length check, reject any `raw_subject` whose codepoints include Unicode general categories `Cc` (control), `Cf` (format — bidi + zero-width), `Cn` (unassigned), `Cs` (surrogates) via `unicodedata.category(c)`. The audit's design intent originally implied human-readable subjects; the audit just surfaced that no check existed to enforce it. Blast radius: closes the rendering-spoof surface for any consumer of `OutflowDAO.subject` (`BalanceView`, CLI `subject` commands, `wallet_leaderboard`). Acceptance signal: `test_a7_h_non_printable_subject_accepted` flips from xfail to pass. Note: while the audit's Non-goals deferred "spec changes to validation rules", A7.h is arguably already in the spec's intent — the audit just confirms the check doesn't exist.

### 5. A7.e (Low) — pick one `TXN_TIMEOUT` comparison operator

The fix lives at three sites: `src/cancelchain/block.py:269` (currently `<`), `src/cancelchain/miller.py:74` (currently `>`), `src/cancelchain/node.py:105` (currently `<=`). Pick one canonical comparison and apply consistently. Recommended: open boundary (`<` for "expired") — change `discard_expired_pending_txns` line 105 from `<=` to `<`, change `Miller.pending_chain_txns` line 74 from `>` to `>=`. After the change, all three sites agree that `txn_ts == now - TXN_TIMEOUT` is "alive". Add a docstring on `TXN_TIMEOUT` clarifying the open/closed semantics. Blast radius: pure refactor; no observable behavior change for txns that aren't exactly at the boundary. Defensive against the refactor risk where a future change to one site silently desynchronizes the other two. Acceptance signal: `test_a7_e_txn_timeout_boundary_inconsistency` flips from xfail to pass.

### 6. A1.f (Low) — mined-txid check in `Node.receive_transaction`

The fix lives at `src/cancelchain/node.py:76-96`. Before `self.pending_txns.add(txn)` at line 92, look up `TransactionDAO.get(txn.txid)` (or equivalently `Chain.get_transaction` against the longest chain) and raise a new `DuplicateMinedTransactionError(InvalidTransactionError)` defined in `src/cancelchain/exceptions.py` when the lookup returns a hit. The check belongs on the receive path (not at block-assembly time, where `Miller.pending_chain_txns` already filters mined txids implicitly) so that the rejection is observable to the submitter as a 400 response and never enters the pool. Blast radius: closes the pending-pool inflation DoS surface; per-receive cost is one indexed lookup on `TransactionDAO.txid`. Acceptance signal: `test_a1_f_mined_txid_replay_into_pending` flips from xfail to pass.

### Out of scope — operator guidance, not validation

**Reorg double-spend cluster (A4.d note + A5.a + A5.b).** Across three traces the audit confirmed: a hostile fork that overtakes the canonical chain can rewrite history including outflow consumption, regardless of what `validate_*` does. This is the canonical Proof-of-Work property; Bitcoin and every UTXO chain inherit the same shape. No validation-pipeline change can reject the attack without breaking PoW longest-chain selection (which would in turn break legitimate reorgs).

Recommendation: document explicit confirmation-depth guidance in user-facing docs — something like "For transactions exchanging value off-chain, recipients should wait N confirmations before treating the payment as settled, where N reflects the operator's risk tolerance and the network's observed orphan rate." This belongs in `README.md` / operator docs, not in `validate_*` code. The chain layer's job ends with "the canonical chain is consistent under its own UTXO rules"; the off-chain settlement guarantee is an application-layer policy.

**Orphan `ChainFill` row sweep (A5.c, no-finding note).** A process kill mid-`fill_chain` leaves `ChainFill` and `ChainFillBlock` rows that no codepath cleans up. The validation-correctness impact is zero (the staging tables are not consulted by validation or longest-chain selection), and the DB-bloat consequence requires external process-kill capability that this audit's threat model doesn't grant. A one-line startup sweep (`DELETE FROM chain_fill` during app init) would close the operational hygiene gap; if the cancelchain operator policy targets long-running deployments, consider a follow-up PR. Not blocking.

### Closed by design

**A3.c (subject censorship).** A malicious miller refusing to include txns matching a pattern (e.g., subject == "their pet peeve") is not a validation-pipeline gap — the chain doesn't enforce inclusion fairness by design. Mitigation is "submit your txn to multiple millers"; the protocol cannot force any single miller to honor any single txn. No remediation appropriate.

**`_is_longest` cross-worker stale cache (A5.d).** Phase 6.5's documented risk. Validation paths do not consult the cache (Cross-cutting observation 1), so the cross-worker stale-cache cannot escalate to validation correctness. Read-layer mitigation belongs to a separate read-layer audit, not this one.
