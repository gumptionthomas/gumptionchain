# EGU #3 / #176a — node transaction provenance lookup — design

**Date:** 2026-06-06
**Status:** Approved — ready for implementation
**Issue:** #182 (first slice of the verifiable stake card #176, EGU #3)
**Type:** New node read endpoint + domain method (Python) — additive; no schema
change, no consensus change.

## Summary

Add a read API to verify a stake's **on-chain provenance** by txid:
`GET /api/transaction/<txid>` returns who staked what on which subject, whether
the transaction is in the canonical chain, and how deeply confirmed. This is the
missing primitive the verifiable stake card (#176b) needs — today the
`/transaction/<txid>` route is **POST-only** (receive); there is no
transaction-by-id read.

Purely additive: a new READER-authed GET view plus a domain method composing the
**existing** lookups (`TransactionDAO.get`, `ChainDAO.get_transaction` /
materialized `LongestChainBlockDAO` ancestry, `PendingTxnDAO.get`). No new
tables, no migration, no change to validation/consensus.

## Endpoint

`GET /api/transaction/<mill_hash:txid>` — **READER** role
(`authorize_reader`; READER honors the `"*"` wildcard, so an operator can make
verification public). Reuses the existing `mill_hash` URL converter. The POST
receive route on the same path is unchanged and stays transactor-authed — GET
and POST bind different views to the same rule (the established `/block` pattern:
reader GET, miller POST).

### Response (200, found)

Amounts in **grains** (raw on-chain integer, matching the balance endpoints; GRIT
conversion is a display concern for the consumer).

```json
{
  "txid": "…",
  "address": "GC…GC",
  "outflows": [
    { "kind": "opposition", "subject": "goblins", "amount": 300 },
    { "kind": "support",    "subject": "…",       "amount": 100 },
    { "kind": "rescind",    "subject": "…", "rescind_kind": "opposition", "amount": 100 },
    { "kind": "transfer",   "address": "GC…GC",   "amount": 500 }
  ],
  "timestamp": "2026-06-06T12:00:00+00:00",
  "status": "canonical",
  "block_hash": "…",
  "height": 1234,
  "confirmations": 5,
  "as_of_block": "<tip hash>"
}
```

**Field rules:**
- **`outflows[].kind`** is derived from which single field the outflow sets:
  `opposition` / `support` / `rescind` (with `rescind_kind`) → `subject`;
  `address` → `kind: "transfer"` with `address`. `amount` is grains.
  Order preserved (`OutflowDAO.idx`).
- **`status`** ∈ `canonical` | `orphaned` | `pending`, resolved in that
  precedence:
  - **canonical** — `ChainDAO.get_transaction(txid)` finds it in the longest
    chain. `block_hash`/`height` are its canonical block; `confirmations =
    tip_height − height + 1` (the containing block counts as 1).
  - **orphaned** — `TransactionDAO.get(txid)` exists but is not in the canonical
    chain (mined only in a non-canonical fork). `block_hash` = a containing
    (non-canonical) block; `height`/`confirmations` are `null`/`0`.
  - **pending** — not a mined `TransactionDAO`, but `PendingTxnDAO.get(txid)`
    exists; provenance (`address`, `outflows`, `timestamp`) is read from the
    pending record's stored `json_data` (`Transaction.from_json`).
    `block_hash`/`height` `null`, `confirmations` `0`.
- **`as_of_block`** = the current tip hash. Provenance — chiefly
  `confirmations` and `status` — is relative to the tip; this echoes the balance
  endpoints and is the cache key. `null` only if the chain is empty (pending-only
  results).

### Response (404)

Unknown txid (not canonical, not orphaned, not pending) → `404` via the standard
error response.

## Domain method

`ChainDAO.transaction_provenance(txid) -> ProvenanceResult | None`, wrapped by
`Chain.transaction_provenance` (mirroring how `balance` wraps
`wallet_balance`). It returns a small typed result (a dataclass/`TypedDict` with
`address`, `outflows`, `timestamp`, `status`, `block_hash`, `height`,
`confirmations`) or `None` for 404. Steps:

1. **Canonical:** `txn = self.get_transaction(txid)` (longest-chain membership
   via `block.get_transaction_in_chain`, the materialized ancestry). If found,
   locate its canonical block by joining `TransactionDAO.blocks` →
   `LongestChainBlockDAO` → `BlockDAO` (one row; `block_hash`, `height =
   block.idx`). `tip_height` = the ChainDAO tip block's `idx`. `confirmations =
   tip_height − height + 1`.
2. **Orphaned:** else `txn = TransactionDAO.get(txid)`. If found, status
   `orphaned`; `block_hash` from any one of its `blocks`; no height/confirmations.
3. **Pending:** else `pending = PendingTxnDAO.get(txid)`. If found, parse
   `pending.json_data` into a `Transaction`; status `pending`.
4. Else `None`.

Outflow → `{kind, subject|address, amount, rescind_kind?}` mapping is shared by
all three sources (a small pure helper over an `Outflow`/`OutflowDAO`).

**Caching:** the view caches under `{tip_hash}.{txid}.txn-provenance` (same
pattern as balances), so `confirmations`/`status` recompute when the tip moves;
a short-lived stale read of a fast-moving `confirmations` is acceptable.

## Testing

- **API (Flask test client, existing fixtures):**
  - **canonical** txn → correct `address`, `outflows` (each kind mapped, grains),
    `status: canonical`, right `block_hash`/`height`/`confirmations`, `as_of_block`
    = tip.
  - **confirmations** increase as more blocks are mined on top.
  - **orphaned** txn (mined in a non-canonical fork block) → `status: orphaned`,
    `null` height/confirmations.
  - **pending** txn (in the mempool, unmined) → `status: pending`, outflows from
    `json_data`, `null` block.
  - **unknown** txid → `404`.
  - **all four outflow kinds** (opposition / support / rescind+rescind_kind /
    transfer) map correctly.
  - **auth:** no/insufficient signature → 401/403; READER (or `"*"`) → 200.
- **Domain:** a `ChainDAO.transaction_provenance` unit test for the
  canonical/orphaned/pending/none branches + confirmations math (incl. a single
  canonical block → `confirmations == 1`).
- Full `uv run pytest` green; `ruff`/`mypy` clean on new code.

## Out of scope

- The composed verifier + signed stake-attestation convention (#176b).
- The `gumption.com/verify` page + Bluesky OG unfurl (#176c / EGU #5).
- Other explorer endpoints (block-by-height, listings, pagination), inflow
  detail in the response, GRIT formatting.
- Any consensus/validation/schema change.

## Decisions log

- **GET on the existing `/transaction/<txid>` path, reader-authed** — mirrors the
  `/block` GET-reader / POST-miller split; no new path shape; READER `"*"` makes
  public verification possible.
- **Provenance view + confirmations** (Q1) — `{address, outflows(kind/subject/
  amount), status, block_hash, height, confirmations, as_of_block}`; not a full
  transaction echo (smaller surface, fit for purpose).
- **Surface `pending` and `orphaned`, 404 only for truly unknown** (Q2) — a
  shared stake card can report "submitted, not yet settled" or "on an orphaned
  fork", not just canonical-or-missing.
- **`confirmations = tip_height − height + 1`** — the containing block is one
  confirmation; relative to the current tip (`as_of_block`).
- **Grains, not GRIT** — matches the balance endpoints; display conversion is the
  consumer's job.
- **Compose existing DAOs** — `TransactionDAO.get`, `ChainDAO.get_transaction`
  (materialized ancestry), `PendingTxnDAO.json_data`; no new tables, no
  consensus touch.

## Definition of done

- `Chain.transaction_provenance` + `ChainDAO.transaction_provenance` (canonical/
  orphaned/pending/none + confirmations) and the shared outflow-mapping helper.
- `GET /api/transaction/<mill_hash:txid>` reader-authed view returning the
  documented JSON, 404 on unknown, cached under the tip.
- API + domain tests (all branches, all outflow kinds, confirmations, auth)
  pass; full `uv run pytest` green; `ruff`/`mypy` clean.
- No schema/migration, no consensus/validation change. Part of #176.
