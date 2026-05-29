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

Six findings from the 2026-05-29 verification pipeline audit. Each has a demonstration `@pytest.mark.xfail(strict=True)` test in `tests/test_verification_audit.py`; remediation removes the xfail and the test becomes a real pass (strict mode forces this).

Pick from this list in priority order (recommended ordering per the audit's Recommendations section — based on tractability + blast-radius alignment, not strict severity).

1. **A2.e — Medium — `Node.fill_chain` partial fork-prefix adoption.** `Node.fill_chain` commits staged blocks one-at-a-time; a hostile peer appending an invalid tip leaves prefix blocks persisted and advances `ChainDAO`'s tip into the fork. Remediation: wrap the apply loop in a single transaction, or validate the entire range before persisting any block (`src/cancelchain/node.py:345-351`). Test: `test_a2_e_partial_chain_adoption_via_invalid_tip`.
2. **A4.c — Medium — Coinbase-txid replay inflates miller `wallet_balance`.** A MILLER can mine a block that replays another miller's coinbase txn; duplicate `block_transactions` m2m rows inflate the original miller's reported balance by REWARD per replay. `InflowDAO` uniqueness still blocks spending the inflated balance, but the accounting invariant is violated. Remediation: add `Chain.get_transaction(cb.txid, start_block=block)` check in `Chain.validate_block_coinbase`. Test: `test_a4_c_ii_coinbase_replay_inflates_balance`.
3. **A7.b — Low — Alternate-genesis admission fragments chain registry.** `Chain.validate_block` has no canonical-genesis uniqueness check; any block with `prev_hash=GENESIS_HASH`, `idx=0`, `target=MAX_TARGET` is accepted as a new chain. Each admission creates a fresh `ChainDAO` row. Closing this also closes A7.j (disjoint-chain reorg entry path) — two-for-one. Test: `test_a7_b_alternate_genesis_fragments_chain_registry`.
4. **A7.h — Low — `validate_subject` accepts non-printable characters.** Length is enforced (1-79 chars) but character class is not — null bytes, control chars, RTL override, ZWJ all pass. Remediation: restrict to printable UTF-8 via unicode-category allow-list in `validate_subject`. Test: `test_a7_h_non_printable_subject_accepted`.
5. **A7.e — Low — `TXN_TIMEOUT` comparison operator inconsistency.** Three call sites use three different operators (`<` in `Block.validate_transaction`, `>` in `Miller.pending_chain_txns`, `<=` in `Node.discard_expired_pending_txns`). At-the-boundary txn is non-expired per block validator, expired per pending maintenance. Pure refactor — pick one operator and propagate. Test: `test_a7_e_txn_timeout_boundary_inconsistency`.
6. **A1.f — Low — Mempool admits already-mined txids.** `Node.receive_transaction` does not reject txids that exist in `TransactionDAO`, so an adversary can replay any mined transaction into the pending pool where it lingers until 4h `TXN_TIMEOUT` expiry. Chain is unaffected (block-assembly filters); pure mempool noise. Remediation: chain-side `TransactionDAO` lookup in `Node.receive_transaction`. Test: `test_a1_f_mined_txid_replay_into_pending`.

**Not on this list (audit-surfaced but not validation-pipeline remediations):**
- Reorg double-spend (A4.d note, A5.a, A5.b) — canonical PoW property; mitigation is operator confirmation-depth guidance in user-facing docs, not validation code.
- ChainFill orphan rows on process crash (A5.c hygiene observation) — operational hygiene; possible periodic-sweep job, below per-finding remediation bar.

Originating audit:
- [Verification pipeline threat-modeled audit](audits/2026-05-29-verification-pipeline-audit.md) — Findings table, Recommendations section.

---

## Future audit — API authentication layer

The API authentication layer (JWT handshake, role keying via `*_ADDRESSES` regex, RSA+AES challenge in `api.py`) was deliberately scoped out of the verification audit (per its Non-goals). A companion audit pass would apply the same threat-modeled methodology to authentication: adversary categories (e.g., token replay, role-regex escape, challenge-decryption bypass, expired-but-valid-signature replay), per-attack traces through `api.Role.address_role` + `ApiToken` + the handshake endpoints, and `@pytest.mark.xfail(strict=True)` demonstration tests for any gaps.

Why deferred: the verification pipeline (chain correctness) is the more foundational layer — gaps there are existential. Auth is bounded blast radius and the audit methodology is now well-established.

Originating spec:
- [Verification audit — Non-goals](specs/2026-05-29-verification-pipeline-audit-design.md) "Not auth"
- [Verification audit — What comes next](specs/2026-05-29-verification-pipeline-audit-design.md) "API auth audit"

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
