# N3 Remediation — Gate Transaction Re-Gossip on Newly-Added — Design

**Status:** Draft for review
**Date:** 2026-06-02
**Remediates:** Audit finding **N3 (Medium)** from the [P2P/networking audit](../audits/2026-06-01-network-p2p-audit.md): an already-pending transaction is re-gossiped on every receipt. `Node.receive_transaction` calls `send_transaction` unconditionally whenever `process=True`, even when the txn was already in the pool — so a peer that repeatedly POSTs the same already-pending txn forces the victim to fan out to all N peers on every duplicate receipt, carrying zero new state (1→N amplification). The block path does not have this gap: it gossips only newly-persisted blocks.

## Problem

`Node.receive_transaction` (`src/cancelchain/node.py`) ends with:

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
        if process:
            self.send_transaction(txn, visited_hosts=visited_hosts)
        return txn if added else None
```

The `added` flag correctly tracks whether the txn was newly admitted, and the return value already uses it (`txn if added else None`). But the gossip — `if process: self.send_transaction(...)` — runs **unconditionally**, regardless of `added`. So a re-receipt of an already-pending txn (`added=False`) still fans out to every non-visited peer (`send_transaction` loops `self.peers`). In the default synchronous config (`process = not API_ASYNC_PROCESSING = True`), each duplicate POST of an already-pending txn makes the node emit up to N outbound peer POSTs carrying no new state; the synchronous handler blocks a worker doing those N sequential posts.

The sibling **block path is the correct precedent**: `process_block` calls `send_block` only inside `if block := self.add_block(block):` — i.e. only when the block was newly persisted — and `receive_block`/`process_block` early-return for a known block before gossiping. The `visited_hosts`/`Peer-Hosts` loop-guard is attacker-controllable (empty/omitted header) and only prevents looping back to nodes already in the path; it does not couple pool-membership to the gossip decision.

## Goal

Gossip a transaction only on the receipt that newly admits it to the pool, mirroring the block path. Flip the N3 demonstration test to a passing regression, and guard against over-gating (a genuinely-new txn must still gossip exactly once).

## Approach

A one-line gate: condition the gossip on `added`.

### Component: `receive_transaction` (`src/cancelchain/node.py`)

Change the gossip line from:

```python
        if process:
            self.send_transaction(txn, visited_hosts=visited_hosts)
```

to:

```python
        if process and added:
            self.send_transaction(txn, visited_hosts=visited_hosts)
```

A re-receipt of an already-pending txn (`added=False`) no longer gossips — exactly mirroring `process_block`'s "gossip only what `add_block` newly persisted." A genuinely-new txn (`added=True`) still gossips exactly once, on its first receipt (first propagation). Nothing else in `receive_transaction` changes.

### Why this is complete (no other changes needed)

- The **return value** (`txn if added else None`) is already correct and unchanged.
- The **async path** is already gated: `TxnView.post` fires `queue_txn_post_process` only `if process is False and txn is not None` — and `txn` is `None` for a re-receipt (`added=False`), so the async post-process path never re-processes an already-pending txn. Only the *synchronous* `send_transaction` re-gossiped unconditionally; this change fixes exactly that.
- **Distinct-txn flooding** (many *different* txns, each legitimately gossiped once) is a separate concern already bounded by the N2 mempool admission cap. N3 is specifically the *same*-txn re-gossip amplification, which the gate closes.
- The audit's *optional* API-layer rate/idempotency guard is **not** included (YAGNI): once re-receipts don't fan out, there is no 1→N amplification left for a rate guard to add value over, and N2 already caps the pool.

## Error handling

No new error paths. The change only removes a redundant gossip call on the `added=False` branch; all existing exception handling in `receive_transaction` and its callers is unchanged.

## Testing

### Flip the demonstration (strict-xfail → passing regression)

`tests/test_network_audit.py::test_n3_pending_txn_regossiped_on_every_receipt`: remove the `@pytest.mark.xfail(strict=True)` marker. The body already does a first receipt (admits + gossips, before the spy is wired), installs a `SpyClient` recording `post_transaction` calls, then does a SECOND receipt of the same already-pending txn and asserts `calls == []`. After the fix, the second receipt has `added=False`, so `send_transaction` is not called → `calls == []` → passes. Past-tense the docstring.

### New positive guard — a new txn still gossips once

Add `tests/test_network_audit.py::test_n3_new_txn_gossips_once`: build a fresh valid signed txn, wire the `SpyClient` **before** the first receipt, call `receive_transaction(t.txid, t.to_json())` once, and assert `calls == [t.txid]` — the first receipt (`added=True`) gossips exactly once. This guards against the gate being too aggressive and silently killing legitimate first-propagation. (Mirror the existing N3 test's `SpyClient`/`m.peers`/`m.clients` wiring and `time_machine` setup.)

### Regression suite

Full suite stays green. After this change: `tests/test_network_audit.py` shows **1 xfailed** (N4 still open) **+ 7 passed** (3 N1 + 2 N2 + the flipped N3 test + the new positive guard); `--runxfail tests/test_network_audit.py` fails only N4. All five CI gates green; `mypy --strict` clean (no signature changes).

## Documentation updates

- **Audit report** (`docs/superpowers/audits/2026-06-01-network-p2p-audit.md`): mark **N3** remediated (✅ on the finding, table row Status, recommendation item 3; past-tense the gap; `(As implemented: …)` note). Update headline **0 Critical / 0 High / 1 Medium / 1 Low → 0 Critical / 0 High / 0 Medium / 1 Low**. Update cross-cutting observation 4 (block-path vs txn-path asymmetry) — the txn path now matches the block path's dedup-before-gossip; only the (deferred) fill_chain/fill_peer note and N4 remain.
- **CLAUDE.md**: in the `Node`/networking section's gossip description, note that `receive_transaction` gossips a txn only on the receipt that newly admits it to the pool (mirroring `process_block`/`send_block`), so an already-pending txn is not re-gossiped.
- **Roadmap** (`docs/superpowers/ROADMAP.md`): mark the N3 bullet ✅ with the impl PR number; update the section intro (N1–N3 closed; N4 the last).

## Out of scope

- N4 (synchronous broker publish on the request thread) — the final remediation.
- The deferred indexed/SQL-filtered mempool expiry perf follow-up (from N2).
- Any change to the `visited_hosts`/`Peer-Hosts` loop-guard (it holds; the audit confirmed it).

## Acceptance criteria

- `receive_transaction` gossips (`send_transaction`) only when the txn was newly added (`added=True`); a re-receipt of an already-pending txn does not gossip.
- A genuinely-new txn still gossips exactly once on first receipt.
- `test_n3_pending_txn_regossiped_on_every_receipt` passes with its xfail marker removed; the new `test_n3_new_txn_gossips_once` passes; full suite green (`tests/test_network_audit.py`: 1 xfailed + 7 passed).
- Audit report headline `0 Critical / 0 High / 0 Medium / 1 Low`; N3 ✅; CLAUDE.md + roadmap updated.
