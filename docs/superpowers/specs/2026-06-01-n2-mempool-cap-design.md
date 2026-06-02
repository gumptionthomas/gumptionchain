# N2 Remediation — Mempool Admission Cap — Design

**Status:** Draft for review
**Date:** 2026-06-01
**Remediates:** Audit finding **N2 (Medium)** from the [P2P/networking audit](../audits/2026-06-01-network-p2p-audit.md): the `pending_txns` mempool has no admission cap, so an authenticated TRANSACTOR can flood unbounded distinct valid txns (admission validation is shape+signature+txid only — no balance/UTXO check — so one key mints unlimited distinct admitted txids by varying timestamp/outflows). The only eviction is the 4h `TXN_TIMEOUT`, so the pool grows without bound within any 4h window.

## Problem

`Node.receive_transaction` (`node.py:79-105`) admits a transaction whenever it is structurally valid, not already mined, and not already pending — with **no check on the pool's size**:

```python
if txn not in self.pending_txns:
    try:
        self.pending_txns.add(txn)
    ...
```

`txn.validate()` checks shape + signature + txid only (`transaction.py:224-233`); chain/UTXO/balance/double-spend validation runs later, at mill time (`miller.py:89-91`). So an authorized TRANSACTOR can cheaply mint unlimited *distinct* valid txids (fresh timestamp/outflows) that each pass `txn not in self.pending_txns` and the `DuplicateMinedTransactionError` check, each committing one `PendingTxnDAO` row (a full `json_data` Text blob) plus per-inflow `PendingIOflowDAO` rows. Grep confirms no `MAX_PENDING`/count ceiling exists anywhere; the only drain is the 4h `TXN_TIMEOUT` expiry.

A secondary amplifier — every `discard_expired_pending_txns`, mill attempt, and `/transaction/pending` read iterates the full pool via `PendingTxnSet.__iter__`, which re-parses every row with `Transaction.from_json` — is **out of scope here** (see below); the admission cap bounds the pool, turning that O(mempool) read cost into O(cap).

## Goal

Cap mempool admission at a configurable `MAX_PENDING_TXNS`, rejecting new txns (retryably) when the pool is full, and flip the N2 demonstration test (`tests/test_network_audit.py::test_n2_mempool_has_no_admission_cap`) from strict-xfail to a passing regression.

## Approach

A size check in the one admission path (`receive_transaction`), reject-when-full via a new retryable error mapped to HTTP 503.

### Component: config field (`src/cancelchain/config.py`)

Add to `EnvAppSettings`, alongside the other numeric settings:

```python
MAX_PENDING_TXNS: int = field(default=10000)
```

Env var `CC_MAX_PENDING_TXNS`; the `CC_` prefix is stripped, so the `app.config` key is `MAX_PENDING_TXNS`. Default **10,000** — ~100× a `MAX_TRANSACTIONS = 100` block, a generous legit backlog that still bounds the flood. No deployment exists yet, so the default is a theoretical ceiling.

### Component: `MempoolFullError` (`src/cancelchain/exceptions.py`)

```python
class MempoolFullError(CCError):
    pass
```

A direct `CCError` subclass — **not** `InvalidTransactionError`. The transaction is valid; the node is temporarily at capacity. This distinction drives the 503 (vs 400) response.

### Component: `receive_transaction` (`src/cancelchain/node.py`)

Add the cap check inside the existing `if txn not in self.pending_txns:` block, before `add`:

```python
if txn not in self.pending_txns:
    if len(self.pending_txns) >= current_app.config['MAX_PENDING_TXNS']:
        raise MempoolFullError()
    try:
        self.pending_txns.add(txn)
    except SQLAlchemyError:
        rollback_session()
        if txn not in self.pending_txns:
            raise
    added = True
```

`len(self.pending_txns)` resolves to `PendingTxnDAO.count()` (`transaction.py:392-393`) — a cheap SQL `COUNT`, not a full materialization. Placing the check **inside** the `not in pending` guard means a re-receipt of an already-pending txn (the N3 path) is never rejected by the cap — only genuinely-new admissions count against it. `current_app` is already imported in `node.py` (added in the N1 remediation). The check is `>=`, so the pool holds at most `MAX_PENDING_TXNS` rows.

