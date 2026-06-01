# Roadmap — post-Phase-8

Consolidated list of forward-looking items deferred from prior phase specs. Each entry links to the originating spec for the full rationale. Items are not strictly ordered — pick by current priority.

When closing an item: remove it from this file (or mark it ✅ with the closing PR/commit) and move on. When discovering a new item during a phase: add a one-line entry here pointing at the spec section that introduced it.

---

## Phase 6.7 (small) — Batched-fetch chain walk

Replace the single-row iterative walk in `ChainDAO._rebuild_longest_chain_blocks` (currently `current = current.prev` per step) with batched fetch (`WHERE id IN (...)` over N collected prev_ids at a time) **if profiling shows the per-step lazy-load is the new bottleneck on long chains**. After Phase 6.6 (smart-reorg) lands, the only walks that benefit are bootstrap (one-time) and catastrophic deep-reorg fallback (rare) — so this drops to lower priority.

Originating spec:
- [Phase 6.5 spec — Risks](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md) "Iterative walk slow on very long chains"

---

## Phase 7+ — Generalize materialization to all chains

Today the `longest_chain_block` table tracks only the canonical chain. The 4 `ChainDAO` property accessors (`blocks`, `transactions`, `outflows`, `inflows`) branch on `_is_longest()` and fall back to the CTE-backed `BlockDAO.block_chain` / `transactions_chain` / `outflows_chain` / `inflows_chain` for non-longest chains. Removing the CTE-backed fallback entirely requires generalizing the materialization to all chains — e.g., a many-to-many `chain_blocks(chain_id, block_id, position)` updated on every chain extension.

Trade-off: storage grows (N chains × M blocks); reorg-handling complexity grows. Only worth it if non-longest chain queries become hot.

Originating spec:
- [Phase 6.5 spec — Non-goals](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md)
- [Phase 6 spec — Risks](specs/2026-05-27-phase-6-longest-chain-materialization-design.md) "Residual CTE on non-longest chain API queries"

---

## Phase 7+ — Cross-worker `_is_longest()` cache invalidation

The class-level generation counter from Phase 6.5 invalidates all in-process `ChainDAO` instances' cached `_is_longest` values, but it's **process-local**. Multi-worker Gunicorn setups have one counter per worker — Worker A's reorg doesn't bump Worker B's counter. A held `ChainDAO` instance in Worker B could return a stale `True` for a brief window after a cross-worker reorg, routing the next property read to the materialized table aligned with the new (different) longest chain.

Mitigations to consider when this matters:
- Session-scoped cache (cleared at request boundaries via Flask `g` / equivalent).
- Cross-process notification (Redis pubsub, Postgres `NOTIFY` channel, or similar).

