# Verification pipeline threat-modeled audit — design spec

**Status:** Draft for review
**Date:** 2026-05-29
**Scope:** A threat-modeled security audit of the cancelchain block/chain/transaction verification pipeline. Defines 7 adversary categories, enumerates each adversary's attack attempts, traces each attempt through the existing validation surface, and documents gaps as findings. Produces a written audit report at `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` plus a `tests/test_verification_audit.py` test module containing one `@pytest.mark.xfail` test per confirmed gap. Remediation of individual findings is out of scope for this audit — each finding becomes the seed of a follow-up PR after the audit lands.

## Goal

Build confidence (or surface lack thereof) that the cancelchain verification pipeline correctly enforces all economic and chain-integrity invariants under each plausible adversary category. The deliverable is a findings report with severity ratings + a concrete failing-test demonstration of each gap. Concrete tests turn "we think there's a gap here" into "here's the exact bytes that exploit it" — and serve as regression prevention once the fix lands.

## Non-goals

- **Not auth.** The API authentication layer (JWT handshake, role keying) gets its own audit pass later. This audit assumes authentication is correct — i.e., every TRANSACTOR / MILLER / ADMIN action originates from an address that legitimately holds that role. Authentication-layer gaps (e.g., role-regex bypass) are deliberately out of scope.
- **Not key management.** Wallet generation, private-key storage, key rotation — all separate concerns.
- **Not DOS / resource exhaustion.** Attacker submits 10M malformed requests to consume CPU is a different threat class. This audit is about chain-integrity correctness.
- **Not side-channels.** Timing attacks, cache leaks, error-message information disclosure — overkill for this threat surface.
- **Not reliability.** What happens if `mill_block` crashes mid-loop, or `Node.fill_chain` is partially applied and then aborts — that's resilience engineering, not verification.
- **Not remediation.** Each finding includes a remediation sketch (one or two sentences pointing at the right place to fix), but actual code changes ride downstream PRs. The audit's job is to surface and prove gaps, not to fix them.
- **No spec changes to validation rules.** This audit catches gaps where the existing rules don't enforce what they should. It doesn't propose changing what the rules are. (E.g., "the rule that subjects must be 1-79 chars is wrong" is out of scope; "the validation method doesn't actually check the upper bound" is in scope.)

## Decisions taken during brainstorming

- **Threat-modeled approach over broad survey or single-concern deep-dive.** A broad survey misses cross-layer composition bugs; a single-concern deep-dive is too narrow for a first-pass audit on a previously-unaudited pipeline. The threat-model approach gives us 7 concrete lenses through which to examine the same code.
- **Audit document goes under `docs/superpowers/audits/`** (a new subdirectory), distinct from `docs/superpowers/specs/`. The shape is different: specs describe what to build; audits report what was found. The directory split keeps the documentation index legible.
- **Demonstration tests use `@pytest.mark.xfail`** rather than being deferred to remediation PRs. Each xfail test serves three purposes: (1) concrete proof the gap exists, (2) regression prevention as soon as the fix lands (xfail → pass), (3) the audit becomes "executable" — anyone can run `pytest tests/test_verification_audit.py` to verify the gaps still exist.
- **Severity rubric is 4 levels** — Critical (chain-correctness existential — bad block in chain, double-spend possible), High (significant invariant violation but bounded blast radius), Medium (edge case that misbehaves but recovers, or requires unrealistic adversary capabilities), Low (cosmetic / documentation / theoretical).
- **No remediation grouping in the audit itself.** Each finding gets an individual severity and a sketch of where the fix would go. Whether to group multiple findings into one remediation PR is a decision for the downstream PR planning, not the audit.

## Architecture

### Threat categories (the seven lenses)

