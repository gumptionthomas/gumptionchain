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

## Audit remediation — CLI findings (2026-06-02)

The [CLI / operator-surface threat-model audit](audits/2026-06-02-cli-audit.md) (the fifth audit; design+plan PR #126) found **0 Critical / 0 High / 1 Medium / 1 Low** — 9 of 11 candidates refuted (cross-references to the closed verification/auth audits, operator self-harm, or UX nits), 10 confirmed strengths. The two findings have strict-xfail demonstrations in `tests/test_cli_audit.py`:

- **CLI1 (Medium) — `wallet create` writes the private key world-readable & unencrypted.** `Wallet.to_file` → `open(filename, 'wb')` lands at the process umask (no `chmod 0o600`, no `O_EXCL`, no passphrase); a co-located local user reads a live signing identity. Fix: create the PEM atomically owner-only (`os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` or temp-file + `chmod 0o600` + rename); optionally add `--passphrase`. Test: `test_cli1_wallet_create_writes_private_key_0600`.
- **CLI4 (Low) — `import` buffers an unbounded single line.** `import_blocks_command` reads each line with no length bound before `Block.from_json`; a crafted `.jsonl` with one multi-GB line OOM-kills the one-shot import (bounded, recoverable, idempotent-resumable). Fix: bound per-line input before parsing. Test: `test_cli4_import_bounds_line_length`.

Originating report:
- [CLI / operator-surface audit](audits/2026-06-02-cli-audit.md) — findings table, strengths, recommendations.

---

## Audit remediation — wallet/crypto findings (2026-06-02)

The [wallet/crypto threat-model audit](audits/2026-06-02-wallet-crypto-audit.md) (the fourth audit; design+plan PR #120) found **0 Critical / 0 High / 0 Medium / 2 Low** — no exploitable findings, 12 confirmed strengths. The two Low items are non-exploitable defense-in-depth / hygiene residuals, each with a demonstration in `tests/test_wallet_audit.py` (strict-xfail while open, a passing regression once remediated):

- ✅ **WC1 (Low) — remove dead bespoke `encrypt`/`decrypt`** — closed. Removed `Wallet.encrypt`/`Wallet.decrypt` (and the now-unused `AESGCM` import + `GCM_NONCE_SIZE`/`AES_SESSION_KEY_SIZE` constants) and their tests; `test_wc1_bespoke_encrypt_decrypt_removed` is now a passing regression.
- ✅ **WC2 (Low) — enforce a public-exponent check on key import** — closed. `Wallet.__init__` now rejects any imported key whose public exponent ≠ `PUBLIC_EXPONENT` (65537, the node's own generation exponent, extracted as a shared constant), alongside the existing `key_size` check; `test_wc2_import_rejects_degenerate_exponent` is now a passing regression.

**Both findings remediated — the wallet/crypto audit is fully closed (0/0/0/0 open).**

**Observations (no finding, address opportunistically):** `schema.validate_signature` performs no key→address binding (safe only because every caller runs `validate_pk_address` first — a load-bearing upstream invariant); the KDF behind `BestAvailableEncryption` on encrypted exports is unpinned (operator-local).

Originating report:
- [Wallet & cryptographic-primitives audit](audits/2026-06-02-wallet-crypto-audit.md) — findings table, strengths, recommendations.

---

## Audit remediation — verification pipeline findings (PR #84)

**All six findings from the 2026-05-29 verification pipeline audit are remediated** (A2.e, A4.c, A7.b, A7.h, A7.e, A1.f — see Closed items). The audit is fully closed at 0 Critical / 0 High / 0 Medium / 0 Low; every `@pytest.mark.xfail(strict=True)` demonstration is now a passing regression test in `tests/test_verification_audit.py`.

**Not on this list (audit-surfaced but not validation-pipeline remediations):**
- Reorg double-spend (A4.d note, A5.a, A5.b) — canonical PoW property; mitigation is operator confirmation-depth guidance in user-facing docs, not validation code.
- ChainFill orphan rows on process crash (A5.c hygiene observation) — operational hygiene; possible periodic-sweep job, below per-finding remediation bar.

Originating audit:
- [Verification pipeline threat-modeled audit](audits/2026-05-29-verification-pipeline-audit.md) — Findings table, Recommendations section.

---

## ✅ Audit remediation — API authentication findings (fully closed 0/0/0/0)

The 2026-05-31 API authentication audit ([report](audits/2026-05-31-api-authentication-audit.md); design+plan [#101](https://github.com/gumptionthomas/cancelchain/pull/101), findings+tests [#102](https://github.com/gumptionthomas/cancelchain/pull/102)) produced **8 findings**. All are now closed — four individually by prior PRs, four dissolved by the protocol replacement (PR #111):

- ✅ **A4.a (High) — exact-match role allowlists** — closed by PR #105. Replaced regex matching with exact-address membership + a READER-only `"*"` sentinel, validated at startup (`Role.validate_config` → `InvalidRoleConfigError`). Test: `test_a4_a_overbroad_admin_regex_does_not_escalate` (passing regression).
- ✅ **A3.a + A5.b (Medium) — live-role re-check in `authorize()`** — closed by PR #107. `authorize()` now calls `Role.address_role(address)` on every request; insufficient or absent live role → 403. Tests: `test_a3_a_forged_role_claim_accepted`, `test_a5_b_stale_role_rejected_after_config_revocation` (passing regressions).
- ✅ **A3.b (Medium) — node-binding** — closed by PR #109 (JWT `iss`/`aud`); dissolved structurally by PR #111 (`cc-sig-v1` canonical includes `node_host`). Test: `test_a3_b_cross_node_token_replay` (re-expressed as signed-request regression).
- ✅ **A2.c + A7.a (Medium) + A1.a + A2.e (Low) — dissolved by protocol replacement** — PR #111. The `/api/token` endpoint, `ApiToken` table, argon2, and `SECRET_KEY`-as-auth are all gone. No unauthenticated write path, no argon2 amplification surface, no content-type oracle, no weak-key forgery risk. Demonstration tests removed (gaps no longer exist).

**Audit is fully closed: 0 Critical / 0 High / 0 Medium / 0 Low.**

**Observation still open (no finding):** `authorize_admin` is bound to no endpoint (ADMIN ≡ MILLER today) — bind it to an ADMIN-only endpoint or document the tier as reserved.

Originating report:
- [API authentication audit — Findings table + Recommendations](audits/2026-05-31-api-authentication-audit.md)

---

## ✅ API auth protocol replacement (PR #111)

The audit's Recommendations flagged two structural roots: the JWT was an unbound symmetric bearer token, and the handshake was a roll-your-own challenge (RSA-OAEP encrypt + argon2 over a 122-bit random secret, while `Wallet.sign`/`validate_signature` sat unused). The replacement design cycle evaluated two candidates (signed-nonce and RFC 9421 / RS256 client-assertion) and implemented a bespoke per-request signature scheme: **`cc-sig-v1`** (PR #111).

`cc-sig-v1` signs each request with the caller's RSA private key over a canonical string (version/method/path/query/body-digest/node-host/timestamp/address). The scheme is stateless, node-bound by construction, and documented in `docs/api-auth-protocol.md`. `Wallet.sign` is now the authentication primitive.

RFC 9421 is deferred as an additive `v2` scheme — see forward entry below.

Originating report:
- [API authentication audit — Recommendations: targeted fixes vs. protocol replacement](audits/2026-05-31-api-authentication-audit.md)

---

## RFC 9421 as an additive `cc-sig-v2` auth scheme

RFC 9421 HTTP Message Signatures is deferred until there is third-party-client demand. The `CC-Sig-Version` header is versioned for this: adding `v2` alongside `v1` requires no breaking change. When implemented, both versions would be accepted side-by-side during a transition window before `v1` is sunset.

---

## Closed items (historical reference)

Each removed from this file when the closing PR landed. Keep here for now so future Claude sessions can see what was on the list.

- ✅ **(perf follow-up) Indexed/SQL-filtered mempool expiry** — closed by PR [#118](https://github.com/gumptionthomas/cancelchain/pull/118). Added an index on `pending_txn.timestamp` (folded into the pre-1.0 base migration); `discard_expired_pending_txns` now uses `PendingTxnDAO.delete_expired(cutoff)` — an indexed SQL filter (`timestamp < cutoff`) + single-commit ORM `session.delete()` per row (so the `ioflows` cascade removes children — the FK has no `ON DELETE CASCADE`); `Miller.pending_chain_txns` iterates `query_json(expired=cutoff)` so the mill path only parses live rows; new `block.expiry_cutoff(reference_dt)` is the single-source cutoff helper. Open-boundary semantics preserved. Surfaced by the 2026-06-01 P2P/networking audit (N2 finding).

- ✅ **P2P/networking threat-modeled audit — fully closed (0 Critical / 0 High / 0 Medium / 0 Low)** — closed by docs PR [#112](https://github.com/gumptionthomas/cancelchain/pull/112) (design + plan) and impl PRs below. All four findings (N1–N4) are remediated; every `@pytest.mark.xfail(strict=True)` demonstration in `tests/test_network_audit.py` is now a passing regression test. The P2P/networking audit is **fully closed at 0/0/0/0**. Originating report: [P2P/networking audit](audits/2026-06-01-network-p2p-audit.md).
  - ✅ **N1 (High) — `fill_chain` unbounded ancestor walk** — configurable depth cap (`CC_MAX_CHAIN_FILL_DEPTH`) + returned-hash check in `request_block`. Closed by PR #114.
  - ✅ **N2 (Medium) — mempool has no admission cap** — `CC_MAX_PENDING_TXNS` admission cap; TxnView maps full pool to HTTP 503. Closed by PR #115.
  - ✅ **N3 (Medium) — duplicate-txn re-gossip amplification** — gate re-gossip on newly-added flag (mirror the block path). Closed by PR #116.
  - ✅ **N4 (Low) — synchronous broker publish on the request thread** — `init_tasks` sets `task_publish_retry=False`, `broker_connection_timeout=2.0`, `broker_connection_max_retries=0` before `celery.conf.update(app.config)`, bounding a degraded-broker publish to ~2 s. Closed by PR #117.

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
- ✅ **API authentication threat-modeled audit** — closed by docs PR [#101](https://github.com/gumptionthomas/cancelchain/pull/101) (design + plan) and impl PR [#102](https://github.com/gumptionthomas/cancelchain/pull/102) (audit report + demonstration tests). Companion to the verification-pipeline audit; first systematic pass over the API auth layer (token handshake, JWT issuance/validation via `authorize()`, role keying via `*_ADDRESSES` exact-address allowlists). 7 adversary categories traced; **8 findings (0 Critical / 0 High (A4.a remediated, PR #105) / 3 Medium (A3.a + A5.b remediated, PR #107) / 2 Low)**, each with a `@pytest.mark.xfail(strict=True)` test in `tests/test_auth_audit.py`. The former High (A4.a) was an unvalidated role-regex foot-gun — now fixed with exact-address matching + `Role.validate_config` startup guard; the Medium cluster (A3.a/A5.b/A3.b) shared one root cause — `authorize()` previously trusted the signed `rol` without re-validating live config; A3.a and A5.b are now remediated by the per-request live-role re-check. Adversary 6 (authorized insider) was fully clean; the JWT decode path correctly pins HS256 and fails closed on `alg=none`/RS256-confusion/expired. Recommendations resolve the targeted-fixes-vs-protocol-replacement question (the challenge/response is a known roll-your-own), naming signed-nonce (`Wallet.sign`) and RFC 9421 / RS256 client-assertion candidates. Remediation items + the replacement design cycle are broken out as their own roadmap entries above. Suite: 256 passed + 8 xfailed + 1 skipped. Review process note: the internal cross-model (Sonnet) review loop caught 28 issues + pruned 1 false-positive test across the two PRs; Copilot's single backstop on #102 had 0 actionable comments.