Note: the verification audit (PR #84) confirmed this is bounded to UX-layer staleness — validation reads use per-block recursive CTEs, not the cache or `LongestChainBlockDAO` materialization, so a stale `True` read cannot cause acceptance of an invalid block. Severity is "non-critical correctness for read consumers" not "chain-correctness existential."

Originating spec:
- [Phase 6.5 spec — Risks](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md) "Cross-worker stale cache"
- [Verification audit — Cross-cutting observations](audits/2026-05-29-verification-pipeline-audit.md) (bounded-blast confirmation)

---

## Audit remediation — verification pipeline findings (PR #84)

**All six findings from the 2026-05-29 verification pipeline audit are remediated** (A2.e, A4.c, A7.b, A7.h, A7.e, A1.f — see Closed items). The audit is fully closed at 0 Critical / 0 High / 0 Medium / 0 Low; every `@pytest.mark.xfail(strict=True)` demonstration is now a passing regression test in `tests/test_verification_audit.py`.

**Not on this list (audit-surfaced but not validation-pipeline remediations):**
- Reorg double-spend (A4.d note, A5.a, A5.b) — canonical PoW property; mitigation is operator confirmation-depth guidance in user-facing docs, not validation code.
- ChainFill orphan rows on process crash (A5.c hygiene observation) — operational hygiene; possible periodic-sweep job, below per-finding remediation bar.

Originating audit:
- [Verification pipeline threat-modeled audit](audits/2026-05-29-verification-pipeline-audit.md) — Findings table, Recommendations section.

---

## Audit remediation — API authentication findings (PR #102)

The 2026-05-31 API authentication audit ([report](audits/2026-05-31-api-authentication-audit.md); design+plan [#101](https://github.com/gumptionthomas/cancelchain/pull/101), findings+tests [#102](https://github.com/gumptionthomas/cancelchain/pull/102)) produced **8 findings: 0 Critical / 0 High (A4.a remediated, PR #105) / 5 Medium / 2 Low**, each with a `@pytest.mark.xfail(strict=True)` demonstration test in `tests/test_auth_audit.py`. Each remediation flips its xfail to a real pass. Grouped by shared fix (see the audit's Recommendations for full detail):

- ✅ **A4.a (High) — exact-match role allowlists** — closed by PR #105. Replaced regex matching with exact-address membership + a READER-only `"*"` sentinel, validated at startup (`Role.validate_config` → `InvalidRoleConfigError`). Test: `test_a4_a_overbroad_admin_regex_does_not_escalate` (flipped from xfail to passing regression) + new exact-match/validation coverage in `tests/test_api.py`.
- **A3.a + A5.b (Medium) — re-validate `rol` against live config in `authorize()`.** One per-request `Role.address_role(address)` re-check closes both the forged-role (A3.a) and stale-role-after-revocation (A5.b) demonstrations. Tests: `test_a3_a_forged_role_claim_accepted`, `test_a5_b_stale_role_rejected_after_config_revocation`.
- **A3.b (Medium) — add + verify JWT `iss`/`aud` (and `iat`/`jti`).** Binds tokens to a node/audience; closes cross-node replay. Test: `test_a3_b_cross_node_token_replay`.
- **A2.c + A7.a (Medium) — throttle the unauthenticated token endpoint.** Wrong-challenge attempt counter + challenge invalidation, unredeemed-row cap/eviction, rate limiting; reorder `TokenView.post` to verify → role-check → reset → issue (closes the challenge-burn observation). Tests: `test_a2_c_unauthenticated_row_creation`, `test_a7_a_repeated_wrong_challenge_invalidates_token`.
- **A1.a (Low) — `SECRET_KEY` minimum-length check at `create_app()`.** Test: `test_a1_a_weak_secret_key_startup_check`.
- **A2.e (Low) — normalize the wrong-`Content-Type` rejection** so the status code doesn't reveal whether a token row exists. Test: `test_a2_e_content_type_oracle`.

**Observations (no demonstration test; from the audit's Cross-cutting):**
- `authorize_admin` is bound to no endpoint (ADMIN ≡ MILLER today) — bind it to an ADMIN-only endpoint or document the tier as reserved.
- The `remote_app` test fixture references `host_netloc`/`remote_host_netloc` as bare names rather than fixture parameters (pre-existing bug, doesn't affect the A3.b demo) — separate `fix(test):` PR.

Originating report:
- [API authentication audit — Findings table + Recommendations](audits/2026-05-31-api-authentication-audit.md)

---

## API auth protocol replacement (design cycle)

Beyond the targeted remediations above, the audit's Recommendations flag two structural roots: the JWT is an unbound symmetric bearer token (no `iss`/`aud`/`jti`, `rol` not re-validated), and the handshake is a roll-your-own challenge (RSA-OAEP encrypt + argon2 over a 122-bit random secret, while `Wallet.sign`/`validate_signature` sit unused). Evaluate **replacing the handshake** as its own brainstorm → spec → plan cycle. Two candidate directions named in the audit:
- **(a) Signed-nonce challenge-response reusing `Wallet.sign`** — low-risk interim; deletes the encrypt/AES-GCM + argon2-on-random-secret path, keeps the rest of the stack.
- **(b) RFC 9421 HTTP Message Signatures / RS256 client-assertion** — stateless; removes the challenge round-trip, the `ApiToken` table, and the shared symmetric `SECRET_KEY` for issuance. Larger change (per-request signing on every client).

Originating report:
- [API authentication audit — Recommendations: targeted fixes vs. protocol replacement](audits/2026-05-31-api-authentication-audit.md)

---

## Closed items (historical reference)

Each removed from this file when the closing PR landed. Keep here for now so future Claude sessions can see what was on the list.

- ✅ **`app.clients` teardown** — closed by [PR #63](https://github.com/gumptionthomas/cancelchain/pull/63) (Phase 5b follow-up). Was originally Phase 5b deferral.
- ✅ **`Wallet.key` type tightening** — closed by [PR #66](https://github.com/gumptionthomas/cancelchain/pull/66). Was originally Phase 5a deferral.
- ✅ **Recursive CTE in `_rebuild_longest_chain_blocks`** — closed by [PR #68](https://github.com/gumptionthomas/cancelchain/pull/68) (Phase 6.5). Was originally Phase 6 deferral.
- ✅ **`_is_longest()` per-call query cost** — closed by [PR #68](https://github.com/gumptionthomas/cancelchain/pull/68) (Phase 6.5). Was originally raised by Copilot on PR #65.
- ✅ **Smart-reorg rebuild (Phase 6.6)** — closed by [PR #72](https://github.com/gumptionthomas/cancelchain/pull/72). Shallow reorgs are now O(reorg depth) instead of O(chain length); the full-rebuild path remains as the bootstrap + catastrophic-deep-reorg fallback. Originated as the algorithmic-cliff concern surfaced during the Phase 6.5 back-of-envelope analysis (1-block reorg on a 5-year chain previously took 4–22 min).
- ✅ **Phase 7 — SQLAlchemy 2.0 modernization** — closed by docs PRs [#75](https://github.com/gumptionthomas/cancelchain/pull/75) + [#77](https://github.com/gumptionthomas/cancelchain/pull/77) and impl PRs [#76](https://github.com/gumptionthomas/cancelchain/pull/76) (Phase 7a: translated all 94 legacy `Model.query` / `db.session.query(...)` call sites to the SA 2.0 idiom across `models.py`, `api.py`, `browser.py`, `chain.py`, `tests/test_models.py`, `tests/test_chain.py`; migrated 21 `Query[X]` return + 3 param annotations to `Select[tuple[X]]`; added `tests/_sa_helpers.py` with `_count`/`_count_select` helpers) and [#78](https://github.com/gumptionthomas/cancelchain/pull/78) (Phase 7b: switched to `db = SQLAlchemy(model_class=Base)` with `class Base(DeclarativeBase): pass`, moved all 11 `db.Model` subclasses to direct `(Base):` subclassing, removed the `# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` block, added 12 narrowly-scoped `# type: ignore[no-any-return]` ignores at chain-factory returns documenting FSA's facade typing limitation with a documented retirement path). Test count stayed 236 across both impl PRs; bench harness (~0.25 ms/step on local SQLite) unchanged. Originally planned as Phase 6 before that slot was repurposed for the recursive-CTE bottleneck fix; carried Phase 3's explicit sunset commitment for the per-file mypy override.
- ✅ **Phase 8 — Flask-Migrate (Alembic) integration** — closed by docs PR [#80](https://github.com/gumptionthomas/cancelchain/pull/80) and impl PR [#81](https://github.com/gumptionthomas/cancelchain/pull/81). Introduced Flask-Migrate as the schema-migration framework: added `Flask-Migrate>=4` dependency, wired `Migrate(app, db, directory=_MIGRATIONS_DIR)` into `create_app()` with a package-relative path (`src/cancelchain/migrations/`) so the CLI works from any CWD, set `MetaData(naming_convention=...)` on `Base.metadata` per Alembic's recommended `ix_`/`uq_`/`ck_`/`fk_`/`pk_` prefixes, changed `cancelchain init` from `db.create_all()` to `flask_migrate.upgrade()`, generated and hand-reviewed the initial migration (`0ca0de5fb211_initial_schema.py` — 12 `op.create_table` calls covering all 11 ORM models + the `block_transaction` association table), and added a fifth CI gate (`cancelchain db upgrade` + `cancelchain db check` against an absolute-URI throwaway SQLite) that catches model edits without a matching migration. Test count stayed 236; bench harness (~0.25 ms/step) unchanged. Verified CWD-independent install via real `uv build --wheel` + `uv pip install` + `cd /tmp && cancelchain init` cycle, plus full `docker build` + `cancelchain init` inside the container. Originated as Phase 3's "Phase 7 — Alembic" deferral.
- ✅ **Verification pipeline threat-modeled audit** — closed by docs PR [#83](https://github.com/gumptionthomas/cancelchain/pull/83) (spec + implementation plan) and impl PR [#84](https://github.com/gumptionthomas/cancelchain/pull/84) (audit report + demonstration tests). First systematic security audit pass over cancelchain's block/chain/transaction verification pipeline. 42 attack attempts traced across 7 adversary categories (external transactor, hostile peer, malicious miller, replay attacker, reorg attacker, race/concurrency, genesis/edge-case) through the 16 `validate_*` methods + 36 exception classes. Six findings produced (0 Critical, 0 High, 2 Medium, 4 Low), each with a `@pytest.mark.xfail(strict=True)` demonstration test in `tests/test_verification_audit.py` — strict mode forces xfail removal as part of each remediation PR. Adversary 3 (Malicious Miller, most capable) surfaced zero findings; PoW core is sound. Audit doc at `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`; remediation items broken out as their own roadmap entry above. Test count stayed 236 passed + 6 xfailed; bench harness unchanged.
- ✅ **Audit finding A2.e — `Node.fill_chain` partial fork-prefix adoption** — closed by docs PR [#86](https://github.com/gumptionthomas/cancelchain/pull/86) (spec + plan) and impl PR [#87](https://github.com/gumptionthomas/cancelchain/pull/87). Made `Node.fill_chain`'s apply loop atomic via deferred commits: added a keyword-only `commit: bool = True` parameter to `BlockDAO.commit()` / `Block.to_db()` / `Chain.to_db()` / `Chain.add_block()` / `Node.add_block()` / `Node.create_chain()`; `fill_chain` passes `commit=False` per block and commits once at the end (rollback on exception). A validation failure on any block rolls back every earlier block's persistence within the same call. Test went from `@pytest.mark.xfail(strict=True)` to a real pass. Originated as finding A2.e (Medium) in the 2026-05-29 verification pipeline audit.
- ✅ **Audit finding A4.c — coinbase-txid replay inflates miller `wallet_balance`** — closed by docs PRs [#88](https://github.com/gumptionthomas/cancelchain/pull/88) (v1, superseded) + [#89](https://github.com/gumptionthomas/cancelchain/pull/89) (v2 design+plan) and impl PR [#90](https://github.com/gumptionthomas/cancelchain/pull/90). The v1 lineage-uniqueness check proved unimplementable (coinbase txids collide for legitimate same-second blocks); v2 binds the block's `prev_hash` into the coinbase txid so consecutive blocks have unique coinbases, and validates `cb.prev_hash == block.prev_hash` (raising `MismatchedCoinbaseError`) to reject replays. Added a nullable `TransactionDAO.prev_hash` column via a regenerated base migration (pre-1.0, no legacy installs). Brings audit severity to 0 Critical / 0 High / 0 Medium / 4 Low.
- ✅ **Audit finding A7.b — alternate-genesis admission fragments the chain registry** — closed by docs PR [#91](https://github.com/gumptionthomas/cancelchain/pull/91) (design+plan) and impl PR [#92](https://github.com/gumptionthomas/cancelchain/pull/92). `Chain.validate_block` now rejects a block claiming genesis when a different genesis is already persisted, raising `DuplicateGenesisError` (via a `Block.genesis_from_db()` helper keyed on `idx == 0`). This also closes A7.j (disjoint-ancestor reorg), whose only entry path is alternate-genesis admission. No schema change. Brings audit severity to 0 Critical / 0 High / 0 Medium / 3 Low.
- ✅ **Audit finding A7.h — subjects accept non-printable characters** — closed by docs PR [#93](https://github.com/gumptionthomas/cancelchain/pull/93) (design+plan) and impl PR [#94](https://github.com/gumptionthomas/cancelchain/pull/94). `validate_subject` / `validate_raw_subject` now require the decoded raw subject to satisfy `str.isprintable()` (via a shared `_valid_raw_subject()` helper) — rejecting control characters, bidi overrides, ZWJ, zero-width and non-ASCII whitespace, private-use, and unassigned codepoints, while allowing letters, marks, numbers, punctuation, symbols/emoji, and plain spaces. No schema change. Brings audit severity to 0 Critical / 0 High / 0 Medium / 2 Low.
- ✅ **Audit finding A7.e — `TXN_TIMEOUT` comparison-operator inconsistency** — closed by docs PR [#95](https://github.com/gumptionthomas/cancelchain/pull/95) (design+plan) and impl PR [#96](https://github.com/gumptionthomas/cancelchain/pull/96). The expiry boundary is now defined once by a `txn_is_expired(txn_ts, reference_dt)` helper in `block.py` (expired ⟺ strictly older than `TXN_TIMEOUT`; open boundary). `Block.validate_transaction` (behavior-identical), `Node.discard_expired_pending_txns`, and `Miller.pending_chain_txns` route through it; the `PendingTxnDAO.json_datas` SQL already matched and carries a cross-ref comment. No schema change. Brings audit severity to 0 Critical / 0 High / 0 Medium / 1 Low.
- ✅ **Audit finding A1.f — mempool admits already-mined txids** — closed by docs PR [#97](https://github.com/gumptionthomas/cancelchain/pull/97) (design+plan) and impl PR [#98](https://github.com/gumptionthomas/cancelchain/pull/98). `Node.receive_transaction` now performs a global `TransactionDAO.get(txn.txid)` lookup after `txn.validate()` and raises `DuplicateMinedTransactionError(InvalidTransactionError)` when the txid is already mined — before the pending-add/gossip, so replays never enter the pool. Global indexed lookup (O(1)) rather than a lineage chain-walk. Mempool admission only; no consensus change, no schema change. **This was the last open audit finding — the verification-pipeline audit is now fully remediated: 0 Critical / 0 High / 0 Medium / 0 Low.**
- ✅ **API authentication threat-modeled audit** — closed by docs PR [#101](https://github.com/gumptionthomas/cancelchain/pull/101) (design + plan) and impl PR [#102](https://github.com/gumptionthomas/cancelchain/pull/102) (audit report + demonstration tests). Companion to the verification-pipeline audit; first systematic pass over the API auth layer (token handshake, JWT issuance/validation via `authorize()`, role keying via `*_ADDRESSES` exact-address allowlists). 7 adversary categories traced; **8 findings (0 Critical / 0 High (A4.a remediated, PR #105) / 5 Medium / 2 Low)**, each with a `@pytest.mark.xfail(strict=True)` test in `tests/test_auth_audit.py`. The former High (A4.a) was an unvalidated role-regex foot-gun — now fixed with exact-address matching + `Role.validate_config` startup guard; the Medium cluster (A3.a/A5.b/A3.b) shares one root cause — `authorize()` trusts the signed `rol` without re-validating live config. Adversary 6 (authorized insider) was fully clean; the JWT decode path correctly pins HS256 and fails closed on `alg=none`/RS256-confusion/expired. Recommendations resolve the targeted-fixes-vs-protocol-replacement question (the challenge/response is a known roll-your-own), naming signed-nonce (`Wallet.sign`) and RFC 9421 / RS256 client-assertion candidates. Remediation items + the replacement design cycle are broken out as their own roadmap entries above. Suite: 256 passed + 8 xfailed + 1 skipped. Review process note: the internal cross-model (Sonnet) review loop caught 28 issues + pruned 1 false-positive test across the two PRs; Copilot's single backstop on #102 had 0 actionable comments.
