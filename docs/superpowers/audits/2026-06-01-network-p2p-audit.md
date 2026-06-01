# P2P / Networking Threat-Modeled Audit

**Date:** 2026-06-01
**Scope:** Node/Miller orchestration + peer-facing API views. Trusted boundaries: the `validate_*` verification pipeline and the `cc-sig-v1` / `authorize()` auth layer. See [design](../specs/2026-06-01-network-p2p-audit-design.md).
**Method:** Three-phase multi-agent fan-out — one analyst per of six adversary categories (discover), an adversarial refuter per candidate (verify), then synthesis. 23 candidate attacks were traced; 7 survived refutation; deduped to 4 findings.

## Executive summary

**0 Critical / 0 High → 1 High / 2 Medium / 1 Low.** The trusted-input boundary holds: every confirmed finding is an **availability / resource-bounding gap on an otherwise-validated, otherwise-authorized path** — "valid, authenticated peer input in hostile volume, depth, or pattern," exactly the in-scope case. None reduces to a verification-validity or authorization break. The recurring root is a **missing-bound pattern**: the node has no depth cap on `fill_chain`'s ancestor walk, no size cap on the `pending_txns` mempool, and no idempotency/rate guard on duplicate transaction gossip (grep confirms no `MAX_DEPTH` / `MAX_FILL` / `MAX_PENDING` / rate-limit constants exist anywhere). A secondary theme: **validation is deferred past the point where resources are already committed** — `request_block` stages an ancestor without checking the returned block's hash matches what was requested, and mempool admission defers chain/UTXO checks to mill time, so an attacker pays ~zero to make the victim commit unbounded staging/pending rows.

| ID | Adversary | Severity | Description | Status | Demonstration test |
|---|---|---|---|---|---|
| N1 | Resource-exhaustion / eclipse / framing (merged) | **High** | `fill_chain` stages an attacker-controlled, uncapped chain of ancestor blocks — one HTTP round-trip + one committed staging row per ancestor, no depth bound, never terminates | ⏳ open (xfail) | `test_network_audit.py::test_n1_fill_chain_has_no_depth_cap` |
| N2 | Resource-exhaustion / framing (merged) | Medium | `pending_txns` mempool has no admission cap; floods of distinct valid txns grow unbounded and every expiry/mill/pending scan re-materializes the whole pool | ⏳ open (xfail) | `test_network_audit.py::test_n2_mempool_has_no_admission_cap` |
| N3 | Gossip-loop / amplification | Medium | Already-pending txn is re-gossiped on every receipt: 1 duplicate POST forces N outbound peer POSTs (the block path has the dedup guard the txn path lacks) | ⏳ open (xfail) | `test_network_audit.py::test_n3_pending_txn_regossiped_on_every_receipt` |
| N4 | Async post-process path | Low | Celery broker publish runs synchronously on the web-request thread; an unreachable/slow broker stalls every block/txn POST (bounded by Celery defaults) | ⏳ open (xfail) | `test_network_audit.py::test_n4_async_publish_blocks_request_thread` |

## Adversary traces

### 1. Resource-exhaustion peer

The dominant category. Two surviving findings, both "no cap on an attacker-influenced accumulator."

