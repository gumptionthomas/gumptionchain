# Roadmap — post-Phase-7

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

Originating spec:
- [Phase 6.5 spec — Risks](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md) "Cross-worker stale cache"

---

## Phase 7+ — Alembic migration framework

Introduce Alembic for schema migrations. Greenfield posture means we don't have an installed-base problem yet, but adding the framework before going to prod prevents pain later (e.g., column adds, index changes, table renames).

Originating spec:
- [Phase 3 spec — What comes next](specs/2026-05-24-phase-3-lint-typing-ci-gating-design.md) "Phase 7 — Alembic"

---

## Closed items (historical reference)

Each removed from this file when the closing PR landed. Keep here for now so future Claude sessions can see what was on the list.

- ✅ **`app.clients` teardown** — closed by [PR #63](https://github.com/gumptionthomas/cancelchain/pull/63) (Phase 5b follow-up). Was originally Phase 5b deferral.
- ✅ **`Wallet.key` type tightening** — closed by [PR #66](https://github.com/gumptionthomas/cancelchain/pull/66). Was originally Phase 5a deferral.
- ✅ **Recursive CTE in `_rebuild_longest_chain_blocks`** — closed by [PR #68](https://github.com/gumptionthomas/cancelchain/pull/68) (Phase 6.5). Was originally Phase 6 deferral.
- ✅ **`_is_longest()` per-call query cost** — closed by [PR #68](https://github.com/gumptionthomas/cancelchain/pull/68) (Phase 6.5). Was originally raised by Copilot on PR #65.
- ✅ **Smart-reorg rebuild (Phase 6.6)** — closed by [PR #72](https://github.com/gumptionthomas/cancelchain/pull/72). Shallow reorgs are now O(reorg depth) instead of O(chain length); the full-rebuild path remains as the bootstrap + catastrophic-deep-reorg fallback. Originated as the algorithmic-cliff concern surfaced during the Phase 6.5 back-of-envelope analysis (1-block reorg on a 5-year chain previously took 4–22 min).
- ✅ **Phase 7 — SQLAlchemy 2.0 modernization** — closed by docs PRs [#75](https://github.com/gumptionthomas/cancelchain/pull/75) + [#77](https://github.com/gumptionthomas/cancelchain/pull/77) and impl PRs [#76](https://github.com/gumptionthomas/cancelchain/pull/76) (Phase 7a: translated all 94 legacy `Model.query` / `db.session.query(...)` call sites to the SA 2.0 idiom across `models.py`, `api.py`, `browser.py`, `chain.py`, `tests/test_models.py`, `tests/test_chain.py`; migrated 21 `Query[X]` return + 3 param annotations to `Select[tuple[X]]`; added `tests/_sa_helpers.py` with `_count`/`_count_select` helpers) and [#78](https://github.com/gumptionthomas/cancelchain/pull/78) (Phase 7b: switched to `db = SQLAlchemy(model_class=Base)` with `class Base(DeclarativeBase): pass`, moved all 11 `db.Model` subclasses to direct `(Base):` subclassing, removed the `# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` block, added 12 narrowly-scoped `# type: ignore[no-any-return]` ignores at chain-factory returns documenting FSA's facade typing limitation with a documented retirement path). Test count stayed 236 across both impl PRs; bench harness (~0.25 ms/step on local SQLite) unchanged. Originally planned as Phase 6 before that slot was repurposed for the recursive-CTE bottleneck fix; carried Phase 3's explicit sunset commitment for the per-file mypy override.
