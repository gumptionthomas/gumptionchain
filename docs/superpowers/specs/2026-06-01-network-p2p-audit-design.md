# P2P / Networking Threat-Modeled Audit — Design

**Status:** Draft for review
**Date:** 2026-06-01
**Kind:** Security audit (design phase — defines scope, adversary model, methodology, and deliverable shape; the audit itself is run during the implementation plan that follows this spec)

This is the third threat-modeled audit of cancelchain, after the [verification-pipeline audit](../audits/2026-05-29-verification-pipeline-audit.md) (closed 0/0/0/0) and the [API-authentication audit](../audits/2026-05-31-api-authentication-audit.md) (closed 0/0/0/0). It targets the peer-to-peer networking and node-coordination layer — the orchestration *around* verification, where a hostile peer's inputs are individually valid and authenticated but hostile in volume, depth, pattern, or timing.

## Motivation

The two prior audits hardened (a) whether an individual block/transaction is *valid* and (b) whether a request is *authenticated/authorized*. Neither examined the layer that ties those together: how the node gossips, pulls and pushes chains, stages fills, guards against gossip loops, and bounds the resources a peer can make it spend. That layer (`Node`, `Miller`, and the peer-facing API views) processes the widest untrusted-input surface in the system and has never been threat-modeled. Concrete smells already visible on inspection:

- `Node.fill_chain` runs `while True`, issuing one `request_block` round-trip **and** persisting one `ChainFillBlock` staging row per ancestor, with no visible depth cap — a hostile peer can serve an endless valid-looking ancestor chain.
- `Node.fill_peer` grows an unbounded `blocks` list (`blocks.insert(0, block)`) and contains a `delay > 10` / `sleep(delay)` back-off loop (cumulative per-block sleep up to ~55s, multiplied across N blocks).
- No `MAX_CONTENT_LENGTH` or any payload-size bound exists anywhere in the codebase (confirmed by grep) — block/transaction JSON bodies are unbounded.
- The `pending_txns` mempool has no documented admission cap (flooding surface).
- `ChainFill`/`ChainFillBlock` staging rows are orphaned on process crash mid-fill (already noted as hygiene observation A5.c in the verification audit, never remediated).

These are candidate attack *seeds*, not pre-judged findings; the audit confirms or refutes each through tracing and adversarial verification.

## Scope & trust boundaries

### In scope

- **Node/Miller orchestration** (`src/cancelchain/node.py`, `src/cancelchain/miller.py`):
  - Gossip: `send_transaction` / `receive_transaction` / `send_block` / `receive_block` / `process_block`.
  - Chain sync: `fill_chain`, `fill_peer`, `request_block`, `request_latest_blocks`, `add_block` / `create_chain`.
  - Mempool: `pending_txns` (`PendingTxnSet`), `discard_expired_pending_txns`, `Miller.update_pending_txns` / `pending_chain_txns` / `pending_txns_gen`.
  - Miller sync: `poll_latest_blocks`, `mill_block` (early-abort on longer chain).
  - Loop-guard: `visited_hosts` plumbing and the `Peer-Hosts` header.
  - Staging lifecycle: `ChainFill` / `ChainFillBlock` create/commit/delete paths.
- **Peer-facing API views** (`src/cancelchain/api.py`) as the entry points where peer bytes first land: the `POST /api/block`, `POST /api/transaction`, and `/process` endpoints, examined for payload-size limits, content-type/framing handling, txid/hash-mismatch behavior, and the `CC_API_ASYNC_PROCESSING` 202 enqueue path (`queue_post_process` → `http_post` signal → `handle_http_post` → `tasks.post_process`).
- **Client gossip** (`src/cancelchain/api_client.py`) only as it participates in the above flows (retry/timeout behavior, fan-out).

### Trusted boundaries (reference, do not re-audit)

- The `validate_*` verification pipeline (block/chain/transaction validity) — closed by the 2026-05-29 audit.
- The `cc-sig-v1` per-request signature auth and `authorize()` role gate — closed by the 2026-05-31 audit.

**Framing consequence (the scope razor):**

- A finding that reduces to *"a malformed/invalid block or transaction is accepted"* belongs to the **verification audit**. If surfaced here, cross-reference it; do not claim it as a new networking finding.
- A finding that reduces to *"an unauthenticated/under-authorized request is honored"* belongs to the **auth audit**. Same treatment.
- This audit owns: *"an individually-valid, authenticated peer input (or sequence/volume/timing of them) makes the node exhaust resources, amplify traffic, loop, wedge, or corrupt its orchestration/staging state."*

### Explicitly out of scope