#### 1. External attacker with valid TRANSACTOR role
**Capabilities:** Has a wallet address that matches a `CC_TRANSACTOR_ADDRESSES` regex. Can authenticate. Can submit transactions via the `/api/transaction` POST endpoint. Knows their own wallet's private key. Does NOT have MILLER privileges (can't submit blocks directly), can submit txns that millers may include.
**Goals:**
- a. Double-spend their own outflow (consume the same UTXO in two separate transactions in the pending pool, hoping both get into different blocks).
- b. Inflate value (submit a transaction where total inflow value > total outflow value, or outflow > inflow).
- c. Smuggle malformed payload past schema validation (oversized subject string, non-base64 signature, etc.).
- d. Exploit forgive/support asymmetry — `forgive` is supposed to rescind opposition (`subject`), but only the original opposer can rescind. Test: can a different address forgive someone else's opposition?
- e. Submit a transaction with `inflow` referencing an outflow that doesn't exist, doesn't belong to them, or was already spent.
- f. Replay a previously-mined transaction (same txid) into the pending pool.
- g. Submit a transaction with a future or past timestamp outside the acceptable window.

#### 2. Hostile peer over gossip
**Capabilities:** Configured in our `CC_PEERS` list (presumed trusted-ish) but adversarial. Can send arbitrary HTTP requests to our `/api/block` and `/api/transaction` endpoints with valid peer credentials. Can craft blocks/txns with malformed content. Sees our public chain state.
**Goals:**
- a. Submit a block that fails one of `Block.validate*` but `Chain.validate_block` doesn't catch (cross-layer gap).
- b. Force expensive reorgs by submitting alternate-chain blocks with adjusted timestamps to pass the difficulty target.
- c. Inject malformed-but-deserializable JSON that breaks downstream code (e.g., negative `idx`, `prev_hash` collision attempt, MAX_TARGET edge case).
- d. Manipulate the ChainFill staging table (block arrives via `Node.fill_chain`, persists in `chain_fill`, but is never validated before apply).
- e. Send a chain whose tip is genuinely longer but whose intermediate blocks fail validation, exposing a "partial chain accepted" gap.
- f. Probe the validation order: send blocks that fail validation at a deep check to see if earlier persistence side-effects leak.