The raise propagates out of `receive_transaction` to its callers (the API view, gossip, and the Miller's pull loop). Gossip/pull callers already wrap `receive_transaction` in broad `except` handlers that log; the API view maps it explicitly (below).

### Component: `TxnView.post` (`src/cancelchain/api.py`)

The view currently catches `CCError` and returns a 400 via `make_error_response`. Add an explicit `MempoolFullError` catch **before** the `CCError` catch, returning a retryable 503:

```python
        except MempoolFullError:
            return make_json_response({'error': 'mempool full'}, 503)
        except CCError as err:
            return make_error_response(err)
```

503 (Service Unavailable) is the correct semantic: the submission is well-formed and authorized, but the node is temporarily full — the client should retry later. Raising rather than silently dropping keeps the rejection visible to the submitter (no silent failure). Because `MempoolFullError` is a `CCError`, the explicit catch must precede the generic `CCError` catch.

## Error handling

One new error path: `MempoolFullError → 503` in `TxnView.post`. The gossip path (`Node.send_transaction` callers) and the Miller pull loop (`pending_txns_gen`) already funnel `receive_transaction` exceptions through their existing `except Exception` logging, so a full pool there is logged and skipped (the peer simply isn't admitted) — acceptable. No change to the async post-process path (it operates on an already-admitted txn). The `finally`/rollback structure of `receive_transaction` is unchanged; the cap check raises before any DB write, so there is nothing to roll back.

## Out of scope (deferred)

- **The O(mempool)/O(cap) read-path optimization.** `PendingTxnSet.__iter__` re-parses every pending row via `Transaction.from_json` on every expiry sweep, mill attempt, and `/transaction/pending` GET. The admission cap bounds the pool, so this read cost becomes **O(cap)** rather than unbounded — the security/availability core of N2 is closed. Converting `discard_expired_pending_txns` (and `PendingTxnView`) to an indexed/SQL-filtered query is a now-bounded **performance** optimization, tracked as a separate roadmap item, not a security finding.
- **Per-sender fairness.** A global cap means one flooding TRANSACTOR can fill the pool and cause honest submitters' txns to be 503'd until the pool drains. On a permissioned chain (curated TRANSACTOR allowlist, operator can revoke a flooder) the global cap is the right-sized defense; per-address accounting is deferred.
- **Eviction.** Reject-when-full was chosen over evict-oldest (simpler, no cascade-delete of `PendingIOflowDAO` companions, no churn of honest txns).

## Testing

### Flip the demonstration (strict-xfail → passing regression)

`tests/test_network_audit.py::test_n2_mempool_has_no_admission_cap`: remove the `@pytest.mark.xfail(strict=True)` marker, and wrap each of the 6 submissions in `try/except MempoolFullError: pass`. The body already sets `app.config['MAX_PENDING_TXNS'] = 3`. Today (no cap) nothing raises, all 6 admit, `len == 6`, `6 <= 3` is false — so the test still demonstrates the gap under the marker. After the fix, 3 admit and submissions 4–6 raise `MempoolFullError` (caught), leaving `len == 3`, so `len(m.pending_txns) <= 3` passes. Past-tense the docstring.

### New regression — 503 at the API layer

Add `tests/test_network_audit.py::test_n2_full_mempool_returns_503`: with `app.config['MAX_PENDING_TXNS']` set low (e.g. 1), submit valid txns through `requests_proxy` as a TRANSACTOR until full, then POST one more valid txn and assert the response status is **503**. Guards the view-layer mapping.

### Regression suite

Full suite stays green. After this change: `tests/test_network_audit.py` shows **2 xfailed** (N3/N4 still open) **+ 5 passed** (the three N1 regressions + the flipped N2 cap test + the new N2 503 API test); `--runxfail tests/test_network_audit.py` fails only N3/N4. All five CI gates green; `mypy --strict` accepts the new int config field and exception.

## Documentation updates

- **Audit report** (`docs/superpowers/audits/2026-06-01-network-p2p-audit.md`): mark **N2** remediated (✅ on the finding, table row Status, recommendation item 2; past-tense the gap; `(As implemented: …)` note covering the cap + 503 + the deferred read-path). Update headline **0 Critical / 0 High / 2 Medium / 1 Low → 0 Critical / 0 High / 1 Medium / 1 Low**. Note in the N2 trace and cross-cutting observation 3 that the read-amplification is now O(cap) and the indexed-expiry is deferred.
- **CLAUDE.md**: in the configuration section's `CC_*` list, add `MAX_PENDING_TXNS` (env `CC_MAX_PENDING_TXNS`, default 10000 — caps mempool admission; a full pool returns 503).
- **Roadmap** (`docs/superpowers/ROADMAP.md`): mark the N2 bullet ✅ with the impl PR number; add a new follow-up entry for the deferred indexed/SQL-filtered mempool expiry/read optimization (now bounded by the cap).

## Acceptance criteria

- `receive_transaction` raises `MempoolFullError` when a new txn would exceed `MAX_PENDING_TXNS`; re-receipts of already-pending txns are unaffected; the cap is `>=` so the pool holds at most `MAX_PENDING_TXNS`.
- `TxnView.post` returns 503 (not 400) for a full mempool.
- `MAX_PENDING_TXNS` is a config field (env `CC_MAX_PENDING_TXNS`, default 10000).
- `test_n2_mempool_has_no_admission_cap` passes with its xfail marker removed; the new `test_n2_full_mempool_returns_503` passes; full suite green (`tests/test_network_audit.py`: 2 xfailed + 5 passed).
- Audit report headline `0 Critical / 0 High / 1 Medium / 1 Low`; N2 ✅; CLAUDE.md + roadmap updated (including the deferred read-path perf item).