- `browser.py` and the human-facing web UI (different threat model — XSS/CSRF/session — offered as its own separate audit).
- The CLI and wallet-file handling.
- Consensus/PoW soundness of an individual block (verification audit's domain). Hostile *patterns* of valid blocks (deep fake chains, reorg-cost amplification) are in scope; the validity of each block is assumed.

## Adversary categories

Six categories tailored to this layer. Each is a lens for the fan-out; a single concrete attack may touch more than one.

1. **Resource-exhaustion peer** — unbounded `fill_chain` depth, `fill_peer` back-off amplification and unbounded `blocks` accumulation, unbounded payload size, memory growth, unbounded mempool admission, `ChainFillBlock` row-count growth.
2. **Eclipse / chain-feeding peer** — serving a valid-but-fake deep or longer chain to drive reorg/fill cost, pin the node in a fill, or dominate `request_latest_blocks` / `poll_latest_blocks`.
3. **Gossip-loop / amplification abuser** — spoofing, omitting, or oversizing `visited_hosts` / `Peer-Hosts` to induce gossip loops, defeat the loop-guard, or amplify fan-out across the peer set.
4. **Protocol / framing abuser** — oversized or slow-streamed bodies, content-type tricks, txid/`block_hash` mismatch handling, and abuse of the 202 async enqueue path (enqueue without doing work, self-`/process` recursion).
5. **Race / concurrency** — concurrent `fill_chain`/`receive_block` against the same chain prefix, `ChainFill` orphan rows on crash or interleave, mempool add/discard races, double-spend-window interactions with sync (only insofar as orchestration, not consensus, is at fault).
6. **Async post-process path** — the Celery `queue_post_process` → worker → self-`/process` loop as an amplification, wedging, or unbounded-retry surface; behavior when the broker is mis/unconfigured.

## Methodology — multi-agent Workflow fan-out

The audit is executed (during the subsequent implementation plan) as a Workflow with three phases, mirroring the prior two audits:

1. **Discover (fan-out):** one analyst agent per adversary category, each given the in-scope file set, the trust-boundary razor, and its category lens. Each traces concrete attack attempts through the code and returns structured candidate findings (attack, code path with `file:line`, precondition, impact, proposed severity).
2. **Verify (adversarial):** for each candidate finding, independent agents attempt to **refute** it — is the impact real, or is it already bounded by a trusted-boundary control, an httpx/Flask default, a timeout, or an existing guard? A finding survives only if refutation fails. This is where false positives (e.g., "unbounded" behavior that is actually bounded by an upstream timeout or `authorize()`) are killed.
3. **Synthesize:** dedupe surviving findings across categories, assign final severities, and compose the report.

Explicit opt-in to the multi-agent Workflow was given by the user; the fan-out spawns a fleet of agents and is the cost driver.

## Severity rubric

Same Critical/High/Medium/Low scale as the prior audits. For **availability/resource findings**, severity is graded on:

- **Amplification factor** — how much work/storage/traffic one cheap peer request induces (O(1), O(chain depth), O(peers), unbounded).
- **Reachability** — does it require a privileged role (`MILLER`/`ADMIN`) or only `TRANSACTOR`/`READER` / any authenticated peer? Lower required privilege ⇒ higher severity.
- **Persistence** — transient (CPU/memory for the request) vs durable (DB rows, disk, wedged state surviving the request).

A finding that lets any authenticated peer durably consume unbounded storage or wedge sync ranks higher than one that costs a bounded amount of transient CPU.

## Deliverable / output format

- **Audit report:** `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`, structured like the prior two — executive summary with the `N Critical / N High / N Medium / N Low` headline, per-adversary attack traces, a findings table (id, adversary, severity, description, status, demonstration test), cross-cutting observations, and a Recommendations section.
- **Demonstration tests:** a new `tests/test_network_audit.py`, one `@pytest.mark.xfail(strict=True)` test per finding (strict mode forces the marker's removal as part of each remediation PR).
  - **Correctness findings** follow the established pattern: the test asserts the buggy behavior under `xfail`, and flips to a passing regression when the fix lands.
  - **Availability findings** use a **bounded-observation** convention: with a mock peer (`requests_mock` / the existing `requests_proxy` fixtures) the test drives the uncapped behavior up to a small, safe bound (e.g., a fake ancestor chain of depth N, a single oversized-but-modest body, a handful of flooding txns) and asserts the missing-cap behavior is observable (e.g., "all N staged with no rejection", "no size-limit error raised"). The marker holds `xfail(strict=True)` until a cap/guard lands, after which the test asserts the cap **rejects or stops** at the threshold. **No test ever actually exhausts real memory, disk, or wall-clock** — bounds are chosen for fast, deterministic runs.
- **Test fixtures:** reuse `tests/conftest.py` wallets and the `requests_proxy` / `remote_requests_proxy` WSGI proxies; `time_machine` where timing participates. New tests that fan out across CPU cores (if any) carry `@pytest.mark.multi`.

## Close-out flow

Each finding is remediated individually after the audit lands, one per cycle: brainstorm → spec → implementation plan → subagent-driven execution, through the internal cross-model review loop (different-model reviewers to convergence) followed by exactly one Copilot backstop on the PR. Each remediation flips its strict-xfail demonstration into a passing regression and updates the report's headline, driving the audit to **0 Critical / 0 High / 0 Medium / 0 Low**. A roadmap entry under "Audit remediation" tracks open findings; pre-existing related observations (A5.c ChainFill orphan rows) are folded in where the same code is touched.

## Non-goals

- Remediation itself (this spec covers producing the audit; fixes are separate cycles).
- Re-auditing the verification pipeline or auth layer.
- Performance optimization (the deferred Phase 6.7 / cache-invalidation roadmap items are separate, profiling-gated work — though an availability finding may reference them where a resource bound and a perf bottleneck coincide).
- Hardening against a malicious *operator* of this node (the threat actor is a hostile or compromised **peer**, plus authenticated lower-privilege callers).

## Acceptance criteria for this design

- Scope, trust boundaries, and the scope razor are unambiguous: every candidate finding can be classified as in-scope, cross-reference-only (trusted boundary), or out-of-scope.
- The six adversary categories cover the in-scope surface with no obvious gap.
- The methodology is the approved three-phase Workflow fan-out.
- The deliverable shape (report + `tests/test_network_audit.py` with the strict-xfail + bounded-observation conventions) matches the prior audits' proven format.
- The audit produces no actual resource exhaustion when its demonstration tests run.