#### 3. Malicious miller (MILLER role)
**Capabilities:** Solves and submits blocks. Authenticated as MILLER. Controls the coinbase address. Can choose which pending transactions to include. Can manipulate block timestamps and `proof_of_work`.
**Goals:**
- a. Include an invalid transaction in their block (because the miller signs the block, txn-level validation must happen on receive regardless of who mined it).
- b. Claim excess coinbase reward (output > REWARD).
- c. Censor specific subjects (refuse to include txns matching a pattern). NOTE: chain doesn't enforce inclusion fairness; this is a known limit, audit should confirm there's no enforcement to bypass.
- d. Embed contradictory inflows/outflows (e.g., two inflows consuming the same outflow within the same block).
- e. Manipulate timestamps to push the difficulty target up or down beyond the ±4× clamp.
- f. Submit a block with a valid proof_of_work but an invalid merkle root (header doesn't actually commit to the included transactions).
- g. Submit a block at the wrong difficulty for the current chain height.

#### 4. Replay attacker
**Capabilities:** Has seen previously-broadcast transactions (they're public). Has not necessarily solved any block. Has whatever roles are useful for resubmission (often TRANSACTOR is enough).
**Goals:**
- a. Resubmit a confirmed transaction (already in some block) into the pending pool — does anything in `Node.receive_transaction` reject duplicates?
- b. Resubmit the same transaction into a competing chain fork — does the validation reject a txid that already exists on a different branch from the current tip?
- c. Replay a coinbase transaction (specifically), since miller coinbase txns have unique structure.
- d. Submit the same outflow consumption (inflow) in two transactions across two different chains — the gold-standard reorg double-spend.

#### 5. Reorg attacker
**Capabilities:** Causes chain reorganizations either via hash power (controls or rents enough mining capacity) or via timing manipulation (gets blocks accepted before the network has propagated competing blocks).
**Goals:**
- a. Invalidate previously-confirmed transactions by having them displaced into a stale branch. Once a previously-confirmed txn is no longer in the active chain, can it be re-spent?
- b. Double-spend across the reorg boundary — confirm a txn on the soon-to-be-stale branch, get goods/credit, then reorg and spend the same outflow on the new branch.
- c. Exploit the gap between `ChainFill` staging and apply — what if `fill_chain` is interrupted partway through, leaving inconsistent state?
- d. Manipulate `_is_longest` cache (per the existing Phase 6.5 risk note about cross-worker cache invalidation) — but only the validation-correctness aspect, not the cache-design aspect (which is a known follow-up).

#### 6. Race / concurrency attacker
**Capabilities:** Coordinates the timing of multiple submissions to exploit windows between validation and persistence.
**Goals:**
- a. TOCTOU: submit two conflicting transactions (e.g., two inflows consuming the same outflow) within the validation-to-commit window of the first. Does `Chain.validate_block_txn` lock or re-check at commit time?
- b. Pending pool race: two miller processes both pull the same pending txn and include it in different blocks.
- c. Block-submission race: two valid blocks at the same height arrive simultaneously; the chain selection must be deterministic and idempotent.
- d. ChainFill race: simultaneous `fill_chain` calls for overlapping ancestry ranges.

#### 7. Genesis / edge-case attacker
**Capabilities:** Anything legitimate. Targets the special-case code paths that are likely under-tested.
**Goals:**
- a. Empty block (no transactions, just coinbase). What does `validate_merkle_root` do with an empty body?
- b. First block of the chain (no `prev_hash`). What validation runs differently?
- c. Block with exactly `MAX_TRANSACTIONS` (= 100) transactions, hitting the upper bound exactly.
- d. Transaction with exactly the boundary subject length (1 char, 79 chars).
- e. Just-expired transaction (timestamp exactly `TXN_TIMEOUT` ago — boundary check).
- f. Transaction with empty inflow list (legal? must be a coinbase?).
- g. Block with proof_of_work = 0 (or just under the target threshold by 1 hash).
- h. Subject string with non-printable UTF-8 (1-79 chars but garbage).
- i. Chain with one block (no parent to validate).
- j. Reorg with zero common ancestor (genuinely disjoint chains).

### Audit methodology (per attack attempt)

For each of the ~30-40 attack attempts above (7 categories × 3-7 attempts each):

1. **Pre-state:** what's true about the chain when the attack begins (e.g., "outflow X belongs to address A, currently unspent, on a chain of height 1000").
2. **Attack:** the exact API call or gossip message the attacker sends. Concrete bytes if possible.
3. **Trace:** which validation methods get called, in what order, what they check. This is the load-bearing step — it's where gaps become visible.
4. **Outcome:** what actually happens. Validation rejects? Persistence accepts? Side effect occurs?
5. **Finding (if there's a gap):** severity rating + 1-sentence remediation sketch.
6. **Demonstration test (if there's a gap):** a `@pytest.mark.xfail` test in `tests/test_verification_audit.py` that exercises the attack and asserts the gap exists.

When the trace shows validation correctly rejects the attack, no finding is produced — but the trace is still documented in the audit report under "Adversary X, attack a" so future readers know we considered it.

### Severity rubric

| Severity | Definition | Examples |
|---|---|---|
| **Critical** | Chain-correctness existential. An invalid block or invalid transaction can end up persisted in the chain, OR an exploit allows value to be created/destroyed contrary to the conservation invariant. | Double-spend goes undetected; coinbase reward inflation; merkle root spoofing. |
| **High** | Significant invariant violation with bounded blast radius. The chain remains consistent overall but a specific protected property fails. | Forgive/support asymmetry bypass; expired txn gets in; cross-branch txid reuse. |
| **Medium** | Edge case that misbehaves but recovers, OR requires adversary capabilities at the upper bound of plausibility (e.g., requires majority hash power). | Reorg-boundary stale-state read from cache; race window of <100ms. |
| **Low** | Cosmetic / documentation / theoretical. The code is technically incorrect but practically harmless under the deploy model. | Error message reveals validator order; redundant check; missing assertion. |

### Test module structure

`tests/test_verification_audit.py` follows the existing tests/ conventions (`pytest` fixtures from `conftest.py`, single-quote string style, full-line docstrings on each test). Each finding becomes a single test:

```python
@pytest.mark.xfail(
    reason='Audit finding A1.a — severity HIGH — double-spend across pending pool reordering. See docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md',
    strict=True,
)
def test_pending_pool_double_spend(...):
    # Pre-state: build a chain with outflow X belonging to address A.
    # Attack: submit two transactions, both consuming outflow X, into the pending pool.
    # Expected after fix: second transaction is rejected by validate_block_txn.
    # Observed today: both make it into pending pool; whichever miller picks up second succeeds.
    ...
```

`strict=True` on xfail means: if the test starts unexpectedly passing (because we fixed the gap), CI fails — forcing the test to be updated (xfail removed → real pass) as part of the remediation PR. This keeps the audit's xfail list synchronized with reality.

The reason string includes the finding ID (e.g., "A1.a" = adversary 1, attack a) and severity, plus a link to the audit doc for context. Auditors writing remediation PRs can grep `grep -rn 'Audit finding A1' tests/` to find the demonstration test for any finding.

### Audit document structure

`docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` sections:

1. **Executive summary** — count of findings by severity, headline conclusions.
2. **Threat model** — the 7 adversaries restated with their capabilities.
3. **Methodology** — how the audit was performed (this section is a recap of this spec).
4. **Findings table** — every finding with ID, severity, one-line description, remediation sketch, test name.
5. **Per-adversary traces** — for each adversary, each attack attempt, the full trace (even for "validation correctly rejects" cases — those are the positive evidence the audit produces alongside the negative findings).
6. **Cross-cutting observations** — patterns that span multiple adversaries (e.g., "validation order is inconsistent between API entry and gossip receive").
7. **Recommendations** — prioritized remediation ordering, dependencies between findings (sometimes fixing finding A enables fixing finding B).

## Changes

### Files (in scope)

- **Create:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` — the findings report. Single file, ~1500-3000 lines depending on how many gaps are found.
- **Create:** `tests/test_verification_audit.py` — one `@pytest.mark.xfail(strict=True)` test per finding.
- **No code changes outside `tests/`.** The audit produces evidence; remediation rides downstream.

### Files (read during audit, not modified)

The audit reads (and references) but doesn't modify:
- `src/cancelchain/block.py` — Block validate methods + invariants
- `src/cancelchain/chain.py` — Chain validate methods + context
- `src/cancelchain/transaction.py` — Transaction validate methods
- `src/cancelchain/schema.py` — Pydantic schemas + format validators
- `src/cancelchain/payload.py` — Inflow/Outflow validate + subject/destination rules
- `src/cancelchain/wallet.py` — Signature verification
- `src/cancelchain/api.py` — POST handlers that invoke validation
- `src/cancelchain/node.py` — `receive_block` / `receive_transaction` / `fill_chain` / `fill_peer`
- `src/cancelchain/miller.py` — `create_block` (validates pending txns into a block)
- `src/cancelchain/exceptions.py` — error taxonomy (36 classes)

## Test plan

- **`tests/test_verification_audit.py` exists and runs.** Every test in it is an `xfail(strict=True)` or, after a remediation lands, a real pass.
- **CI behavior:** `pytest` should report each `xfail` as `XFAIL` (expected to fail) — no actual failures. Total test count grows by N where N = number of findings, but pytest's pass/fail summary stays clean.
- **CI gate verification:** `uv run pytest --runxfail 2>&1 | grep "XFAIL"` returns N matches.
- **Manual verification:** read the audit doc end-to-end; verify each finding has a corresponding test; verify each "validation correctly rejects" trace cites the validation method that does the rejecting.

Total test count after audit: 236 + N (where N is the number of audit findings; estimated 5-20 based on the size of the validation surface and how thoroughly it's been thought through).

## Acceptance

- `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` exists and is complete (Executive summary, Threat model, Methodology, Findings table, Per-adversary traces, Cross-cutting observations, Recommendations).
- `tests/test_verification_audit.py` exists; every finding has a corresponding `xfail(strict=True)` test whose `reason` string cites the finding ID, severity, and audit-doc path.
- `uv run pytest 2>&1 | tail -3` shows `236 passed, N xfailed, 1 skipped` where N = number of findings.
- `uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3` shows `N failed` (the audit's xfail tests genuinely demonstrate gaps).
- `uv run ruff check src tests` + `uv run ruff format --check src tests` exit 0.
- `uv run mypy` exits 0 (the test module's typing is consistent with the project's strict mypy config).
- `uv run cancelchain db check` still passes (audit doesn't touch models or migrations).
- Audit doc passes a `grep -c '^## Adversary' <audit-file>` returning at least 7 (one section per threat category).
- Findings table has at least one row per severity level represented in the findings (or explicitly states "no findings at this severity").

## Risks

- **Audit takes longer than estimated.** The 7 × 3-7 = 21-49 attack attempts might each take more time than expected if traces are tangled. Mitigation: timebox per-adversary work to a half day; if running over, surface the partial findings rather than blocking on completeness.
- **Findings interact.** Fixing finding A might invalidate the test for finding B (because the new check rejects B's pre-state). Mitigation: write each test against the smallest sufficient pre-state, document inter-finding dependencies in the audit doc's Recommendations section.
- **False positives.** A trace might miss an existing check that *does* reject the attack, leading to a "finding" that's actually correct behavior. Mitigation: each finding requires the demonstration test to actually fail (not just the prose to claim it does); if writing the xfail test reveals validation catches the attack, the finding moves from "Findings table" to "Per-adversary trace" as an "attack correctly rejected" entry.
- **False negatives.** The audit might miss real gaps because the threat model didn't enumerate them. Mitigation: the audit is a snapshot; future audits (after auth audit, after first real adversary, after a security incident) can extend the threat model. Document "what we did NOT consider" explicitly in the audit's Cross-cutting observations.
- **The audit accidentally becomes remediation.** While tracing a finding, the auditor might be tempted to "just fix it real quick." Mitigation: this is explicit Non-goal; fixes go in dedicated remediation PRs. The audit PR adds the xfail test and the finding entry — and stops.
- **`strict=True` xfail tests break under unrelated refactors.** If someone refactors the validation in a way that incidentally rejects the attack (without explicitly fixing the finding), the xfail will start passing and CI will fail. Mitigation: this is desired behavior — it forces engagement with the audit's findings when surrounding code changes touch the same area. The CI failure points the refactorer at the finding doc so they can intentionally remove the xfail or update the test.
- **Reviewer fatigue on a 3000-line audit doc.** PR review of a single huge document is hard. Mitigation: structure the doc with per-adversary sections that can be reviewed independently; the spec PR (this one) is reviewed before the audit PR even opens, so reviewers know the structure in advance.

## Open decisions

None at design time. Brainstorming resolved:
- Threat-modeled approach (not broad survey, not single-concern deep-dive).
- All 7 adversary categories in scope.
- Audit doc at `docs/superpowers/audits/` (new directory, separate from `specs/`).
- Demonstration tests with `xfail(strict=True)` — included in audit deliverable, not deferred to remediation.
- 4-level severity rubric (Critical/High/Medium/Low).
- Each finding gets ID-based naming (A<adversary>.<attack-letter>) for grep-ability between audit doc and test module.

## What comes next

- **Audit impl PR.** Executes this plan: writes the audit doc and the xfail test module.
- **Remediation PRs.** Each finding (or group of related findings) becomes a follow-up PR. Severity ordering: Critical first, then High, then Medium. Low findings may be deferred indefinitely or fixed opportunistically when code in the area changes.
- **API auth audit.** The deliberately-deferred follow-up that the user originally raised alongside this one. Methodology can reuse this audit's structure (threat-modeled, xfail demonstration tests) — the threat model will differ (it's about authentication, not chain integrity).
- **Future audits.** As new attack surfaces appear (new API endpoints, new domain features, new adversary types), additional audits can extend the test module and the audit doc. The `docs/superpowers/audits/` directory holds them all.