- **N1 (High) — `fill_chain` unbounded ancestor walk.** A peer the victim *pulls from* (a `CC_PEERS` sync target, or a Miller's `milling_peer` via `poll_latest_blocks`, which `mill_block` invokes automatically every poll cycle) answers `get_block(latest)` with a structurally-valid tip whose `prev_hash` is unknown to the victim. `fill_chain` (`node.py:343-361`) enters `while True` and calls `request_block(prev_hash)` per ancestor. `request_block` (`node.py:221-236`) returns `Block.from_json(r.text)` for any 200 **without** verifying the returned block's hash equals the requested `prev_hash` and **without** calling `block.validate()` — only structural Pydantic validation runs during the walk. The attacker answers every request with a fresh structurally-valid block whose `prev_hash` again never resolves and is never genesis, so none of the three loop-termination conditions is ever met (all three are attacker-controlled). Each iteration issues one outbound round-trip and commits one `ChainFillBlock` Text-blob row (`node.py:356-361`); the walk never terminates and the `finally` cleanup (`node.py:410-412`) never runs. The per-request httpx 10s timeout (`api_client.py:55`) does not help — a cooperative attacker answers quickly, so the timeout never trips. All cost is paid in the pre-validation staging phase; the batch is discarded at apply if it doesn't link, so the work is pure waste.

- **N2 (Medium) — mempool unbounded admission.** See category 4 (the framing analyst surfaced the same finding); merged into N2.

### 2. Eclipse / chain-feeding peer

Converged on **N1**. The eclipse lens independently traced the same uncapped `fill_chain` walk from the chain-feeding angle (serving a valid-but-fake deep chain to pin the node), confirming the reachability via the automated `Miller.poll_latest_blocks` → `mill_block` path rather than only the CLI `sync`. No additional distinct finding — the deep-chain feed and the resource-exhaustion walk are the same code gap.

### 3. Gossip-loop / amplification abuser

- **N3 (Medium) — duplicate-txn re-gossip amplification.** In `Node.receive_transaction` (`node.py:95-105`) the duplicate check only guards the mempool *add* (the `added` flag); `send_transaction` at lines 103-104 is called **unconditionally** whenever `process=True` (the synchronous default, `CC_API_ASYNC_PROCESSING` off, set in `TxnView.post` `api.py:380-388`). So an authenticated TRANSACTOR peer that repeatedly POSTs the *same* already-pending txn with an empty/omitted `Peer-Hosts` header forces the victim to re-fan-out to all N peers on every duplicate receipt, carrying zero new state, while the synchronous handler blocks a worker doing up to N sequential outbound POSTs. This is a genuine asymmetry vs. the block path, which early-returns `None` for a known block *before* gossiping (`node.py:162-163, 181-182`). The `visited_hosts`/`Peer-Hosts` loop-guard is attacker-controllable and only prevents looping back to nodes already in the path; it does not couple mempool-membership to the gossip decision.

The loop-guard itself was probed for spoofing/loop-induction and **holds** — the visited-set growth makes each storm terminate (bounded by peer count N and mesh diameter), so the abuse is N-fold amplification per request, not an infinite or exponential loop.

### 4. Protocol / framing abuser

Converged on **N1** and **N2**.

- **N2 (Medium) — mempool unbounded admission.** An attacker holding a TRANSACTOR-allowlisted address POSTs distinct, individually-valid, freshly-timestamped txns to `/api/transaction/<txid>`. Admission validation (`txn.validate`, `transaction.py:224-233`) is shape + signature + txid only — it does **not** check inflows against chain UTXO/balance/double-spend (that runs only at mill time, `miller.py:89-91`), so one key mints unlimited distinct admitted txids without holding real funds. Each admit commits one `PendingTxnDAO` row plus up to `MAX_FLOWS=50` `PendingIOflowDAO` rows (`transaction.py:393-437`). No admission cap exists; the only eviction is `discard_expired_pending_txns`, gated on `TXN_TIMEOUT=4h`. Worse, `PendingTxnSet.__iter__` (`transaction.py:385-388`) builds a `Transaction.from_json` per row with no SQL filter, and the expiry sweep (`node.py:107-113`), `Miller.create_block` (`miller.py:62-102`), and the READER-reachable `PendingTxnView.get` (`api.py:593-614`) all iterate the full set — so every mill attempt, sweep, and `/transaction/pending` GET is O(mempool) full deserialization on the milling critical path.

- Payload-size: there is **no `MAX_CONTENT_LENGTH` anywhere** (grep-confirmed). This was traced, but every body-size path the framing analyst followed either reached `authorize()`/`validate()` first (cross-references to the auth/verification boundaries, out of scope per the razor) or reduced to N2's accumulator growth, so no separate payload-size finding survived as an in-scope networking issue.

### 5. Race / concurrency

**No surviving findings.** Candidates here (concurrent `fill_chain`/`receive_block` against the same prefix; `ChainFill` orphan rows on interleave) were refuted: the A2.e atomic-apply remediation (deferred per-block commits + single post-loop commit/rollback in `fill_chain`, `node.py:369-393`) bounds partial-state corruption, and the `finally`-block `ChainFill` cleanup handles the normal-exit case. The pre-existing **`ChainFill` orphan-rows-on-process-crash** hygiene observation (A5.c from the verification audit — staging rows leaked if the process is killed mid-fill) remains a documented operational-hygiene note below the per-finding remediation bar, not a new finding here. (Note N1 makes the orphan-row exposure worse in practice — a wedged, never-terminating walk accumulates uncommitted-cleanup staging rows — which is an additional argument for the N1 depth cap.)

### 6. Async post-process path

- **N4 (Low) — synchronous broker publish on the request thread.** With `CC_API_ASYNC_PROCESSING=true` and `CELERY_BROKER_URL` set to a down/slow broker, `BlockView.post`/`TxnView.post` → `queue_*_post_process` → `http_post_signal.send` (fired synchronously in the request thread, `api.py:120-127`) → `handle_http_post` → `post_process.delay()` (`api.py:150-151`) runs **before** the 202 is built. `tasks.py:16` does only `celery.conf.update(app.config)` — no `broker_transport_options`, no publish-retry/timeout overrides — so the kombu publish runs inline on the gunicorn worker and stalls on a dead broker. **Bounded, hence Low:** Celery 5.6.3 defaults (`task_publish_retry` max_retries=3 at 0/0.2/0.4/0.6s, `broker_connection_timeout=4s`) cap a silently-dropping publish at ~16s before `.delay()` raises (caught by `exception_response`) and the thread frees; a connection-refused broker fails near-instantly; the pool self-recovers when the broker returns. Reachability is weak under the hostile-peer model — the peer cannot induce the broker outage (operator config + infra failure); it only adds ordinary POST volume atop a pre-existing operator-side degraded condition.

## Cross-cutting observations

1. **Missing-bound pattern repeats across the P2P/staging/mempool surface.** No depth cap on `fill_chain`'s ancestor walk, no size cap on the `pending_txns` mempool, no idempotency/rate guard on duplicate txn gossip. Every confirmed finding is a "valid authenticated input in hostile volume/pattern" availability issue — the trusted-input boundary holds; the **resource-bounding boundary does not**.

2. **Validation is deferred past the point where resources are already committed.** `request_block` runs only structural Pydantic validation and never checks returned-hash == requested-prev_hash; full `Block.validate()` (PoW/merkle/chain) runs only in the apply phase after the entire chain is staged. Mempool admission checks shape+signature+txid but defers chain/UTXO/balance/double-spend to mill time. In both paths an attacker pays ~zero to make the victim commit unbounded staging/pending rows before any meaningful rejection. Pulling a cheap structural+identity check forward (and capping the work) closes both.

3. **The documented full-pool re-materialization cost is the amplifier for N2.** `PendingTxnSet.__iter__` re-parses every row via `Transaction.from_json` on every expiry sweep, mill attempt, and `/transaction/pending` GET, with no SQL filter — the same class as the project's known recursive-CTE/full-scan performance bottleneck. Unbounded admission and O(mempool) full-scan reads compound multiplicatively; fixing either alone helps, fixing both is needed.

4. **Block-path vs txn-path asymmetry is a recurring tell.** The block path already early-returns on a known block before gossiping (`node.py:162-163`); the txn path re-gossips unconditionally. The inbound missing-parent path uses the bounded `fill_peer` push; the outbound sync path uses the unbounded `fill_chain` walk. The safer pattern already exists in-tree on the sibling path — the fixes are largely "make the txn/`fill_chain` path match the block/`fill_peer` path," which lowers fix risk.

5. **What was checked and holds (not findings):** the `cc-sig-v1` per-request signature auth and the live `Role.address_role` re-authorization gate, the `Peer-Hosts`/`visited_hosts` loop-guard correctness, the A2.e atomic `fill_chain` apply, and the block apply-phase validation. The four confirmed issues are all resource-bounding gaps on otherwise-validated, otherwise-authorized paths.

## Recommendations

Prioritized; each maps to a finding and to the [roadmap](../ROADMAP.md) remediation entry.

1. **FIRST (N1, High): Cap `fill_chain`'s staging walk.** Add a configurable maximum depth/row count to the `while True` loop (`node.py:343-361`), aborting and cleaning up the `ChainFill` when exceeded; **and** in `request_block` (`node.py:221-236`) verify the returned block's hash equals the requested `prev_hash` before staging. Together these turn an unbounded, non-terminating, attacker-steered walk into a bounded one. Lock it in with the `requests_mock` bounded-observation test.

2. **(N2, Medium): Add a mempool admission cap.** A configurable `MAX_PENDING` checked in `receive_transaction` (`node.py:95`) before `pending_txns.add`, rejecting/evicting once full. Pair it with an indexed/SQL-filtered expiry query so `discard_expired_pending_txns` and `PendingTxnView` no longer re-materialize the whole pool in Python on the milling critical path — addressing both the disk-growth and the O(mempool) read-amplification halves.

3. **(N3, Medium): Couple txn gossip to mempool-membership.** Move `send_transaction` inside the `if txn not in self.pending_txns` block in `receive_transaction` (`node.py:95-105`), mirroring the block path's early-return-before-gossip. Optionally add a short-window API-layer idempotency/rate guard. Removes the 1→N per-duplicate-request amplification with a well-tested in-tree precedent.

4. **(N4, Low): Harden the async publish path.** Move the `post_process.delay()` publish off the request thread (fire-and-forget with a short bounded connection timeout / `broker_transport_options`, or enqueue without blocking the 202) so a degraded broker can't stall gossip POSTs even for the ~16s Celery-default window. Lowest priority — bounded and operator-gated today.

5. **Adopt a "bound every attacker-influenced accumulator" convention.** Where authenticated-but-hostile peer input can drive growth (staging tables, mempool, gossip fan-out, retry loops), require an explicit configurable cap **and** a bounded-observation availability test at the same time the accumulator is introduced. These four findings are the same missing-bound class surfacing in four places; a convention prevents the next one.

## Demonstration tests

Each finding has a `@pytest.mark.xfail(strict=True)` test in `tests/test_network_audit.py`. Availability findings use the bounded-observation convention — they drive the uncapped behavior only up to a small, safe bound and never exhaust real resources. `--runxfail tests/test_network_audit.py` makes every demonstration fail (proving the gap); strict mode forces each marker's removal when the finding is remediated.
